"""PCM pdc16853-17448; named machine/process fields and static formulas."""
from decimal import Decimal
from ...common import get,required,number,CalculationError
from ..shared import material_details,material,process_rate,process_lines,markups,finish,value,checkpoint
SOURCE={'function':'pdc','lines':'16853-17448'}
def calculate(record,context):
    casting,part=value(record,'Casting Wt'),value(record,'Part Wt');loss=casting*Decimal('0.07')
    gross=casting+loss;shot=casting/Decimal('0.65');rate=material(context,record)
    scrap_rate=rate*Decimal('0.7');scrap_wt=casting-part;scrap=scrap_wt*scrap_rate*Decimal('0.85')
    rm=rate*gross-scrap
    checkpoint(context,record,{'Rate/UOM':rate,'Burning loss in %':Decimal('0.07'),'Burning loss in Kg':loss,
        'SHOT WEIGHT':shot,'GROSS WEIGHT':gross,'Scrap Weight':scrap_wt,'Scrap Rate':scrap_rate,'Scrap Cost':scrap,
        'RM Cost':rm,'RM Cost/Pc':rm,'RM':rate*gross})
    melting=process_rate(context,record,'PDC Melting rate/Kg')*shot
    checkpoint(context,record,{'MELTING COST':melting})
    # The machine process is a supplied named process identifier, never a column offset.
    machine=required(record,'PDC Machine Process');minutes=value(record,'PDC Machine Minutes')
    machine_cost=process_rate(context,record,machine)*minutes/60
    checkpoint(context,record,{'PDC cost':machine_cost})
    fettling_rate=process_rate(context,record,'PDC FETTLING');blast_rate=process_rate(context,record,'SHOT BLASTING')
    fettling=casting*fettling_rate;blast=casting*blast_rate;conversion=machine_cost+fettling+blast
    checkpoint(context,record,{'PDC FETTLING':fettling,'SHOT BLASTING':blast,'PDC Conversion cost':conversion})
    process=conversion+melting+process_lines(context,record)
    values={'Rate/UOM':rate,'Burning loss in %':Decimal('0.07'),'Burning loss in Kg':loss,'SHOT WEIGHT':shot,'GROSS WEIGHT':gross,
            'Scrap Weight':scrap_wt,'Scrap Rate':scrap_rate,'Scrap Cost':scrap,'RM Cost':rm,'RM':rate*gross,
            'MELTING COST':melting,'PDC cost':machine_cost,'PDC FETTLING':fettling,'SHOT BLASTING':blast,
            'PDC Basic Process':fettling_rate*fettling+blast_rate*blast,'PDC Conversion cost':conversion,
            'PDC Conversion /Kg':conversion/part if part else Decimal(0),'RM Cost/Pc':rm,'Process Cost/Pc':process,'Basic Cost/Pc':rm+process}
    values=markups(context,record,values,fields=('ICC','Handling','REJ','OH','PROFIT'))
    values['Total']=values['Basic Cost/Pc']+values['MARKUP/Pc']
    values.update(material_details(context,record))
    return finish(record,values)
