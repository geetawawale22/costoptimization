"""Authoritative current Shift_Factor_Rate formulas; no production fixtures."""
from decimal import Decimal as D
from types import SimpleNamespace
from pathlib import Path
import hashlib
import json
import pytest
from openpyxl import load_workbook
from app.calculations.context import CalculationContext
from app.calculations.common import CalculationError
from app.calculations.dispatcher import dispatch
from app.calculations.commodities.hard.make import shift_field
from app.calculations.commodities.hard import shift_policy
from test_calculation_engine import zero_norm_master


def masters(process):
    master=zero_norm_master({'Applicable Process':process,'efficiency':'.8','factor':'.06',
                            'c_0':'10','c_401':'20','c_1000':'30','c_2001':'40','c_1k':'50','c_2k':'60'})
    previous=master.lookup
    def lookup(code,keys,field,**kwargs):
        if code=='norms_shift_factor' and keys=={'shift_factor':'Deflashing'}:return D('.2')
        return previous(code,keys,field,**kwargs)
    master.lookup=lookup
    def records(code):
        assert code=='norms_gross_wt'
        return [SimpleNamespace(value=lambda f:{'criteria':'rest all','net_wt':None}.get(f),
                                decimal=lambda f:D('1.1'))]
    master.records=records
    return master


def row(process='Default',type_='MAKE',**overrides):
    return {'Commodity':'HARD','Type':type_,'Supplier':'fixture','Qty/Veh':1,
            'Material Code':'fixture','RM Base':'fixture','Part Wt':1,'Gross Wt':2,
            'Cycle Time':30,'Cavity':2,'m/c Tonnage':100,**overrides}


@pytest.mark.parametrize('process',['Blow Molding','Roto Molding','Compression Molding'])
def test_current_process_policy_uses_database_process_rate_over_capacity(process):
    r=row(**{'m/c Tonnage':'named machine'});m=masters(process)
    result=dispatch('HARD','MAKE').calculate(r,CalculationContext(m,[r]))
    assert result['Shift Factor']==0 and result['Shift Rate']==2
    assert result['Pc per shift']==1536 and result['Molding Cost']==D(2)/1536
    assert result['Deflashing cost']==0
    assert any(code=='process_master' and keys=={'process':'named machine'} for code,keys,field in m.calls)


@pytest.mark.parametrize('process',['PP','EP','Default','Rubber Molding','unknown',None])
def test_current_norm_policy_is_capacity_over_shift_not_its_reciprocal(process):
    r=row();m=masters(process or '')
    result=dispatch('HARD','MAKE').calculate(r,CalculationContext(m,[r]))
    assert result['Shift Policy']==(process if process in {'PP','EP'} else 'Default')
    assert result['Shift Rate']==1000 and result['Molding Cost']==D(1536)/1000
    assert result['Deflashing cost']==D('.2') and result['Gross Wt']==2


def test_unlisted_rubber_with_machine_label_blocks_without_guessing_numeric_tonnage():
    r=row(**{'m/c Tonnage':'Rubber Molding 200T'});m=masters('Rubber Molding')
    with pytest.raises(CalculationError) as e:dispatch('HARD','MAKE').calculate(r,CalculationContext(m,[r]))
    assert e.value.code=='shift_policy_input_incompatible'
    assert e.value.details['selected_policy']=='Default'
    assert all(call[0]=='material_master' for call in m.calls)  # partial RM may resolve; incompatible process/norm rates remain prohibited


def test_make_supplied_gross_and_buy_non_std_rebuild_are_distinct_original_paths():
    r=row(type_='BUY NON STD');m=masters('Blow Molding')
    output=dispatch('HARD','BUY NON STD').calculate(r,CalculationContext(m,[r]))
    assert output['Gross Wt']==D('1.1') and output['RM Cost/Pc']==D('2.2')
    assert output['PnF/Pc']==output['Total Cost/Pc']*D('.06')
    m=masters('Blow Molding');r['Type']='MAKE'
    assert dispatch('HARD','MAKE').calculate(r,CalculationContext(m,[r]))['Gross Wt']==2


@pytest.mark.parametrize('tons,field',[(0,'c_0'),(400,'c_0'),(D('400.5'),'c_0'),(401,'c_401'),(999,'c_401'),(1000,'c_1000'),(2001,'c_2001')])
def test_verified_lower_bound_tonnage_headers(tons,field):
    assert shift_field({'m/c Tonnage':tons})==field


def test_current_reference_contract_checksum_and_all_six_policies():
    p=Path(r'C:\Users\Admin\Downloads\CostOptima_PCM_updated (1)\CostOptima\Shift_Factor_Rate.xlsx')
    if not p.is_file():pytest.skip('Authoritative workbook unavailable')
    assert hashlib.sha256(p.read_bytes()).hexdigest()==shift_policy.REFERENCE_SHA256
    w=load_workbook(p,read_only=True,data_only=False)
    try:
        assert w.sheetnames==['Sheet1'] and w.active.sheet_state=='visible'
        rows=list(w.active.values);headers=rows[0];data=[dict(zip(headers,r)) for r in rows[1:]]
        assert len(rows)==7 and len(headers)==21
        assert hashlib.sha256(json.dumps(rows,ensure_ascii=False,separators=(',',':')).encode('utf-8')).hexdigest()==shift_policy.REFERENCE_POLICY_SHA256
        assert {r['Applicable Process'] for r in data}==shift_policy.PROCESS_POLICIES|shift_policy.NORM_POLICIES
        for r in data:
            assert r['Molding Cost']==('{Shift Rate}/{Pc per shift}' if r['Applicable Process'] in shift_policy.PROCESS_POLICIES else '{Pc per shift}/{Shift Rate}')
    finally:w.close()


def test_make_uses_first_detail_named_period_and_location_even_if_later_differs():
    first=row(**{'_detail_row':3,'RM Base':'first period','Sector & Location':'first location'})
    current=row(**{'_detail_row':8,'RM Base':'other period','Sector & Location':'other location'})
    m=masters('Blow Molding');context=CalculationContext(m,[current,first])
    result=dispatch('HARD','MAKE').calculate(current,context)
    assert result['RM Base']=='first period' and result['Sector & Location']=='first location'
    assert any(code=='material_master' and getattr(field,'group',None)=='first period' for code,keys,field in m.calls)
    assert any(code=='process_master' and getattr(field,'group',None)=='first location' for code,keys,field in m.calls)
    assert current['RM Base']=='other period'  # immutable input ownership


def test_make_missing_later_period_inherits_first_make_and_blank_location_is_ad():
    first=row(**{'_detail_row':3,'Sector & Location':None})
    current=row(**{'_detail_row':8,'RM Base':None})
    result=dispatch('HARD','MAKE').calculate(current,CalculationContext(masters('Blow Molding'),[current,first]))
    assert result['RM Base']=='fixture' and result['Sector & Location']=='AD'


def test_make_does_not_search_later_period_or_borrow_nonstd_batch():
    first=row(**{'_detail_row':3,'RM Base':None});current=row(**{'_detail_row':8})
    with pytest.raises(CalculationError) as error:
        dispatch('HARD','MAKE').calculate(current,CalculationContext(masters('Blow Molding'),[first,current]))
    assert error.value.field=='RM Base'
    bns=row(type_='BUY NON STD',**{'_detail_row':2,'RM Base':'bns period'})
    result=dispatch('HARD','BUY NON STD').calculate(bns,CalculationContext(masters('Blow Molding'),[current,bns]))
    assert result['RM Base']=='bns period'
