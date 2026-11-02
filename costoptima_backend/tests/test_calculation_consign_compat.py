"""Primary PCM CONSIGN initialization and shared handling compatibility.

No production database records or rates are used. Primary source evidence:
consign 10748-10786; merge 19246-19347, especially 19289 and 19325.
"""
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from app.calculations.common import CalculationError
from app.calculations.context import CalculationContext
from app.calculations.types.consign import calculate


class ConsignMasters:
    def __init__(self, defaults=True):
        self.calls = []
        self.defaults = defaults

    def records(self, code):
        assert code == "norms_consign"
        if not self.defaults:
            return ()
        # Deliberately differs from both input suppliers. The first ordered
        # master record supplies the original global lower-volume default.
        return (SimpleNamespace(value=lambda field: "configured default"),)

    def lookup(self, code, keys, field, **kwargs):
        self.calls.append((code, keys, field, kwargs))
        assert kwargs["decimal"] is True
        if field == "handling_500_2":
            assert keys == {"supplier": "configured default"}
            return D("0.125")
        assert field == "handling_500"
        assert keys == {"supplier": "first supplier"}
        return D("0.075")


def row(supplier="first supplier", qty=1000, **fields):
    return {"Type": "CONSIGN", "Supplier": supplier, "Qty/Veh": qty,
            "_variants": {"earlier": 1, "final": 1}, **fields}


def test_volume_includes_legacy_initialized_actual_cost_not_only_quantity():
    rows = [row(qty=1000, **{"Actual Cost/Pc": 999}), row("second supplier", qty=2000)]
    context = CalculationContext(ConsignMasters(), rows)
    assert context.consign_volume(rows[0]) == 0
    assert context.consign_volume(rows[1]) == 0


def test_every_consign_row_uses_the_shared_lower_volume_default():
    rows = [row(qty=1000), row("second supplier", qty=2000)]
    master = ConsignMasters()
    context = CalculationContext(master, rows)
    for record in rows:
        values = calculate(record, context)
        assert values["Handling"] == D("0.125")
        assert values["Actual Cost/Pc"] == 0
        assert values["Basic Cost/Pc"] == 0
        assert values["Grand Total Cost/Pc"] == 0
    assert all(call[1] == {"supplier": "configured default"} for call in master.calls)


def test_higher_volume_branch_uses_first_consign_supplier_globally():
    # The public initialized-cost path is zero. Isolate the retained branch
    # condition to verify its original shared-supplier behavior explicitly.
    rows = [row(), row("second supplier")]
    master = ConsignMasters()
    context = CalculationContext(master, rows)
    context.consign_volume = lambda record: D(501)
    assert calculate(rows[1], context)["Handling"] == D("0.075")
    assert master.calls[-1][1] == {"supplier": "first supplier"}


def test_missing_default_is_blocked_without_inventing_a_rate():
    rows = [row()]
    with pytest.raises(CalculationError) as error:
        calculate(rows[0], CalculationContext(ConsignMasters(defaults=False), rows))
    assert error.value.code == "consign_default_missing"


def test_missing_variant_and_non_numeric_quantity_stay_validation_errors():
    record = {"Type": "CONSIGN", "Qty/Veh": 1}
    with pytest.raises(CalculationError) as error:
        CalculationContext(None, [record]).consign_volume(record)
    assert error.value.code == "consign_variant_missing"
    record = row(qty="invalid")
    with pytest.raises(CalculationError) as error:
        CalculationContext(None, [record]).consign_volume(record)
    assert error.value.code == "invalid_number"
