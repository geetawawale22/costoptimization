"""Original seat14511-14574 / seat_output14392-14507, named DB fields.

Seat is an output classification, never a newly invented commodity calculator.
The material-code dictionary's last-match behavior and broadcast assignments
are deliberately preserved; existing BOM Seat_Function values take precedence.
"""
from decimal import Decimal
from ..common import get,empty,normalized,number,CalculationError
from ..masters import MasterLookupError


def seat_enabled(rows):
    # Intentional correction of the old mere-column-existence gate15320-15344:
    # creating a Parent Commodity column alone must not activate Seat output.
    return any(normalized(get(row.inputs,'Parent Commodity'))=='seat' for row in rows)


def _cost(row):
    type_=normalized(row.inputs.get('_ownership_type',row.type));commodity=normalized(row.commodity)
    if type_=='end item':return None  # Matrix END ITEM has no Total Cost/Pc mapping.
    if type_ in {'buy std','buy non std','consign'}:keys=('Total/Pc','Total Cost/Pc')
    elif type_ in {'pkg','frt'}:keys=('Basic Cost','Basic Cost/Pc')
    elif commodity=='hard' and type_=='make':keys=('Grand Total Cost/Pc',)
    elif commodity=='hard' and type_=='assy':keys=('Total','Total Cost/Pc')
    else:keys=('Total','Total Cost','Total Cost/Pc')
    for key in keys:
        value=get(row.values,key)
        if value is not None:return number(value,key)
    raise CalculationError('seat_total_missing','Original Master Output total has no verified producer',record_id=row.record_id)


def consolidate_seat(rows,master,metadata=None):
    if not seat_enabled(rows):return {'enabled':False,'blocks':[],'issues':[]}
    matrix=master.records('seat_output_matrix')
    if not matrix:raise CalculationError('seat_matrix_missing','Seat output requires the configured database Seat Output Matrix')
    configured=[{key:record.value(key) for key in ('Base','Type','Selection Priority')} for record in matrix]
    bases=[str(row['Base']) for row in configured if not empty(row['Base'])]
    if len(bases)!=len(configured) or len(set(normalized(name) for name in bases))!=len(bases):
        raise CalculationError('seat_matrix_invalid','Seat output bases must be named and unique')
    if normalized(bases[-1])!='sub total':raise CalculationError('seat_subtotal_mapping_unresolved','Original subtotal must follow its named base contributions')
    priorities={}
    for rule in configured:
        priority=str(rule['Selection Priority'] or '').strip()
        if priority in {'1','2'}:priorities.setdefault(priority,str(rule['Type']))
    if set(priorities)!={'1','2'}:raise CalculationError('seat_priority_missing','Both original Seat selection priorities are required')
    allowed={priority:{str(row['Base']).upper() for row in configured if row['Type']==field} for priority,field in priorities.items()}
    classifications=[];issues=[]
    for row in rows:
        code=get(row.inputs,'Material Code');family=None
        trace_start=len(getattr(master,'trace',[]))
        if not empty(code):
            family=master.lookup('material_master',{'material_code':code},'Material Family',duplicate_policy='last',missing='none')
        row.provenance.extend(getattr(master,'trace',[])[trace_start:])
        fields={**row.inputs,'Material Family':family}
        classifications.append({'row':row,'fields':fields,'derived':None})
    # Original14558-14569 assigns with .loc to *all* equal source-field rows.
    # Thus a later qualifying row can classify another row whose two fields
    # are both populated. Input iteration order is part of original precedence.
    for index,item in enumerate(classifications):
        a=get(item['fields'],priorities['1']);b=get(item['fields'],priorities['2'])
        chosen='2' if empty(a) and not empty(b) else '1' if empty(b) and not empty(a) else None
        if chosen is None:continue
        value=b if chosen=='2' else a
        if str(value).upper() not in allowed[chosen]:continue
        field=priorities[chosen]
        for target in classifications:
            if get(target['fields'],field)==value:
                previous=target['derived']
                if previous is not None and previous!=value:
                    issues.append({'code':'seat_broadcast_override','record_id':target['row'].record_id,'previous':previous,'current':value,'source_record_id':item['row'].record_id})
                target['derived']=value
    for item in classifications:
        existing=get(item['row'].inputs,'Seat_Function')
        final=item['derived'] if empty(existing) else existing
        item['row'].values['Seat_Function']=final
        if not empty(existing) and not empty(item['derived']) and existing!=item['derived']:
            issues.append({'code':'seat_bom_classification_preserved','record_id':item['row'].record_id,'existing':existing,'derived':item['derived']})
    variants=list(dict.fromkeys(v for row in rows for v in row.inputs.get('_variants',{})))
    layout=(metadata or {}).get('sbc_layout',{})
    labels={item['key']:item.get('label') or item['key'] for item in layout.get('variants',[])}
    owners=list(dict.fromkeys(str(get(row.inputs,'End Item') or '') for row in rows))
    blocks=[]
    for variant in variants:
        output=[]
        for owner in owners:
            values={base:Decimal(0) for base in bases};blocked=[];errors=[]
            for row in rows:
                if row.dependency_only or str(get(row.inputs,'End Item') or '')!=owner:continue
                if normalized(row.inputs.get('_ownership_type',row.type))=='end item':continue
                try:
                    if row.status!='success':raise CalculationError('seat_component_blocked','A Seat output contribution is blocked',record_id=row.record_id)
                    flags=row.inputs.get('_variants',{})
                    if variant not in flags:raise CalculationError('seat_variant_missing','A Seat output contribution has no verified variant flag',variant,record_id=row.record_id)
                    contribution=_cost(row)*number(get(row.inputs,'Qty/Veh'),'Qty/Veh')*number(flags[variant] or 0,variant)
                    classification=get(row.values,'Seat_Function')
                    target=next((base for base in bases if normalized(base)==normalized(classification)),None)
                    if empty(classification):target=next((base for base in bases if normalized(base)=='others'),None)
                    if target is not None and normalized(target)!='sub total':values[target]+=contribution
                    elif not empty(classification):issues.append({'code':'seat_unmatched_classification','record_id':row.record_id,'classification':classification})
                except CalculationError as exc:blocked.append(row.record_id);errors.append(exc.as_dict())
            if not blocked:values[bases[-1]]=sum((values[base] for base in bases[:-1]),Decimal(0))
            output.append({'End Item':owner,'values':{} if blocked else values,'status':'incomplete' if blocked else 'success','blocked_records':blocked,'issues':errors})
        blocks.append({'key':variant,'label':labels.get(variant,variant),'rows':output})
    return {'enabled':True,'bases':bases,'blocks':blocks,'issues':issues,'selection_fields':priorities,
            'classification_behavior':'Original ordered broadcast; existing BOM Seat_Function wins; priority3 bases are included through the selected Function field, without an independent third-priority branch.',
            'gate_difference':'Requires an actual Parent Commodity=SEAT row instead of mere header presence.',
            'legacy_numerically_verified':False}
