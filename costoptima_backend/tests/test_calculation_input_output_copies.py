"""Original family-input projections, not masters or a second calculation flow.

Primary PCM1559,4009,7164,8758,12887,15682,16890 and final merge17741-18250.
Fixtures verify literal uploaded formulas never become executable Excel code.
"""
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
from xml.etree import ElementTree as ET

import pytest
from openpyxl import Workbook, load_workbook

from app.calculations.context import CalculationRun, RowResult
from app.calculations.input import parse_workbook
from app.calculations.output import build_workbook
from app.calculations.output.formulas import persist_numeric_caches, restore_trusted_numeric_caches
from app.calculations.output.workbook import source_input_output


HEADERS=['Supplier','End Item','Customer No','Type','Qty/Veh','Commodity','Date','Annotation']


def input_book(family='SOFT',sheet='Soft',header=2,formula=False):
    book=Workbook();bom=book.active;bom.title='BOM';bom.append(HEADERS)
    record=['Supplier','Owner',12,'MAKE',1,family,datetime(2026,10,29),None]
    bom.append(record);bom['C2'].number_format='000000'
    detail=book.create_sheet(sheet)
    for row in range(1,header):detail.cell(row,1,'Original grouped row '+str(row))
    for column,value in enumerate(HEADERS,1):detail.cell(header,column,value)
    for column,value in enumerate(record,1):detail.cell(header+1,column,value)
    detail.cell(header+1,3).number_format='000000'
    detail.cell(header+1,7).number_format='yyyy-mm-dd'
    detail.cell(header+1,8,'=WEBSERVICE("https://untrusted.invalid/token")' if formula else '=a literal label')
    # Both this empty row and the blank middle field must keep their position.
    detail.cell(header+3,8,'End annotation')
    detail.cell(header+3,1).hyperlink='https://untrusted.invalid'
    output=BytesIO();book.save(output)
    return output.getvalue()


def output_for(parsed,family='SOFT'):
    row=RowResult('record',family,'MAKE',{'Supplier':'Supplier','Customer No':'000012','Qty/Veh':1},status='blocked')
    return build_workbook(CalculationRun([row],metadata={**parsed.metadata,'branch_counts':[{'commodity':family,'type':'MAKE','count':1}]}))


@pytest.mark.parametrize('family,sheet,header,expected,export_header',[
    ('SOFT','Soft',2,'Soft',2),('STAMPING','Stamping',2,'Stamping',2),
    ('FORGING','Forging',1,'Forging',1),('THERMAL','Thermal',1,'Thermal',1),
    ('GDC','GDC',2,'GDC_Casting_Input',1),('PDC','PDC_Input',2,'PDC_Input',1),
    ('SAND CASTING','Sand Casting',2,'Sand_Casting_Input',1)])
def test_original_conditional_input_sheets_preserve_rows_types_and_header_projection(family,sheet,header,expected,export_header):
    parsed=parse_workbook(input_book(family,sheet,header))
    copy=parsed.metadata['input_sheet_copies'][0]
    assert copy['source_header_row']==header and copy['export_header_row']==export_header
    content=output_for(parsed,family)
    book=load_workbook(BytesIO(content),data_only=False);ws=book[expected]
    assert ws.cell(export_header,3).value=='Customer No'
    assert ws.cell(export_header+1,3).value=='000012'
    assert ws.cell(export_header+1,7).value==datetime(2026,10,29)
    assert ws.cell(export_header+1,7).number_format=='yyyy-mm-dd'
    assert ws.cell(export_header+2,8).value is None
    assert ws.cell(export_header+3,8).value=='End annotation'
    assert ws.cell(export_header+3,1).hyperlink is None
    assert not book._external_links
    assert ws.freeze_panes=='A'+str(export_header+1)
    if export_header==2:assert ws['A1'].value=='Original grouped row 1'


def test_uncached_uploaded_formula_is_inert_text_in_source_projection():
    parsed=parse_workbook(input_book(formula=True))
    assert any(issue['code']=='uncached_input_formula' for issue in parsed.issues)
    content=output_for(parsed);book=load_workbook(BytesIO(content),data_only=False)
    assert book['Soft']['H3'].value=='=WEBSERVICE("https://untrusted.invalid/token")'
    assert book['Soft']['H3'].data_type=='s'
    assert not any(cell.data_type=='f' for row in book['Soft'] for cell in row)
    assert load_workbook(BytesIO(content),data_only=True)['Soft']['H3'].value.startswith('=WEBSERVICE')


def test_cached_uploaded_expression_is_copied_as_value_without_formula_or_link():
    data=input_book(formula=True);out=BytesIO()
    # Construct a normal Excel formula-result cache without executing anything.
    with ZipFile(BytesIO(data)) as source,ZipFile(out,'w',ZIP_DEFLATED) as target:
        for item in source.infolist():
            content=source.read(item.filename)
            if item.filename=='xl/worksheets/sheet2.xml':
                root=ET.fromstring(content)
                ns='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
                target_cell=next(cell for cell in root.iter(ns+'c') if cell.attrib.get('r')=='H3')
                target_cell.find(ns+'v').text='7.25'
                content=ET.tostring(root,encoding='utf-8')
            target.writestr(item,content)
    parsed=parse_workbook(out.getvalue());content=output_for(parsed)
    cell=load_workbook(BytesIO(content),data_only=False)['Soft']['H3']
    assert cell.value==7.25 and cell.data_type=='n'


def test_no_hard_bom_pcba_or_inactive_family_duplicate_input_sheets():
    parsed=parse_workbook(input_book('HARD','Hard'))
    assert parsed.metadata['input_sheet_copies']==[]
    parsed=parse_workbook(input_book())
    content=output_for(parsed,'HARD');book=load_workbook(BytesIO(content))
    assert 'Soft' not in book.sheetnames and 'BOM' not in book.sheetnames
    assert 'Hard' not in book.sheetnames and 'PCBA_Input' not in book.sheetnames


def test_input_projection_budget_is_bounded_and_omission_is_explicit(monkeypatch):
    import app.calculations.input as parser
    monkeypatch.setattr(parser,'MAX_INPUT_COPY_CELLS',3)
    parsed=parse_workbook(input_book())
    assert parsed.metadata['input_sheet_copies']==[]
    assert any(issue['code']=='input_copy_limit' for issue in parsed.issues)
    assert parsed.records


def test_duplicate_family_source_targets_are_all_withheld_not_first_selected():
    parsed=parse_workbook(input_book('GDC','GDC'))
    copied=parsed.metadata['input_sheet_copies'][0]
    parsed.metadata['input_sheet_copies'].append({**copied,'source_sheet':'GDC_Input'})
    content=output_for(parsed,'GDC');book=load_workbook(BytesIO(content))
    assert 'GDC_Casting_Input' not in book.sheetnames
    assert any('Several source input sheets' in str(cell.value) for row in book['Diagnostics'] for cell in row)


def test_existing_generated_formula_caches_survive_source_projection_addition():
    root=RowResult('root','','END ITEM',{'End Item':'Owner','Customer No':'0001','Type':'END ITEM',
        'Qty/Veh':1,'_variants':{'V':1}},values={'V':Decimal('2.125')},status='success')
    original=build_workbook(CalculationRun([root]))
    formulas=load_workbook(BytesIO(original),data_only=False)
    numbers=load_workbook(BytesIO(original),data_only=True)
    count=restore_trusted_numeric_caches(formulas,numbers)
    assert count==3  # End Item SUM, SBC SUM and restored Analysis-to-End-Item link.
    assert formulas['Analysis']['B18'].value=="='End Item'!G5"
    metadata=parse_workbook(input_book()).metadata
    source_input_output(formulas,metadata)
    saved=BytesIO();formulas.save(saved)
    content=persist_numeric_caches(saved.getvalue(),formulas)
    result=load_workbook(BytesIO(content),data_only=False)
    cached=load_workbook(BytesIO(content),data_only=True)
    assert 'Soft' in result and result['End Item']['G5'].value=='=SUM(G2:G2)'
    assert cached['End Item']['G5'].value==2.125
    assert cached['SBC']['D6'].value==2.125
    assert cached['Analysis']['B18'].value==2.125
    assert cached['Analysis']['B19'].value is None  # No category total, so no invented reconciliation.


def test_cache_restore_rejects_arbitrary_formula_and_mismatched_numeric_total():
    book=Workbook();book.active['A1']=2;book.active['A2']=3
    book.active['A3']='=SUM(A1:A2)'
    cache=Workbook();cache.active['A1']=2;cache.active['A2']=3;cache.active['A3']=6
    with pytest.raises(ValueError,match='outside the trusted'):
        restore_trusted_numeric_caches(book,cache)
    book.active['A3']='=WEBSERVICE("https://untrusted.invalid")';cache.active['A3']=0
    with pytest.raises(ValueError,match='outside the trusted'):
        restore_trusted_numeric_caches(book,cache)
