"""PCM part1_stamp3997-6503, explicit supplier scrap and dies maintenance.

Consumption evaluation is shared; STAMPING's supplier scrap master and fifth
markup differ from SOFT and must never be replaced by SOFT Handling.
"""
from decimal import Decimal
from ...common import required,get,number,normalized,divide,CalculationError,supplied_consumption_scrap
from ..shared import material,process_lines,markups,finish,process_rate,checkpoint
SOURCE={'function':'part1_stamp;stamping','lines':'3997-6503;661-872'}
ZERO=Decimal(0)


def calculate(record,context):
    data=dict(record);code=get(data,'Material Code');values={};rm=ZERO
    if code not in (None,''):
        master=context.master.record('material_master',{'material_code':code})
        for field in ('Material Family','Material Sub Family','Material Category','Material Grade','UOM','GSM/ Density','Blank Calculation',
                      'Blank Length Allowance','Blank Width Allowance','Gross cal Method','% wastage'):
            data[field]=master.value(field)
        supplied=get(record,'GSM/ Density')
        data['GSM/ Density']=number(supplied) if supplied not in (None,'') and number(supplied)>0 else number(data['GSM/ Density'] or 0)*1000
        supplier_rows=[r for r in context.rows if normalized(get(r,'Commodity'))=='stamping' and normalized(get(r,'Supplier'))==normalized(get(record,'Supplier'))]
        def populated(field):return any(get(r,field) is not None and str(get(r,field)).strip() for r in supplier_rows)
        optional=(populated('Net RM consumption') and populated('Yield (%)')) or populated('Actual Gross Consumption')
        if optional:
            net=number(get(data,'Net RM consumption') or 0,'Net RM consumption')
            actual=number(get(data,'Actual Gross Consumption') or 0,'Actual Gross Consumption')
            yield_=number(get(data,'Yield (%)') or 0,'Yield (%)')
            gross=actual if actual>0 else divide(net,yield_,'Net RM consumption / Yield (%)')
            actual_yield=yield_ if yield_>0 else net/gross if gross else ZERO
            values={'Effective Gross Consumption':gross,'Actual Gross Consumption':actual,'Actual Yield':actual_yield,
                    'Net RM consumption':net,'_stamping_optional_consumption':True}
        else:
            values=context.consumption(data,'STAMPING')
            gross=number(values['Effective Gross Consumption'])
        # Current Master Output Matrix exposes these actual database labels.
        values.update({field:data[field] for field in ('Material Family','Material Sub Family','Material Category','Material Grade','UOM','GSM/ Density')})
        # The optional input branch uses gross-net directly (source6132).
        # The full branch uses uploaded blank/parts with IFERROR0 (source6130).
        scrap=gross-net if optional else supplied_consumption_scrap(data,gross)
        checkpoint(context,record,values)
        rate=material(context,data)
        checkpoint(context,record,{'Rate/UOM':rate,'Scrap':scrap,'Scrap Weight':scrap})
        # Original6124 matches output Supplier to Scrap Stamping's key,
        # then RM Base to its period header. No Material Code match is used.
        scrap_rate=context.require_master('scrap_stamping',{'Stamping':required(record,'Supplier')},required(record,'RM Base'),blank_zero=True)
        consumables=context.consumables({**data,**values})
        rm=rate*gross-scrap_rate*scrap+consumables
        values.update({'Scrap':scrap,'Scrap Weight':scrap,'Rate/UOM':rate,'Scrap Rate':scrap_rate,'Consumables':consumables})
    checkpoint(context,record,{**values,'RM Cost/Pc':rm,'RM Cost / Pc':rm})
    process=process_lines(context,data,labor=True,grade=data.get('Material Grade'),material_cost=rm)
    stroke=welding=ZERO
    for line in context.process_lines(data):
        name=required(line,'name')
        category=str(context.master.lookup('process_master',{'process':name},'m_c_specs',missing='default',default='') or '').strip().casefold()
        amount=process_rate(context,data,name)*number(line.get('quantity') or 0,name)
        if category=='stroke':stroke+=amount
        elif category=='welding':welding+=amount
    values.update({'RM Cost/Pc':rm,'RM Cost / Pc':rm,'Process Cost/Pc':process,'Process Cost':process,
        'Total stroke cost':stroke,'Total welding cost':welding,'Basic Cost/Pc':rm+process})
    values=markups(context,data,values,fields=('ICC','REJ','OH','PROFIT','Dies & Fixture Maint. cost'),blank_applicability_zero=True)
    values['Dies COST']=values['Dies & Fixture Maint. cost COST']
    values['Total Markup']=values['MARKUP/Pc'];values['Total']=values['Basic Cost/Pc']+values['MARKUP/Pc']
    return finish(record,values)
