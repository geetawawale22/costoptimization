"""Source-expression tests: forging_data13017-81 and setoftwo_format12631.

Isolated named master fixtures only. No MySQL mutation, original workbook
execution or full legacy runtime parity is claimed by these tests.
"""
from decimal import Decimal as D
from fractions import Fraction as F
import pytest
from app.calculations.common import CalculationError
from app.calculations.context import CalculationContext
from app.calculations.masters import FieldRef
from app.calculations.commodities.forging.make import (
    calculate,calculated_weight,section_weight,geometry_weights,
)


class ForgingMasters:
    def __init__(self):
        self.calls=[]
        self.rates={'Forging':D(2),'Melting':D(3),'Normalising':D(7),
                    'Hardening & Tempering':D(5),'Drilling':D(6)}
        self.density=D('7.85')
    def lookup(self,master,keys,field,**kwargs):
        self.calls.append((master,keys,field))
        if master=='material_master':
            if keys.get('Material Code',keys.get('material_code'))!='steel':
                raise CalculationError('missing_master','No fixture material')
            name=field.name if isinstance(field,FieldRef) else field
            if name=='GSM/ Density':
                if self.density is None:raise CalculationError('missing_master','No fixture density')
                return self.density
            return {'Rate/UOM':D(10),'Scrap Rate':D(2)}[name]
        if master=='process_master':
            name=keys.get('process')
            if field=='uom':return 'Rs/Pc'
            assert isinstance(field,FieldRef) and field.name=='Rate/UOM'
            if name not in self.rates:raise CalculationError('missing_master','No fixture process')
            return self.rates[name]
        if master=='norms_make':return D(0)
        raise AssertionError((master,keys,field))


def record(**changes):
    return {'Commodity':'FORGING','Type':'MAKE','Supplier':'fixture','Material Code':'steel',
            'RM Base':'fixture period','Input Wt':D(2),'Part Wt':D(1),
            'Forging Wt':D('1.25'),'Casting Wt':D(99),'Qty/Veh':2,'End Item':'fixture owner',
            '_hardening_lines':[],'_process_lines':[],**changes}


def run(row,masters=None,rows=None):
    masters=masters or ForgingMasters()
    return calculate(row,CalculationContext(masters,rows or [row])),masters


def test_original_supplied_forging_weight_precedes_calculated_and_casting():
    row=record(**{'Calculated Wt':D(17),'_hardening_lines':[
        {'name':'Forging','quantity':1},{'name':'Normalising','quantity':2}]})
    result,_=run(row)
    # Source13079 input basis=2;13081 calculated basis=1.25.
    assert result['Calculated Wt']==D('1.25')
    assert result['Hardening Cost']==D(2)*2+D(7)*2*D('1.25')
    assert result['_hardening_costs'][1]['basis']==D('1.25')
    assert result['RM Cost/Pc']==20-D('.8')*2+D('21.5')
    assert result['Process Cost/Pc']==0
    assert result['Yield (%)']==D('.5')


@pytest.mark.parametrize('value',[None,'',0])
def test_preferred_known_blank_or_zero_weight_does_not_take_other_column(value):
    row=record(**{'Forging Wt':value,'Calculated Wt':D(11),
                  '_hardening_lines':[{'name':'Normalising','quantity':2}]})
    result,_=run(row)
    assert result['Calculated Wt']==0
    assert result['Hardening Cost']==0


def test_calculated_weight_is_fallback_only_when_forging_header_absent():
    row=record(**{'Calculated Wt':D(3),'_hardening_lines':[{'name':'Hardening & Tempering','quantity':2}]})
    del row['Forging Wt']
    result,_=run(row)
    assert result['Calculated Wt']==3 and result['Hardening Cost']==30


def test_casting_weight_cannot_silently_become_forging_weight():
    row=record(**{'_hardening_lines':[{'name':'Normalising','quantity':1}]})
    del row['Forging Wt']
    with pytest.raises(CalculationError) as error:run(row)
    assert error.value.code=='forging_weight_missing'
    assert calculated_weight(row) is None


def test_inactive_weighted_hardening_does_not_block_independent_input_weight_cost():
    row=record(**{'_hardening_lines':[{'name':'Normalising','quantity':0},{'name':'Forging','quantity':1}]})
    del row['Forging Wt']
    result,_=run(row)
    assert result['Hardening Cost']==4
    assert 'Calculated Wt' not in result


def test_material_less_generic_process_uses_real_process_rates_and_known_blanks():
    row=record(**{'Type':'PROCESS','Material Code':None,'Input Wt':None,'Part Wt':None,
                  'Forging Wt':None,'_process_lines':[{'name':'Drilling','quantity':2}]})
    result,masters=run(row)
    assert result['RM Cost/Pc']==0 and result['Process Cost/Pc']==12
    assert result['Grand Total Cost/Pc']==12 and result['Grand Total Cost/Veh']==24
    assert not any(master=='material_master' for master,_,_ in masters.calls)


def test_generic_process_missing_input_header_is_not_a_known_blank():
    row=record(**{'Type':'PROCESS','Material Code':None})
    del row['Input Wt']
    with pytest.raises(CalculationError) as error:run(row)
    assert error.value.code=='missing_input' and error.value.field=='Input Wt'


@pytest.mark.parametrize('kind',['MAKE','BUY NON STD'])
def test_make_and_bns_cannot_use_material_less_process_fallback(kind):
    with pytest.raises(CalculationError) as error:run(record(**{'Type':kind,'Material Code':None}))
    assert error.value.code=='missing_input'


def test_nonblank_unknown_material_is_not_zero_for_process():
    with pytest.raises(CalculationError) as error:run(record(**{'Type':'PROCESS','Material Code':'unknown'}))
    assert error.value.code=='missing_master'


def test_no_make_hardening_belongs_to_process_once():
    row=record(**{'Type':'PROCESS','_hardening_lines':[{'name':'Normalising','quantity':2}]})
    result,_=run(row)
    assert result['Hardening Cost']==D('17.5')
    assert result['RM Cost/Pc']==D('18.4')
    assert result['Process Cost/Pc']==D('17.5')
    assert result['Grand Total Cost/Pc']==D('35.9')


def test_positive_variable_selection_excludes_negative_process_column():
    row=record(**{'_process_lines':[{'name':'Drilling','quantity':2},
                                  {'name':'not selected negative','quantity':-3}]})
    result,masters=run(row)
    assert result['Process Cost/Pc']==12
    assert not any(keys.get('process')=='not selected negative' for _,keys,_ in masters.calls)


def test_first_family_input_owns_process_location():
    first=record(**{'Sector & Location':'APPROVED FIRST'})
    row=record(**{'Sector & Location':'IGNORED SECOND','_process_lines':[{'name':'Drilling','quantity':2}]})
    _,masters=run(row,rows=[first,row])
    assert all(field.group=='APPROVED FIRST' for master,keys,field in masters.calls
               if master=='process_master' and isinstance(field,FieldRef))


def test_setoftwo_retains_named_normalising_rate_without_adding_cost_twice():
    row=record(**{'End Item':'Set of two fixture','_hardening_lines':[{'name':'Normalising','quantity':1}]})
    result,masters=run(row)
    assert result['_master_rates']['Normalising']==7
    assert result['Normalising Cost']==D('8.75')
    assert result['Hardening Cost']==D('8.75')
    assert result['RM Cost/Pc']==D('27.15')
    assert result['Grand Total Cost/Pc']==D('27.15')
    assert result['GSM/ Density']==D('7.85')


def test_setoftwo_missing_density_is_specific_estimate_gap_not_zero_core_cost():
    master=ForgingMasters();master.density=None
    result,_=run(record(**{'End Item':'setoftwo'}),master)
    assert result['Grand Total Cost/Pc']==D('18.4')
    assert result['_estimate_issues'][0]['code']=='forging_density_missing'
    assert 'GSM/ Density' not in result


def test_setoftwo_missing_named_process_rate_never_uses_literal_five():
    master=ForgingMasters();del master.rates['Normalising']
    result,_=run(record(**{'End Item':'setoftwo'}),master)
    assert '_master_rates' not in result and 'Normalising Cost' not in result
    assert result['_estimate_issues'][0]['code']=='forging_normalising_rate_missing'
    assert result['Grand Total Cost/Pc']==D('18.4')


def section(**changes):
    return {'Section':'Solid Section','Length( in mm)':100,'Breadth(in mm)':20,
            'Thickness(in mm)':10,'Outer Dia(in mm)':0,'Hollow Dia(in mm)':0,**changes}


def test_rectangular_and_annular_weights_match_exact_original_expressions():
    # Original12631: solid volume * density/1e6. Original3.14 is retained.
    density=D('7.85')
    assert section_weight(section(),density)==D('0.157')
    annular=section(**{'Section':'Hollow Section','Outer Dia(in mm)':100,'Hollow Dia(in mm)':60})
    expected=F(314,100)*(F(100,2)**2-F(60,2)**2)*10*F(785,100)/1000000
    assert section_weight(annular,density)==D(expected.numerator)/D(expected.denominator)
    assert section_weight(annular,density)==D('.394384')


def test_known_blank_outer_diameter_selects_rectangular_branch():
    assert section_weight(section(**{'Outer Dia(in mm)':None}),D('7.85'))==D('.157')


def test_named_sections_sum_separate_original_blocks_without_replacing_input_weight():
    sections=[section(),section(**{'Section':'Hollow Section','Outer Dia(in mm)':100,'Hollow Dia(in mm)':60})]
    result=geometry_weights(sections,D('7.85'))
    assert result['Solid Section Weight']==D('.157')
    assert result['Hollow Section Weight']==D('.394384')
    assert 'Calculated Wt' not in result
    assert [row['FRG WT'] for row in result['Sections']]==[D('.157'),D('.394384')]


@pytest.mark.parametrize('density',[0,-1,None])
def test_geometry_requires_actual_positive_named_density(density):
    with pytest.raises(CalculationError):section_weight(section(),density)


def test_ambiguous_or_impossible_geometry_is_rejected():
    with pytest.raises(CalculationError) as error:
        section_weight(section(**{'Outer Dia(in mm)':10,'Hollow Dia(in mm)':11}),D('7.85'))
    assert error.value.code=='forging_geometry_invalid'
    row=section();del row['Thickness(in mm)']
    with pytest.raises(CalculationError) as error:section_weight(row,D('7.85'))
    assert error.value.code=='missing_input'
    with pytest.raises(CalculationError):geometry_weights([section(**{'Section':'unknown'})],D('7.85'))
    with pytest.raises(CalculationError) as error:geometry_weights([section()]*7,D('7.85'))
    assert error.value.code=='forging_geometry_limit'


def test_material_less_fallback_requires_a_known_material_header():
    row=record(**{'Type':'PROCESS'});del row['Material Code']
    with pytest.raises(CalculationError) as error:run(row)
    assert error.value.code=='missing_input' and error.value.field=='Material Code'


@pytest.mark.parametrize('density',[0,-1])
def test_setoftwo_invalid_density_is_distinguished_from_missing(density):
    master=ForgingMasters();master.density=D(density)
    result,_=run(record(**{'End Item':'setoftwo'}),master)
    assert result['_estimate_issues'][0]['code']=='forging_density_invalid'
    assert result['Grand Total Cost/Pc']==D('18.4')


def test_forging_output_metadata_is_taken_from_matched_database_material():
    from types import SimpleNamespace
    master=ForgingMasters()
    matched={'Commodity':'FORGING MASTER','Material Family':'verified family',
             'Material Sub Family':'verified subgroup','Material Grade':'verified grade','UOM':'KG'}
    def record_from_master(master_code,keys):
        assert master_code=='material_master' and keys=={'material_code':'steel'}
        return SimpleNamespace(value=lambda field:matched[field])
    master.record=record_from_master
    result,_=run(record(**{'Material Type':'uploaded mismatch','UOM':'untrusted UOM'}),master)
    assert result['Material Type']=='FORGING MASTER'
    assert result['UOM']=='KG'
    assert result['Material Family']=='verified family'
    assert result['Material Grade']=='verified grade'
    assert result['Yield (%)']==D('.5')


@pytest.mark.parametrize('kind,expected',[('MAKE',D('43.15')),('PROCESS',D(12))])
def test_normal_workflow_parses_named_forging_headers_and_preserves_owner_rollup(kind,expected):
    from io import BytesIO
    from openpyxl import Workbook
    from app.calculations.engine import execute
    workbook=Workbook();bom=workbook.active;bom.title='BOM'
    base=['0 / 1 / 2 / 3','Base','Supplier','End Item','Customer No','Commodity','Type','Qty/Veh','Material Code']
    bom.append(base)
    bom.append([0,1,'fixture','owner','root','','END ITEM',1,None])
    bom.append([1,1,'fixture','owner','component','FORGING',kind,1,'steel' if kind=='MAKE' else None])
    details=workbook.create_sheet('Forging')
    details.append(base+['RM Base','Input Wt','Part Wt','Forging Wt','Calculated Wt','Yield (%)',
                         'Forging','Normalising','Hardening & Tempering','Drilling'])
    details.append([1,1,'fixture','owner','component','FORGING',kind,1,'steel' if kind=='MAKE' else None,
                    'fixture period',2 if kind=='MAKE' else None,1 if kind=='MAKE' else None,
                    1.25 if kind=='MAKE' else None,99,None,1 if kind=='MAKE' else None,
                    1 if kind=='MAKE' else None,None,2])
    source=BytesIO();workbook.save(source)
    result=execute(source.getvalue(),ForgingMasters(),development=True)
    row=next(r for r in result.rows if r.inputs['Customer No']=='component')
    owner=next(r for r in result.rows if r.type=='END ITEM')
    assert row.status=='success',row.issues
    assert owner.status=='success',owner.issues
    assert row.values['Grand Total Cost/Pc']==expected
    assert owner.values['Base']==expected
    assert row.values['Calculated Wt']==(D('1.25') if kind=='MAKE' else D(0))
    assert row.inputs['Supplier']=='fixture'


def test_process_location_preserves_detail_input_order_when_bom_order_differs():
    first_bom=record(**{'Sector & Location':'SECOND DETAIL','_detail_row':9})
    second_bom=record(**{'Sector & Location':'FIRST DETAIL','_detail_row':3,
                         '_process_lines':[{'name':'Drilling','quantity':2}]})
    _,masters=run(second_bom,rows=[first_bom,second_bom])
    assert all(field.group=='FIRST DETAIL' for master,keys,field in masters.calls
               if master=='process_master' and isinstance(field,FieldRef))
