"""Current authoritative output contract; workbooks are test evidence, never runtime masters."""
from pathlib import Path
from decimal import Decimal as D
from io import BytesIO
import pytest
from openpyxl import load_workbook
from app.calculations.output.layouts import MASTER_MATRIX
from app.calculations.context import RowResult, CalculationRun
from app.calculations.output import build_workbook


def reference_path():
    path=Path(__file__).resolve().parents[3]/"CostOptima_PCM_updated (1)"/"CostOptima"/"Master Output Matrix.xlsx"
    if not path.is_file():
        pytest.skip("Authoritative reference workbook is unavailable; fixed contract checks remain active")
    return path


def test_current_matrix_exact_source_contract_all_cells():
    book=load_workbook(reference_path(),data_only=False)
    sheet=book["Master output Final"]
    headers={str(cell.value).strip().upper():cell.column for cell in sheet[2] if cell.value is not None}
    families=list(MASTER_MATRIX[0]["mappings"])
    expected=[]
    for row in sheet.iter_rows(min_row=3):
        if row[0].value is None:continue
        expected.append({"header":row[0].value,"bom":row[1].value=="Y",
                         "mappings":{family:sheet.cell(row[0].row,headers[family]).value for family in families}})
    assert MASTER_MATRIX==expected
    assert len(MASTER_MATRIX)==84 and len(families)==21
    assert not any(str(value).startswith("=") for row in MASTER_MATRIX for value in row["mappings"].values())


def test_current_matrix_material_labels_and_removed_markup_breakdown():
    by_header={row["header"]:row for row in MASTER_MATRIX}
    assert not ({"ICC Cost","REJ Cost","O&H Cost","PROFIT Cost","Handling_Soft Cost"}&set(by_header))
    for family in ("HARD MAKE","HARD BUY NON STD","GDC","PDC","SAND CASTING","SOFT","THERMAL"):
        assert by_header["Material"]["mappings"][family]=="Material Family"
        assert by_header["Grade"]["mappings"][family]=="Material Grade"
    assert by_header["Markup Cost/Pc"]["mappings"]["PCBA"]=="MARKUP/Pc"


def test_current_matrix_exports_computed_material_and_grade_and_pcba_markup():
    inputs={"Supplier":"owner","Customer No":"0001","End Item":"END","Commodity":"PCBA","Type":"ASSY","Qty/Veh":1,"_variants":{"V":1}}
    values={"Material Family":"Electronics","Material Grade":"Verified Grade","MARKUP/Pc":D("2.75"),"Basic Cost/Pc":D(12),"Total Cost/Pc":D("14.75")}
    row=RowResult("one","PCBA","ASSY",inputs,values=values,status="success")
    hard=RowResult("two","HARD","MAKE",{**inputs,"Commodity":"HARD","Type":"MAKE"},values=values,status="success")
    book=load_workbook(BytesIO(build_workbook(CalculationRun([row,hard]))))
    sheet=book["Master_Output"]
    result={cell.value:sheet.cell(2,cell.column).value for cell in sheet[1]}
    # PCBA Material/Grade cells are intentional blanks in the current matrix.
    assert result["Material"] is None and result["Grade"] is None
    hard_result={cell.value:sheet.cell(3,cell.column).value for cell in sheet[1]}
    assert hard_result["Material"]=="Electronics" and hard_result["Grade"]=="Verified Grade"
    assert result["Markup Cost/Pc"]==2.75
    assert result["Customer No"]=="0001"


def test_master_output_uses_consecutive_filtered_bom_prefix_not_every_late_y_flag():
    from app.calculations.context import RowResult,CalculationRun
    from app.calculations.output import build_workbook
    from openpyxl import load_workbook
    from io import BytesIO
    row=RowResult('assy','HARD','ASSY',{'Supplier':'owner','End Item':'E','Customer No':'001',
        'Type':'ASSY','Qty/Veh':1,'Material Code':'uploaded-code','Finish':'uploaded-finish'},
        values={'Operation Process':'verified-operation','Material Code':'computed-code','Finish':'computed-finish'},status='success')
    w=load_workbook(BytesIO(build_workbook(CalculationRun([row]))))
    s=w['Master_Output'];values={c.value:s.cell(2,c.column).value for c in s[1]}
    assert values['Supplier']=='owner'
    assert values['Material Code']=='computed-code' and values['Finish']=='computed-finish'
    assert values['Operation Process']=='verified-operation'
