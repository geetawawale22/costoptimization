"""Named condition failures retain source identity without changing arithmetic."""
from types import SimpleNamespace
from decimal import Decimal

import pytest

from app.calculations.common import CalculationError
from app.calculations.consumption import select_rule
from app.calculations.masters import MasterField, MasterRecord


def context():
    fields=(MasterField('condition','Con1','L','TEXT'),
            MasterField('expression','L','L','TEXT'))
    rules=[MasterRecord('gross_consumption_base','rules_table','ROOT',i,
                {'condition':condition,'expression':formula},fields)
           for i,condition,formula in (
               (31,'Gross cal Method =%','{Blank Length}*{% wastage}'),
               (32,'Gross cal Method =Blank','{Blank Length}'),
               (33,'Gross cal Method =Nesting','{L}'))]
    material=MasterRecord('material_master','material_source','Family',901,
                {'material_code':'NEW-KEY','gross_method':None},
                (MasterField('material_code','Material Code',None,'TEXT'),
                 MasterField('gross_method','Gross cal Method',None,'TEXT')))
    class Master:
        def records(self,code):assert code=='gross_consumption_base';return rules
        def record(self,code,keys):
            assert code=='material_master' and keys=={'material_code':'NEW-KEY'}
            return material
    return SimpleNamespace(master=Master(),provenance=[])


def test_null_material_method_reports_exact_named_source_and_candidates():
    ctx=context()
    with pytest.raises(CalculationError) as caught:
        select_rule(ctx,'gross_consumption_base','L',
                    {'Material Code':'NEW-KEY','Gross cal Method':None},['Con1'])
    error=caught.value
    assert error.code=='missing_condition'
    assert error.details['master']=='gross_consumption_base'
    assert error.details['master_field']=='L'
    assert error.details['condition_field']=='Con1'
    operands=error.details['condition_inputs']
    assert operands==[{'field':'Gross cal Method','present':True,'value':None,
                      'master_source':{'master':'material_master','lookup_keys':{'material_code':'NEW-KEY'},
                                       'source_table':'material_source','source_row_id':901,
                                       'database_column':'gross_method','database_value':None}}]
    assert [item['source_row_id'] for item in error.details['attempted_conditions']]==[31,32,33]
    assert ctx.provenance==[]


def test_absent_operand_remains_absent_and_never_borrows_database_value():
    ctx=context()
    with pytest.raises(CalculationError) as caught:
        select_rule(ctx,'gross_consumption_base','L',{'Material Code':'NEW-KEY'},['Con1'])
    error=caught.value
    assert error.code=='unknown_operand'
    assert error.field=='Gross cal Method'
    assert error.details['condition_inputs'][0]['present'] is False
    assert error.details['condition_inputs'][0]['value'] is None
    assert error.details['condition_inputs'][0]['master_source']['database_value'] is None


def test_matching_generic_method_keeps_original_precedence_and_no_diagnostics():
    ctx=context()
    expression=select_rule(ctx,'gross_consumption_base','L',
                           {'Material Code':'NEW-KEY','Gross cal Method':'Blank'},['Con1'])
    assert expression=='{Blank Length}'
    assert ctx.provenance==[{'master':'gross_consumption_base','source_table':'rules_table',
                            'source_row_id':32,'field':'L','duplicate_candidates':1}]


def test_sand_density_failure_retains_material_identity_and_cause():
    from test_calculation_sand_complete import fixture
    from app.calculations.commodities.sand_casting.make import calculate
    row,ctx,masters=fixture()
    masters.densities['casting code']=None
    with pytest.raises(CalculationError) as caught:calculate(row,ctx)
    details=caught.value.details
    assert details['master']=='material_master'
    assert details['lookup_keys']=={'Material Code':'casting code'}
    assert details['master_field']=='GSM/ Density'
    assert details['required_units']=='g/cm3'
    assert details['master_error']['code']=='master_lookup_blocked'
    assert details['partial_values']['Total Charge Kg']==17


def test_forging_density_estimate_issue_identifies_its_exact_material_requirement():
    from test_calculation_forging_geometry import ForgingMasters,record,run
    master=ForgingMasters();master.density=None
    result,_=run(record(**{'End Item':'setoftwo'}),master)
    issue=result['_estimate_issues'][0]
    assert issue['code']=='forging_density_missing'
    assert issue['lookup_keys']=={'Material Code':'steel'}
    assert issue['master']=='material_master' and issue['master_field']=='GSM/ Density'
    assert issue['required_units']=='g/cm3'
    assert issue['master_error']['code']=='missing_master'
    assert result['Grand Total Cost/Pc']==Decimal('18.4')


def test_forging_rate_issue_identifies_named_process_and_selected_location():
    from test_calculation_forging_geometry import ForgingMasters,record,run
    master=ForgingMasters();del master.rates['Normalising']
    result,_=run(record(**{'End Item':'setoftwo','Sector & Location':'OTHER LOCATION'}),master)
    issue=result['_estimate_issues'][0]
    assert issue['code']=='forging_normalising_rate_missing'
    assert issue['master']=='process_master'
    assert issue['lookup_keys']=={'process':'Normalising'}
    assert issue['master_field']=='Rate/UOM' and issue['master_group']=='OTHER LOCATION'
    assert issue['master_error']['code']=='missing_master'
    assert 'Normalising Cost' not in result
