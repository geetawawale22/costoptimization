"""Independent named-input contracts; no production data, rates or saved results.

Primary PCM always reads BOM (190,19799); its Matrix BOM Y/N flags at
15158-15184 choose output field provenance, not row inclusion. Family input
and BOM fallback are explicit in make_new14602-14617 and make_bns_prev.
"""
from decimal import Decimal as D
from io import BytesIO

import pytest
from openpyxl import Workbook, load_workbook
from app.calculations.input import InputValidationError, parse_workbook
from app.calculations.engine import execute
from test_calculation_engine import Masters


HEADERS = ['Project', 'PF Code', '0 / 1 / 2 / 3', 'Version East', 'Version West',
           'Supplier', 'End Item', 'Customer No', 'Type', 'Qty/Veh', 'Commodity',
           'Part Wt', 'BOM']


def owner(**overrides):
    value = {'Project': 'Independent project Q', 'PF Code': 'Q-007',
             '0 / 1 / 2 / 3': 1, 'Version East': 2, 'Version West': 0,
             'Supplier': 'New supplier 43', 'End Item': 'Assembly X',
             'Customer No': '000937', 'Type': 'MAKE', 'Qty/Veh': 3,
             'Commodity': 'HARD', 'BOM': 'Y'}
    value.update(overrides)
    return value


def workbook(rows, details=(), *, detail_name='Hard', instructions=0,
             headers=None, detail_headers=None):
    book = Workbook()
    bom = book.active
    bom.title = 'BOM'
    headers = headers or HEADERS
    for number in range(instructions):
        bom.append(['Instructions %s' % number])
    bom.append(headers)
    for row in rows:
        bom.append([row.get(name) for name in headers])
    if details:
        detail = book.create_sheet(detail_name)
        detail.append(['Family input instructions'])
        detail_headers = detail_headers or headers
        detail.append(detail_headers)
        for row in details:
            detail.append([row.get(name) for name in detail_headers])
    output = BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


@pytest.mark.parametrize('field,value', [('Commodity', 'SOFT')])
def test_named_identity_conflict_is_not_borrowed_despite_equal_old_join_key(field, value):
    bom = owner(**{'Part Wt': D('0.125')})
    detail = owner(**{field: value, 'Part Wt': D('9.999')})
    parsed = parse_workbook(workbook([bom], [detail]))
    row = parsed.records[0]
    assert row['Part Wt'] == .125
    assert row['_details'] == []
    assert row['Purchasing Supplier'] == row['Costing Supplier'] == 'New supplier 43'
    issues = [issue for issue in parsed.issues if issue['code'] == 'incompatible_detail_identity']
    assert len(issues) == 1
    assert field in issues[0]['rejected_details'][0]['fields']
    assert parsed.metadata['unassigned_details'][0]['Part Wt'] == 9.999


def test_descriptive_project_does_not_arbitrarily_disambiguate_duplicate_details():
    valid = owner(**{'Part Wt': .125})
    foreign = owner(Project='Other project', **{'Part Wt': 9.999})
    parsed = parse_workbook(workbook([owner()], [foreign, valid]))
    assert parsed.records[0].get('Part Wt') is None
    assert len(parsed.records[0]['_details']) == 2
    assert any(issue['code'] == 'ambiguous_detail_match' for issue in parsed.issues)
    assert not any(issue['code'] == 'incompatible_detail_identity' for issue in parsed.issues)


@pytest.mark.parametrize('family', ['HARD', 'SOFT', 'STAMPING', 'FORGING',
                                    'SAND CASTING', 'GDC', 'PDC', 'THERMAL'])
def test_same_semantic_identity_accepts_reordered_headers_and_dynamic_input_rows(family):
    names = HEADERS[5:] + HEADERS[:5]
    # The variant region still follows the named Level/Supplier contract. The
    # surrounding identities and numerical inputs can appear in any positions.
    names = [name for name in names if name not in ['0 / 1 / 2 / 3', 'Version East', 'Version West']]
    names[ names.index('Supplier'):names.index('Supplier') ] = ['0 / 1 / 2 / 3', 'Version East', 'Version West']
    expected = owner(Commodity=family, **{'Part Wt': .333})
    parsed = parse_workbook(workbook([owner(Commodity=family)], [expected],
                                    detail_name=family, instructions=9,
                                    headers=names, detail_headers=names))
    assert parsed.records[0]['Part Wt'] == .333
    assert parsed.records[0]['_variants'] == {'Version East': 2, 'Version West': 0}
    assert parsed.records[0]['Customer No'] == '000937'
    assert parsed.records[0]['_row'] == 11
    assert len(parsed.records[0]['_details']) == 1


@pytest.mark.parametrize('bom_flag', ['Y', 'N', None])
def test_annotation_bom_flag_does_not_silently_drop_a_named_bom_record(bom_flag):
    parsed = parse_workbook(workbook([owner(BOM=bom_flag)]))
    assert len(parsed.records) == 1
    assert parsed.records[0]['BOM'] == bom_flag
    assert parsed.records[0]['_bom_values']['BOM'] == bom_flag


def test_blank_optional_project_labels_do_not_manufacture_identity():
    parsed = parse_workbook(workbook([owner()], [owner(Project=None, **{'PF Code': None, 'Part Wt': .75})]))
    assert parsed.records[0]['Part Wt'] == .75
    assert parsed.records[0]['Project'] == 'Independent project Q'
    assert not any(issue['code'] == 'incompatible_detail_identity' for issue in parsed.issues)


def test_compatible_duplicate_details_stay_ambiguous_and_are_never_collapsed():
    parsed = parse_workbook(workbook([owner()], [owner(**{'Part Wt': .5}), owner(**{'Part Wt': .75})]))
    assert len(parsed.records[0]['_details']) == 2
    assert any(issue['code'] == 'ambiguous_detail_match' and issue['severity'] == 'error'
               for issue in parsed.issues)
    assert 'Part Wt' not in parsed.records[0] or parsed.records[0]['Part Wt'] is None


def test_soft_unique_detail_type_override_preserves_bom_project_with_warning():
    parsed = parse_workbook(workbook([owner(Commodity='SOFT', Type='MAKE')],
                                    [owner(Commodity='SOFT', Type='ASSY', Project='Other project', **{'Part Wt': 7})],
                                    detail_name='Soft'))
    assert parsed.records[0]['Type'] == 'ASSY'
    assert parsed.records[0]['_ownership_type'] == 'MAKE'
    assert parsed.records[0]['Project'] == 'Independent project Q'
    assert parsed.records[0]['Part Wt'] == 7
    assert len(parsed.records[0]['_details']) == 1
    assert any(issue['code'] == 'detail_type_differs_from_bom' for issue in parsed.issues)
    assert any(issue['code'] == 'bom_detail_disagreement' and 'Project' in issue['fields']
               for issue in parsed.issues)


def test_detail_only_file_is_preserved_but_not_assigned_an_invented_cost_owner():
    book = Workbook()
    sheet = book.active
    sheet.title = 'Hard'
    sheet.append(HEADERS)
    sheet.append([owner(**{'Part Wt': .75}).get(name) for name in HEADERS])
    output = BytesIO()
    book.save(output)
    book.close()
    with pytest.raises(InputValidationError, match='no-BOM'):
        parse_workbook(output.getvalue())
    parsed = parse_workbook(output.getvalue(), partial_output=True)
    assert len(parsed.records) == 1
    assert parsed.records[0]['_dependency_blocked']
    assert parsed.records[0]['Part Wt'] == .75
    assert parsed.metadata['unsupported_bom_structure']
    assert parsed.metadata['input_sheet_copies']


def test_new_identity_bom_only_assy_runs_original_arithmetic_without_sample_keys():
    # Original assy CT=points*point-time+handling; cost=CT*MHR/3600.
    # Test master is isolated and current-run; no prior workbook supplies costs.
    row = owner(Commodity='HARD', Type='ASSY', **{'Process / Assy Name': 'Independent fixture operation',
                                               'No of Points': 7, 'Qty/Veh': 4})
    data = workbook([row], headers=HEADERS + ['Process / Assy Name', 'No of Points'])
    master = Masters({'ICC': '.01', 'REJ': '.02', 'OH': '.03', 'PROFIT': '.04', 'markup': '.1'})
    run = execute(data, master, development=True)
    result = run.rows[0]
    assert result.status == 'success', result.issues
    expected_cycle = D(7) * D(2) + D(4)
    expected_basic = expected_cycle * D(360) / D(3600)
    assert result.values['CT(sec)'] == expected_cycle
    assert result.values['Basic Cost/Pc'] == expected_basic
    assert result.values['Grand Total Cost/Pc'] == expected_basic * D('1.1')
    assert result.values['Grand Total Cost/Veh'] == expected_basic * D('1.1') * D(4)
    assert result.inputs['Customer No'] == '000937'
    assert result.inputs['Supplier'] == 'New supplier 43'


def test_leading_empty_header_and_optional_cells_keep_actual_row_identity():
    headers = [None] + HEADERS
    data = workbook([owner(Project=None, **{'PF Code': None})],
                    [owner(Project=None, **{'PF Code': None, 'Part Wt': .75})],
                    instructions=5, headers=headers, detail_headers=headers)
    parsed = parse_workbook(data)
    row = parsed.records[0]
    assert row['_row'] == 7 and row['_record_id'].endswith(':BOM:7')
    assert row['_detail_row'] == 3
    assert row['_details'][0]['_record_id'].endswith(':Hard:3')
    assert row['Part Wt'] == .75
    assert not any(issue['severity'] == 'error' for issue in parsed.issues)


def test_leading_blank_formula_row_is_diagnostic_not_workbook_failure():
    data = workbook([owner(Project=None, **{'PF Code': None})],
                    [owner(Project=None, **{'PF Code': None, 'Part Wt': '=1+1'})],
                    headers=[None] + HEADERS, detail_headers=[None] + HEADERS)
    parsed = parse_workbook(data)
    assert parsed.records[0]['_row'] == 2
    assert any(issue['code'] == 'uncached_input_formula' and issue['row'] == 3
               for issue in parsed.issues)
    assert any(issue['code'] == 'detail_validation_failed' and issue['row'] == 2
               for issue in parsed.issues)
    assert parsed.records[0]['Part Wt'] is None


@pytest.mark.parametrize('boundary', ['Actual Gross Consumption', 'Yield (%)'])
def test_bom_only_stamping_uses_original_named_preprocessing_boundary(boundary):
    row = owner(Commodity='STAMPING', **{boundary: .6, 'Independent press': 3, 'Independent weld': 2})
    parsed = parse_workbook(workbook([row], headers=HEADERS + [boundary, 'Independent press', 'Independent weld']))
    assert [(line['name'], line['quantity']) for line in parsed.records[0]['_process_lines']] == [
        ('Independent press', 3), ('Independent weld', 2)]
    assert all('rate' not in line for line in parsed.records[0]['_process_lines'])


def test_stamping_actual_gross_header_has_precedence_over_yield_fallback():
    headers = HEADERS + ['Yield (%)', 'Actual Gross Consumption', 'Independent press']
    parsed = parse_workbook(workbook([owner(Commodity='STAMPING', **{'Yield (%)': .6,
                                                                  'Actual Gross Consumption': None,
                                                                  'Independent press': 3})], headers=headers))
    assert [line['name'] for line in parsed.records[0]['_process_lines']] == ['Independent press']


def test_bom_only_forging_retains_separate_heat_and_machining_lines():
    fields = ['Yield (%)', 'Independent normalising', 'Hardening & Tempering', 'Independent machining']
    row = owner(Commodity='FORGING', **{'Yield (%)': .8, 'Independent normalising': 2,
                                       'Hardening & Tempering': 4, 'Independent machining': 6})
    parsed = parse_workbook(workbook([row], headers=HEADERS + fields))
    assert [(line['name'], line['quantity']) for line in parsed.records[0]['_hardening_lines']] == [
        ('Independent normalising', 2), ('Hardening & Tempering', 4)]
    assert [(line['name'], line['quantity']) for line in parsed.records[0]['_process_lines']] == [
        ('Independent machining', 6)]


def test_explicit_stamping_detail_supersedes_bom_input_projection():
    headers = HEADERS + ['Actual Gross Consumption', 'Independent press']
    parsed = parse_workbook(workbook([owner(Commodity='STAMPING', **{'Independent press': 9})],
                                    [owner(Commodity='STAMPING', **{'Independent press': 2})],
                                    detail_name='Stamping', headers=headers, detail_headers=headers))
    assert parsed.records[0]['_process_lines'][0]['quantity'] == 2
    assert parsed.records[0]['Process Lines'][0]['quantity'] == 2


def test_bom_only_process_stamping_resolves_current_rates_and_independent_arithmetic():
    headers = HEADERS + ['Actual Gross Consumption', 'Independent press']
    row = owner(Commodity='STAMPING', **{'Actual Gross Consumption': None, 'Independent press': 7})
    master = Masters({'Rate/UOM': '2.5', 'uom': 'piece', 'm_c_specs': 'Stroke',
                      'ICC': 0, 'REJ': 0, 'OH': 0, 'PROFIT': 0, 'Dies & Fixture Maint. cost': 0})
    run = execute(workbook([row], headers=headers), master, development=True)
    result = run.rows[0]
    assert result.status == 'success', result.issues
    expected = D(7) * D('2.5')
    assert result.values['RM Cost/Pc'] == 0  # Explicit process-only source branch.
    assert result.values['Process Cost/Pc'] == expected
    assert result.values['Grand Total Cost/Pc'] == expected
    assert result.values['Grand Total Cost/Veh'] == expected * D(3)
    assert any(code == 'process_master' and keys == {'process': 'Independent press'}
               for code, keys, field in master.calls)


def test_existing_unmatched_family_sheet_does_not_trigger_bom_projection():
    headers = HEADERS + ['Actual Gross Consumption', 'Independent press']
    parsed = parse_workbook(workbook([owner(Commodity='STAMPING', **{'Independent press': 9})],
                                    [owner(Commodity='STAMPING', **{'Customer No': 'different-part', 'Independent press': 2})],
                                    detail_name='Stamping', headers=headers, detail_headers=headers))
    assert '_process_lines' not in parsed.records[0]
    assert '_bom_family_projection' not in parsed.records[0]
    assert parsed.records[0]['_details'] == []
    assert any(issue['code'] == 'missing_detail_match' for issue in parsed.issues)


@pytest.mark.parametrize('field,value', [('Project', 'Other project'), ('PF Code', 'OTHER-009')])
def test_unique_strict_supplier_detail_retains_costs_and_warns_about_descriptive_labels(field, value):
    parsed = parse_workbook(workbook([owner()], [owner(**{field: value, 'Part Wt': .125})]))
    row = parsed.records[0]
    assert row['Part Wt'] == .125
    assert row[field] == owner()[field]
    assert len(row['_details']) == 1
    assert row['Purchasing Supplier'] == row['Costing Supplier'] == 'New supplier 43'
    assert not any(issue['code'] == 'incompatible_detail_identity' for issue in parsed.issues)
    warnings = [issue for issue in parsed.issues if issue['code'] == 'bom_detail_disagreement']
    assert len(warnings) == 1 and field in warnings[0]['fields']
    assert warnings[0]['severity'] == 'warning'


@pytest.mark.parametrize('identity', ['Alpha', 'Beta'])
@pytest.mark.parametrize('type_', ['BUY STD', 'BUY NON STD', 'CONSIGN', 'PKG', 'FRT'])
def test_repeated_purchasing_occurrences_use_sql_masters_and_count_each_quantity_once(identity, type_):
    from test_calculation_branch_generality import RelationalFixture
    fixture = RelationalFixture(identity)
    try:
        common = {'Commodity': '', 'Supplier': fixture.supplier, 'End Item': 'Independent root '+identity,
                  'Project': 'New project '+identity, 'Customer No': fixture.part, 'Type': type_}
        root = owner(**{**common, 'Customer No': 'root '+identity, 'Type': 'END ITEM',
                        '0 / 1 / 2 / 3': 0, 'Qty/Veh': 1, 'Version East': 1, 'Version West': 1})
        first = owner(**{**common, 'Qty/Veh': 2, 'Version East': 1, 'Version West': 0})
        second = owner(**{**common, 'Qty/Veh': 3, 'Version East': 2, 'Version West': 1})
        run = execute(workbook([root, second, first], instructions=4), fixture.resolver, development=True)
        purchases = [row for row in run.rows if row.type == type_]
        result = next(row for row in run.rows if row.type == 'END ITEM')
        assert len(purchases) == 2 and len({row.record_id for row in purchases}) == 2
        assert all(row.calculation_status == 'CALCULATED' for row in purchases), [row.issues for row in purchases]
        assert all(row.inputs['_purchasing_occurrence'] for row in purchases)
        expected = fixture.price * D('1.1') if type_ in {'BUY STD', 'BUY NON STD'} else D(0)
        assert all(row.values['Grand Total Cost/Pc'] == expected for row in purchases)
        assert result.values['Version East'] == expected * (D(3)*2 + D(2))
        assert result.values['Version West'] == expected * D(3)
        assert not run.producer_rows
        assert not any(issue['code'] == 'duplicate_bom_business_key' for issue in run.issues)
        assert len([issue for issue in run.issues if issue['code'] == 'repeated_purchasing_occurrence']) == 2
    finally:
        fixture.close()


def test_repeated_legacy_key_with_mixed_commodity_does_not_get_purchasing_exemption():
    rows = [owner(Commodity='', Type='BUY NON STD'), owner(Commodity='HARD', Type='BUY NON STD')]
    parsed = parse_workbook(workbook(rows))
    assert len([issue for issue in parsed.issues if issue['code'] == 'duplicate_bom_business_key']) == 2
    assert not any(row.get('_purchasing_occurrence') for row in parsed.records)


def test_duplicate_manufacturing_or_end_item_ownership_remains_blocked():
    for commodity, type_ in [('HARD', 'MAKE'), ('HARD', 'ASSY'), ('SOFT', 'BUY NON STD'), ('', 'END ITEM')]:
        parsed = parse_workbook(workbook([owner(Commodity=commodity, Type=type_), owner(Commodity=commodity, Type=type_)]))
        assert len([issue for issue in parsed.issues if issue['code'] == 'duplicate_bom_business_key']) == 2
        assert not any(row.get('_purchasing_occurrence') for row in parsed.records)


def test_unique_soft_detail_with_blank_end_item_keeps_bom_owner_and_detail_type():
    headers = [None] + HEADERS + ['Actual Gross Consumption', 'Independent assembly']
    bom = owner(Commodity='SOFT', Type='MAKE', Project='Assembly project')
    detail = owner(Commodity='SOFT', Type='ASSY', Project='Detail project',
                   **{'End Item': None, 'Actual Gross Consumption': None, 'Independent assembly': 7})
    parsed = parse_workbook(workbook([bom], [detail], detail_name='Soft',
                                    headers=headers, detail_headers=headers, instructions=6))
    row = parsed.records[0]
    assert row['Type'] == 'ASSY' and row['_ownership_type'] == 'MAKE'
    assert row['End Item'] == 'Assembly X' and row['Project'] == 'Assembly project'
    assert row['_details'][0]['End Item'] is None
    assert row['_details'][0]['Project'] == 'Detail project'
    assert row['_process_lines'][0]['name'] == 'Independent assembly'
    assert row['_process_lines'][0]['quantity'] == 7
    assert row['Costing Supplier'] == row['Purchasing Supplier'] == 'New supplier 43'
    assert not parsed.metadata['unassigned_details']
    assert not any(issue['severity'] == 'error' for issue in parsed.issues)
    assert any(issue['code'] == 'soft_detail_end_item_omitted' for issue in parsed.issues)
    assert any(issue['code'] == 'bom_detail_disagreement' and 'Project' in issue['fields'] for issue in parsed.issues)


@pytest.mark.parametrize('change', ['two_details', 'two_end_items', 'two_types', 'another_commodity'])
def test_blank_end_item_soft_detail_rejects_ambiguous_owner_or_details(change):
    bom = owner(Commodity='SOFT', Type='MAKE')
    rows = [bom]
    details = [owner(Commodity='SOFT', Type='ASSY', **{'End Item': None})]
    if change == 'two_details':
        details.append(owner(Commodity='SOFT', Type='ASSY', **{'End Item': None}))
    elif change == 'two_end_items':
        rows.append(owner(Commodity='SOFT', Type='MAKE', **{'End Item': 'Another assembly'}))
    elif change == 'two_types':
        rows.append(owner(Commodity='SOFT', Type='ASSY'))
    else:
        rows.append(owner(Commodity='HARD', Type='MAKE'))
    parsed = parse_workbook(workbook(rows, details, detail_name='Soft'))
    assert not parsed.records[0]['_details']
    assert parsed.records[0]['Type'] == 'MAKE'
    assert any(issue['code'] == 'ambiguous_soft_detail_owner' and issue['severity'] == 'error'
               for issue in parsed.issues)
    assert parsed.metadata['unassigned_details']


@pytest.mark.parametrize('overrides', [{'End Item': 'Another assembly'}, {'Supplier': 'Another supplier', 'End Item': None},
                                      {'Supplier': None, 'End Item': None}, {'Commodity': 'HARD', 'End Item': None}])
def test_soft_blank_end_item_rule_does_not_relax_supplier_or_explicit_end_item(overrides):
    detail = owner(Commodity='SOFT', Type='ASSY')
    detail.update(overrides)
    parsed = parse_workbook(workbook([owner(Commodity='SOFT', Type='MAKE')], [detail], detail_name='Soft'))
    assert parsed.records[0]['_details'] == []
    assert parsed.records[0]['Type'] == 'MAKE'
    assert not any(issue['code'] == 'soft_detail_end_item_omitted' for issue in parsed.issues)
    assert parsed.metadata['unassigned_details']


@pytest.mark.parametrize('identity', ['Alpha', 'Beta'])
def test_blank_end_item_soft_process_detail_live_sql_cost_and_root_ownership(identity):
    from test_calculation_branch_generality import RelationalFixture
    fixture = RelationalFixture(identity)
    try:
        common = {'Supplier': fixture.supplier, 'Customer No': fixture.part, 'End Item': 'Assembly '+identity,
                  'Project': 'Owner project '+identity}
        root = owner(**{**common, 'Customer No': 'root '+identity, 'Commodity': '', 'Type': 'END ITEM',
                        'Qty/Veh': 1, 'Version East': 1, 'Version West': 1})
        component = owner(**{**common, 'Commodity': 'SOFT', 'Type': 'MAKE', 'Qty/Veh': 3,
                             'Version East': 2, 'Version West': 0})
        detail = owner(**{**common, 'Commodity': 'SOFT', 'Type': 'ASSY', 'End Item': None,
                          'Project': 'Detail project '+identity, 'Actual Gross Consumption': None,
                          fixture.process: 7})
        headers = HEADERS + ['Actual Gross Consumption', fixture.process]
        run = execute(workbook([root, component], [detail], detail_name='Soft', headers=headers,
                               detail_headers=headers, instructions=3), fixture.resolver, development=True)
        result = next(row for row in run.rows if row.commodity == 'SOFT')
        parent = next(row for row in run.rows if row.type == 'END ITEM')
        assert result.calculation_status == 'CALCULATED', result.issues
        assert parent.calculation_status == 'CALCULATED', parent.issues
        expected = D(7) * fixture.process_rate
        assert result.values['Grand Total Cost/Pc'] == expected
        assert parent.values['Version East'] == expected * D(3) * D(2)
        assert parent.values['Version West'] == 0
        assert result.inputs['End Item'] == common['End Item']
        assert result.inputs['Project'] == common['Project']
        assert result.inputs['_ownership_type'] == 'MAKE' and result.type == 'ASSY'
        assert not run.producer_rows
    finally:
        fixture.close()
