"""Source-backed consumption/input regressions; no database or source writes."""
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

from app.calculations.common import CalculationError, evaluate_expression
from app.calculations.consumption import select_rule
from app.calculations.input import parse_workbook
from app.calculations.masters import (
    FieldRef, MasterField, MasterMappingError, MasterRecord, resolve_field,
)


def test_verified_con_condition_labels_are_not_treated_as_excel_coordinates():
    fields = (MasterField("col_a", "Con1", None, "TEXT", True),
              MasterField("col_b", "Con23", None, "TEXT", True))
    assert resolve_field(fields, "Con1").column == "col_a"
    assert resolve_field(fields, "Con23").column == "col_b"
    with pytest.raises(MasterMappingError):
        resolve_field(fields, "A1")
    with pytest.raises(MasterMappingError):
        resolve_field(fields, "col_a")
    with pytest.raises(MasterMappingError):
        resolve_field(fields, "Con22")


def test_consumption_uses_actual_con_condition_instead_of_first_formula():
    # Original part1:1848-2110 narrows ConN first; create_indexformula2 then
    # expands the selected named expression. The zero row must not win here.
    fields = (MasterField("condition", "Con1", None, "TEXT"),
              MasterField("formula", "Blank Length", None, "TEXT"))
    records = (
        MasterRecord("blank_consumption_base", "source", "ROOT", 3,
                     {"condition": "Blank calculation = Size", "formula": "0"}, fields),
        MasterRecord("blank_consumption_base", "source", "ROOT", 4,
                     {"condition": "Blank calculation = %",
                      "formula": "{L / Unfold}*{Blank Length Allowance}"}, fields),
    )
    context = SimpleNamespace(master=SimpleNamespace(records=lambda code: records), provenance=[])
    values = {"Blank Calculation": "%", "L / Unfold": 837,
              "Blank Length Allowance": Decimal("1.0")}
    expression = select_rule(context, "blank_consumption_base", "Blank Length",
                             values, ["Con1"])
    assert evaluate_expression(expression, values) == Decimal("837.0")
    assert context.provenance[0]["source_row_id"] == 4


def _input(*, include_pipe=True, display_labels=("Base", "High"), keys=("Pack 1", "Pack 2")):
    book = Workbook()
    bom = book.active
    bom.title = "BOM"
    headers = ["0 / 1 / 2 / 3", *keys, "Supplier", "End Item", "Customer No", "Type", "Qty/Veh", "Commodity"]
    row = [1, 1, 1, "Supplier", "Assembly", "00012", "MAKE", 1, "SOFT"]
    bom.append(headers)
    bom.append(row)
    detail = book.create_sheet("Soft")
    detail.append(headers + (["Pipe Dia"] if include_pipe else []))
    detail.append(row + ([None] if include_pipe else []))
    sbc = book.create_sheet("SBC")
    sbc.append([None, None, None, *display_labels])
    sbc.append(["End Item", "Customer No", "Desc", *keys])
    output = BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def test_detected_empty_detail_field_remains_known_blank_not_unknown_operand():
    # PCM part1:1650 reads the existing Pipe Dia input cell; an empty cell is
    # a known Excel blank. It is not permission to zero-fill unknown headers.
    parsed = parse_workbook(_input())
    record = parsed.records[0]
    assert "Pipe Dia" in record and record["Pipe Dia"] is None
    assert evaluate_expression("{Pipe Dia}+1", record, excel_blank_zero=True) == 1
    unknown = parse_workbook(_input(include_pipe=False)).records[0]
    with pytest.raises(CalculationError) as error:
        evaluate_expression("{Pipe Dia}+1", unknown, excel_blank_zero=True)
    assert error.value.code == "unknown_operand"


def test_sbc_keeps_bom_variant_keys_separate_from_presentation_labels():
    layout = parse_workbook(_input()).metadata["sbc_layout"]
    assert layout["identity_headers"] == ["End Item", "Customer No", "Desc"]
    assert layout["variants"] == [{"key": "Pack 1", "label": "Base"},
                                  {"key": "Pack 2", "label": "High"}]
    assert layout["header_row"] == 2


def test_unmapped_condition_metadata_cannot_silently_choose_first_formula():
    fields=(MasterField("formula", "Blank Length", None, "TEXT"),)
    record=MasterRecord("blank_consumption_base", "source", "ROOT", 3,
                        {"formula":"0"}, fields)
    context=SimpleNamespace(master=SimpleNamespace(records=lambda code:[record]),provenance=[])
    with pytest.raises(CalculationError) as error:
        select_rule(context,"blank_consumption_base","Blank Length",{},["Con1"])
    assert error.value.code=="master_condition_mapping_missing"


def _pcba_input(*, wrong_owner=False):
    book=Workbook()
    bom=book.active; bom.title="BOM"
    bom.append(["0 / 1 / 2 / 3","Pack A","Supplier","End Item","Customer No","Type","Qty/Veh","Commodity"])
    bom.append([1,1,"Board Supplier","Assembly","00012","ASSY",2,"PCBA"])
    component=book.create_sheet("PCBA")
    component.append(["Supplier","End Item","PCBA Part Number","Description","Material Code","RM Supplier","Pack A","Pack B"])
    component.append(["Other Supplier" if wrong_owner else "Board Supplier","Assembly","00012","Component 1","00100","RM vendor",2,0])
    component.append(["Board Supplier","Assembly","00012","Component 2","00101","RM vendor",3,4])
    output=BytesIO(); book.save(output); book.close()
    return output.getvalue()


def test_pcba_named_board_identifier_attaches_multiple_components_without_ambiguity():
    # Original pcba_op:13946-13995 uses PCBA Part Number -> BOM Customer No;
    # component quantity headers come after RM Supplier:14004-14012.
    parsed=parse_workbook(_pcba_input())
    assert parsed.ok, parsed.issues
    board=parsed.records[0]
    assert len(parsed.records)==1
    assert board["Customer No"]=="00012"
    assert board["_pcba_quantity_headers"]==["Pack A","Pack B"]
    assert [c["Material Code"] for c in board["_pcba_components"]]==["00100","00101"]
    assert [c["Pack A"] for c in board["_pcba_components"]]==[2,3]
    assert not any(i["code"]=="ambiguous_detail_match" for i in parsed.issues)
    assert parsed.metadata["detail_record_count"]==2


def test_pcba_components_cannot_cross_supplier_ownership():
    parsed=parse_workbook(_pcba_input(wrong_owner=True))
    assert not parsed.ok
    assert len(parsed.records[0]["_pcba_components"])==1
    assert any(i["code"]=="pcba_component_owner_conflict" and i["sheet"]=="BOM"
               for i in parsed.issues)


def test_consumption_material_dimensions_use_explicit_rule_binding_not_output_shadow():
    # Original create_indexformula2:1425-1445 selects Material Master L while
    # Output L is the generated sheet dimension. Equal names are not aliases.
    fields=(MasterField("condition","Con1","L","TEXT"),
            MasterField("binding","Material Master","L","TEXT"),
            MasterField("formula","L","L","TEXT"))
    rule=MasterRecord("gross_consumption_base","source","ROOT",5,
                      {"condition":"Gross cal Method =Nesting", "binding":"{L}","formula":"{L}"},fields)
    material=SimpleNamespace(value=lambda name:Decimal("2000") if name=="L" else None)
    master=SimpleNamespace(records=lambda code:[rule], record=lambda code,keys:material)
    context=SimpleNamespace(master=master,provenance=[])
    values={"Material Code":"00100","Gross cal Method":"Nesting","L":Decimal("100")}
    bindings={}
    expression=select_rule(context,"gross_consumption_base","L",values,["Con1"],bindings=bindings)
    assert evaluate_expression(expression,{**values,**bindings})==Decimal("2000")
    assert values["L"]==Decimal("100")
    assert bindings=={"L":Decimal("2000")}


class _RawCastingResolver:
    """Exercise actual semantic-grid adapters without any database."""
    def __new__(cls, rows, headers):
        from app.calculations.masters import SemanticMasterResolver
        resolver=SemanticMasterResolver(None)
        resolver._read=lambda source:rows
        source={"data_table_name":"verified_source","folder_path":"Casting",
                "fields":tuple(MasterField(key,None,None,"TEXT") for key in headers)}
        return resolver, source


def test_casting_recipe_uses_verified_supplier_and_grade_headers():
    rows=[{"row_id":1,"ingredient":None,"material":None,"recipe1":"Supplier A","recipe2":"Supplier B"},
          {"row_id":2,"ingredient":"Addition in kg","material":"Material code","recipe1":"FG 220","recipe2":"FG 220"},
          {"row_id":3,"ingredient":"Pig Iron","material":"0012","recipe1":"175","recipe2":"200"}]
    resolver,source=_RawCastingResolver(rows,["ingredient","material","recipe1","recipe2"])
    record=resolver._raw_records("casting_recipie_master",source)[0]
    assert record.value("Addition in kg")=="Pig Iron"
    assert record.value("Material code")=="0012"
    assert record.decimal(FieldRef("FG 220","Supplier A"))==175
    assert record.decimal(FieldRef("FG 220","Supplier B"))==200
    with pytest.raises(MasterMappingError):record.value("FG 220")


def test_complexity_rejection_group_inherits_category_without_other_grid_collision():
    headers=["category","type","ra","rb","rc","separator","other_grid"]
    rows=[{"row_id":1,"category":"Rejection Category","type":"Type","ra":"Rejection %"},
          {"row_id":2,"type":"Complexity Factor","ra":"A","rb":"B","rc":"C","other_grid":"C"},
          {"row_id":3,"category":"Casting ( Pre Machining )","type":"ARPA","ra":"0.03","rb":"0.05","rc":"0.08"},
          {"row_id":4,"type":"HPML","ra":"0.03","rb":"0.03","rc":"0.04"},
          {"row_id":5,"category":"Casting ( Post Machining )","type":"ARPA","ra":"0.02","rb":"0.03","rc":"0.04"},
          {"row_id":6,"type":"HPML","ra":"0.015","rb":"0.02","rc":"0.03"},
          {"row_id":7,"category":"Core","type":"Number","ra":"Index"}]
    resolver,source=_RawCastingResolver(rows,headers)
    records=resolver._raw_records("casting_complexity_master",source)
    assert len(records)==4
    assert records[1].value("Rejection Category")=="Casting ( Pre Machining )"
    assert records[1].decimal(FieldRef("C","Rejection %"))==Decimal("0.04")
    assert records[3].value("Rejection Category")=="Casting ( Post Machining )"
    assert records[3].decimal(FieldRef("A","Rejection %"))==Decimal("0.015")
    assert rows[3].get("category") is None


def test_original_sand_input_worksheet_attaches_named_process_lines():
    # Primary sand_casting:7144 and 7207-7216 identify Sand_Input and the
    # named Process boundary; this is an input-sheet alias, not a new branch.
    book=Workbook();bom=book.active;bom.title="BOM"
    headers=["Supplier","End Item","Customer No","Type","Qty/Veh","Commodity"]
    values=["Foundry","Assembly","00012","MAKE",1,"SAND CASTING"]
    bom.append(headers);bom.append(values)
    sheet=book.create_sheet("Sand_Input")
    sheet.append(headers+["Process","Shot blasting"])
    sheet.append(values+[None,3])
    stream=BytesIO();book.save(stream);book.close()
    parsed=parse_workbook(stream.getvalue())
    assert parsed.ok,parsed.issues
    assert parsed.records[0]["_process_lines"][0]["name"]=="Shot blasting"
    assert parsed.records[0]["_process_lines"][0]["quantity"]==3
    assert next(s for s in parsed.metadata["sheets"] if s["sheet"]=="Sand_Input")["family"]=="SAND CASTING"


class _ConsumableMaster:
    def __init__(self, *, material_code="STMF005", include_density_binding=True, missing_rate=False):
        declarations={"code":"Material Code", "base":"Base Part", "name":"Consumable",
                      "rate":"Material Master1","waste":"Material Master2", "density":"Material Master3",
                      "area":"Output1", "expression":"Consumables cost calculations"}
        fields=tuple(MasterField(column,header,None,"TEXT") for column,header in declarations.items())
        self.row=MasterRecord("consumable","consumable_source","ROOT",3,
            {"code":material_code,"base":"Display label differs from parent grade","name":"Solvent",
             "rate":"{Rate/UOM}","waste":"{% wastage}",
             "density":"{GSM/ Density}" if include_density_binding else None,
             "area":"{Total Surface Area}",
             "expression":"{Rate/UOM}*{% wastage}*{Total Surface Area}*{GSM/ Density}*10^-3"},fields)
        self.missing_rate=missing_rate
        self.material_calls=[]
    def records(self,code):
        assert code=="consumable"
        return [self.row]
    def record(self,code,keys):
        self.material_calls.append(keys)
        assert code=="material_master" and keys=={"Material Grade":"Solvent"}
        return SimpleNamespace(value=lambda field:{"% wastage":Decimal("1.05"),"GSM/ Density":Decimal("80")}[field])
    def lookup(self,code,keys,field,**kwargs):
        self.material_calls.append(keys)
        assert code=="material_master" and keys=={"Material Grade":"Solvent"}
        assert field==FieldRef("Rate/UOM","Jan#24")
        if self.missing_rate:
            from app.calculations.masters import MissingMasterError
            raise MissingMasterError("No verified consumable rate")
        return Decimal("10")


def test_consumable_formula_uses_its_material_density_and_wastage_not_parent_values():
    # Original3411 selects by code; create_indexformulacon1492-1509 resolves
    # Material Master operands using the consumable grade passed at3428.
    from app.calculations.context import CalculationContext
    master=_ConsumableMaster();context=CalculationContext(master,[])
    result=context.consumables({"Material Code":"STMF005","Material Grade":"Parent grade",
                               "RM Base":"Jan#24","GSM/ Density":100,"% wastage":Decimal("1.1"),
                               "Total Surface Area":2})
    assert result==Decimal("1.68000")
    assert context.provenance[0]["material_operands"]==["% wastage","GSM/ Density","Rate/UOM"]


def test_nonmatching_consumable_code_does_not_fallback_to_base_part_or_borrow_a_rate():
    from app.calculations.context import CalculationContext
    master=_ConsumableMaster(material_code="OTHER")
    context=CalculationContext(master,[])
    assert context.consumables({"Material Code":"STMF005","Material Grade":"Display label differs from parent grade"})==0
    assert not master.material_calls


def test_applicable_consumable_missing_rate_remains_blocked():
    from app.calculations.context import CalculationContext
    context=CalculationContext(_ConsumableMaster(missing_rate=True),[])
    with pytest.raises(CalculationError) as error:
        context.consumables({"Material Code":"STMF005","RM Base":"Jan#24","Total Surface Area":2})
    assert error.value.code=="master_lookup_blocked"


def test_consumable_missing_binding_cannot_silently_use_parent_density():
    from app.calculations.context import CalculationContext
    context=CalculationContext(_ConsumableMaster(include_density_binding=False),[])
    with pytest.raises(CalculationError) as error:
        context.consumables({"Material Code":"STMF005","RM Base":"Jan#24","Total Surface Area":2,
                             "GSM/ Density":100})
    assert error.value.code=="consumable_binding_missing"


def test_calculated_consumption_and_yields_remain_distinct_from_input_overrides(monkeypatch):
    from app.calculations import consumption
    formulas={'Cal RM cons':'4','Blank Length':'10','Blank Width':'5','Cal Blank Cons':'8','Cal Parts Per Blank':'2','L':'10','W':'5','Cal parts / Sheet':'2','Cal Gross Consumption':'10'}
    calls=[]
    def rule(context,master,output,*args,**kwargs):
        calls.append(output);return formulas[output]
    monkeypatch.setattr(consumption,'select_rule',rule)
    record={'Net RM consumption':6,'Actual Gross Consumption':20,'Pipe Dia':0,'T':1}
    values=consumption.calculate_consumption(record,SimpleNamespace(),'SOFT')
    assert values['Cal RM cons']==4 and values['Effective Net RM consumption']==6
    assert values['Cal Gross Consumption']==10 and values['Effective Gross Consumption']==20
    assert values['Cal Yield']==Decimal('.4') and values['Actual Yield']==Decimal('.3')
    assert values["B' Surface area"]==Decimal('0.0000075')
    assert calls.count('Cal RM cons')==1 and calls.count('Cal Gross Consumption')==1
