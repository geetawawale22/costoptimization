"""PCM13946-13959/14203-14225 board identity with separate detail labels."""
from copy import deepcopy
from decimal import Decimal as D
from io import BytesIO
from pathlib import Path
import pytest
from openpyxl import Workbook, load_workbook
from app.calculations.input import parse_workbook
from app.calculations.engine import execute
from test_calculation_engine import zero_norm_master


def workbook(*, detail_end="High Mounted Stop Lamp", supplier="PCBA", project="Z121",
             pf="1703A", commodity="", second_owner=False, second_supplier="PCBA"):
    book=Workbook(); bom=book.active; bom.title="BOM"
    headers=["PF Code","Project","0 / 1 / 2 / 3","PACK A","PACK B","Supplier","End Item",
             "Customer No","Commodity","Type","Qty/Veh","RM Base"]
    bom.append(headers)
    bom.append(["1703A","Z121",0,1,1,"final owner","HMSL","root","","END ITEM",1,"fixture"])
    board=["1703A","Z121",1,1,1,"PCBA","HMSL","board001","PCBA","BUY NON STD",2,"fixture"]
    bom.append(board)
    if second_owner:
        other=deepcopy(board);other[5]=second_supplier;other[6]="Other assembly";bom.append(other)
    components=book.create_sheet("PCBA")
    components.append(["PF Code","Project","Supplier","End Item","PCBA Part Number","Material Code",
                       "Commodity","RM Supplier","PACK A","PACK B"])
    components.append([pf,project,supplier,detail_end,"board001","a",commodity,"vendor",2,100])
    components.append([pf,project,supplier,detail_end,"board001","b",commodity,"vendor",3,200])
    output=BytesIO();book.save(output);book.close();return output.getvalue()


def board(parsed):
    return next(row for row in parsed.records if row["Customer No"]=="board001")


def test_unique_board_retains_bom_owner_and_original_detail_end_item():
    parsed=parse_workbook(workbook()); assert parsed.ok, parsed.issues
    row=board(parsed)
    assert row["End Item"]=="HMSL" and row["Supplier"]=="PCBA"
    assert row["_pcba_detail_end_items"]==["High Mounted Stop Lamp"]
    assert row["_pcba_quantity_headers"]==["PACK A","PACK B"]
    assert len(row["_pcba_components"])==2
    for component in row["_pcba_components"]:
        assert component["End Item"]=="High Mounted Stop Lamp"
        assert component["_original_values"]["End Item"]=="High Mounted Stop Lamp"
        assert component["_pcba_detail_end_item"]=="High Mounted Stop Lamp"
        assert component["_pcba_owner_end_item"]=="HMSL"
        assert component["_pcba_owner_record_id"]==row["_record_id"]
    assert any(issue["code"]=="pcba_detail_end_item_differs" for issue in parsed.issues)
    assert not any(issue["code"]=="unmatched_pcba_component" for issue in parsed.issues)


def test_unchanged_matching_end_item_needs_no_ownership_warning():
    parsed=parse_workbook(workbook(detail_end="HMSL"));assert parsed.ok,parsed.issues
    assert not any(issue["code"]=="pcba_detail_end_item_differs" for issue in parsed.issues)


@pytest.mark.parametrize("kwargs,field",[
    ({"supplier":"other"},"Supplier"),({"supplier":""},"Supplier"),
    ({"project":"Other project"},"Project"),({"pf":"other PF"},"PF Code"),
    ({"commodity":"HARD"},"Commodity")])
def test_board_label_adapter_does_not_weaken_supplier_or_project_validation(kwargs,field):
    parsed=parse_workbook(workbook(**kwargs));assert not parsed.ok
    assert board(parsed)["_pcba_components"]==[]
    assert any(issue["code"]=="pcba_component_owner_conflict" and field in issue["fields"]
               for issue in parsed.issues)


@pytest.mark.parametrize("supplier",["PCBA","another supplier"])
def test_repeated_board_cannot_attribute_one_component_group_to_two_bom_owners(supplier):
    parsed=parse_workbook(workbook(second_owner=True,second_supplier=supplier));assert not parsed.ok
    owners=[row for row in parsed.records if row["Customer No"]=="board001"]
    assert len(owners)==2 and all(not owner["_pcba_components"] for owner in owners)
    assert sum(issue["code"]=="ambiguous_pcba_board_owner" for issue in parsed.issues)==2


def test_pcba_producer_purchasing_wrapper_and_end_item_keep_one_cost_attribution():
    # Original expressions: landed=5*2*1.2=12; solder=3*2=6;
    # first populated quantity column gives (12+6)*(2+3)=90 per board.
    master=zero_norm_master({"icc":"0","rej":"0","handling":"0","profit":"0","markup":"0"})
    run=execute(workbook(),master,development=True)
    assert all(row.status=="success" for row in run.rows),[(row.record_id,row.issues) for row in run.rows]
    owner=next(row for row in run.rows if row.commodity=="PCBA")
    root=next(row for row in run.rows if row.type=="END ITEM")
    assert len(run.producer_rows)==1
    assert run.producer_rows[0].values["Total Cost/Pc"]==90
    assert run.producer_rows[0].inputs["End Item"]==owner.inputs["End Item"]=="HMSL"
    assert owner.values["Grand Total Cost/Pc"]==90
    assert root.values["PACK A"]==root.values["PACK B"]==D(180)
    assert [item["producer_record_id"] for item in owner.provenance if "producer_record_id" in item]==[run.producer_rows[0].record_id]
    analysis=run.summaries["legacy_analysis"]
    assert all(row["status"]=="success" and row["values"]["PCBA"]==180 and row["values"]["Buy Non Std"]==0 for row in analysis)


def test_actual_hmsl_board_identifiers_attach_every_component_without_relabeling():
    path=Path(r"C:\Users\Admin\Downloads\CostOptima_PCM_updated (1)\CostOptima\legacy_reference\New folder\input\Input_Z121 HMSL TSO Working_Rev2.xlsx")
    if not path.is_file():pytest.skip("Original supplied HMSL reference unavailable")
    parsed=parse_workbook(path)
    boards=[row for row in parsed.records if row["Commodity"]=="PCBA"]
    assert {row["Customer No"] for row in boards}=={"1703AZ121LED PCB-1","1703AZ121LED PCB-2","1703AZ121DRIVER PCB Assy"}
    reference=load_workbook(path,read_only=True,data_only=True)
    try:
        rows=list(reference["PCBA"].iter_rows(values_only=True));headers=rows[0]
        board_col=headers.index("PCBA Part Number");material_col=headers.index("Material Code")
        for row in boards:
            expected=[values[material_col] for values in rows[1:] if values[board_col]==row["Customer No"]]
            assert [component["Material Code"] for component in row["_pcba_components"]]==expected
            assert row["End Item"]=="HMSL" and row["_pcba_detail_end_items"]==["High Mounted Stop Lamp"]
            assert row["_pcba_quantity_headers"]==["PACK A","PACK B","PACK C"]
            assert not any(issue["severity"]=="error" and issue["sheet"]==row["_sheet"] and issue["row"]==row["_row"] for issue in parsed.issues)
    finally:reference.close()
    assert not any(issue["code"]=="unmatched_pcba_component" for issue in parsed.issues)


def test_buy_non_std_selected_blank_norm_percentages_do_not_block_valid_pcba_cost():
    master=zero_norm_master();original=master.lookup;blank_calls=[]
    def lookup(code,keys,field,**kwargs):
        if code=='norms_buy_non_std':
            assert kwargs.get('blank_zero') is True
            blank_calls.append(field);return D(0)
        return original(code,keys,field,**kwargs)
    master.lookup=lookup
    run=execute(workbook(),master,development=True)
    assert all(row.status=='success' for row in run.rows)
    assert set(blank_calls)=={'icc','rej','handling','profit','markup'}
    assert next(row for row in run.rows if row.type=='END ITEM').values['PACK A']==180


def test_buy_non_std_missing_supplier_record_remains_blocked_even_with_blank_policy():
    from app.calculations.masters import MissingMasterError
    master=zero_norm_master();original=master.lookup
    def lookup(code,keys,field,**kwargs):
        if code=='norms_buy_non_std':raise MissingMasterError('No matching supplier record')
        return original(code,keys,field,**kwargs)
    master.lookup=lookup
    run=execute(workbook(),master,development=True)
    assert run.producer_rows[0].status=='success'
    assert all(row.status=='blocked' for row in run.rows)
    assert next(row for row in run.rows if row.commodity=='PCBA').issues[0]['code']=='master_lookup_blocked'


def test_pcba_buy_non_std_outputs_original_components_once_with_resolved_period():
    from app.calculations.output import build_workbook
    from app.calculations.context import RowResult
    run=execute(workbook(),zero_norm_master({'icc':'0','rej':'0','handling':'0','profit':'0','markup':'0'}),development=True)
    producer=run.producer_rows[0];producer.values['RM Base']='selected first BOM period';producer.inputs['RM Base']=None
    content=build_workbook(run);book=load_workbook(BytesIO(content),data_only=False)
    assert 'PCBA_OP' in book
    sheet=book['PCBA_OP'];headers={cell.value:cell.column for cell in sheet[1]}
    assert sheet.max_row==3  # two actual components, not zero rows or two copies
    assert [sheet.cell(r,headers['Material Code']).value for r in [2,3]]==['a','b']
    assert all(sheet.cell(r,headers['RM Base']).value=='selected first BOM period' for r in [2,3])
    assert all(sheet.cell(r,headers['End Item']).value=='High Mounted Stop Lamp' for r in [2,3])
    assert run.rows[-1].inputs['End Item']=='HMSL'  # separate final ownership
    assert 'PCBA' in book and 'Buy Non Std' in book


def test_pcba_component_output_deduplicates_producer_and_owner_projection():
    from app.calculations.output import build_workbook
    run=execute(workbook(),zero_norm_master(),development=True)
    run.rows[0].values.update(run.producer_rows[0].values)
    book=load_workbook(BytesIO(build_workbook(run)),data_only=True)
    assert book['PCBA_OP'].max_row==3


def test_pcba_component_body_formulas_match_original_expressions_and_cached_costs():
    from app.calculations.output import build_workbook
    run=execute(workbook(),zero_norm_master({'icc':'0','rej':'0','handling':'0','profit':'0','markup':'0'}),development=True)
    content=build_workbook(run);formulas=load_workbook(BytesIO(content),data_only=False)['PCBA_OP']
    cached=load_workbook(BytesIO(content),data_only=True)['PCBA_OP']
    cells={cell.value:cell.column_letter for cell in formulas[1]}
    assert formulas[cells['Landed Cost/Pc']+'2'].value=='='+cells['FOB']+'2*'+cells['EXCHANGE']+'2*'+cells['Landing Factor']+'2'
    assert formulas[cells['Process Cost']+'2'].value=='='+cells['Solder Points']+'2*'+cells['SOLDERING']+'2'
    assert formulas[cells['Basic Cost/Pc']+'2'].value=='='+cells['Landed Cost/Pc']+'2+'+cells['Process Cost']+'2'
    assert formulas[cells['MARKUP total']+'2'].value=='=SUM('+cells['ICC COST']+'2:'+cells['PROFIT COST']+'2)'
    assert formulas[cells['Grand Total Cost']+'2'].value=='=SUM('+cells['Total Cost']+'2:'+cells['PnF']+'2)'
    assert cached[cells['Landed Cost/Pc']+'2'].value==12
    assert cached[cells['Process Cost']+'2'].value==6
    assert cached[cells['Grand Total Cost']+'2'].value==18
    assert cached[cells['PACK A Cost/Veh']+'2'].value==36
    assert formulas[cells['Material Code']+'2'].data_type=='s'
