"""Primary special_process6509-6558; Paint projection19504-19550."""
from decimal import Decimal
from ...common import get,required,normalized,CalculationError
from ...merging.paint import calculate_paint_group
from ..shared import finish,checkpoint
SOURCE={'function':'paint;special_process;merge','lines':'598-654;6509-6558;19362-19550'}


def calculate(record,context):
    mode=str(get(record,'Special Process') or get(record,'Process / Assy Name') or '').strip()
    configured=context.master.record('norms_special_process_norms',{'special_process':mode},missing='none')
    if configured is None:raise CalculationError('unsupported_special_process','Process is not in the original Special Process Norms catalogue',mode)
    if normalized(mode) not in {'make paint','buy paint'}:
        raise CalculationError('hard_special_process_dependency_missing','Original HARD special-process projection requires a supported Paint producer',mode)
    paint=calculate_paint_group(record,context)
    tier=paint['Paint Markup']+paint['Paint PnF'] if normalized(mode)=='buy paint' else Decimal(0)
    basic=paint['Paint RM Cost']+paint['Paint Process Cost']+tier
    values={'Sr':paint['Sr'],'Surface Area (in SQM)':paint['Surface Area (in SQM)'],'Process / Assy Name':mode,
        'Total RM Cost':paint['Paint RM Cost'],'Consumable / Masking Cost':Decimal(0),'RM Cost':paint['Paint RM Cost'],
        'RM Cost/Pc':paint['Paint RM Cost'],'Process Cost':paint['Paint Process Cost'],'Process Cost/Pc':paint['Paint Process Cost'],
        'Tier 2 Markup+PnF':tier,'Basic cost':basic,'Basic Cost/Pc':basic,'_paint_result':paint}
    checkpoint(context,record,values)
    if normalized(mode)=='make paint':
        values.update({'ICC':paint['ICC'],'REJ':sum(paint[name] for name in ('REJ ON INJ MOLDED PART','REJ ON MATERIAL & PROCESS','OVERALL REJECTION')),
            'OH':paint['OVERHEAD'],'PROFIT':paint['PROFIT'],'ICC COST':paint['ICC Cost'],
            'REJ COST':sum(paint[name+' Cost'] for name in ('REJ ON INJ MOLDED PART','REJ ON MATERIAL & PROCESS','OVERALL REJECTION')),
            'OH COST':paint['OVERHEAD Cost'],'PROFIT COST':paint['PROFIT Cost']})
    else:
        supplier=required(record,'Supplier')
        if context.master.record('norms_buy_non_std',{'supplier':supplier},missing='none') is None:supplier='Other'
        for name,field in [('ICC','icc'),('REJ','rej'),('OH','handling'),('PROFIT','profit')]:
            rate=context.require_master('norms_buy_non_std',{'supplier':supplier},field,blank_zero=True)
            values[name]=rate;values[name+' COST']=basic*rate
            checkpoint(context,record,values)
    values['MARKUP/Pc']=sum(values[name+' COST'] for name in ('ICC','REJ','OH','PROFIT'))
    values['MARKUP']=values['MARKUP/Pc'];values['Total cost /PC']=basic+values['MARKUP/Pc']
    return finish(record,values)
