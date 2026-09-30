import copy
import json
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET
import zipfile

import pytest
from openpyxl import load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models.workbook import WorkbookType, WorkbookTab, WorkbookRow
from app.services.pcm_master_views import materialize_views
from app.services.pcm_runtime import Interpreter, read_inputs, write_snapshot_output
from app.services.pcm_master_normalize import normalize_master_workbook, NOT_YET_CONVERTED
from tools.verify_pcm_reference import reference_run, ROOT


@pytest.fixture(scope="module")
def reference(tmp_path_factory):
    return reference_run(tmp_path_factory.mktemp("pcm_reference"))


def test_supplied_workbooks_match_numeric_checks(reference):
    report, *_ = reference
    assert report["rows"] == 14
    assert report["comparisons"] >= 212
    assert report["errors"] == []
    assert report["mismatches"] == []
    assert report["max_absolute_difference"] < 1e-8


def test_blank_bom_variants_are_not_filled_from_hard(reference):
    _, definition, _, rows, _ = reference
    paint = next(row for row in rows if row["Type"] == "SPECIAL PROCESS")
    assert paint["MX 1"] is None
    assert paint["AX 5"] == 1
    assert len(definition["output"]["variants"]) == 9


def test_modified_input_file_changes_calculation_and_export(reference, tmp_path):
    _, definition, masters, rows, original = reference
    source = ROOT.parent / "S302 BSO COVER_Sprint Input_Rev 2_09.09.2026 (1).xlsx"
    changed = tmp_path / "different_input.xlsx"
    # Edit the input cell in OOXML while retaining unrelated formula caches.
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(changed, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "xl/worksheets/sheet2.xml":
                tree = ET.fromstring(data)
                ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                cell = tree.find('.//s:c[@r="AL3"]/s:v', ns)  # Hard: Part Wt, first MAKE row.
                assert float(cell.text) == 0.15
                cell.text = "0.30"
                data = ET.tostring(tree)
            dst.writestr(item, data)
    configured = copy.deepcopy(definition)
    results = Interpreter(configured, masters).run(read_inputs(changed, configured))
    assert all(r["status"] == "Completed" for r in results)
    make = next(r for r in results if r.get("profile") == "hard")
    before = next(r for r in original if r.get("profile") == "hard")
    assert make["values"]["RM Cost/Pc"] == pytest.approx(before["values"]["RM Cost/Pc"] * 2)
    assert make["values"]["Total"] > before["values"]["Total"]
    assert results[8]["values"] == original[8]["values"]  # RH part unchanged.
    path = tmp_path / "different_output.xlsx"
    write_snapshot_output(SimpleNamespace(definition=configured, results=results, input_sha256="changed", definition_sha256="test"), path)
    wb = load_workbook(path, data_only=True)
    rows = list(wb["Make"].values)
    assert dict(zip(rows[0], rows[1]))["Total"] == pytest.approx(make["values"]["Total"])
    wb.close()


def test_missing_unavailable_and_unimplemented_have_distinct_errors(reference):
    _, definition, masters, rows, _ = reference
    make = copy.deepcopy(next(r for r in rows if r["Type"] == "MAKE"))
    missing = {**make, "Material Code": "DOES_NOT_EXIST"}
    missing_result = Interpreter(definition, masters).run([missing])[0]
    assert missing_result["issues"][0]["code"] == "MISSING_MASTER"
    unavailable = Interpreter(definition, []).run([make])[0]
    assert unavailable["issues"][0]["code"] == "MASTER_NOT_AVAILABLE"
    unsupported = Interpreter(definition, masters).run([{**make, "Commodity": "UNMAPPED COMMODITY"}])[0]
    assert unsupported["issues"][0]["code"] == "NOT_IMPLEMENTED"
    assert all("Total" not in r["values"] for r in [missing_result, unavailable, unsupported])


def test_paint_input_changes_cost_without_reference_output(reference):
    _, definition, masters, rows, original = reference
    changed = copy.deepcopy(next(r for r in rows if r["Type"] == "SPECIAL PROCESS"))
    changed["_related"]["Paint"][0]["Consumption (in Litre)"] = 0.038
    result = Interpreter(definition, masters).calculate(changed)
    before = next(r for r in original if r.get("profile") == "special_process")
    assert result["values"]["Paint RM Cost"] > before["values"]["Paint RM Cost"]
    assert result["values"]["Total"] > before["values"]["Total"]


def test_all_consumption_master_formulas_are_normalized():
    rules = normalize_master_workbook(ROOT.parent / "Master_Rate_Data.xlsx")
    assert {name: len(rows) for name, rows in rules.items()} == {
        "Net Consumption base": 12,
        "Blank Consumption base": 40,
        "Gross Consumption base": 29,
    }
    assert NOT_YET_CONVERTED == []


def test_export_contains_database_backed_sbc_and_paint_detail(reference):
    path = ROOT / "docs/pcm/verification/pcm_reference_calculated.xlsx"
    wb = load_workbook(path, data_only=True)
    assert "SBC" in wb.sheetnames and "Paint" in wb.sheetnames
    sbc = list(wb["SBC"].values)
    assert len(sbc) == 3  # header and two end items
    assert sbc[1][0] == "BSO COVER ASSY LH"
    paint = list(wb["Paint"].values)
    header = list(paint[0])
    first = dict(zip(header, paint[1]))
    assert first["Rate / Litre"] == pytest.approx(1032.3)
    assert first["RM cost / Pc"] == pytest.approx(19.6137)
    assert first["Paint RM Cost"] == pytest.approx(50.895914)
    wb.close()


@pytest.mark.parametrize("mode,count", [("0", 3), ("1", 3), ("0,1", 3), ("Single", 2)])
def test_virtual_merge_concatenation_first_source_and_duplicates(mode, count):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        for code, values in [("source_a", [1, 1]), ("source_b", [2])]:
            book = WorkbookType(code=code, name=code)
            tab = WorkbookTab(workbook_type=book, tab_name="Rates")
            tab.rows.extend([WorkbookRow(row_data={"Key": value, "Rate/UOM_AD": 10}) for value in values])
            db.add(book)
        db.commit()
        spec = {"table": "Combined", "header_mode": mode, "unique": ["Key"], "position": 8,
                "sources": [{"workbook": "source_a", "table": "Rates"}, {"workbook": "source_b", "table": "Rates"}]}
        result = materialize_views(db, [spec])
        assert len(result) == count
        assert result[1]["duplicate_in_source"] == (mode != "Single")
        assert result[0]["position"] == 8
        assert result[0]["data"]["Rate/UOM_AD"] == 10
    engine.dispose()
