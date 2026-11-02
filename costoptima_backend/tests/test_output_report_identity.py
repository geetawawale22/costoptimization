from datetime import date
from types import SimpleNamespace
import pytest
from app.calculations.development import output_filename
from app.calculations.output.workbook import result_value

@pytest.mark.parametrize('project,part,stamp,expected',[('Other Project','Part x','2026-10-29T20:00:00+00:00','30 Oct 2026 01.30.00 AM'),('Second','Part 02','2024-01-01T01:00:00+00:00','01 Jan 2024 06.30.00 AM')])
def test_filename_uses_named_first_owner_and_recorded_india_report_time(project,part,stamp,expected):
    payload={'metadata':{'report_generated_at':stamp},'rows':[{'inputs':{'PF Code':'A/B','Project':project,'Part Name':part,'BC / SBC / TKO / RoCM':'SBC','Date':'old','RM Base':'Jan#24'}}]}
    name=output_filename(payload,42,'unrelated.xlsx')
    assert name.startswith('A_B '+project+' '+part+' SBC ')
    assert expected in name and name.endswith('_42.xlsx')
    assert '/' not in name and 'unrelated' not in name

@pytest.mark.parametrize('period',['Jan#24','May#26'])
def test_output_report_date_does_not_overwrite_input_or_period(period):
    inputs={'Date':date(2020,2,3),'RM Base':period,'_report_date':date(2026,10,30)}
    row=SimpleNamespace(inputs=inputs,values={},commodity='',dependency_only=False)
    assert result_value(row,'Date','source')==date(2026,10,30)
    assert result_value(row,'RM Base','source')==period
    assert inputs['Date']==date(2020,2,3)

def test_filename_rejects_naive_or_missing_generation_time():
    for metadata in ({},{'report_generated_at':'2026-01-01T00:00:00'}):
        with pytest.raises(ValueError):output_filename({'metadata':metadata},1)


def test_report_table_preserves_native_excel_dates_with_readable_number_format():
    from openpyxl import Workbook,load_workbook
    from io import BytesIO
    from app.calculations.output.workbook import table
    book=Workbook();book.remove(book.active)
    ws=table(book,'Report',['Date','RM Base'],[[date(2026,10,30),'Jan#24']],['General','General'])
    assert ws['A2'].number_format=='dd.mmm.yyyy'
    stream=BytesIO();book.save(stream)
    loaded=load_workbook(BytesIO(stream.getvalue()),data_only=True)
    assert loaded['Report']['A2'].value.date()==date(2026,10,30)
    assert loaded['Report']['B2'].value=='Jan#24'


@pytest.mark.parametrize('family',['GDC','PDC','FORGING','SAND_CASTING','THERMAL'])
def test_family_detail_input_date_is_not_bom_generation_day(family):
    from datetime import datetime
    actual=datetime(2024,7,12,8,30)
    row=SimpleNamespace(inputs={'Date':actual,'_report_date':date(2026,10,30),
                               '_report_datetime':datetime(2026,10,30,18,45,10)},values={},commodity=family,dependency_only=False)
    assert result_value(row,'Date','input',family)==actual

@pytest.mark.parametrize('family',['SOFT','STAMPING'])
def test_soft_stamping_detail_date_uses_source_defined_generation_timestamp(family):
    from datetime import datetime
    stamp=datetime(2026,10,30,18,45,10)
    row=SimpleNamespace(inputs={'Date':date(2024,7,12),'_report_date':stamp.date(),
                               '_report_datetime':stamp},values={},commodity=family,dependency_only=False)
    assert result_value(row,'Date','input',family)==stamp


def test_native_report_timestamp_keeps_time_after_excel_serialization():
    from datetime import datetime
    from openpyxl import Workbook,load_workbook
    from io import BytesIO
    from app.calculations.output.workbook import table
    actual=datetime(2026,10,30,23,59,58)
    book=Workbook();book.remove(book.active)
    sheet=table(book,'Current report',['Date'],[[actual]],['General'])
    assert sheet['A2'].number_format=='dd.mmm.yyyy hh:mm:ss'
    stream=BytesIO();book.save(stream)
    restored=load_workbook(BytesIO(stream.getvalue()))
    assert restored['Current report']['A2'].value==actual


@pytest.mark.parametrize('family,prefix',[('SOFT','ST_'),('STAMPING','SM_')])
def test_legacy_detail_accounting_style_cannot_override_native_timestamp(family,prefix):
    from datetime import datetime
    from io import BytesIO
    from openpyxl import load_workbook
    from app.calculations.context import RowResult,CalculationRun
    from app.calculations.output import build_workbook
    stamp=datetime(2026,10,30,20,27,4,892204)
    row=RowResult('independent-owner',family,'MAKE',{
        'Customer No':'different-customer','Supplier':'Independent supplier',
        'End Item':'Independent item','Date':date(2024,3,2),'RM Base':'May#26',
        '_report_date':stamp.date(),'_report_datetime':stamp,
        'Qty/Veh':1,'_variants':{'Other variant':1}},values={},status='blocked')
    data=build_workbook(CalculationRun([row]))
    book=load_workbook(BytesIO(data),data_only=True)
    sheet=book[prefix+'Independent supplier']
    column=next(cell.column for cell in sheet[2] if cell.value=='Date')
    cell=sheet.cell(3,column)
    assert isinstance(cell.value,datetime)
    assert abs((cell.value-stamp).total_seconds())<.001
    assert cell.number_format=='dd.mmm.yyyy hh:mm:ss'
    assert row.inputs['Date']==date(2024,3,2)
    assert row.inputs['RM Base']=='May#26'
