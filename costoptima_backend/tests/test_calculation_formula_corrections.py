"""Source-proven scalar/branch corrections, with isolated rate fixtures only.

No production connection or source workbook is read/written. Masters here are
explicit test doubles; these tests establish arithmetic, not legacy runtime parity.
"""
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from app.calculations.common import CalculationError, supplied_consumption_scrap
from app.calculations.context import CalculationContext
from app.calculations.dispatcher import dispatch
from app.calculations.masters import FieldRef
from app.calculations.commodities.shared import process_lines


class RateFixtures:
    def __init__(self,icc='0'):
        self.calls=[];self.icc=D(icc)

    def record(self,master,keys,**kwargs):
        if master=='norms_make_applicability':return SimpleNamespace(value=lambda field:'Basic Cost/Pc')
        assert master=='material_master'
        metadata={'Material Family':'fixture family','Material Sub Family':'fixture subfamily',
                  'Material Category':'fixture category','Material Grade':'fixture grade','UOM':'Kg',
                  'GSM/ Density':D(1),'Blank Calculation':'fixture','Blank Length Allowance':D(0),
                  'Blank Width Allowance':D(0),'Gross cal Method':'fixture','% wastage':D(0)}
        return SimpleNamespace(value=lambda field:metadata[field])

    def lookup(self,master,keys,field,**kwargs):
        label=field.name if isinstance(field,FieldRef) else field
        self.calls.append((master,keys,field))
        if master=='material_master':
            assert keys=={'material_code':'verified material'}
            assert isinstance(field,FieldRef) and field.group=='verified period'
            return D(10) if label=='Rate/UOM' else D(2) if label=='Scrap Rate' else None
        if master=='process_master':
            if label=='Rate/UOM':return D(4)
            if label=='uom':return 'piece'
            if label=='m_c_specs':return 'stroke'
        if master=='scrap_stamping':return D(2)
        if master=='norms_make':return self.icc if label=='ICC' else D(0)
        if master=='norms_make_applicability':return 'Basic Cost/Pc'
        raise CalculationError('missing_master','No isolated fixture for '+master+'.'+str(label))


def sand_record(type_='PROCESS',**overrides):
    return {'Commodity':'SAND CASTING','Type':type_,'Supplier':'fixture supplier','Qty/Veh':2,
            'Material Code':'verified material','RM Base':'verified period','Gross Wt':2,'Part Wt':1,
            'Tool Cost':1,'Packing':2,'Freight':3,'_process_lines':[{'name':'verified process','quantity':2}],
            **overrides}


def test_sand_process_keeps_material_scrap_ancillary_and_source_rate_order():
    row=sand_record();master=RateFixtures(icc='.1')
    result=dispatch('SAND CASTING','PROCESS').calculate(row,CalculationContext(master,[row]))
    assert result['RM Cost/Pc']==20
    assert result['Process Cost/Pc']==8
    assert result['ICC COST']==D('2.8')
    assert result['Casting Cost']==D('30.8')
    assert result['Ancillary Cost']==6
    assert result['Machining Scrap Recovery']==D('2.125')
    assert result['Total Casting rate /Kg']==D('18.4')  # before recovery / gross
    assert result['Total']==D('34.675')
    assert result['Grand Total Cost/Veh']==D('69.350')
    assert result['Total Process Markup']==D('2.8')
    assert ('material_master',{'material_code':'verified material'},FieldRef('Rate/UOM','verified period')) in master.calls


def test_sand_buy_non_std_keeps_ancillary_after_casting_before_recovery():
    row=sand_record('BUY NON STD');master=RateFixtures(icc='.1')
    result=dispatch('SAND CASTING','BUY NON STD').calculate(row,CalculationContext(master,[row]))
    assert result['Casting Cost']==D('30.8')
    assert result['Total Cost/Pc']==D('34.675')
    assert result['Total Casting rate /Kg']==D('18.4')
    assert 'Total Process Markup' not in result


@pytest.mark.parametrize('code',[None,'',' '])
def test_sand_process_blank_material_has_no_material_lookup(code):
    row=sand_record(**{'Material Code':code,'Tool Cost':None,'Packing':'','Freight':0})
    master=RateFixtures();result=dispatch('SAND CASTING','PROCESS').calculate(row,CalculationContext(master,[row]))
    assert result['RM Cost/Pc']==0 and result['Machining Scrap Recovery']==0
    assert result['Grand Total Cost/Pc']==8
    assert not any(name=='material_master' for name,keys,field in master.calls)


@pytest.mark.parametrize('gross',[None,'',0])
def test_sand_zero_gross_preserves_original_iferror_zero_perkg(gross):
    row=sand_record(**{'Gross Wt':gross,'Part Wt':None});master=RateFixtures()
    result=dispatch('SAND CASTING','PROCESS').calculate(row,CalculationContext(master,[row]))
    assert result['Total Casting rate /Kg']==0
    assert result['Grand Total Cost/Pc']==14


def test_sand_missing_weight_header_does_not_become_blank_cell():
    row=sand_record();row.pop('Gross Wt');master=RateFixtures()
    with pytest.raises(CalculationError) as error:
        dispatch('SAND CASTING','PROCESS').calculate(row,CalculationContext(master,[row]))
    assert error.value.code=='missing_input'


def test_sand_nonblank_unknown_master_is_not_a_process_only_fallback():
    row=sand_record();master=RateFixtures()
    original=master.lookup
    def missing(master_code,keys,field,**kwargs):
        if master_code=='material_master':raise CalculationError('missing_master','Explicit missing master fixture')
        return original(master_code,keys,field,**kwargs)
    master.lookup=missing
    with pytest.raises(CalculationError) as error:
        dispatch('SAND CASTING','PROCESS').calculate(row,CalculationContext(master,[row]))
    assert error.value.code=='missing_master'


@pytest.mark.parametrize('net,blank,parts,expected',[(4,6,3,6),(None,6,3,8),(0,6,3,8),(-1,6,3,8),
                                                  (None,None,3,10),(None,6,0,0),(None,6,None,0),(None,'bad',3,0)])
def test_source_scrap_uses_supplied_blank_fields_and_outer_iferror(net,blank,parts,expected):
    assert supplied_consumption_scrap({'Net RM consumption':net,'Blank Consumption':blank,
                                      'No of parts / Blank':parts},D(10))==expected


def test_source_scrap_does_not_mask_missing_input_header():
    with pytest.raises(CalculationError) as error:
        supplied_consumption_scrap({'Net RM consumption':0,'Blank Consumption':6},D(10))
    assert error.value.code=='missing_input'


@pytest.mark.parametrize('commodity',['SOFT','STAMPING'])
def test_full_branch_scrap_does_not_substitute_calculated_blank_over_upload(commodity):
    row={'Commodity':commodity,'Type':'MAKE','Supplier':'fixture supplier','Qty/Veh':1,
         'Material Code':'verified material','RM Base':'verified period','Net RM consumption':None,
         'Blank Consumption':6,'No of parts / Blank':3,'_process_lines':[]}
    master=RateFixtures();context=CalculationContext(master,[row])
    # Consumption pipeline is independently covered elsewhere. Deliberately
    # different calculated values expose the incorrect AL/AN versus AK/AM use.
    context.consumption=lambda *args:{'Effective Gross Consumption':D(10),'Cal Blank Cons':D(80),'Cal Parts Per Blank':D(2)}
    context.consumables=lambda *args:D(0)
    result=dispatch(commodity,'MAKE').calculate(row,context)
    assert result['Scrap']==8
    assert result['RM Cost/Pc']==84


def test_process_provenance_has_final_cost_after_hour_and_labor_scaling():
    master=RateFixtures();original=master.lookup
    def hourly(master_code,keys,field,**kwargs):
        if master_code=='process_master' and field=='uom':return 'Rs/hr'
        return original(master_code,keys,field,**kwargs)
    master.lookup=hourly
    row={'_process_lines':[{'name':'press','quantity':30},{'name':'LHR_SKILLED','quantity':2}]}
    context=CalculationContext(master,[row]);total=process_lines(context,row,labor=True)
    lines=[p for p in context.provenance if p.get('kind')=='process_line']
    assert total==6 and [p['cost'] for p in lines]==[D(2),D(4)]
    assert sum(p['cost'] for p in lines)==total
    assert lines[1]=={'kind':'process_line','name':'LHR_SKILLED','quantity':D(2),'rate':D(4),'cost':D(4),'uom':'Rs/hr'}


def test_sand_trusted_derived_rate_is_internal_only_and_upload_rate_is_ignored():
    from app.calculations.commodities.sand_casting.buy_non_std import calculate
    row=sand_record('MAKE',**{'Rate/UOM':999,'derived_material_rate':999})
    master=RateFixtures();context=CalculationContext(master,[row])
    ordinary=calculate(row,context)
    assert ordinary['Rate/UOM']==10
    count=sum(code=='material_master' for code,keys,field in master.calls)
    derived=calculate(row,context,derived_material_rate=D(12))
    assert derived['Rate/UOM']==12 and derived['RM Cost/Pc']==24
    assert sum(code=='material_master' for code,keys,field in master.calls)==count
    assert derived['Machining Scrap Recovery']==D('2.55')
    assert derived['Grand Total Cost/Pc']==D('35.45')


def test_output_rate_trace_never_selects_conflicting_locations_or_recalculates_costs():
    from app.calculations.engine import _retain_output_trace
    values={'Total Cost/Pc':D(15)}
    _retain_output_trace(values,[{'kind':'master_rate','name':'press','rate':D(10),'location':'AD'},
                                {'kind':'master_rate','name':'press','rate':D(20),'location':'other'},
                                {'kind':'master_rate','name':'shot','rate':D(3),'location':'AD'}])
    assert values['_master_rates']=={'shot':D(3)}
    assert values['_master_rate_conflicts']==['press']
    assert values['Total Cost/Pc']==15
