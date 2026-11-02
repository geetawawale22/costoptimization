"""Partial arithmetic, owner safety and downloadable diagnostics use isolated values."""
from decimal import Decimal as D
from io import BytesIO
from types import SimpleNamespace
import pytest
from openpyxl import Workbook,load_workbook
from app.calculations.common import CalculationError
from app.calculations.context import CalculationRun,CalculationContext,RowResult
from app.calculations.engine import execute
from app.calculations.input import parse_workbook,InputValidationError
from app.calculations.output import build_workbook
from app.calculations.merging.rollup import amount
from test_calculation_engine import Masters


def input_bytes(rows=None,title='BOM',period=None):
    book=Workbook();sheet=book.active;sheet.title=title
    if period is not None:sheet.append(['RM Base',period])
    sheet.append(['0 / 1 / 2 / 3','V','Supplier','End Item','Customer No','Type','Commodity','Qty/Veh'])
    for row in rows or [[1,1,'supplier','Owner','0001','PKG','',1]]:sheet.append(row)
    buffer=BytesIO();book.save(buffer);return buffer.getvalue()


def render(run):return load_workbook(BytesIO(build_workbook(run)),data_only=True)


def test_readable_missing_bom_is_an_incomplete_artifact_not_an_invented_calculation():
    data=input_bytes(title='Hard')
    with pytest.raises(InputValidationError):parse_workbook(data)
    run=execute(data,Masters(),development=True)
    assert run.status=='partial' and run.counts['blocked']==1
    assert run.rows[0].inputs['Customer No']=='0001' and run.rows[0].values=={}
    output=render(run)
    assert {'Input_Hard','Master_Output','Calculation_Diagnostics','Calculation_Summary'}<=set(output.sheetnames)
    assert output['Input_Hard']['E2'].value=='0001'


def test_invalid_zip_is_rejected_instead_of_misrepresented_as_a_partial_result():
    with pytest.raises(InputValidationError):execute(b'not an xlsx',Masters(),development=True)


def test_checkpoint_values_survive_mid_formula_failure_and_independent_rows_continue(monkeypatch):
    from app.calculations import engine
    def calculate(record,ctx):
        if record['Customer No']=='0001':
            ctx.checkpoint(record,{'RM Cost/Pc':D('12.75'),'Material Rate':D('5.1')})
            raise CalculationError('missing_master','Conversion rate is unavailable','conversion_rate')
        return {'Basic Cost/Pc':D(3),'Grand Total Cost/Pc':D(3)}
    monkeypatch.setattr(engine,'dispatch',lambda *args:SimpleNamespace(module='isolated',calculate=calculate))
    data=input_bytes([[1,1,'supplier','Owner','0001','PKG','',1],[1,1,'supplier','Owner','0002','PKG','',1]])
    run=execute(data,Masters(),development=True)
    assert run.counts=={'total':2,'calculated':1,'partial':1,'blocked':0,'unverified':0,'error':0}
    first=next(r for r in run.rows if r.inputs['Customer No']=='0001')
    assert first.values['RM Cost/Pc']==D('12.75') and 'Grand Total Cost/Pc' not in first.values
    assert next(r for r in run.rows if r.inputs['Customer No']=='0002').values['Grand Total Cost/Pc']==3
    with pytest.raises(CalculationError):amount(first,'V')
    book=render(run);assert any(c.value==12.75 for row in book['Calculation_Intermediates'] for c in row)


def test_unexpected_row_error_keeps_completed_values_and_other_rows(monkeypatch):
    from app.calculations import engine
    def calculate(record,ctx):
        ctx.checkpoint(record,{'RM Cost/Pc':D(4)})
        if record['Customer No']=='0001':raise RuntimeError('isolated arithmetic handler error')
        return {'Grand Total Cost/Pc':D(4)}
    monkeypatch.setattr(engine,'dispatch',lambda *args:SimpleNamespace(module='isolated',calculate=calculate))
    run=execute(input_bytes([[1,1,'supplier','Owner','0001','PKG','',1],[1,1,'supplier','Owner','0002','PKG','',1]]),Masters(),development=True)
    assert run.counts['error']==1 and run.counts['calculated']==1
    assert run.rows[0].values['RM Cost/Pc']==4
    assert 'Calculation_Diagnostics' in render(run)


def test_checkpoint_boundary_prevents_cost_and_sensitive_field_leakage():
    ctx=CalculationContext(Masters(),[]);ctx.begin_record('a')
    ctx.checkpoint({'_record_id':'a'},{'RM Cost/Pc':D(8),'Grand Total Cost/Pc':D(99),'password':'unsafe','secret_key':'unsafe'})
    assert ctx.partial_values()=={'RM Cost/Pc':D(8),'Computed incomplete Grand Total Cost/Pc':D(99)}
    assert ctx.partial_values().get('Grand Total Cost/Pc') is None
    with pytest.raises(CalculationError):ctx.checkpoint({'_record_id':'b'},{'RM Cost/Pc':D(3)})
    ctx.begin_record('b');assert ctx.partial_values()=={}


def test_partial_master_output_uses_calculated_fields_and_never_uploaded_unknown_costs():
    record={'Customer No':'0001','End Item':'Owner','Supplier':'owner','Purchasing Supplier':'owner','Costing Supplier':'costing',
        'Type':'MAKE','Commodity':'HARD','Qty/Veh':1,'RM Cost/Pc':999,'Grand Total Cost/Pc':999,'_variants':{'V':1}}
    row=RowResult('row','HARD','MAKE',record,{'RM Cost/Pc':D('7.25')},status='blocked',issues=[{'code':'missing_master','field':'process_rate','message':'Missing conversion rate'}])
    book=render(CalculationRun([row]));sheet=book['Master_Output'];values={c.value:sheet.cell(2,c.column).value for c in sheet[1]}
    assert values['RM Cost']==7.25 and values['Total Cost/Pc'] is None
    assert values['Execution status']=='PARTIAL'
    diagnostic=book['Calculation_Diagnostics'];headers={c.value:c.column for c in diagnostic[1]}
    assert diagnostic.cell(2,headers['Purchasing Supplier']).value=='owner'
    assert diagnostic.cell(2,headers['Costing Supplier']).value=='costing'
    assert 'process_rate' in diagnostic.cell(2,headers['Missing master fields']).value


def test_failed_end_item_and_sbc_remain_blank_with_visible_status():
    root=RowResult('root','','END ITEM',{'Customer No':'0001','End Item':'Owner','Supplier':'owner','Type':'END ITEM','Qty/Veh':1,'_variants':{'V':1}},status='blocked',issues=[{'code':'incomplete_end_item','message':'Missing child result'}])
    book=render(CalculationRun([root]))
    assert book['End Item']['G2'].value is None and book['SBC']['D3'].value is None
    assert book['End Item']['H2'].value=='BLOCKED'


def test_unassigned_detail_keeps_original_values_without_joining_rollups():
    orphan={'_record_id':'orphan','_sheet':'Hard','_row':3,'Customer No':'0099','Supplier':'supplier','Part Wt':D('.045')}
    run=CalculationRun([],metadata={'unassigned_details':[orphan]})
    book=render(run);assert book['Calculation_Diagnostics']['C2'].value=='0099'
    assert any(c.value==.045 for row in book['Calculation_Intermediates'] for c in row)
    assert run.counts['total']==0


def test_unknown_output_projection_does_not_remove_other_sheets(monkeypatch):
    from app.calculations.output import estimation
    monkeypatch.setattr(estimation,'part_estimation_output',lambda *a:(_ for _ in ()).throw(ValueError('isolated estimate layout failure')))
    row=RowResult('one','','PKG',{'Customer No':'0001','Type':'PKG','Qty/Veh':1},values={'Basic Cost/Pc':D(0)},status='success')
    run=CalculationRun([row]);book=render(run)
    assert 'Master_Output' in book and 'Calculation_Diagnostics' in book
    assert any(issue['code']=='output_projection_error' for issue in run.issues)


def test_diagnostic_formula_like_identifiers_are_plain_text():
    row=RowResult('=unsafe','','PKG',{'Customer No':'=1+1','password':'never store this'},status='blocked')
    book=load_workbook(BytesIO(build_workbook(CalculationRun([row]))),data_only=False)
    assert book['Calculation_Diagnostics']['A2'].data_type=='s'
    assert not any(c.value=='never store this' for row in book['Calculation_Intermediates'] for c in row)


@pytest.mark.parametrize('status,values,expected',[
 ('success',{'RM Cost/Pc':D(1)},'CALCULATED'),('blocked',{'RM Cost/Pc':D(0)},'PARTIAL'),
 ('blocked',{},'BLOCKED'),('error',{'RM Cost/Pc':D(1)},'ERROR'),('unverified',{'RM Cost/Pc':D(1)},'UNVERIFIED')])
def test_presentation_status_does_not_relax_dependency_eligibility(status,values,expected):
    row=RowResult('a','','PKG',{},values,status=status)
    assert row.calculation_status==expected
    assert row.as_dict()['calculation_status']==expected


def test_pcba_independent_components_remain_visible_when_one_master_is_missing():
    from test_calculation_pcba_ownership import workbook,zero_norm_master
    master=zero_norm_master();original=master.lookup
    def lookup(code,keys,field,**kwargs):
        if code=='ebom' and keys.get('material_code')=='b':raise CalculationError('missing_master','eBom b missing','fob')
        return original(code,keys,field,**kwargs)
    master.lookup=lookup;run=execute(workbook(),master,development=True)
    board=run.producer_rows[0]
    assert board.status=='blocked' and board.calculation_status=='PARTIAL'
    assert len(board.values['_pcba_component_results'])==2
    assert board.values['_pcba_component_results'][0]['calculation_status']=='CALCULATED'
    assert board.values['_pcba_component_results'][1]['calculation_status']=='BLOCKED'
    assert board.values.get('Total Cost/Pc') is None
    book=render(run);sheet=book['PCBA_OP'];headers={c.value:c.column for c in sheet[1]}
    assert sheet.max_row==3
    assert sheet.cell(2,headers['Material Code']).value=='a'
    assert sheet.cell(3,headers['Material Code']).value=='b'
    assert sheet.cell(2,headers['Grand Total Cost']).value is not None
    assert sheet.cell(3,headers['Grand Total Cost']).value is None
    assert sheet.cell(3,headers['PACK A Cost/Veh']).value is None


@pytest.mark.parametrize('family',['Soft','Stamping'])
def test_original_named_grouped_period_overrides_bom_for_supported_family(family):
    book=Workbook();bom=book.active;bom.title='BOM'
    bom.append(['Supplier','End Item','Customer No','Type','Commodity','Qty/Veh','RM Base'])
    bom.append(['supplier','Owner','0001','MAKE',family.upper(),1,'bom period'])
    detail=book.create_sheet(family);detail.append(['RM Base','group period'])
    detail.append(['Supplier','End Item','Customer No','Type','Commodity','Qty/Veh'])
    detail.append(['supplier','Owner','0001','MAKE',family.upper(),1])
    stream=BytesIO();book.save(stream);parsed=parse_workbook(stream.getvalue())
    assert parsed.records[0]['RM Base']=='group period'
    assert parsed.records[0]['_bom_values']['RM Base']=='bom period'


@pytest.mark.parametrize('bom_period', ['absent',None,'explicit'])
def test_hard_grouped_period_is_only_the_original_missing_header_fallback(bom_period):
    book=Workbook();bom=book.active;bom.title='BOM'
    headers=['Supplier','End Item','Customer No','Type','Commodity','Qty/Veh']
    values=['supplier','Owner','0001','MAKE','HARD',1]
    if bom_period!='absent':headers+=['RM Base'];values+=[bom_period]
    bom.append(headers);bom.append(values)
    detail=book.create_sheet('Hard');detail.append(['RM Base','group period'])
    detail.append(['Supplier','End Item','Customer No','Type','Commodity','Qty/Veh'])
    detail.append(['supplier','Owner','0001','MAKE','HARD',1])
    stream=BytesIO();book.save(stream);parsed=parse_workbook(stream.getvalue())
    assert parsed.records[0].get('RM Base')==('group period' if bom_period=='absent' else bom_period)


def test_consumption_stages_are_saved_before_a_later_formula_fails(monkeypatch):
    from app.calculations import consumption
    def select(context,master,output,*args,**kwargs):
        if output=='Cal RM cons':return '2*3'
        raise CalculationError('missing_condition','Later blank consumption condition missing',output)
    monkeypatch.setattr(consumption,'select_rule',select)
    record={'_record_id':'owner'};ctx=CalculationContext(Masters(),[record]);ctx.begin_record('owner')
    with pytest.raises(CalculationError):ctx.consumption(record,'SOFT')
    assert ctx.partial_values()=={'Cal RM cons':D(6)}


def test_all_failed_pcba_components_do_not_invent_zero_board_subtotals():
    from test_calculation_pcba_ownership import workbook,zero_norm_master
    master=zero_norm_master();original=master.lookup
    def lookup(code,keys,field,**kwargs):
        if code=='ebom':raise CalculationError('missing_master','No component master','fob')
        return original(code,keys,field,**kwargs)
    master.lookup=lookup;run=execute(workbook(),master,development=True)
    board=run.producer_rows[0]
    assert board.calculation_status=='BLOCKED'
    assert board.values.get('Completed component RM Cost/Pc') is None
    assert board.values.get('Total Cost/Pc') is None


def test_explicitly_unverified_result_is_visible_but_not_an_eligible_dependency():
    inputs={'Customer No':'0001','Supplier':'supplier','End Item':'Owner','Qty/Veh':1,'_variants':{'V':1}}
    row=RowResult('one','HARD','MAKE',inputs,{'Grand Total Cost/Pc':D(9),'_calculation_unverified':True},status='success')
    assert row.calculation_status=='UNVERIFIED'
    with pytest.raises(CalculationError):amount(row,'V')
    ctx=CalculationContext(Masters(),[]);ctx.result_records.append(row)
    with pytest.raises(CalculationError):ctx.require_result(inputs,'HARD','MAKE','Grand Total Cost/Pc')
    book=render(CalculationRun([row]));assert 'Calculation_Diagnostics' in book
    assert any(c.value==9 for record in book['Calculation_Intermediates'] for c in record)


def test_soft_group_producer_checkpoint_identity_is_separate_from_owner():
    from test_calculation_soft_special import detail,owner,Masters as SoftMasters
    from app.calculations.commodities.soft.special_process import calculate
    record=owner([detail('a','board',qty=2,process=1),detail('child','child',qty=4,process=3)])
    ctx=CalculationContext(SoftMasters(),[record]);ctx.begin_record(record['_record_id'])
    values=calculate(record,ctx)
    assert values['Grand Total Cost/Pc']==D('30.8')
    assert all(producer.status=='success' for producer in ctx.result_records)
    assert ctx._checkpoint_record_id==record['_record_id']


def test_soft_group_failure_keeps_later_independent_producer_without_valid_group_total():
    from test_calculation_soft_special import detail,owner,Masters as SoftMasters
    from app.calculations.commodities.soft.special_process import calculate
    first=detail('a','board');first['_process_lines']=[{'name':'missing process','quantity':1}]
    record=owner([first,detail('child','child',qty=4,process=3)])
    ctx=CalculationContext(SoftMasters(),[record]);ctx.begin_record(record['_record_id'])
    with pytest.raises(CalculationError) as error:calculate(record,ctx)
    assert error.value.code=='soft_special_process_producer_failed'
    assert [producer.status for producer in ctx.result_records]==['blocked','success']
    assert ctx.partial_values()['Completed grouped Process Cost']==24
    assert ctx.partial_values().get('Grand Total Cost/Pc') is None


def test_unknown_family_keeps_identity_and_cannot_capture_reserved_diagnostic_sheet_name():
    inputs={'Customer No':'0099','Supplier':'supplier','End Item':'Owner','Commodity':'CALCULATION_DIAGNOSTICS','Type':'UNSUPPORTED','Qty/Veh':1}
    row=RowResult('one','CALCULATION_DIAGNOSTICS','UNSUPPORTED',inputs,status='blocked')
    book=render(CalculationRun([row]));sheet=book['Master_Output'];headers={c.value:c.column for c in sheet[1]}
    assert sheet.cell(2,headers['Customer No']).value=='0099'
    assert sheet.cell(2,headers['Execution status']).value=='BLOCKED'
    assert book['Calculation_Diagnostics']['A1'].value=='Record ID'
    unknown=next(sheet for sheet in book if sheet.title.startswith('Unresolved_'))
    assert unknown['A2'].value=='0099'
