"""Input normalization checks: no database access and no legacy script execution."""
from datetime import datetime
from io import BytesIO
from pathlib import Path
import unittest

from openpyxl import Workbook

from app.calculations.input import InputValidationError, parse_workbook


HEADERS = ["PF Code", "Project", "0 / 1 / 2 / 3", "Base", "High",
           "Supplier", "End Item", "Customer No", "Type", "Qty/Veh", "Commodity"]


def workbook_bytes(rows, detail_rows=None, *, detail_headers=None, preamble=0, detail_name="Hard"):
    book = Workbook()
    bom = book.active
    bom.title = "BOM"
    for i in range(preamble):
        bom.append([f"Application instructions {i}"])
    bom.append(HEADERS)
    for row in rows:
        bom.append(row)
    if detail_rows is not None:
        detail = book.create_sheet(detail_name)
        detail.append(["Grouped presentation headings"])
        detail.append(detail_headers or HEADERS)
        for row in detail_rows:
            detail.append(row)
    output = BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def row(customer="00012", type_="MAKE", commodity="HARD", supplier="Supplier A", end_item="Assembly A"):
    return ["001", "P1", 1, 1, 0, supplier, end_item, customer, type_, 2, commodity]


class CalculationInputTests(unittest.TestCase):
    def test_header_detection_uses_named_fields_after_instructions(self):
        parsed = parse_workbook(workbook_bytes([row()], preamble=6))
        self.assertEqual(parsed.metadata["sheets"][0]["header_row"], 7)
        self.assertEqual(parsed.records[0]["Customer No"], "00012")
        self.assertEqual(parsed.records[0]["_variants"], {"Base": 1, "High": 0})

    def test_detail_join_uses_business_identity_not_row_order(self):
        headers = HEADERS + ["Part Wt"]
        parsed = parse_workbook(workbook_bytes(
            [row("001"), row("002")],
            [row("002") + [4.2], row("001") + [1.3]], detail_headers=headers))
        self.assertEqual([record["Part Wt"] for record in parsed.records], [1.3, 4.2])
        self.assertEqual(parsed.records[0]["_detail_row"], 4)

    def test_same_customer_in_different_end_items_is_not_cross_joined(self):
        headers = HEADERS + ["Part Wt"]
        parsed = parse_workbook(workbook_bytes(
            [row(end_item="A"), row(end_item="B")],
            [row(end_item="B") + [4], row(end_item="A") + [7]], detail_headers=headers))
        self.assertEqual([record["Part Wt"] for record in parsed.records], [7, 4])

    def test_blank_commodity_and_type_are_preserved(self):
        parsed = parse_workbook(workbook_bytes([row(type_="", commodity="")]))
        self.assertEqual(parsed.records[0]["Commodity"], "")
        self.assertEqual(parsed.records[0]["Type"], "")

    def test_identifiers_dates_and_quantities_keep_distinct_types(self):
        data = workbook_bytes([row(customer=12)], detail_rows=None)
        from openpyxl import load_workbook
        book = load_workbook(BytesIO(data))
        book["BOM"]["H2"].number_format = "000000"
        book["BOM"]["L1"] = "Date"
        book["BOM"]["L2"] = datetime(2026, 10, 27)
        output = BytesIO()
        book.save(output)
        book.close()
        parsed = parse_workbook(output.getvalue())
        self.assertEqual(parsed.records[0]["Customer No"], "000012")
        self.assertEqual(parsed.records[0]["Qty/Veh"], 2)
        self.assertIsInstance(parsed.records[0]["Date"], datetime)

    def test_bom_quantity_owns_output_despite_detail_conflict(self):
        detail = row()
        detail[9] = 9
        parsed = parse_workbook(workbook_bytes([row()], [detail]))
        self.assertEqual(parsed.records[0]["Qty/Veh"], 2)
        self.assertEqual(parsed.records[0]["_details"][0]["Qty/Veh"], 9)
        self.assertIn("bom_detail_disagreement", {issue["code"] for issue in parsed.issues})

    def test_original_soft_detail_type_and_bom_ownership_are_separate(self):
        parsed = parse_workbook(workbook_bytes(
            [row(commodity="SOFT", type_="MAKE")],
            [row(commodity="SOFT", type_="ASSY")], detail_name="Soft"))
        self.assertEqual(parsed.records[0]["Type"], "ASSY")
        self.assertEqual(parsed.records[0]["_ownership_type"], "MAKE")
        self.assertEqual(parsed.records[0]["_bom_values"]["Type"], "MAKE")
        self.assertIn("detail_type_differs_from_bom", {issue["code"] for issue in parsed.issues})

    def test_ambiguous_details_are_retained_and_rejected(self):
        parsed = parse_workbook(workbook_bytes([row()], [row(), row()]))
        self.assertEqual(len(parsed.records[0]["_details"]), 2)
        self.assertFalse(parsed.ok)
        self.assertIn("ambiguous_detail_match", {issue["code"] for issue in parsed.issues})

    def test_duplicate_bom_keys_are_not_deduplicated_silently(self):
        parsed = parse_workbook(workbook_bytes([row(), row()]))
        self.assertEqual(len(parsed.records), 2)
        self.assertFalse(parsed.ok)

    def test_soft_process_lines_keep_actual_header_names_and_input_quantities(self):
        headers = HEADERS + ["Actual Gross Consumption", "Roller diecut press", "PU Foaming"]
        parsed = parse_workbook(workbook_bytes(
            [row(commodity="SOFT")],
            [row(commodity="SOFT") + [1.2, 3.5, None]],
            detail_headers=headers, detail_name="Soft"))
        lines = parsed.records[0]["_process_lines"]
        self.assertEqual([(item["name"], item["quantity"]) for item in lines],
                         [("Roller diecut press", 3.5)])
        self.assertNotIn("rate", lines[0])

    def test_uncached_input_formula_is_flagged_never_executed(self):
        headers = HEADERS + ["Part Wt"]
        parsed = parse_workbook(workbook_bytes([row()], [row() + ["=100/2"]], detail_headers=headers))
        self.assertIn("uncached_input_formula", {issue["code"] for issue in parsed.issues})
        self.assertFalse(parsed.ok)

    def test_invalid_workbook_is_rejected(self):
        with self.assertRaises(InputValidationError):
            parse_workbook(b"not a workbook")

    def test_no_bom_external_workflow_is_explicitly_blocked(self):
        book = Workbook()
        book.active.title = "Hard"
        book.active.append(HEADERS)
        book.active.append(row())
        output = BytesIO()
        book.save(output)
        book.close()
        with self.assertRaisesRegex(InputValidationError, "no-BOM"):
            parse_workbook(output.getvalue())

    def test_duplicate_headers_are_visible_not_overwritten(self):
        parsed = parse_workbook(workbook_bytes(
            [row()], [row() + [1, 2]], detail_headers=HEADERS + ["Rate", "Rate"]))
        self.assertEqual(parsed.records[0]["_details"][0]["Rate"], 1)
        self.assertEqual(parsed.records[0]["_details"][0]["Rate__duplicate_2"], 2)
        self.assertFalse(parsed.ok)

    def test_all_provided_workbooks_parse_without_source_writes(self):
        root = Path(r"C:\Users\Admin\Downloads\CostOptima_PCM_updated (1)\CostOptima\InputFiles")
        files = sorted(root.rglob("*.xlsx"))
        if not files:
            self.skipTest("Supplied InputFiles are not available in this checkout")
        from hashlib import sha256
        for path in files:
            before = sha256(path.read_bytes()).hexdigest()
            # The supplied reference folder grows and can include prior output
            # workbooks without a BOM. Partial mode preserves their readable
            # evidence without treating cached output costs as live inputs.
            parsed = parse_workbook(path, partial_output=True)
            self.assertTrue(parsed.records or parsed.metadata.get("input_sheet_copies"), path.name)
            self.assertEqual(len(parsed.records), parsed.metadata["record_count"])
            if parsed.metadata.get("unsupported_bom_structure"):
                self.assertTrue(all(record.get("_dependency_blocked") for record in parsed.records))
            self.assertEqual(before, sha256(path.read_bytes()).hexdigest())
        self.assertGreater(len(files), 0)


if __name__ == "__main__":
    unittest.main()



def _sparse_tail_input(row,cell_xml):
    from io import BytesIO
    from zipfile import ZipFile,ZIP_DEFLATED
    from openpyxl import Workbook
    import re
    book=Workbook();sheet=book.active;sheet.title='BOM'
    sheet.append(['Supplier','End Item','Customer No','Commodity','Type','Qty/Veh'])
    sheet.append(['S','E','001','','PKG',1])
    original=BytesIO();book.save(original);data=BytesIO()
    with ZipFile(original) as source,ZipFile(data,'w',ZIP_DEFLATED) as target:
        for name in source.namelist():
            content=source.read(name)
            if name=='xl/worksheets/sheet1.xml':
                xml=content.decode();xml=re.sub(r'<dimension ref="[^"]+"\s*/>',f'<dimension ref="A1:F{row}"/>',xml)
                xml=xml.replace('</sheetData>',f'<row r="{row}">{cell_xml}</row></sheetData>')
                content=xml.encode()
            target.writestr(name,content)
    return data.getvalue()


def test_trailing_formatted_empty_cells_do_not_create_input_records():
    from app.calculations.input import parse_workbook
    parsed=parse_workbook(_sparse_tail_input(1048576,'<c r="F1048576" s="0" t="n"></c>'))
    assert len(parsed.records)==1 and parsed.records[0]['Customer No']=='001'
    assert any(issue['code']=='trimmed_empty_formatting' for issue in parsed.issues)


def test_real_out_of_bounds_value_is_not_discarded_as_formatting():
    from app.calculations.input import parse_workbook,InputValidationError
    import pytest
    with pytest.raises(InputValidationError,match='contains data beyond'):
        parse_workbook(_sparse_tail_input(100001,'<c r="A100001" t="n"><v>12</v></c>'))
