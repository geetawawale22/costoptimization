"""Calculation completion is a result contract, never the absence of messages."""
from decimal import Decimal as D
from io import BytesIO
from types import SimpleNamespace
import pytest
from openpyxl import Workbook, load_workbook
from app.calculations.input import parse_workbook
from app.calculations.context import CalculationRun, RowResult
from app.calculations.engine import execute
from test_calculation_engine import Masters

def workbook(rows=None, detail=None, pcba=False):
    book=Workbook();sheet=book.active;sheet.title='BOM'
    sheet.append(['0 / 1 / 2 / 3','New variant','Supplier','End Item','Customer No','Type','Commodity','Qty/Veh'])
    for row in rows or [[1,1,'New supplier','New owner','OTHER-001','MAKE','HARD',1]]:sheet.append(row)
    if detail is not None:
        sheet=book.create_sheet('Hard');sheet.append(['Supplier','End Item','Customer No','Type','Commodity']);sheet.append(detail)
    if pcba:
        sheet=book.create_sheet('PCBA');sheet.append(['PCBA Part Number','Material Code','Supplier','End Item','RM Supplier','Pack quantity'])
        sheet.append(['BOARD-OTHER','COMP-OTHER','New supplier','Descriptive board label','RM supplier',2])
    out=BytesIO();book.save(out);return out.getvalue()

@pytest.mark.parametrize('type_',['MAKE','BUY NON STD','ASSY','SPECIAL PROCESS'])
def test_hard_bom_only_is_source_input_not_a_missing_detail(type_):
    parsed=parse_workbook(workbook([[1,1,'New supplier','New owner','OTHER-001',type_,'HARD',1]]))
    issue=next(item for item in parsed.issues if item['code']=='bom_calculation_input')
    assert issue['severity']=='info' and issue['blocking'] is False
    assert not any(item['code']=='missing_detail_match' for item in parsed.issues)
    assert parsed.records[0]['_calculation_input_source']=='BOM'

def test_existing_hard_sheet_cannot_supply_another_customers_detail():
    parsed=parse_workbook(workbook(detail=['New supplier','New owner','SOMEONE-ELSE','MAKE','HARD']))
    issue=next(item for item in parsed.issues if item['code']=='missing_detail_match')
    assert issue['severity']=='error' and issue['blocking'] is True
    assert parsed.records[0]['_details']==[]

def test_pcba_descriptive_end_item_is_information_with_verified_ownership():
    parsed=parse_workbook(workbook([[1,1,'New supplier','New owner','BOARD-OTHER','BUY NON STD','PCBA',1]],pcba=True))
    issue=next(item for item in parsed.issues if item['code']=='pcba_detail_end_item_differs')
    assert issue['severity']=='info' and issue['blocking'] is False
    assert parsed.records[0]['End Item']=='New owner'
    assert parsed.records[0]['_pcba_components'][0]['_pcba_owner_end_item']=='New owner'

def test_information_does_not_turn_complete_costs_into_partial_run():
    row=RowResult('a','','PKG',{'Type':'PKG'},values={'Grand Total Cost/Pc':D(2)},status='success')
    run=CalculationRun([row],issues=[{'severity':'info','message':'Retained evidence','blocking':False}])
    assert run.status=='calculated' and run.as_dict()['final_cost_complete'] is True
    run.issues.append({'severity':'error','message':'Required summary failed','blocking':True})
    assert run.status=='partial' and run.as_dict()['final_cost_complete'] is False

def test_success_without_final_cost_stays_partial_and_does_not_stop_other_rows(monkeypatch):
    import app.calculations.engine as engine
    def calculate(record,context):
        return {'Basic Cost/Pc':D(7)} if record['Customer No']=='OTHER-001' else {'Grand Total Cost/Pc':D(3)}
    monkeypatch.setattr(engine,'dispatch',lambda *args:SimpleNamespace(module='isolated',calculate=calculate))
    run=execute(workbook([[1,1,'New supplier','New owner','OTHER-001','PKG','',1],[1,1,'New supplier','New owner','OTHER-002','PKG','',1]]),Masters(),development=True)
    assert run.counts['partial']==1 and run.counts['calculated']==1
    row=next(row for row in run.rows if row.inputs['Customer No']=='OTHER-001')
    assert row.values['Basic Cost/Pc']==7 and row.issues[0]['code']=='missing_final_cost'
    assert row.final_cost_complete is False
    assert 'Grand Total Cost/Pc' not in row.values

@pytest.mark.parametrize('invalid',[None,'',True,D('NaN'),D('Infinity')])
def test_invalid_final_cost_is_never_calculated(monkeypatch,invalid):
    import app.calculations.engine as engine
    monkeypatch.setattr(engine,'dispatch',lambda *args:SimpleNamespace(module='isolated',calculate=lambda *args:{'Basic Cost/Pc':D(2),'Total Cost/Pc':D(1),'Grand Total Cost/Pc':invalid}))
    run=execute(workbook([[1,1,'New supplier','New owner','OTHER-001','PKG','',1]]),Masters(),development=True)
    assert run.counts['calculated']==0 and run.rows[0].final_cost_complete is False

def test_incomplete_producer_cannot_feed_a_purchasing_wrapper(monkeypatch):
    import app.calculations.engine as engine
    monkeypatch.setattr(engine,'dispatch',lambda *args:SimpleNamespace(module='isolated',calculate=lambda *args:{'Basic Cost/Pc':D(2)}))
    run=execute(workbook([[1,1,'New supplier','New owner','OTHER-001','BUY NON STD','HARD',1]]),Masters(),development=True)
    assert run.producer_rows[0].calculation_status=='PARTIAL'
    assert run.rows[0].calculation_status=='BLOCKED'
    assert run.rows[0].issues[0]['code']=='failed_producer'
    assert 'Grand Total Cost/Pc' not in run.rows[0].values

def test_diagnostics_use_current_server_run_and_exact_owner(monkeypatch):
    import app.calculations.engine as engine
    monkeypatch.setattr(engine,'dispatch',lambda *args:SimpleNamespace(module='isolated',calculate=lambda *args:{'Grand Total Cost/Pc':D(2)}))
    data=workbook()
    runs=[execute(data,Masters(),development=True) for _ in range(2)]
    assert runs[0].metadata['run_id']!=runs[1].metadata['run_id']
    for run in runs:
        row=run.rows[0];issue=row.input_diagnostics[0]
        assert issue['record_id']==row.record_id and issue['run_id']==run.metadata['run_id']
        assert run.status=='calculated' and row.final_cost_complete


@pytest.mark.parametrize('type_',['MAKE','ASSY'])
def test_hard_without_type_header_uses_original_bom_fallback(type_):
    book=load_workbook(BytesIO(workbook([[1,1,'New supplier','New owner','OTHER-001',type_,'HARD',1]])))
    hard=book.create_sheet('Hard');hard.append(['Supplier','Customer No']);hard.append(['New supplier','unrelated annotation'])
    raw=BytesIO();book.save(raw)
    parsed=parse_workbook(raw.getvalue())
    assert parsed.records[0]['_calculation_input_source']=='BOM'
    assert not any(i.get('blocking') for i in parsed.issues)


def test_hard_assy_with_explicit_different_detail_requires_matching_owner():
    parsed=parse_workbook(workbook([[1,1,'New supplier','New owner','OTHER-001','ASSY','HARD',1]],detail=['New supplier','New owner','UNRELATED','ASSY','HARD']))
    issue=next(i for i in parsed.issues if i['code']=='missing_detail_match')
    assert issue['blocking'] is True
    assert parsed.records[0]['_details']==[]


@pytest.mark.parametrize('kind',['material','process'])
@pytest.mark.parametrize('rate',[None,'0'])
def test_required_rates_distinguish_missing_from_explicit_zero(kind,rate):
    from sqlalchemy import Table,MetaData
    from app.calculations.context import CalculationContext
    from app.calculations.common import CalculationError
    from app.calculations.commodities.shared import material,process_rate
    from test_calculation_branch_generality import RelationalFixture
    fixture=RelationalFixture('Alpha')
    try:
        code='material_master' if kind=='material' else 'process_master'
        table=Table('src_contract_'+code,MetaData(),autoload_with=fixture.db.connection())
        column='material_rate' if kind=='material' else 'process_rate'
        fixture.db.execute(table.update().values(**{column:rate}))
        context=CalculationContext(fixture.resolver,[])
        record={'Material Code':fixture.material,'RM Base':fixture.period,'Sector & Location':'AD'}
        call=lambda:material(context,record) if kind=='material' else process_rate(context,record,fixture.process)
        if rate is None:
            with pytest.raises(CalculationError) as error:call()
            assert error.value.details['master_error']['master_failure']=='blank_value'
            assert error.value.details['master_error']['source_table']=='src_contract_'+code
        else:assert call()==D(0)
    finally:fixture.close()


def test_unused_zero_quantity_process_has_zero_cost_not_a_fabricated_rate():
    from app.calculations.context import CalculationContext
    from app.calculations.common import CalculationError
    from app.calculations.commodities.shared import process_lines
    from test_calculation_branch_generality import RelationalFixture
    f=RelationalFixture('Alpha')
    try:
        context=CalculationContext(f.resolver,[])
        record={'Sector & Location':'AD','Process Lines':[{'name':'Unused unmapped process','quantity':0},{'name':f.process,'quantity':2}]}
        assert process_lines(context,record)==f.process_rate*2
        unused=next(x for x in context.provenance if x.get('name')=='Unused unmapped process')
        assert unused['cost']==0 and unused['rate'] is None
        record['Process Lines']=[{'name':'Unused unmapped process','quantity':1}]
        with pytest.raises(CalculationError):process_lines(CalculationContext(f.resolver,[]),record)
    finally:f.close()


@pytest.mark.parametrize('type_',['MAKE','ASSY'])
def test_unique_hard_detail_label_keeps_original_bom_owner(type_):
    parsed=parse_workbook(workbook([[1,1,'New supplier','New owner','OTHER-001',type_,'HARD',1]],detail=['New supplier','Descriptive detail label','OTHER-001',type_,'HARD']))
    row=parsed.records[0]
    assert row['End Item']=='New owner' and row['_details'][0]['End Item']=='Descriptive detail label'
    assert row['Costing Supplier']==row['Purchasing Supplier']=='New supplier'
    assert not any(i.get('blocking') for i in parsed.issues)
    assert any(i['code']=='hard_detail_end_item_differs' for i in parsed.issues)


def test_hard_label_match_rejects_two_possible_bom_owners():
    parsed=parse_workbook(workbook([[1,1,'New supplier','Owner one','OTHER-001','MAKE','HARD',1],[1,1,'New supplier','Owner two','OTHER-001','MAKE','HARD',1]],detail=['New supplier','Other label','OTHER-001','MAKE','HARD']))
    assert all(not r['_details'] for r in parsed.records)
    assert any(i['code']=='ambiguous_hard_detail_owner' and i['blocking'] for i in parsed.issues)


def test_hard_label_match_rejects_multiple_detail_candidates():
    book=load_workbook(BytesIO(workbook(detail=['New supplier','Other label','OTHER-001','MAKE','HARD'])))
    book['Hard'].append(['New supplier','Second label','OTHER-001','MAKE','HARD'])
    out=BytesIO();book.save(out);parsed=parse_workbook(out.getvalue())
    assert parsed.records[0]['_details']==[]
    assert any(i['code']=='ambiguous_hard_detail_owner' for i in parsed.issues)


def test_hard_label_match_does_not_relax_both_supplier_and_owner():
    parsed=parse_workbook(workbook(detail=['Different supplier','Other label','OTHER-001','MAKE','HARD']))
    assert not parsed.records[0]['_details']
    assert any(i['code']=='missing_detail_match' and i['blocking'] for i in parsed.issues)


def test_hard_renamed_detail_executes_database_arithmetic_once_and_rolls_up():
    from test_calculation_branch_generality import RelationalFixture
    f=RelationalFixture('Alpha')
    try:
        book=Workbook();bom=book.active;bom.title='BOM'
        bom.append(['0 / 1 / 2 / 3','V','Supplier','End Item','Customer No','Type','Commodity','Qty/Veh'])
        bom.append([0,1,f.supplier,'Final owner','ROOT-NEW','END ITEM','',1])
        bom.append([1,1,f.supplier,'Final owner',f.part,'MAKE','HARD',1])
        hard=book.create_sheet('Hard');hard.append(['Source introduction'])
        hard.append(['Supplier','End Item','Customer No','Type','Commodity','Qty/Veh','Material Code','RM Base','Gross Wt','Part Wt','Cycle Time','Cavity','m/c Tonnage'])
        hard.append([f.supplier,'Descriptive manufacturer label',f.part,'MAKE','HARD',1,f.material,f.period,2,1,30,2,100])
        out=BytesIO();book.save(out)
        run=execute(out.getvalue(),f.resolver,development=True)
        assert run.counts['calculated']==2
        part=next(r for r in run.rows if r.commodity=='HARD');root=next(r for r in run.rows if r.type=='END ITEM')
        expected=f.rate*2+(D(8)*3600*2*D('.8')/30)/(100*D(10))+D('.2')
        assert part.values['Grand Total Cost/Pc']==expected
        assert root.values['V']==expected and part.inputs['End Item']=='Final owner'
        assert not run.producer_rows
    finally:f.close()


@pytest.mark.parametrize('scrap,rate,expected',[(D(0),None,D(100)),(D(0),D(7),D(100)),(D(2),D(7),D(86))])
def test_soft_zero_scrap_is_zero_recovery_not_a_fabricated_price(scrap,rate,expected):
    from test_calculation_formula_corrections import RateFixtures
    from app.calculations.context import CalculationContext
    from app.calculations.common import CalculationError
    from app.calculations.masters import FieldRef
    from app.calculations.dispatcher import dispatch
    master=RateFixtures();original=master.lookup
    def lookup(name,keys,field,**kwargs):
        if name=='material_master' and isinstance(field,FieldRef) and field.name=='Scrap Rate':
            if rate is None and kwargs.get('missing')!='none':
                raise ValueError('Selected scrap price is blank')
            return rate
        return original(name,keys,field,**kwargs)
    master.lookup=lookup
    row={'Commodity':'SOFT','Type':'MAKE','Supplier':'new supplier','Qty/Veh':1,
         'Customer No':'independent-part','Project':'independent-project',
         'Material Code':'verified material','RM Base':'verified period',
         'Net RM consumption':D(10)-scrap,'Blank Consumption':10,
         'No of parts / Blank':1,'_process_lines':[]}
    context=CalculationContext(master,[row])
    context.consumption=lambda *args:{'Effective Gross Consumption':D(10)}
    context.consumables=lambda *args:D(0)
    result=dispatch('SOFT','MAKE').calculate(row,context)
    assert result['RM Cost/Pc']==expected and result['Grand Total Cost/Pc']==expected
    assert result['Scrap Rate']==rate and result['Scrap']==scrap
    if rate is None:
        assert any(p.get('rate_required') is False and p.get('recovery')==0 and p.get('rate') is None for p in context.provenance)


@pytest.mark.parametrize('scrap',[D(1),D(-1)])
def test_soft_nonzero_scrap_still_requires_the_exact_master_price(scrap):
    from test_calculation_formula_corrections import RateFixtures
    from app.calculations.context import CalculationContext
    from app.calculations.common import CalculationError
    from app.calculations.masters import FieldRef
    from app.calculations.dispatcher import dispatch
    master=RateFixtures();original=master.lookup
    def lookup(name,keys,field,**kwargs):
        if name=='material_master' and isinstance(field,FieldRef) and field.name=='Scrap Rate':
            assert kwargs.get('missing','error')=='error'
            raise ValueError('Selected scrap price is blank')
        return original(name,keys,field,**kwargs)
    master.lookup=lookup
    row={'Commodity':'SOFT','Type':'MAKE','Supplier':'new supplier','Qty/Veh':1,
         'Material Code':'verified material','RM Base':'verified period',
         'Net RM consumption':D(10)-scrap,'Blank Consumption':10,
         'No of parts / Blank':1,'_process_lines':[]}
    context=CalculationContext(master,[row]);context.consumption=lambda *args:{'Effective Gross Consumption':D(10)}
    context.consumables=lambda *args:D(0)
    with pytest.raises(CalculationError) as exc:dispatch('SOFT','MAKE').calculate(row,context)
    assert exc.value.code=='master_lookup_blocked'
