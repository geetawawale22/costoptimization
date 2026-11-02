"""PCM forging_data: supplied weight, hardening and supplier-wide MAKE path.

Calculated Wt is supplied Forging Wt when that header exists, otherwise
supplied Calculated Wt (source13017-19/13057). Casting Wt is not equivalent.
"""
from decimal import Decimal
from ...common import required,get,number,CalculationError,empty,normalized
from ..shared import material,process_rate,process_lines,markups,finish,checkpoint
SOURCE={'function':'forging_data;forging;forg_estimation;setoftwo_format','lines':'12432-13920'}
ZERO=Decimal(0)


def _linked_number(record,field):
    marker=object();raw=get(record,field,marker)
    if raw is marker:raise CalculationError('missing_input',f'Required input {field} is missing',field)
    return ZERO if empty(raw) else number(raw,field)


def calculated_weight(record,*,required_for_process=False):
    """Source header preference preserves known blank/zero, not truthiness."""
    marker=object()
    for field in ('Forging Wt','Calculated Wt'):
        raw=get(record,field,marker)
        if raw is not marker:return ZERO if empty(raw) else number(raw,field)
    if required_for_process:
        raise CalculationError('forging_weight_missing',
            'Hardening needs supplied Forging Wt or Calculated Wt; Casting Wt is not equivalent','Calculated Wt')
    return None


def _process_location(record,context):
    # Source12949-60 selects the first family input's location, then AD.
    rows=[r for r in context.rows if normalized(get(r,'Commodity'))=='forging']
    positioned=[r for r in rows if isinstance(r.get('_detail_row'),int)]
    # Parser provenance is an input order, not a positional master key. BOM
    # order may differ from the original Forging sheet's physical row order.
    first=min(positioned,key=lambda r:r['_detail_row']) if positioned else next(iter(rows),record)
    return get(first,'Sector & Location') or 'AD'


def section_weight(section,density):
    """Source12631 annular/rectangular weight using named mm dimensions.

    Density must be the matched DB material density in g/cm3. 1e6 converts
    mm3*g/cm3 to kg. The original3.14 is retained; its hardcoded steel density
    is never a fallback. Empty editable sections are not invented input rows.
    """
    density=number(density,'GSM/ Density')
    if density<=0:raise CalculationError('forging_density_invalid','Geometry density must be positive','GSM/ Density')
    outer=_linked_number(section,'Outer Dia(in mm)');thickness=_linked_number(section,'Thickness(in mm)')
    if outer:
        inner=_linked_number(section,'Hollow Dia(in mm)')
        if outer<0 or inner<0 or inner>outer or thickness<0:
            raise CalculationError('forging_geometry_invalid','Invalid annular section dimensions')
        volume=Decimal('3.14')*((outer/2)**2-(inner/2)**2)*thickness
    else:
        length=_linked_number(section,'Length( in mm)');breadth=_linked_number(section,'Breadth(in mm)')
        if min(length,breadth,thickness)<0:
            raise CalculationError('forging_geometry_invalid','Geometry dimensions must be nonnegative')
        volume=length*breadth*thickness
    return volume*density/Decimal(1000000)


def geometry_weights(sections,density):
    """Explicit named original six solid/four hollow editable slots.

    This projection never replaces the independently supplied Calculated Wt.
    Normal input dimensions do not prove a binding to these editable slots.
    """
    if not isinstance(sections,(list,tuple)):
        raise CalculationError('forging_geometry_missing','Explicit named solid/hollow sections are required')
    groups={'solid section':[],'hollow section':[]}
    for section in sections:
        if not isinstance(section,dict):raise CalculationError('forging_geometry_invalid','A section must contain named dimensions')
        group=normalized(required(section,'Section'))
        if group not in groups:raise CalculationError('forging_geometry_invalid','Section must be Solid Section or Hollow Section','Section')
        groups[group].append({**section,'FRG WT':section_weight(section,density)})
    if len(groups['solid section'])>6 or len(groups['hollow section'])>4:
        raise CalculationError('forging_geometry_limit','Original estimate supports six solid and four hollow slots')
    return {'Solid Section Weight':sum((r['FRG WT'] for r in groups['solid section']),ZERO),
            'Hollow Section Weight':sum((r['FRG WT'] for r in groups['hollow section']),ZERO),
            'Sections':[r for rows in groups.values() for r in rows]}


def _set_of_two_estimate(record,context,weight,location):
    """Source12559 Normalising is an estimate, not another cost addition."""
    if 'setoftwo' not in normalized(get(record,'End Item')).replace(' ',''):return {}
    result={};issues=[]
    try:
        rate=process_rate(context,record,'Normalising',location=location)
        result['_master_rates']={'Normalising':rate}
        if weight is not None:result['Normalising Cost']=rate*weight
    except (CalculationError,ValueError) as error:
        issues.append({'code':'forging_normalising_rate_missing','field':'Normalising','location':str(location),
                       'master':'process_master','lookup_keys':{'process':'Normalising'},
                       'master_field':'Rate/UOM','master_group':str(location),
                       'master_error':error.as_dict() if hasattr(error,'as_dict') else {'message':str(error)},
                       'message':'Set-of-two estimate requires the exact named Process Master rate'})
    if weight is None:issues.append({'code':'forging_weight_missing','field':'Calculated Wt',
                                   'message':'Estimate requires supplied Forging Wt or Calculated Wt'})
    try:
        density=context.require_master('material_master',{'Material Code':required(record,'Material Code')},'GSM/ Density')
        if density<=0:raise CalculationError('forging_density_invalid','Geometry density must be positive','GSM/ Density')
        result['GSM/ Density']=density
    except (CalculationError,ValueError) as error:
        issues.append({'code':'forging_density_invalid' if isinstance(error,CalculationError) and error.code=='forging_density_invalid' else 'forging_density_missing','field':'GSM/ Density',
                       'master':'material_master','lookup_keys':{'Material Code':get(record,'Material Code')},
                       'master_field':'GSM/ Density','required_units':'g/cm3',
                       'master_error':error.as_dict() if hasattr(error,'as_dict') else {'message':str(error)},
                       'message':'Editable set-of-two sections need verified positive matched material density in g/cm3'})
    if issues:result['_estimate_issues']=issues
    return result


def calculate(record,context):
    input_wt=_linked_number(record,'Input Wt');part=_linked_number(record,'Part Wt')
    marker=object();code=get(record,'Material Code',marker);kind=normalized(get(record,'Type'))
    if code is marker:raise CalculationError('missing_input','Required input Material Code header is missing','Material Code')
    # Source13037-65 leaves RM cells blank for material-less PROCESS/else.
    # A known selected blank is0, but a missing named material key is an error.
    if kind not in {'make','buy non std'} and empty(code):rate=scrap_rate=ZERO
    else:rate=material(context,record);scrap_rate=material(context,record,'Scrap Rate')
    scrap_wt=(input_wt-part)*Decimal('0.8');recovery=scrap_wt*scrap_rate
    checkpoint(context,record,{'Input Wt':input_wt,'Part Wt':part,'Rate/UOM':rate,'Scrap Rate':scrap_rate,
        'Scrap Wt':scrap_wt,'Scrap Recovery':recovery,'Material Cost before Hardening':rate*input_wt-recovery})
    lines=record.get('_hardening_lines')
    if lines is None:raise CalculationError('hardening_mapping_missing','Verified hardening process header grouping is required')
    weight=calculated_weight(record);location=_process_location(record,context)
    hardening=ZERO;hardening_costs=[]
    for line in lines:
        name=required(line,'name');quantity=number(line.get('quantity') or 0,name)
        input_basis=any(x in str(name).casefold() for x in ('forging','melting'))
        if not input_basis and quantity!=0 and weight is None:calculated_weight(record,required_for_process=True)
        basis=input_wt if input_basis else weight
        applied=ZERO if quantity==0 else quantity*number(basis,'Calculated Wt')
        rate_for_line=process_rate(context,record,name,location=location)
        cost=rate_for_line*applied;hardening+=cost
        hardening_costs.append({'name':name,'quantity':quantity,'rate':rate_for_line,'cost':cost,'basis':basis,'weight':applied})
        checkpoint(context,record,{'Completed Hardening Cost':hardening,'_hardening_costs':hardening_costs})
    has_make=context.batch_has_make(record);rm=rate*input_wt-recovery+(hardening if has_make else ZERO)
    checkpoint(context,record,{'Hardening Cost':hardening,'RM Cost/Pc':rm})
    variable=record.get('_process_lines')
    if variable is None:raise CalculationError('process_mapping_missing','Verified variable process grouping is required')
    # Source13125-29 selects strictly positive quantities; negative columns
    # are not included just because a different process has positive quantity.
    positive=[line for line in variable if number(line.get('quantity') or 0)>0]
    process=process_lines(context,{**record,'_process_lines':positive},location=location) if positive else ZERO if has_make else hardening
    values=markups(context,record,{'Input Wt':input_wt,'Part Wt':part,'Scrap Wt':scrap_wt,
        'Scrap Recovery':recovery,'Hardening Cost':hardening,'RM Cost/Pc':rm,
        'Process Cost/Pc':process,'Basic Cost/Pc':rm+process})
    values.update({'Rate/UOM':rate,'Scrap Rate':scrap_rate,'_hardening_costs':hardening_costs,
                   'Yield (%)':part/input_wt if input_wt else None})
    if weight is not None:values['Calculated Wt']=weight
    values['Total']=values['Basic Cost/Pc']+values['MARKUP/Pc']
    if not empty(code) and hasattr(context.master,'record'):
        metadata=context.master.record('material_master',{'material_code':code})
        values.update({field:metadata.value(field) for field in ('Material Family','Material Sub Family','Material Grade','UOM')})
        # Original13026/13032/13045 maps master Commodity to Material Type.
        values['Material Type']=metadata.value('Commodity')
    values.update(_set_of_two_estimate(record,context,weight,location))
    return finish(record,values)
