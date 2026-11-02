"""Isolated original SOFT special-process grouping, formulas and ownership.

Primary source:soft884(Sr),merge19518(first customer),19520-19525(SUMPRODUCT),
19539-19548(outer BUY PAINT markup). No production values or DB writes.
"""
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from app.calculations.common import CalculationError
from app.calculations.context import CalculationContext
from app.calculations.masters import FieldRef
from app.calculations.merging.dependencies import plan
from app.calculations.merging.special_process import select_soft_group
from app.calculations.commodities.soft.special_process import calculate


class Masters:
    def __init__(self, missing_supplier=False):
        self.calls = []
        self.missing_supplier = missing_supplier

    def record(self, master, keys, **kwargs):
        self.calls.append((master, keys, 'record'))
        if master == 'norms_special_process_norms':
            return SimpleNamespace(value=lambda field: None) if keys['special_process'] in {'Buy Paint', 'Make Chrome', 'Make Paint'} else None
        if master == 'norms_buy_non_std':
            return None if self.missing_supplier else SimpleNamespace(value=lambda field: None)
        raise AssertionError((master, keys))

    def lookup(self, master, keys, field, **kwargs):
        label = field.name if isinstance(field, FieldRef) else field
        self.calls.append((master, keys, label))
        if master == 'norms_make':
            return D(0)
        if master == 'process_master':
            if keys['process'] != 'verified process':
                raise CalculationError('missing_master', 'Unknown process')
            return 'piece' if label == 'uom' else D(2)
        if master == 'norms_buy_non_std':
            return D('.1') if label == 'icc' else D(0)
        raise AssertionError((master, keys, field))


def detail(identity, customer, sr='001', qty=2, process=1, end='E', supplier='S'):
    return {'_record_id': identity, '_sheet': 'Soft', '_row': 10,
            'Supplier': supplier, 'End Item': end, 'Customer No': customer,
            'Sr': sr, 'Supplier No': 'not-a-group-key', 'Commodity': 'SOFT',
            'Type': 'ASSY', 'Qty/Veh': qty, 'Material Code': None,
            '_process_lines': [{'name': 'verified process', 'quantity': process}]}


def owner(components, mode='Buy Paint'):
    return {'_record_id': 'bom:owner', 'Supplier': 'S', 'End Item': 'E',
            'Customer No': 'board', 'Commodity': 'SOFT', 'Type': 'ASSY',
            '_ownership_type': 'SPECIAL PROCESS', 'Qty/Veh': 3,
            '_variants': {'V': 1}, 'Special Process': mode,
            '_soft_components': components, '_details': [components[0]]}


def test_group_uses_exact_sr_and_retains_nonadjacent_source_order():
    rows = [detail('a', 'board'), detail('other', 'different', sr='1'),
            detail('child', 'child', qty=4), detail('foreign', 'child', supplier='Other')]
    selected = select_soft_group(owner(rows), rows)
    assert [row['_record_id'] for row in selected] == ['a', 'child']


def test_supplier_number_does_not_substitute_for_required_sr_header():
    rows = [detail('a', 'board')]
    rows[0].pop('Sr')
    with pytest.raises(CalculationError) as error:
        select_soft_group(owner(rows), rows)
    assert error.value.code == 'soft_special_process_sr_missing'


def test_cross_end_item_group_is_blocked_instead_of_borrowing_costs():
    rows = [detail('a', 'board'), detail('child', 'child', end='Other End Item')]
    with pytest.raises(CalculationError) as error:
        select_soft_group(owner(rows), rows)
    assert error.value.code == 'soft_special_process_owner_conflict'


def test_real_soft_pipeline_then_quantity_weighted_group_and_outer_markup():
    rows = [detail('a', 'board', qty=2, process=1), detail('child', 'child', qty=4, process=3)]
    record = owner(rows)
    context = CalculationContext(Masters(), [record])
    values = calculate(record, context)
    # Process rates come from masters:2*1*2 +2*3*4 =28.
    assert values['RM Cost/Pc'] == 0
    assert values['Process Cost/Pc'] == 28
    assert values['Basic Cost/Pc'] == 28
    assert values['ICC COST'] == D('2.8')
    assert values['Grand Total Cost/Pc'] == D('30.8')
    assert values['Grand Total Cost/Veh'] == D('92.4')
    from io import BytesIO
    from openpyxl import load_workbook
    from app.calculations.context import RowResult,CalculationRun
    from app.calculations.output import build_workbook
    output=load_workbook(BytesIO(build_workbook(CalculationRun([RowResult('owner','SOFT','ASSY',record,values,status='success')]))))
    sheet=output['Master_Output'];headers=[cell.value for cell in sheet[1]]
    assert sheet.cell(2,headers.index('Total Cost/Pc')+1).value==30.8
    output.close()
    assert len(context.result_records) == 2
    assert all(row.dependency_only and row.status == 'success' for row in context.result_records)
    assert all(row.type == 'SOFT SPECIAL PROCESS PRODUCER' for row in context.result_records)


def test_repeated_consumer_projection_reuses_each_calculated_producer():
    rows = [detail('a', 'board'), detail('child', 'child')]
    record = owner(rows)
    master = Masters()
    context = CalculationContext(master, [record])
    calculate(record, context)
    before = sum(call[0] == 'process_master' for call in master.calls)
    calculate(record, context)
    assert len(context.result_records) == 2
    assert sum(call[0] == 'process_master' for call in master.calls) == before


def test_other_configured_selector_retains_original_zero_outer_markup():
    rows = [detail('a', 'board')]
    record = owner(rows, 'Make Chrome')
    values = calculate(record, CalculationContext(Masters(), [record]))
    assert values['Basic Cost/Pc'] == 4
    assert values['MARKUP/Pc'] == 0


def test_buy_paint_missing_supplier_uses_verified_named_other_fallback():
    rows = [detail('a', 'board')]
    record = owner(rows)
    master = Masters(missing_supplier=True)
    values = calculate(record, CalculationContext(master, [record]))
    assert values['ICC COST'] == D('.4')
    assert any(call[0] == 'norms_buy_non_std' and call[1] == {'supplier': 'Other'} for call in master.calls)


def test_separately_owned_bom_component_cannot_be_attributed_twice():
    rows = [detail('a', 'board'), detail('child', 'child')]
    record = owner(rows)
    child = {**rows[1], '_record_id': 'bom:child', '_details': [rows[1]], '_variants': {'V': 1}}
    with pytest.raises(CalculationError) as error:
        calculate(record, CalculationContext(Masters(), [record, child]))
    assert error.value.code == 'soft_special_process_duplicate_ownership'


def test_failed_group_producer_remains_blocked_without_zero_cost():
    rows = [detail('a', 'board')]
    rows[0]['_process_lines'][0]['name'] = 'unknown process'
    record = owner(rows)
    with pytest.raises(CalculationError) as error:
        calculate(record, CalculationContext(Masters(), [record]))
    assert error.value.code == 'soft_special_process_producer_failed'
    assert error.value.details['producer_issues'][0]['code'] == 'missing_master'


def test_special_process_owner_dependencies_survive_assy_detail_type():
    consumer = {'_record_id': 'paint', 'Type': 'ASSY', '_ownership_type': 'SPECIAL PROCESS',
                'Commodity': 'SOFT', 'Special Process': 'Make Paint', 'End Item': 'E', '_paint_components': [{'Make Part no': 'M'}]}
    producer = {'_record_id': 'make', 'Commodity': 'HARD', 'Type': 'MAKE', 'End Item': 'E', 'Customer No': 'M'}
    execution, edges = plan([consumer, producer])
    assert execution == ['make', 'paint'] and edges['paint'] == {'make'}


def test_soft_buy_paint_does_not_depend_on_unused_paint_make_rows():
    row=owner([detail('a','board')]);row['_paint_components']=[{'Make Part no':'not-a-soft-producer'}]
    execution,edges=plan([row])
    assert execution==['bom:owner'] and edges['bom:owner']==set()
