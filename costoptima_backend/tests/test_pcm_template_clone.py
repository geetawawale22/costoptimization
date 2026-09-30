from pathlib import Path

from openpyxl import Workbook, load_workbook

from app.services.pcm_template_clone import render_legacy_workbook_from_template


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT.parent / "2302C S302 FENDER OUTER TRIMS BC 10 Sep 2026 09.52.04 AM_50002264.xlsx"


def test_legacy_template_renderer_updates_table_refs_and_clears_empty_sheets(tmp_path):
    path = tmp_path / "source.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "SBC"
    ws.append(["End Item", "Customer No", "Desc", "MX 1", "MX 2", "MX 3", "MX 5"])
    for idx in range(3):
        ws.append([f"EI {idx}", f"CUST{idx}", f"Desc {idx}", 1, 2, 3, 4])

    ws = wb.create_sheet("Make")
    ws.append(["Supplier", "End Item", "Supplier No", "Customer No", "Desc", "Total"])
    ws.append(["SUP", "EI", "SN", "CUST", "DESC", 123.45])
    wb.save(path)
    wb.close()

    applied = render_legacy_workbook_from_template(
        path,
        str(TEMPLATE),
        {"output": {"variants": ["MX 1", "MX 2", "MX 3", "MX 5"]}},
    )

    assert applied is True
    wb = load_workbook(path, data_only=False)
    assert wb.sheetnames == [
        "SBC",
        "End Item",
        "Master_Output",
        "Analysis",
        "End Item Analysis",
        "Make",
        "Buy Std",
        "Assy",
        "Pkg",
        "Frt",
        "Paint",
        "Special Process",
    ]
    assert wb["SBC"].tables["SBC"].ref == "A2:G5"
    assert wb["SBC"].tables["File"].ref == "A12:D30"
    assert wb["SBC"]["D8"].value == "=SUM(D3:D5)"
    assert wb["Make"]["AQ2"].value == 123.45
    assert wb["Buy Std"]["A2"].value is None
    wb.close()


def test_legacy_template_renderer_uses_default_template_when_definition_is_old(tmp_path):
    path = tmp_path / "source.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "SBC"
    ws.append(["End Item", "Customer No", "Desc", "MX 1", "MX 2"])
    ws.append(["BSO COVER ASSY LH", "2302BS3A0630N_END", "BSO COVER ASSY LH", 55.25, 55.25])
    ws.append(["BSO COVER ASSY RH", "2302BS3A0620N_END", "BSO COVER ASSY LH", 55.25, 55.25])
    wb.create_sheet("Errors").append(["diagnostic"])
    wb.save(path)
    wb.close()


def test_legacy_template_renderer_preserves_extra_calculated_commodity_sheets(tmp_path):
    path = tmp_path / "source.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "SBC"
    ws.append(["End Item", "Customer No", "Desc", "MX 1"])
    ws.append(["CARPET FRONT", "2303DU300701N", "CARPET ASSY AT", 100])
    extra = wb.create_sheet("Buy Non Std")
    extra.append(["Customer No", "Type", "Total"])
    extra.append(["2303DU100461N", "BUY NON STD", 123.45])
    wb.create_sheet("Errors").append(["diagnostic"])
    wb.save(path)
    wb.close()

    applied = render_legacy_workbook_from_template(
        path,
        str(TEMPLATE),
        {"output": {"variants": ["MX 1"]}},
    )

    assert applied is True
    wb = load_workbook(path, data_only=False)
    assert "Buy Non Std" in wb.sheetnames
    assert "Errors" not in wb.sheetnames
    assert wb["Buy Non Std"]["A2"].value == "2303DU100461N"
    assert wb["SBC"].tables["SBC"].ref == "A2:D3"
    wb.close()

    applied = render_legacy_workbook_from_template(
        path,
        None,
        {"output": {"variants": ["MX 1", "MX 2"]}},
    )

    assert applied is True
    wb = load_workbook(path, data_only=False)
    assert wb.sheetnames == [
        "SBC",
        "End Item",
        "Master_Output",
        "Analysis",
        "End Item Analysis",
        "Make",
        "Buy Std",
        "Assy",
        "Pkg",
        "Frt",
        "Paint",
        "Special Process",
    ]
    ws = wb["SBC"]
    assert "SBC" in ws.tables
    assert "File" in ws.tables
    assert ws.tables["SBC"].ref == "A2:E4"
    assert ws.tables["File"].ref == "A11:D29"
    assert ws["D7"].value == "=SUM(D3:D4)"
    assert ws["A11"].value is not None
    wb.close()
