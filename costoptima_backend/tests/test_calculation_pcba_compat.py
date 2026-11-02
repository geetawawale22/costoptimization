"""Primary pcba_op14203-14236 grouped component graph, isolated masters."""
from decimal import Decimal as D
import pytest
from app.calculations.context import CalculationContext
from app.calculations.dispatcher import dispatch
from app.calculations.common import CalculationError
from test_calculation_engine import zero_norm_master


def board():
    return {'Commodity':'PCBA','Type':'ASSY','Supplier':'s','Customer No':'board001',
        'End Item':'1','Qty/Veh':2,'RM Base':'fixture','_pcba_quantity_headers':['unused','pack1','pack2'],
        '_pcba_components':[{'PCBA Part Number':'board001','Material Code':'a','unused':None,'pack1':2,'pack2':100},
                            {'PCBA Part Number':'board001','Material Code':'b','unused':0,'pack1':3,'pack2':200}]}


def test_board_uses_one_first_populated_quantity_column_and_database_prices():
    row=board();row['_pcba_components'][0]['FOB']=9999
    values=dispatch('PCBA','ASSY').calculate(row,CalculationContext(zero_norm_master(),[row]))
    assert values['Selected Population']=='pack1'
    assert values['RM Cost/Pc']==60
    assert values['Process Cost/Pc']==30
    assert values['Total Cost/Pc']==90
    assert values['Grand Total Cost/Veh']==180
    assert [x['quantity'] for x in values['_pcba_component_results']]==[2,3]


def test_board_markup_is_in_board_basic_once():
    row=board();master=zero_norm_master({'ICC':'.1'})
    previous=master.lookup
    def lookup(code,keys,field,**kwargs):
        if code=='norms_make_applicability':return 'Basic Cost/Pc'
        return previous(code,keys,field,**kwargs)
    master.lookup=lookup
    values=dispatch('PCBA','ASSY').calculate(row,CalculationContext(master,[row]))
    assert values['PCBA Markup/Pc']==9
    assert values['Basic Cost/Pc']==99
    assert values['Total Cost/Pc']==99
    assert values['Grand Total Cost/Pc']==99


def test_board_missing_components_and_empty_population_are_blocked():
    row=board();row['_pcba_components']=[]
    with pytest.raises(CalculationError,match='component rows'):dispatch('PCBA','ASSY').calculate(row,CalculationContext(zero_norm_master(),[row]))
    row=board()
    for component in row['_pcba_components']:
        for name in row['_pcba_quantity_headers']:component[name]=0
    with pytest.raises(CalculationError,match='dash'):dispatch('PCBA','ASSY').calculate(row,CalculationContext(zero_norm_master(),[row]))


def test_board_rejects_cross_board_component():
    row=board();row['_pcba_components'][0]['PCBA Part Number']='other'
    with pytest.raises(CalculationError,match='another PCBA'):dispatch('PCBA','ASSY').calculate(row,CalculationContext(zero_norm_master(),[row]))


def test_pcba_period_is_first_original_bom_period_even_for_blank_or_different_board():
    row=board();row['RM Base']=None
    first={'Commodity':'','Type':'END ITEM','RM Base':'approved first period'}
    master=zero_norm_master();result=dispatch('PCBA','ASSY').calculate(row,CalculationContext(master,[first,row]))
    assert result['RM Base']=='approved first period' and result['Total Cost/Pc']==90
    assert all(field.group=='approved first period' for code,keys,field in master.calls
               if code=='ebom' and getattr(field,'name',str(field))=='Rate/UOM')
    row['RM Base']='other period'
    assert dispatch('PCBA','ASSY').calculate(row,CalculationContext(zero_norm_master(),[first,row]))['RM Base']=='approved first period'


def test_pcba_missing_first_bom_period_is_not_filled_from_later_board():
    row=board();first={'Commodity':'','Type':'END ITEM','RM Base':None}
    with pytest.raises(CalculationError) as error:
        dispatch('PCBA','ASSY').calculate(row,CalculationContext(zero_norm_master(),[first,row]))
    assert error.value.field=='RM Base'
