"""Isolated semantic master tests; never connect to the shared MySQL database."""
import unittest
from decimal import Decimal
from unittest.mock import patch
from sqlalchemy import create_engine, Table, Column, Integer, String, MetaData
from sqlalchemy.orm import Session

from app.masters.metadata import metadata, masters, families, sources, columns
from app.calculations.masters import (
    SemanticMasterResolver, FieldRef, MasterMappingError, MissingMasterError,
    DuplicateMasterError, InvalidMasterValue, as_decimal,
)


class MasterResolverTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        # This fixture is isolated in memory. Application database schema and
        # production masters are never used or modified by these tests.
        metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.add_master(1, "material_master")
        self.add_master(2, "norms_make")
        self.add_master(3, "assy", kind="Single")
        self.add_source(1, 1, "src_material_first", "First", 0, [
            ("material_code", "Material Code", None),
            ("rate_uom_jan_26", "Rate/UOM", "JAN#26"),
            ("rate_uom_oct_25", "Rate/UOM", "OCT#25"),
        ], [{"material_code":"00012", "rate_uom_jan_26":"12.3400", "rate_uom_oct_25":"10"},
            {"material_code":"AB", "rate_uom_jan_26":"9", "rate_uom_oct_25":"8"}])
        self.add_source(2, 1, "src_material_second", "Second", 1, [
            ("material_code", "Material Code", None),
            ("rate_uom_jan_26", "Rate/UOM", "JAN#26"),
            ("rate_uom_oct_25", "Rate/UOM", "OCT#25"),
        ], [{"material_code":"00012", "rate_uom_jan_26":"99", "rate_uom_oct_25":"88"}])
        self.add_source(3, 2, "src_supplier_norms", "Norms", 0, [
            ("supplier", "Supplier", None), ("icc", "ICC", None),
        ], [{"supplier":"Trusted", "icc":"0.05"}, {"supplier":"Empty", "icc":None}])
        self.add_source(4, 3, "src_trim_assy", "Trim", 0, [
            ("col_a", "A", None), ("col_b", "B", None),
            ("col_bx", "BX", None), ("col_by", "BY", None),
            ("col_es", "ES", None), ("col_et", "ET", None),
        ], [{"col_a":"Supplier", "col_b":"MHR", "col_bx":"Supplier", "col_by":"CT per Point", "col_es":"Supplier", "col_et":"Handling Time"},
            {"col_a":None, "col_b":"Fix", "col_bx":None, "col_by":"Fix", "col_es":None, "col_et":"Fix"},
            {"col_a":None, "col_b":"Manual", "col_bx":None, "col_by":"Manual", "col_es":None, "col_et":"Manual"},
            {"col_a":"Supplier A", "col_b":"120", "col_bx":"Supplier B", "col_by":"6", "col_es":"Supplier C", "col_et":"4"}])
        self.db.commit()
        self.resolver = SemanticMasterResolver(self.db)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def add_master(self, master_id, code, kind="0"):
        self.db.execute(masters.insert().values(master_id=master_id, master_code=code,
            master_name=code, sheet_name=code, header_kind=kind, sort_order=master_id, view_name="vw_"+code))

    def add_source(self, source_id, master_id, table_name, family, order, fields, rows):
        self.db.execute(families.insert().values(family_id=source_id, folder_path=family,
                                                display_name=family, sort_order=source_id))
        self.db.execute(sources.insert().values(source_id=source_id, master_id=master_id,
            family_id=source_id, data_table_name=table_name, source_file="reference.xlsx",
            expected_rows=len(rows), row_count=len(rows), merge_order=order))
        t = Table(table_name, MetaData(), Column("row_id", Integer, primary_key=True),
                  *(Column(name, String(255)) for name, _, _ in fields))
        t.create(self.db.connection())
        for i, (name, header, group) in enumerate(fields, 1):
            self.db.execute(columns.insert().values(source_id=source_id, position=i,
                column_key=name, header_text=header, group_text=group, sql_type="VARCHAR(255)", storage_type="VARCHAR(255)"))
        if rows:
            self.db.execute(t.insert(), [dict(row_id=i, **row) for i, row in enumerate(rows, 1)])

    def test_verified_period_and_decimal(self):
        result = self.resolver.material("00012", FieldRef("Rate/UOM", "JAN#26"), decimal=True)
        self.assertEqual(result, Decimal("12.3400"))
        self.assertEqual(self.resolver.material("00012", "rate_uom_oct_25", decimal=True), Decimal("10"))

    def test_first_match_uses_source_precedence(self):
        rows = self.resolver.matches("material_master", {"material_code":"00012"})
        self.assertEqual([r.source_family for r in rows], ["First", "Second"])
        self.assertEqual(self.resolver.material("00012", "rate_uom_jan_26"), "12.3400")
        self.assertEqual(self.resolver.material("00012", "rate_uom_jan_26", duplicate_policy="last"), "99")
        with self.assertRaises(DuplicateMasterError):
            self.resolver.material("00012", "rate_uom_jan_26", duplicate_policy="error")

    def test_supplier_case_and_explicit_family(self):
        self.assertEqual(self.resolver.supplier("norms_make", " trusted ", "ICC", decimal=True), Decimal("0.05"))
        self.assertEqual(self.resolver.material("00012", "rate_uom_jan_26", family="Second"), "99")
        with self.assertRaises(MasterMappingError):
            self.resolver.material("00012", "rate_uom_jan_26", family="Unknown")

    def test_identifiers_keep_leading_zeroes(self):
        with self.assertRaises(MissingMasterError):
            self.resolver.material("12", "rate_uom_jan_26")
        self.assertEqual(self.resolver.record("material_master", {"Material Code":"00012"}).value("material_code"), "00012")

    def test_missing_values_do_not_become_zero(self):
        with self.assertRaises(MissingMasterError):
            self.resolver.supplier("norms_make", "Missing", "icc")
        with self.assertRaises(MissingMasterError):
            self.resolver.supplier("norms_make", "Empty", "icc")
        self.assertIsNone(self.resolver.supplier("norms_make", "Missing", "icc", missing="none"))
        self.assertEqual(self.resolver.supplier("norms_make", "Missing", "icc", missing="default", default="explicit"), "explicit")

    def test_ambiguous_rate_and_unknown_field_are_blocked(self):
        with self.assertRaises(MasterMappingError):
            self.resolver.material("00012", "Rate/UOM")
        with self.assertRaises(MasterMappingError):
            self.resolver.material("00012", "Invented Rate")
        with self.assertRaises(MasterMappingError):
            self.resolver.material("Missing", "Invented Rate", missing="default", default=0)

    def test_public_coordinates_and_raw_grids_are_blocked(self):
        for name in ("col_a", "BX12", "ES42", "B2"):
            with self.subTest(field=name), self.assertRaises(MasterMappingError):
                self.resolver.lookup("material_master", {name:"abc"}, "rate_uom_jan_26")
        with self.assertRaises(MasterMappingError):
            self.resolver.records("assy")
        with self.assertRaises(MasterMappingError):
            self.resolver.lookup("material_master", {}, "rate_uom_jan_26")

    def test_explicit_unsafe_decimal_values_are_rejected(self):
        for value in (None, "", "NA", "NaN", "Infinity", True):
            with self.subTest(value=value), self.assertRaises(InvalidMasterValue):
                as_decimal(value)
        self.assertEqual(as_decimal("0.10000000000000001"), Decimal("0.10000000000000001"))

    def test_assy_supplier_columns_are_independently_mapped(self):
        mapping = {"source_table":"src_trim_assy", "blocks":{
            "MHR":{"supplier_field":"col_a", "operation_fields":{"Fix":"col_b"}},
            "CT per Point":{"supplier_field":"col_bx", "operation_fields":{"Fix":"col_by"}},
            "Handling Time":{"supplier_field":"col_es", "operation_fields":{"Fix":"col_et"}},
        }}
        with patch("app.masters.repository.assy_lookup_mapping", return_value=mapping):
            self.assertEqual(self.resolver.assy("Supplier A", "Fix", "MHR"), Decimal("120"))
            self.assertEqual(self.resolver.assy("Supplier B", "Fix", "CT per Point"), Decimal("6"))
            self.assertEqual(self.resolver.assy("Supplier C", "Fix", "Handling Time"), Decimal("4"))
            with self.assertRaises(MissingMasterError):
                self.resolver.assy("Supplier A", "Fix", "CT per Point")
            with self.assertRaises(MissingMasterError):
                self.resolver.assy("", "Fix", "MHR")
            with self.assertRaises(MasterMappingError):
                self.resolver.assy("Supplier A", "Invented operation", "MHR")

    def test_reads_make_no_master_changes(self):
        before = self.db.execute(sources.select()).mappings().all()
        self.resolver.material("00012", "rate_uom_jan_26")
        self.assertEqual(self.db.execute(sources.select()).mappings().all(), before)
        self.assertFalse(self.db.new)
        self.assertFalse(self.db.dirty)
        self.assertFalse(self.db.deleted)


    def test_selected_blank_percentage_is_zero_but_missing_row_and_mapping_stay_errors(self):
        self.assertEqual(self.resolver.lookup('norms_make',{'supplier':'Empty'},'ICC',decimal=True,blank_zero=True),Decimal(0))
        with self.assertRaises(MissingMasterError):
            self.resolver.lookup('norms_make',{'supplier':'Missing'},'ICC',decimal=True,blank_zero=True)
        with self.assertRaises(MasterMappingError):
            self.resolver.lookup('norms_make',{'supplier':'Empty'},'Unverified percent',decimal=True,blank_zero=True)

    def test_mapping_cache_cannot_cross_metadata_or_period_boundaries(self):
        from app.calculations.masters import resolve_field,MasterField
        one=(MasterField('jan','Rate/UOM','JAN','DECIMAL'),MasterField('feb','Rate/UOM','FEB','DECIMAL'))
        other=(MasterField('another_jan','Rate/UOM','JAN','DECIMAL'),)
        resolve_field.cache_clear()
        self.assertEqual(resolve_field(one,FieldRef('Rate/UOM','JAN')).column,'jan')
        self.assertEqual(resolve_field(one,FieldRef('Rate/UOM','FEB')).column,'feb')
        self.assertEqual(resolve_field(other,FieldRef('Rate/UOM','JAN')).column,'another_jan')
        before=resolve_field.cache_info().hits
        self.assertEqual(resolve_field(one,FieldRef('Rate/UOM','JAN')).column,'jan')
        self.assertEqual(resolve_field.cache_info().hits,before+1)
        self.assertEqual(resolve_field.cache_info().maxsize,4096)


if __name__ == "__main__":
    unittest.main()
