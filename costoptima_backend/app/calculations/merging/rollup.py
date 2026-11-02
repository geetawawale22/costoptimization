"""Named BOM ownership/variant rollup, primary EndItem/Analysis SUMPRODUCT."""
from decimal import Decimal
from ..common import get,number,normalized,CalculationError


def amount(row,variant,root_flag=1):
    if row.status!='success' or row.calculation_status!='CALCULATED':raise CalculationError('incomplete_component','Cannot roll up a failed/blocked component')
    values=row.values
    keys=['Grand Total Cost/Pc','Total Cost/Pc','Total/Pc','Total']
    key=next((k for k in keys if get(values,k) is not None),None)
    if key is None:raise CalculationError('missing_total','Component has no final cost')
    flags=row.inputs.get('_variants',{})
    if variant not in flags:raise CalculationError('missing_variant',f'Variant {variant} is absent from component')
    return number(values[key],key)*number(get(row.inputs,'Qty/Veh'),'Qty/Veh')*number(flags[variant] or 0,variant)*number(root_flag or 0,variant)


def rollup_owner(record,rows):
    owned=[r for r in rows if not r.dependency_only and normalized(get(r.inputs,'End Item'))==normalized(get(record,'End Item')) and normalized(r.type)!='end item']
    if not owned:raise CalculationError('missing_components','END ITEM has no owned components')
    if any(r.status!='success' for r in owned):raise CalculationError('incomplete_end_item','At least one owned component is blocked; no zero-filled End Item total')
    flags=record.get('_variants',{})
    if not flags:raise CalculationError('missing_variant','END ITEM has no variant columns')
    return {variant:sum((amount(r,variant,flag) for r in owned),Decimal(0)) for variant,flag in flags.items()}


LEGACY_ANALYSIS_CATEGORIES=('RM cost','Process cost','Buy Std','Buy Non Std','Assy','Special Process','Markup','RM cost Soft','Process cost Soft','Markup Soft','PCBA','Machine Scrap Recovery','Other6','PnF','Total')
LEGACY_ANALYSIS_BREAKUP=('ICC Cost','REJ Cost','O&H Cost','PROFIT Cost')


def _analysis_number(row,*fields):
    for field in fields:
        value=get(row.values,field)
        if value is not None:return number(value,field)
    raise CalculationError('analysis_value_missing','A source-required Analysis result is missing',fields[0],record_id=row.record_id)


def _analysis_type(row):
    return normalized(row.inputs.get('_ownership_type',get(row.inputs,'Type',row.type)))


def legacy_analysis(rows):
    """Primary Analysis11208-11989 and PCBA deduction18969-18973.

    Component quantity times variant applies; root variant does not. Missing
    values block the summary. Optional blank source categories stay blank.
    """
    components=[r for r in rows if not r.dependency_only and _analysis_type(r)!='end item']
    variants=list(dict.fromkeys(v for r in rows for v in r.inputs.get('_variants',{})))
    make_families={normalized(r.commodity) for r in components if _analysis_type(r)=='make'}
    core={'stamping','forging','sand casting','gdc','pdc','thermal'}
    sand_suppliers={normalized(get(r.inputs,'Supplier')) for r in components if normalized(r.commodity)=='sand casting' and _analysis_type(r)!='buy non std'}
    results=[]
    for variant in variants:
        values={name:None for name in LEGACY_ANALYSIS_CATEGORIES}
        for name in ('RM cost','Process cost','Markup','PnF',*LEGACY_ANALYSIS_BREAKUP):values[name]=Decimal(0)
        issues=[];blocked=[]
        def add(category,amount):values[category]=(values[category] or Decimal(0))+amount
        def breakup(row,factor,*,handling=False,dies=False):
            add('ICC Cost',_analysis_number(row,'ICC COST')*factor)
            add('REJ Cost',_analysis_number(row,'REJ COST')*factor)
            overhead=_analysis_number(row,'OH COST','O&H Cost')
            if handling:overhead+=_analysis_number(row,'Handling COST')
            if dies:overhead+=_analysis_number(row,'Dies COST')
            add('O&H Cost',overhead*factor)
            add('PROFIT Cost',_analysis_number(row,'PROFIT COST')*factor)
        for row in components:
            try:
                if row.status!='success' or row.calculation_status!='CALCULATED':raise CalculationError('analysis_component_blocked','An Analysis component did not calculate',record_id=row.record_id)
                flags=row.inputs.get('_variants',{})
                if variant not in flags:raise CalculationError('analysis_variant_missing','A component has no verified variant field',variant,record_id=row.record_id)
                factor=number(get(row.inputs,'Qty/Veh'),'Qty/Veh')*number(flags[variant] or 0,variant)
                commodity,type_=normalized(row.commodity),_analysis_type(row)
                if type_=='buy non std':
                    basic=_analysis_number(row,'Basic Cost/Pc')
                    if commodity=='pcba':
                        board=_analysis_number(row,'PCBA Basic Cost/Pc')
                        add('Buy Non Std',(basic-board)*factor);add('PCBA',board*factor)
                    else:add('Buy Non Std',basic*factor)
                    add('Markup',_analysis_number(row,'MARKUP/Pc','Markup')*factor);breakup(row,factor)
                elif type_=='buy std' and not commodity:
                    add('Buy Std',_analysis_number(row,'Basic Cost/Pc')*factor)
                    add('Markup',_analysis_number(row,'MARKUP/Pc','Markup')*factor);breakup(row,factor)
                elif commodity=='hard' and type_=='assy':
                    add('Assy',_analysis_number(row,'Basic Cost/Pc')*factor)
                    add('Markup',_analysis_number(row,'MARKUP/Pc','Markup')*factor);breakup(row,factor)
                elif type_=='special process':
                    add('Special Process',_analysis_number(row,'Basic Cost/Pc')*factor)
                    add('Markup',_analysis_number(row,'MARKUP/Pc','Markup')*factor);breakup(row,factor)
                elif type_=='consign':
                    markup=_analysis_number(row,'MARKUP/Pc','Markup')*factor
                    add('Markup',markup);add('O&H Cost',markup)
                elif type_ in {'pkg','frt'} and not commodity:add('PnF',_analysis_number(row,'Basic Cost/Pc')*factor)
                elif commodity=='pcba':
                    if row.values.get('_pcba_board_aggregated') is not True:raise CalculationError('analysis_pcba_board_missing','PCBA Analysis requires a grouped board result',record_id=row.record_id)
                    add('PCBA',_analysis_number(row,'Basic Cost/Pc')*factor)
                elif commodity=='soft':
                    add('RM cost Soft',_analysis_number(row,'RM Cost/Pc')*factor)
                    add('Process cost Soft',_analysis_number(row,'Process Cost/Pc')*factor)
                    add('Markup Soft',_analysis_number(row,'MARKUP/Pc')*factor);breakup(row,factor,handling=True)
                elif (commodity=='hard' and type_=='make') or (commodity in core and commodity in make_families):
                    add('RM cost',_analysis_number(row,'RM Cost/Pc')*factor)
                    add('Process cost',_analysis_number(row,'Process Cost/Pc')*factor)
                    add('Markup',_analysis_number(row,'MARKUP/Pc')*factor);breakup(row,factor,dies=commodity=='stamping')
                    if commodity=='sand casting':
                        if len(sand_suppliers)!=1:raise CalculationError('analysis_sand_precedence_unresolved','Original multi-supplier machine-scrap assignment overwrites in unordered supplier order',record_id=row.record_id)
                        add('Machine Scrap Recovery',-_analysis_number(row,'Machining Scrap Recovery')*factor)
                else:raise CalculationError('analysis_category_unresolved','No complete original Analysis category mapping for this branch',record_id=row.record_id,commodity=row.commodity,type=type_)
            except CalculationError as error:
                blocked.append(row.record_id);issues.append(error.as_dict())
        if issues:results.append({'variant':variant,'values':{},'status':'incomplete','blocked_records':list(dict.fromkeys(blocked)),'issues':issues})
        else:
            values['Total']=sum((values[name] or Decimal(0) for name in LEGACY_ANALYSIS_CATEGORIES if name!='Total'),Decimal(0))
            results.append({'variant':variant,'values':values,'status':'success','blocked_records':[],'issues':[],'legacy_numerically_verified':False})
    return results


def end_item_analysis(rows):
    """End_Analysis12089-12427, named owner selection without root multipliers.

    The original Master Output separates typed basic from material/process
    contributions. SOFT joins the owner RM/process/markup buckets here; it has
    separate buckets only in global Analysis. Missing producers remain blocked.
    """
    owners=list(dict.fromkeys(str(get(r.inputs,'End Item') or '') for r in rows))
    series=[]
    for owner in owners:
        owned=[r for r in rows if normalized(get(r.inputs,'End Item'))==normalized(owner)]
        for result in legacy_analysis(owned):
            values=result['values']
            if result['status']=='success':
                values={**values,'RM cost':(values.get('RM cost') or Decimal(0))+(values.get('RM cost Soft') or Decimal(0)),
                    'Process cost':(values.get('Process cost') or Decimal(0))+(values.get('Process cost Soft') or Decimal(0)),
                    'Markup':(values.get('Markup') or Decimal(0))+(values.get('Markup Soft') or Decimal(0))}
                keys=('RM cost','Process cost','Buy Std','Buy Non Std','PCBA','Assy','Special Process','Markup','Machine Scrap Recovery','PnF')
                values={key:values.get(key) or Decimal(0) for key in (*keys,*LEGACY_ANALYSIS_BREAKUP)}
                values['Total']=sum((values[key] for key in keys),Decimal(0))
            series.append({**result,'End Item':owner,'values':values})
    return series


def consolidate(rows):
    # Only final owner records enter summaries; producer-only records are excluded.
    components=[r for r in rows if normalized(r.type)!='end item' and not r.dependency_only]
    roots=[r for r in rows if normalized(r.type)=='end item']
    variants=list(dict.fromkeys(k for r in rows for k in r.inputs.get('_variants',{})))
    categories={'RM cost':'RM Cost/Pc','Process cost':'Process Cost/Pc','Markup':'MARKUP/Pc','PnF':'PnF/Pc'}
    analysis=[]
    for variant in variants:
        total={name:Decimal(0) for name in categories};failed=[]
        for r in components:
            if r.status!='success':failed.append(r.record_id);continue
            factor=number(get(r.inputs,'Qty/Veh'),'Qty/Veh')*number(r.inputs.get('_variants',{}).get(variant,0) or 0,variant)
            for category,key in categories.items():
                if key not in r.values:failed.append(r.record_id);continue
                total[category]+=number(r.values[key],key)*factor
        analysis.append({'variant':variant,'values':total if not failed else {},'status':'incomplete' if failed else 'success','blocked_records':list(dict.fromkeys(failed))})
    return {'end_item_analysis':end_item_analysis(rows),'legacy_analysis':legacy_analysis(rows),'Analysis':analysis,'End Item':[{'record_id':r.record_id,'End Item':get(r.inputs,'End Item'),'values':r.values,'status':r.status} for r in roots],
            'SBC':[{'Customer No':get(r.inputs,'Customer No'),'values':r.values,'status':r.status} for r in roots],
            'create_end_item_analysis':len({normalized(get(r.inputs,'End Item')) for r in roots})>1,
            'coverage':'development output; legacy matrix/layout and grouped contribution parity remain unverified'}
