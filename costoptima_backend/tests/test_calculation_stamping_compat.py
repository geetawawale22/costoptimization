"""Source part1_stamp supplier scrap/dies/process categorization."""
from decimal import Decimal as D
from types import SimpleNamespace
from app.calculations.context import CalculationContext
from app.calculations.dispatcher import dispatch
from test_calculation_engine import zero_norm_master


def test_stamping_has_dies_instead_of_soft_handling_and_named_process_subtotals():
    row={'Commodity':'STAMPING','Type':'MAKE','Supplier':'s','Qty/Veh':2,
         '_process_lines':[{'name':'press','quantity':3},{'name':'weld','quantity':4}]}
    master=zero_norm_master({'Dies & Fixture Maint. cost':'.1','Handling':'.9'})
    old=master.lookup
    def lookup(code,keys,field,**kwargs):
        if code=='process_master' and field=='m_c_specs':return 'stroke' if keys['process']=='press' else 'welding'
        if code=='norms_make_applicability':return 'Basic Cost/Pc'
        return old(code,keys,field,**kwargs)
    master.lookup=lookup
    master.record=lambda *args,**kwargs:SimpleNamespace(value=lambda field:'Basic Cost/Pc')
    values=dispatch('STAMPING','MAKE').calculate(row,CalculationContext(master,[row]))
    assert values['Process Cost/Pc']==14
    assert values['Total stroke cost']==6 and values['Total welding cost']==8
    assert values['Dies COST']==D('1.4')
    assert values['MARKUP/Pc']==D('1.4')
    assert 'Handling COST' not in values
    assert values['Grand Total Cost/Veh']==D('30.8')


def test_stamping_optional_actual_gross_skips_unneeded_consumption_rules():
    row={'Commodity':'STAMPING','Type':'MAKE','Supplier':'s','Qty/Veh':1,'Material Code':'fixture',
         'RM Base':'fixture','Net RM consumption':2,'Yield (%)':'.5','Actual Gross Consumption':5,'_process_lines':[]}
    master=zero_norm_master({'Dies & Fixture Maint. cost':'0'})
    master.record=lambda *args,**kwargs:SimpleNamespace(value=lambda field:None)
    previous=master.lookup
    def lookup(code,keys,field,**kwargs):
        if code=='scrap_stamping':
            assert keys=={'Stamping':'s'} and field=='fixture'
            return D('.5')
        return previous(code,keys,field,**kwargs)
    master.lookup=lookup
    context=CalculationContext(master,[row]);context.consumables=lambda record:D(0)
    context.consumption=lambda *args:(_ for _ in ()).throw(AssertionError('Optional branch must not evaluate full consumption masters'))
    values=dispatch('STAMPING','MAKE').calculate(row,context)
    assert values['Effective Gross Consumption']==5
    assert values['Scrap Weight']==3
    assert values['RM Cost/Pc']==D('8.5')
