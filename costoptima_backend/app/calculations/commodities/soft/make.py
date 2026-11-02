"""PCM part1 material/consumption/process/markup pipeline1548-3954."""
from decimal import Decimal
from ...common import required,get,number,CalculationError,supplied_consumption_scrap
from ..shared import material,process_lines,markups,finish,checkpoint
SOURCE={'function':'part1;soft','lines':'1548-3954;877-1014'}

def calculate(record,context):
    data=dict(record);code=get(data,'Material Code');zero=Decimal(0)
    if code not in (None,''):
        for field in ['Material Family','Material Sub Family','Material Category','Material Grade','UOM','GSM/ Density','Blank Calculation','Blank Length Allowance','Blank Width Allowance','Gross cal Method','% wastage']:
            value=context.master.record('material_master',{'material_code':code}).value(field)
            data[field]=value
        supplied=get(record,'GSM/ Density')
        if supplied not in (None,'') and number(supplied)>0:data['GSM/ Density']=number(supplied)
        else:data['GSM/ Density']=number(data['GSM/ Density'] or 0)*1000
        metadata={field:data[field] for field in ['Material Family','Material Sub Family','Material Category','Material Grade','UOM','GSM/ Density','Blank Calculation','Blank Length Allowance','Blank Width Allowance','Gross cal Method','% wastage']}
        values=context.consumption(data,str(get(record,'Commodity') or 'SOFT'))
        values={**metadata,**values}
        checkpoint(context,record,values)
        gross=number(values['Effective Gross Consumption'])
        scrap=supplied_consumption_scrap(data,gross)
        rate=material(context,data)
        checkpoint(context,record,{'Rate/UOM':rate,'Scrap':scrap})
        if scrap == zero:
            # PCM part1:3511-3515 subtracts Scrap Rate * Scrap. A proven
            # zero quantity has zero recovery without inventing a price.
            # The material record and period were already verified above;
            # mapping errors and invalid nonblank rates still raise.
            from ...masters import FieldRef
            scrap_rate=context.require_master('material_master',{'material_code':code},
                FieldRef('Scrap Rate',group=str(required(data,'RM Base'))),missing='none')
            if scrap_rate is None:
                context.provenance.append({'kind':'unused_scrap_rate','quantity':zero,
                    'rate':None,'recovery':zero,'rate_required':False})
        else:
            scrap_rate=material(context,data,'Scrap Rate')
        # Consumables require verified named BOM/material quantities. Do not omit a required master line.
        consumables=context.consumables({**data,**values})
        recovery=zero if scrap_rate is None else scrap_rate*scrap
        rm=rate*gross-recovery+consumables
        values.update({'Scrap':scrap,'Rate/UOM':rate,'Scrap Rate':scrap_rate,'Consumables':consumables})
    else:values={};rm=zero
    checkpoint(context,record,{**values,'RM Cost/Pc':rm,'RM Cost / Pc':rm})
    process=process_lines(context,data,labor=True,grade=data.get('Material Grade'),material_cost=rm,location='AD')
    values.update({'RM Cost/Pc':rm,'RM Cost / Pc':rm,'Process Cost/Pc':process,'Process Cost':process,'Basic Cost/Pc':rm+process})
    # part13866-3869 and3913-3915: Skin is Material Family, not grade.
    # Its OH excludes RM; Handling applies to RM independently of blank labels.
    bases={'OH':process,'Handling':rm} if str(get(data,'Material Family') or '').strip().casefold()=='skin' else None
    values=markups(context,data,values,fields=('ICC','REJ','OH','PROFIT','Handling'),blank_applicability_zero=True,base_overrides=bases)
    values['Total Markup']=values['MARKUP/Pc'];values['Total']=values['Basic Cost/Pc']+values['MARKUP/Pc']
    return finish(record,values)
