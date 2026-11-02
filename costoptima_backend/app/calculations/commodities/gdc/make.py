"""PCM gdc_casting8718-9284: original current (not _prev) arithmetic."""
from decimal import Decimal
from ...common import get,required,number,normalized
from ..shared import material_details,material,process_rate,process_lines,markups,finish,value,checkpoint
SOURCE={'function':'gdc_casting','lines':'8718-9284'}
def process_location(record,context):
    """Original gdc_casting8812-8836: first supplier-row location, then AD.

    AD is selected only when the requested group is absent from actual Process
    Master metadata, not because a process record or selected rate is missing.
    """
    rows=[row for row in context.rows if normalized(get(row,'Commodity'))=='gdc'
          and normalized(get(row,'Supplier'))==normalized(get(record,'Supplier'))]
    positioned=[row for row in rows if isinstance(row.get('_detail_row'),int)]
    first=min(positioned,key=lambda row:row['_detail_row']) if positioned else next(iter(rows),record)
    requested=get(first,'Sector & Location') or 'AD';selected=requested
    if hasattr(context.master,'fields'):
        fields=context.master.fields('process_master')
        groups={normalized(field.group) for field in fields if normalized(field.header)=='rate/uom'}
        if normalized(requested) not in groups and normalized('AD') in groups:selected='AD'
    context.provenance.append({'kind':'legacy_batch_selector','source':'gdc_casting8812-8836',
        'record_id':first.get('_record_id'),'requested_process_location':requested,
        'resolved_process_location':selected})
    return {**record,'Sector & Location':selected}


def calculate(record,context):
    record=process_location(record,context)
    casting,part=number(get(record,'Casting Wt',0) or 0,'Casting Wt'),number(get(record,'Part Wt',0) or 0,'Part Wt')
    loss=casting*Decimal('0.07');gross=casting+loss;rate=material(context,record) if get(record,'Material Code') not in (None,'') else Decimal(0)
    scrap_rate=rate*Decimal('0.7');scrap_wt=casting-part;scrap=scrap_wt*scrap_rate*Decimal('0.85')
    checkpoint(context,record,{'Burning Loss in %':Decimal('0.07'),'Burning Loss in Kg':loss,'Gross Wt':gross,
        'Rate/UOM':rate,'Scrap Rate':scrap_rate,'Scrap Wt':scrap_wt,'Scrap Cost':scrap,'RM Cost with out scrap cost':rate*gross})
    core_weight=number(get(record,'Core Weight per Part',0) or 0,'Core Weight per Part')
    core=core_weight*process_rate(context,record,'Core Cost')
    checkpoint(context,record,{'Core Cost':core,'RM Cost/Pc':rate*gross-scrap+core})
    decor=core_weight*process_rate(context,record,'De coring cost')
    checkpoint(context,record,{'De coring cost':decor})
    casting_process=casting*process_rate(context,record,'GDC Process cost')
    checkpoint(context,record,{'GDC Process cost':casting_process})
    fettling=casting*process_rate(context,record,'GDC-Fettling')
    checkpoint(context,record,{'GDC-Fettling':fettling})
    blast=casting*process_rate(context,record,'GDC-Shot Blasting')
    conversion=decor+casting_process+fettling+blast
    checkpoint(context,record,{'GDC-Shot Blasting':blast,'GDC Cost':conversion})
    dynamic=process_lines(context,record)
    process=conversion+dynamic;rm=rate*gross-scrap+core
    values={'Process Rate Location':get(record,'Sector & Location'),'Burning Loss in %':Decimal('0.07'),'Burning Loss in Kg':loss,'Gross Wt':gross,'Rate/UOM':rate,
            'Scrap Rate':scrap_rate,'Scrap Wt':scrap_wt,'Scrap Cost':scrap,'Core Cost':core,'De coring cost':decor,
            'GDC Process cost':casting_process,'GDC-Fettling':fettling,'GDC-Shot Blasting':blast,'GDC Cost':conversion,
            'RM Cost with out scrap cost':rate*gross,'RM Cost/Pc':rm,'Process Cost/Pc':process,'Basic Cost/Pc':rm+process}
    values=markups(context,record,values,fields=('ICC','Handling','REJ','OH','PROFIT'))
    # Original blank Excel numeric inputs add as zero, not missing producer results.
    ancillary=sum((number(get(record,k,0) or 0,k) for k in ('Tool Cost','Packing','Freight')),Decimal(0))
    values['Ancillary Cost']=ancillary;values['Total Cost/Pc']=values['Basic Cost/Pc']+values['MARKUP/Pc']+ancillary
    values['Total']=values['Total Cost/Pc']
    values.update(material_details(context,record))
    return finish(record,values)
