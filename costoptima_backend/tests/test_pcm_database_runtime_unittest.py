"""No external services. Run: python -m unittest discover -s tests -p '*_unittest.py'."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from openpyxl import load_workbook, Workbook

from app import models
from app.database import Base
from app.models.app_config import AppConfig
from app.models.workbook import WorkbookType, WorkbookTab, WorkbookRow
from app.models.bom import Bom
from app.models.pcm_run import PcmRunSnapshot
from app.services.pcm_runtime import Interpreter, PcmError, number, digest, snapshot_masters, load_definition, write_snapshot_output


DEFINITION = json.loads((Path(__file__).parents[1] / "data/pcm_definition.json").read_text(encoding="utf-8"))


def record(table, data, scope="forging_self", row_id=1):
    return {"workbook": scope, "table": table, "data": data, "row_id": row_id, "version": 1, "section_id": None}


def forging_masters():
    rows = [record("Material Master", {"Material Code": "M", "Rate/UOM": 120, "Scrap Rate": 30}),
            record("Norms Make", {"Supplier": "S", "ICC": "1%", "REJ": "2%", "OH": "3%", "PROFIT": "4%"})]
    for i, (name, expr) in enumerate([("ICC", "rm_cost"), ("REJ", "basic_cost"), ("OH", "process_cost"), ("PROFIT", "basic_cost")], 10):
        rows.append(record("Norms Make Applicability", {"Output": name + " Base", "Expression": expr, "Priority": 1}, row_id=i))
    return rows


def forging_row():
    return {"Type": "MAKE", "Commodity": "FORGING", "Material Code": "M", "Supplier": "S", "Customer No": "C", "End Item": "E",
            "Input Wt": 10, "Part Wt": 7, "Heat Treatment Cost": 20, "Process Cost": 60, "Qty/Veh": 2}


class RuntimeTests(unittest.TestCase):
    def test_documented_commodity_components(self):
        cases = [
            ("HARD", {"Gross Wt": 10, "m/c Tonnage": 100, "Cycle Time": 60, "Cavity": 2, "Sector & Location": "P"},
             [record("Norms Shift Factor", {"Process": "Injection", "Sector & Location": "P", "Shift Factor": 10})],
             {"RM Cost/Pc": 1200, "Pc per shift": 864, "Molding Cost": 1000 / 864}),
            ("GDC", {"Casting Wt": 10, "Core Weight per Part": 2, "Sector & Location": "P"},
             [record("Process Master", {"Process": "CORE", "Sector & Location": "P", "Rate": 10})],
             {"RM Cost/Pc": 1089.8, "Scrap Cost": 214.2}),
            ("PDC", {"Casting Wt": 10}, [], {"RM Cost/Pc": 1069.8, "Scrap Cost": 214.2}),
            ("THERMAL", {"Type": "BUY NON STD", "Sector & Location": "P"},
             [record("Process Master", {"Process": "Core Coversion Cost", "Sector & Location": "P", "Rate": 0.15})],
             {"RM Cost/Pc": 1008, "Process Cost/Pc": 151.2}),
            ("PCBA", {"Solder Points": 10, "Sector & Location": "P"},
             [record("eBom", {"Customer No": "C", "Supplier": "S", "FOB": 10, "EXCHANGE": 80, "Landing Factor": 1.2}),
              record("Process Master", {"Process": "SOLDERING", "Sector & Location": "P", "Rate": 2})],
             {"RM Cost/Pc": 960, "Process Cost/Pc": 20}),
        ]
        for commodity, extras, additional, expected in cases:
            with self.subTest(commodity=commodity):
                definition = copy.deepcopy(DEFINITION)
                for p in definition["profiles"].values():
                    p["scope"] = "forging"
                    p.pop("scope_by_commodity", None)
                masters = forging_masters() + additional
                masters[0]["data"]["Applicable Process"] = "Injection"
                masters[1]["data"]["EFFICIENCY"] = 0.9
                row = {**forging_row(), "Commodity": commodity, **extras}
                values = Interpreter(definition, masters).calculate(row)["values"]
                for label, value in expected.items():
                    self.assertAlmostEqual(values[label], value)

    def test_consign_band_uses_exclusive_upper_boundary(self):
        masters = [record("Norms Consign", {"Supplier": "S", "Min": 0, "Max": 500, "Handling": 2}, scope="pcm_master"),
                   record("Norms Consign", {"Supplier": "S", "Min": 500, "Max": None, "Handling": 5}, scope="pcm_master", row_id=2)]
        row = {"Type": "CONSIGN", "Supplier": "S", "Part Wt": 500, "Actual Cost/Pc": 10}
        self.assertEqual(Interpreter(DEFINITION, masters).calculate(row)["values"]["Total"], 15)

    def test_assy_explicit_cycle_time_overrides_points_calculation(self):
        definition = copy.deepcopy(DEFINITION)
        definition["profiles"]["assy"]["scope"] = "forging"
        masters = forging_masters()
        masters[1]["table"] = "Norms Assy"
        masters += [record("Assy", {"Category": "A", "Process": "Fix", "MHR": 120})]
        row = {**forging_row(), "Type": "ASSY", "Category": "A", "Operation Process": "Fix", "CT(sec)": 0}
        self.assertEqual(Interpreter(definition, masters).calculate(row)["values"]["Total"], 0)

    def test_existing_calc_rules_do_not_mask_real_input_columns_with_zero(self):
        from app.calc_engine import run_synchronous_calculation
        rules = [SimpleNamespace(id=1, is_deleted=False, priority=1, condition_expr=None,
                                 formula_expr="rate_per_uom * input_wt", output_field="Total")]
        line = SimpleNamespace(commodity_code="custom", row_data={"Rate/UOM": 12, "Input Wt": 5})
        result = run_synchronous_calculation([line], rules_by_commodity={"custom": rules})
        self.assertEqual(result["custom"]["lines"][0]["Total"], 60)

    def test_invalid_quantity_is_a_row_error(self):
        result = Interpreter(DEFINITION, forging_masters()).run([{**forging_row(), "Qty/Veh": "invalid"}])[0]
        self.assertEqual(result["status"], "Needs Review")

    def test_missing_process_mapping_is_not_assumed_free(self):
        row = forging_row()
        del row["Process Cost"]
        result = Interpreter(DEFINITION, forging_masters()).run([row])[0]
        self.assertIn("Missing process mapping", result["error"])

    def test_forging_percentages_are_applied_to_configured_bases(self):
        result = Interpreter(DEFINITION, forging_masters()).calculate(forging_row())
        values = result["values"]
        self.assertAlmostEqual(values["Scrap Wt"], 2.4)
        self.assertAlmostEqual(values["RM Cost/Pc"], 1148)
        self.assertAlmostEqual(values["ICC Cost"], 11.48)
        self.assertAlmostEqual(values["Total"], 1293.76)
        self.assertTrue(result["master_refs"])

    def test_database_formula_and_parameter_edits_change_results(self):
        definition = copy.deepcopy(DEFINITION)
        definition["profiles"]["forging"]["parameters"]["scrap_recovery_factor"] = 0.5
        definition["profiles"]["forging"]["steps"][-1]["expression"] = "basic_cost"
        result = Interpreter(definition, forging_masters()).calculate(forging_row())
        self.assertEqual(result["values"]["Total"], 1235)

    def test_missing_rate_never_becomes_zero_cost(self):
        result = Interpreter(DEFINITION, []).run([forging_row()])[0]
        self.assertEqual(result["status"], "Needs Review")
        self.assertNotIn("Total", result["values"])
        self.assertIn("expected one match", result["error"])

    def test_duplicate_lookup_is_an_error(self):
        masters = forging_masters()
        masters.append(record("Material Master", {"Material Code": "M", "Rate/UOM": 99}, row_id=99))
        with self.assertRaisesRegex(PcmError, "found 2"):
            Interpreter(DEFINITION, masters).calculate(forging_row())

    def test_other_commodity_rates_do_not_leak(self):
        masters = forging_masters() + [record("Material Master", {"Material Code": "M", "Rate/UOM": 999}, scope="trim_master_user")]
        self.assertEqual(Interpreter(DEFINITION, masters).calculate(forging_row())["values"]["RM Cost/Pc"], 1148)

    def test_explicit_zero_is_not_replaced_by_calculated_consumption(self):
        definition = copy.deepcopy(DEFINITION)
        definition["profiles"]["soft"]["scope"] = "forging"
        row = {**forging_row(), "Commodity": "SOFT", "Actual Gross Consumption": 0, "L": 100, "W": 50}
        masters = forging_masters() + [record("Net Consumption base", {
            "Output": "Cal RM cons", "Expression": 'n(v("Part Wt"))', "Priority": 1}),
            record("Blank Consumption base", {"Output": "Blank Length", "Expression": 'n(v("L"))', "Priority": 1}, row_id=101),
            record("Blank Consumption base", {"Output": "Blank Width", "Expression": 'n(v("W"))', "Priority": 1}, row_id=102)]
        result = Interpreter(definition, masters).calculate(row)
        self.assertEqual(result["values"]["RM Cost/Pc"], 0)

    def test_invalid_number_and_divide_by_zero_require_review(self):
        for invalid in ("abc", "NaN", "Infinity", None):
            with self.assertRaises(PcmError):
                number(invalid)
        result = Interpreter(DEFINITION, forging_masters()).run([{**forging_row(), "Input Wt": 0}])[0]
        self.assertEqual(result["status"], "Needs Review")

    def test_condition_priority_and_recursion_guard(self):
        masters = forging_masters()
        masters[-1]["data"]["Expression"] = 'rule("Norms Make Applicability", "PROFIT Base")'
        with self.assertRaisesRegex(PcmError, "Recursive rule"):
            Interpreter(DEFINITION, masters).calculate(forging_row())

    def test_unsafe_expression_is_rejected(self):
        definition = copy.deepcopy(DEFINITION)
        definition["profiles"]["forging"]["steps"][0]["expression"] = '(1).__class__'
        with self.assertRaises(PcmError):
            Interpreter(definition, [])

    def test_unknown_dispatch_is_not_success(self):
        result = Interpreter(DEFINITION, []).run([{"Commodity": "NEW"}])[0]
        self.assertEqual(result["status"], "Needs Review")

    def test_dispatch_is_definition_driven(self):
        definition = copy.deepcopy(DEFINITION)
        definition["profiles"]["forging"]["scope"] = "forging"
        definition["dispatch"] = [{"match": {"Commodity": ["ANY NEW COMMODITY"]}, "profile": "forging"}]
        row = {**forging_row(), "Commodity": "ANY NEW COMMODITY"}
        result = Interpreter(definition, forging_masters()).calculate(row)
        self.assertEqual(result["profile"], "forging")

    def test_type_specific_dispatch_precedes_broad_commodity_dispatch(self):
        definition = json.loads((Path(__file__).parents[1] / "data/pcm_reference_definition.json").read_text(encoding="utf-8"))
        row = {"Type": "BUY NON STD", "Commodity": "Hard", "Qty/Veh": 1}
        self.assertEqual(Interpreter(definition, []).profile_for(row), "buy_non_std")

    def test_sand_scrap_is_deducted_after_markup(self):
        masters = forging_masters()
        row = {**forging_row(), "Commodity": "SAND CASTING", "Type": "BUY NON STD", "Gross Wt": 10}
        result = Interpreter(DEFINITION, masters).calculate(row)
        # 1200 RM + 60 process + 12 ICC + 25.2 REJ + 1.8 OH + 50.4 profit - 76.5 scrap.
        self.assertAlmostEqual(result["values"]["Total"], 1272.9)

    def test_hourly_process_rate_conversion(self):
        definition = copy.deepcopy(DEFINITION)
        definition["profiles"]["forging"]["operations"]["conversion"] = [
            {"process": "Cut", "quantity": 'n(v("Cut"))', "unit": "per_hour"}]
        row = forging_row()
        del row["Process Cost"]
        row.update({"Cut": 3, "Sector & Location": "PUNE"})
        masters = forging_masters() + [record("Process Master", {"Process": "Cut", "Sector & Location": "PUNE", "Rate": 120})]
        values = Interpreter(definition, masters).calculate(row)["values"]
        self.assertEqual(values["Process Cost/Pc"], 6)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_master_snapshot_excludes_deleted_and_unconfigured_data(self):
        wb = WorkbookType(code="forging_self", name="Forging")
        tab = WorkbookTab(tab_name="Material Master", workbook_type=wb)
        tab.rows.extend([WorkbookRow(row_data={"Material Code": "M", "Rate/UOM": 2}),
                         WorkbookRow(row_data={"Material Code": "M", "Rate/UOM": 3}, is_deleted=True)])
        self.db.add(wb)
        self.db.commit()
        masters = snapshot_masters(self.db, DEFINITION)
        self.assertEqual(len(masters), 1)
        self.assertEqual(masters[0]["version"], 1)

    def test_configuration_loaded_from_database(self):
        self.assertIsNone(load_definition(self.db))
        config = AppConfig(group="PCM_ENGINE", key="definition", type="json", value=json.dumps(DEFINITION))
        self.db.add(config)
        self.db.commit()
        self.assertEqual(digest(load_definition(self.db)), digest(DEFINITION))

    def test_application_service_persists_and_replays_values(self):
        from app.services.pcm_calculation import run_calculation
        from app.config import settings
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.xlsx"
            wb = Workbook()
            ws = wb.active
            ws.title = "BOM"
            row = forging_row()
            ws.append(list(row))
            ws.append(list(row.values()))
            wb.save(path)
            by_table = {}
            master_wb = WorkbookType(code="forging_self", name="Forging")
            for entry in forging_masters():
                tab = by_table.setdefault(entry["table"], WorkbookTab(tab_name=entry["table"], workbook_type=master_wb))
                tab.rows.append(WorkbookRow(row_data=entry["data"]))
            self.db.add(master_wb)
            bom = Bom(project_name="PCM runtime test", file_path=str(path))
            self.db.add(bom)
            self.db.commit()
            previous = settings.UPLOAD_DIR
            settings.UPLOAD_DIR = directory
            try:
                calc = run_calculation(self.db, bom, SimpleNamespace(id=1), DEFINITION)
                self.assertEqual(calc.status, "Completed")
                self.assertAlmostEqual(calc.result.total_cost, 2587.52)
                snapshot = self.db.query(PcmRunSnapshot).one()
                replay = Interpreter(snapshot.definition, snapshot.masters).run([r["input"] for r in snapshot.results])
                self.assertEqual(replay, snapshot.results)
                output = Path(directory) / "replay.xlsx"
                write_snapshot_output(snapshot, output)
                book = load_workbook(output, data_only=False)
                self.assertTrue(all(cell.data_type != "f" for sheet in book for row in sheet for cell in row))
                self.assertIn("Formula Trace", book.sheetnames)
                book.close()
            finally:
                settings.UPLOAD_DIR = previous


if __name__ == "__main__":
    unittest.main()
