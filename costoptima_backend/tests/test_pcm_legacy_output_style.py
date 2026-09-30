from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from app.services.pcm_legacy_output_style import apply_legacy_output_template_styles


def test_apply_legacy_output_template_styles_preserves_values_and_copies_layout(tmp_path):
    template_path = tmp_path / "template.xlsx"
    output_path = tmp_path / "output.xlsx"

    template = Workbook()
    ws = template.active
    ws.title = "SBC"
    ws.sheet_properties.tabColor = "FF0000"
    ws.freeze_panes = "A2"
    ws.column_dimensions["A"].width = 31
    ws.row_dimensions[1].height = 24
    ws["A1"] = "Legacy Header"
    ws["A1"].fill = PatternFill("solid", fgColor="1F4E78")
    ws["A1"].font = Font(color="FFFFFF", bold=True)
    ws["A2"].fill = PatternFill("solid", fgColor="D9EAF7")
    ws["A2"].number_format = "0.000"
    master = template.create_sheet("Master_Output")
    master["A1"] = "Master"
    master["A1"].fill = PatternFill("solid", fgColor="70AD47")
    template.save(template_path)
    template.close()

    output = Workbook()
    ws_out = output.active
    ws_out.title = "Master_Output"
    ws_out["A1"] = "DB master value"
    ws_out = output.create_sheet("SBC")
    ws_out["A1"] = "DB header value"
    ws_out["A2"] = 123.4567
    output.create_sheet("Errors")["A1"] = "diagnostic"
    output.save(output_path)
    output.close()

    used_template = apply_legacy_output_template_styles(output_path, template_path)
    assert used_template == str(template_path.resolve())

    styled = load_workbook(output_path)
    try:
        assert styled.sheetnames == ["SBC", "Master_Output", "Errors"]
        sbc = styled["SBC"]
        assert sbc["A1"].value == "DB header value"
        assert sbc["A2"].value == 123.4567
        assert sbc["A1"].fill.fgColor.rgb == "001F4E78"
        assert sbc["A1"].font.bold is True
        assert sbc["A1"].font.color.rgb == "00FFFFFF"
        assert sbc["A2"].fill.fgColor.rgb == "00D9EAF7"
        assert sbc["A2"].number_format == "0.000"
        assert sbc.column_dimensions["A"].width == 31
        assert sbc.row_dimensions[1].height == 24
        assert sbc.freeze_panes == "A2"
        assert styled["Master_Output"]["A1"].value == "DB master value"
        assert styled["Master_Output"]["A1"].fill.fgColor.rgb == "0070AD47"
        assert styled["Errors"]["A1"].value == "diagnostic"
    finally:
        styled.close()


def test_pcm_sheet_metal_formulas_are_loaded_from_layout_repository():
    from openpyxl import Workbook
    from app.services.pcm_sheet_metal import _apply_formula_columns, _write_variant_sum_footer

    wb = Workbook()
    ws = wb.active
    ws.title = "Make"
    ws.append(["Material Code", "Material Family", "AX1"])
    ws.append(["MAT-1", None, 1])
    layout = {
        "formula_repository": {
            "custom_family_formula": "=CONCAT(\"DB-\",A{row})",
            "custom_variant_footer": "=MAX(${variant_col}2:${variant_col}${data_last})",
        },
        "formula_columns": {
            "Make": [{"column": "Material Family", "formula_key": "custom_family_formula"}]
        },
    }

    _apply_formula_columns(ws, layout, "Make")
    assert ws["B2"].value == '=CONCAT("DB-",A2)'

    _write_variant_sum_footer(
        ws,
        layout,
        {"formula_key": "custom_variant_footer"},
        minimum=5,
        variant_start_col=3,
    )
    assert ws.cell(5, 3).value == "=MAX(C2:C2)"
