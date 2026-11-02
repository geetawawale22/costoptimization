"""Actual semantic resolver failures remain distinguishable; no shared DB access."""
from decimal import Decimal
import pytest
from app.calculations.context import CalculationContext, RowResult
from app.calculations.common import CalculationError
from app.calculations.masters import FieldRef, MissingMasterError, MasterMappingError
import test_calculation_masters as resolver_tests

@pytest.fixture
def resolver():
    fixture=resolver_tests.MasterResolverTests(methodName="test_verified_period_and_decimal")
    fixture.setUp()
    try:yield fixture.resolver
    finally:fixture.tearDown()


def test_missing_key_and_selected_blank_have_different_exact_provenance(resolver):
    context=CalculationContext(resolver,[])
    with pytest.raises(CalculationError) as absent:
        context.require_master("norms_make",{"supplier":"New supplier - no record"},"icc")
    absent=absent.value.as_dict()
    assert absent["code"]=="master_lookup_blocked"
    assert absent["master_error"]["master_failure"]=="no_record"
    assert absent["lookup_keys"]=={"supplier":"New supplier - no record"}
    with pytest.raises(CalculationError) as blank:
        context.require_master("norms_make",{"supplier":"Empty"},"icc")
    detail=blank.value.details["master_error"]
    assert detail["master_failure"]=="blank_value"
    assert detail["source_table"]=="src_supplier_norms" and detail["source_row_id"]==2
    assert detail["database_column"]=="icc"
    assert context.require_master("norms_make",{"supplier":"Empty"},"icc",blank_zero=True)==0


def test_missing_semantic_field_is_not_reported_as_missing_business_record(resolver):
    with pytest.raises(CalculationError) as error:
        CalculationContext(resolver,[]).require_master("material_master",{"material_code":"00012"},FieldRef("Rate/UOM","FUTURE"))
    detail=error.value.details["master_error"]
    assert detail["master_failure"]=="unmapped_field" and detail["master_group"]=="FUTURE"
    assert "source_row_id" not in detail


def test_duplicate_failure_keeps_safe_identities_without_embedded_master_values(resolver):
    with pytest.raises(ValueError) as error:
        resolver.record("material_master",{"material_code":"00012"},duplicate_policy="error")
    detail=error.value.as_dict()
    assert detail["master_failure"]=="duplicate_record"
    assert detail["matching_records"]==[{"source_table":"src_material_first","source_row_id":1},{"source_table":"src_material_second","source_row_id":1}]
    assert "12.3400" not in str(detail)


def test_paint_cannot_consume_a_computed_but_explicitly_unverified_make_result():
    context=CalculationContext(None,[])
    inputs={"Customer No":"DIFFERENT-0007","Supplier":"New company","End Item":"New owner"}
    context.result_records=[RowResult("fresh-record","HARD","MAKE",inputs,
        values={"Basic Cost/Pc":Decimal("17.25"),"_calculation_unverified":True},status="success")]
    with pytest.raises(CalculationError) as error:
        context.producer_by_customer(inputs,"HARD","MAKE","Basic Cost/Pc","DIFFERENT-0007")
    assert error.value.code=="failed_producer"
    assert error.value.details["producer_record_id"]=="fresh-record"
    context.result_records[0].values.pop("_calculation_unverified")
    assert context.producer_by_customer(inputs,"HARD","MAKE","Basic Cost/Pc","DIFFERENT-0007")==Decimal("17.25")


def test_existing_record_with_an_unmapped_return_field_retains_selected_source():
    fixture=resolver_tests.MasterResolverTests(methodName="test_verified_period_and_decimal")
    fixture.setUp()
    try:
        fixture.add_source(5,1,"src_partial_schema","Verified different schema",-1,
            [("material_code","Material Code",None)], [{"material_code":"00012"}])
        context=CalculationContext(fixture.resolver,[])
        with pytest.raises(CalculationError) as error:
            context.require_master("material_master",{"material_code":"00012"},FieldRef("Rate/UOM","JAN#26"))
        detail=error.value.details["master_error"]
        assert detail["master_failure"]=="unmapped_field"
        assert detail["source_table"]=="src_partial_schema" and detail["source_row_id"]==1
        assert detail["master_group"]=="JAN#26"
    finally:fixture.tearDown()


def test_assy_supplier_operation_and_metric_are_reported_without_coordinate_fallback(resolver):
    with pytest.raises(MissingMasterError) as missing:
        resolver.assy("Different supplier with no match","Fix",metric="MHR")
    detail=missing.value.as_dict()
    assert detail["master"]=="assy" and detail["master_failure"]=="no_record"
    assert detail["lookup_keys"]=={"supplier":"Different supplier with no match"}
    assert detail["master_field"]=="Fix" and detail["master_group"]=="MHR"
    with pytest.raises(MasterMappingError) as operation:
        resolver.assy("Supplier A","Unconfigured operation",metric="MHR")
    assert operation.value.details["master_failure"]=="unmapped_field"
    assert operation.value.details["source_table"]=="src_trim_assy"


@pytest.mark.parametrize("invalid_ct",["not a cycle time","#REF!","NaN","Infinity"])
def test_invalid_assembly_cycle_is_an_exact_input_error_not_an_unhandled_numeric_exception(invalid_ct):
    from app.calculations.commodities.hard.assy import calculate
    with pytest.raises(CalculationError) as error:
        calculate({"Supplier":"Independent supplier","Process / Assy Name":"Independent operation","CT(sec)":invalid_ct},CalculationContext(None,[]))
    assert error.value.code=="invalid_number"
    assert error.value.field=="CT(sec)"
