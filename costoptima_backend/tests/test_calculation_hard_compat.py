"""Source-backed HARD process key and BNS purchasing-producer tests."""
from decimal import Decimal as D
from types import SimpleNamespace
from app.calculations.context import CalculationContext
from app.calculations.dispatcher import dispatch
from test_calculation_engine import zero_norm_master


def record():
    return {'Commodity':'HARD','Type':'BUY NON STD','Supplier':'s','Qty/Veh':2,
            'Material Code':'fixture','RM Base':'fixture','Part Wt':1,
            'Cycle Time':30,'Cavity':2,'m/c Tonnage':'Rubber Molding 200T',
            'Sector & Location':'different input location'}


def test_supported_process_machine_name_is_a_business_key_and_bns_uses_ad():
    row=record();master=zero_norm_master({'Applicable Process':'Blow Molding','efficiency':'.8'})
    master.records=lambda code:[SimpleNamespace(value=lambda f:{'criteria':'rest all','net_wt':None}.get(f),decimal=lambda f:D('1.2'))] if code=='norms_gross_wt' else []
    original=master.lookup
    def lookup(code,keys,field,**kw):
        if code=='process_master':
            assert keys=={'process':'Rubber Molding 200T'}
            assert field.group=='AD'
            return D(1000)
        if code=='norms_pnf':
            assert keys=={'pnf':'BNS_Make'}
            return D('.06')
        return original(code,keys,field,**kw)
    master.lookup=lookup
    result=dispatch('HARD','BUY NON STD').calculate(row,CalculationContext(master,[row]))
    assert result['Gross Wt']==D('1.2')
    assert result['Molding Cost']==D(1000)/1536
    assert result['Deflashing cost']==0
    assert result['PnF/Pc']==result['Total Cost/Pc']*D('.06')
    assert result['PnF/Veh']==result['PnF/Pc']*2
    assert result['Grand Total Cost/Veh']==result['Grand Total Cost/Pc']*2


def test_soft_skin_overhead_excludes_rm_and_handling_uses_rm():
    from app.calculations.commodities.shared import markups
    row={'Supplier':'fixture'}
    master=zero_norm_master({'OH':'.1','Handling':'.2'})
    values=markups(CalculationContext(master,[row]),row,
        {'RM Cost/Pc':D(40),'Process Cost/Pc':D(10),'Basic Cost/Pc':D(50)},
        fields=('ICC','REJ','OH','PROFIT','Handling'),base_overrides={'OH':D(10),'Handling':D(40)})
    assert values['OH COST']==1
    assert values['Handling COST']==8
    assert values['MARKUP/Pc']==9
    assert not any(code=='norms_make_applicability' for code,keys,field in master.calls)


def test_nonrubber_supplied_gross_does_not_require_part_weight():
    from app.calculations.commodities.hard.make import gross_weight
    assert gross_weight({'Gross Wt':'1.25'},None,'Default')==D('1.25')


def test_soft_final_process_lookup_uses_ad_despite_other_input_location():
    row={'Commodity':'SOFT','Type':'ASSY','Supplier':'s','Qty/Veh':1,'Sector & Location':'other',
         '_process_lines':[{'name':'fixture process','quantity':2}]}
    master=zero_norm_master();previous=master.lookup
    def lookup(code,keys,field,**kwargs):
        if code=='process_master' and hasattr(field,'group'):
            assert field.group=='AD'
        return previous(code,keys,field,**kwargs)
    master.lookup=lookup
    assert dispatch('SOFT','ASSY').calculate(row,CalculationContext(master,[row]))['Process Cost/Pc']==4
