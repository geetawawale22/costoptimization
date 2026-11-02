"""Isolated source-formula/dependency/output tests; never production fixtures."""
from decimal import Decimal as D
from io import BytesIO
from types import SimpleNamespace
import importlib
import openpyxl
import pytest
from app.calculations.common import CalculationError,evaluate_expression,first_matching_condition
from app.calculations.context import CalculationContext,RowResult,CalculationRun
from app.calculations.dispatcher import dispatch,BRANCHES
from app.calculations.merging.dependencies import order
from app.calculations.merging.rollup import rollup_owner
from app.calculations.output import build_workbook
from app.calculations.engine import execute
from app.calculations.masters import FieldRef

class Masters:
    def __init__(self,values=None):self.values=values or {};self.calls=[]
    def lookup(self,master,keys,field,**kwargs):
        label=field.name if isinstance(field,FieldRef) else field
        self.calls.append((master,keys,field))
        found=self.values.get((master,label),self.values.get(label))
        if callable(found):return found(keys,field)
        if found is None:raise CalculationError('missing_master',f'{master}.{label}')
        return D(str(found)) if kwargs.get('decimal') else found
    def assy(self,supplier,operation,metric='MHR',**kwargs):return {'CT':D(2),'Handling':D(4),'MHR':D(360)}[metric]
    def records(self,code):return []


def test_named_expression_is_safe_and_lazy():
    assert evaluate_expression('IF({gross}>0,{gross}*2,1/0)',{'gross':D(3)})==6
    assert evaluate_expression('ROUNDDOWN(-2.9,0)',{})==D(-2)
    with pytest.raises(CalculationError):evaluate_expression('__import__("os").system("bad")',{})
    with pytest.raises(CalculationError):evaluate_expression('A1+3',{})
    with pytest.raises(CalculationError):evaluate_expression('{missing}+3',{})


def test_condition_order_and_numeric_equality():
    rows=[{'Con1':'L>10','value':1},{'Con1':'L>0','value':2}]
    assert first_matching_condition(rows,{'L':20},'Con1')['value']==1
    assert first_matching_condition([{'Con1':'L=2.0'}],{'L':D(2)},'Con1')
    with pytest.raises(CalculationError):first_matching_condition([{'Con1':'L<=2'}],{'L':1},'Con1')

@pytest.mark.parametrize('key',list(BRANCHES))
def test_every_registered_branch_has_callable_entry(key):
    branch=dispatch(*key);module=importlib.import_module('app.calculations.'+branch.module)
    assert callable(module.calculate)

@pytest.mark.parametrize('name',['BUY STD','BUY NON STD','CONSIGN','PKG','FRT','END ITEM'])
def test_type_only_dispatch_is_not_hard_or_soft(name):
    assert dispatch('',name).module.startswith('types.')


def test_no_invented_commodity_alias_or_blank_cost():
    for commodity in ['HVAC','SHEET METAL','SEAT','SEAT MECHANISM','UNKNOWN']:
        with pytest.raises(CalculationError):dispatch(commodity,'MAKE')
    with pytest.raises(CalculationError):dispatch('','').calculate({},CalculationContext(Masters(),[]))


def test_default_guard_precedes_parsing_or_database_access():
    with pytest.raises(CalculationError,match='disabled'):execute(b'not a workbook',None)


def test_dependency_cycle_and_missing_producer():
    assert order(['a','b'],{'b':{'a'}})==['a','b']
    with pytest.raises(CalculationError):order(['a','b'],{'a':{'b'},'b':{'a'}})
    with pytest.raises(CalculationError):order(['a'],{'a':{'missing'}})
    with pytest.raises(CalculationError):CalculationContext(Masters(),[]).require_result({},'SOFT','MAKE','Total')


def test_assy_source_formula_and_no_uploaded_rate():
    record={'Supplier':'fixture','Process / Assy Name':'fix','No of Points':3,'Qty/Veh':2,'MHR':99999}
    master=Masters({'ICC':'.01','REJ':'.02','OH':'.03','PROFIT':'.04','markup':'.1'})
    result=dispatch('HARD','ASSY').calculate(record,CalculationContext(master,[record]))
    # Source CT=points*point-time+handling=(3*2)+4; basic=CT*MHR/3600.
    assert result['CT(sec)']==10
    assert result['Basic Cost/Pc']==1
    assert result['Grand Total Cost/Pc']==D('1.1')
    assert result['Grand Total Cost/Veh']==D('2.2')


def test_bop_reads_named_database_cost_and_owned_end_item_supplier():
    root={'Type':'END ITEM','End Item':'Root','Supplier':'owner'}
    record={'Customer No':'0012','Supplier':'component','End Item':'Root','Qty/Veh':2}
    master=Masters({'cost_pc':'10','icc':'.01','rej':'.02','handling':'.03','profit':'.04','markup':'.10'})
    values=dispatch('','BUY STD').calculate(record,CalculationContext(master,[root,record]))
    assert values['Grand Total Cost/Pc']==11
    assert any(keys=={'supplier':'owner'} for code,keys,field in master.calls if code=='norms_buy_std')
    assert master.calls[0][1]['customer_no']=='0012'

@pytest.mark.parametrize('type_',['PKG','FRT'])
def test_original_zero_logistics_base_is_explicit(type_):
    result=dispatch('',type_).calculate({'Qty/Veh':3,'Basic Cost':999},None)
    assert result['Basic Cost/Pc']==0 and result['Grand Total Cost/Pc']==0


def test_owned_rollup_blocks_missing_cost_and_weights_both_variants():
    root={'End Item':'E','_variants':{'V':2}}
    component=RowResult('1','HARD','MAKE',{'End Item':'E','Qty/Veh':3,'_variants':{'V':4}},values={'Grand Total Cost/Pc':D(5)},status='success')
    assert rollup_owner(root,[component])=={'V':D(120)}
    component.status='blocked'
    with pytest.raises(CalculationError):rollup_owner(root,[component])


def test_output_preserves_identifier_and_neutralizes_formula_text():
    row=RowResult('1','','PKG',{'Customer No':'0012','End Item':'=2+2','Qty/Veh':1,'_variants':{'V':1}},values={'Basic Cost/Pc':D(0),'MARKUP/Pc':D(0),'Grand Total Cost/Pc':D(0)},status='success')
    run=CalculationRun([row],metadata={'run_id':'fixture'},summaries={'Analysis':[]})
    w=openpyxl.load_workbook(BytesIO(build_workbook(run)),data_only=False)
    assert {'Master_Output','Analysis','Diagnostics'}<=set(w.sheetnames)
    master=w['Master_Output'];headers={str(cell.value):cell.column for cell in master[1]}
    identifier=master.cell(2,headers['Customer No']);owner=master.cell(2,headers['End Item'])
    assert identifier.value=='0012';assert owner.value=='=2+2';assert owner.data_type=='s'
    # User text stays inert while the writer may emit its verified PNF summary.
    formulas=[(sheet.title,cell.coordinate,cell.value) for sheet in w for values in sheet for cell in values if cell.data_type=='f']
    assert formulas==[('Master_Output','J2',"='Pkg'!F2"),('Master_Output','L2',"='Pkg'!F2"),('Pkg','H5','=SUMPRODUCT(F2:F2,E2:E2,H2:H2)')]
    cached=openpyxl.load_workbook(BytesIO(build_workbook(run)),data_only=True)
    assert cached['Pkg']['H5'].value==0
    assert owner.value=='=2+2' and owner.data_type=='s'


def zero_norm_master(extra=None):
    return Masters({'Material Family':'fixture family','Material Sub Family':'fixture subfamily','Material Grade':'fixture grade','Rate/UOM':'2','Scrap Rate':'2','uom':'piece','ICC':'0','REJ':'0','OH':'0','PROFIT':'0','Handling':'0',
                    'fob':'5','landing_factor':'1.2','currency':'fixture currency','solder_point':'3',**(extra or {})})


def test_gdc_current_source_scrap_core_conversion_graph():
    record={'Commodity':'GDC','Type':'MAKE','Supplier':'s','Qty/Veh':1,'Material Code':'fixture','RM Base':'fixture',
            'Casting Wt':2,'Part Wt':1,'Core Weight per Part':D('.1'),'_process_lines':[]}
    master=zero_norm_master()
    # Material10; all process rates2. Expected from P8901,8914,8940,9170-9227.
    original=master.lookup
    def lookup(code,keys,field,**kw):
        if code=='material_master' and isinstance(field,FieldRef) and field.name=='Rate/UOM':return D(10)
        return original(code,keys,field,**kw)
    master.lookup=lookup
    result=dispatch('GDC','MAKE').calculate(record,CalculationContext(master,[record]))
    assert result['RM Cost/Pc']==D('15.65')
    assert result['GDC Cost']==D('12.2')
    assert result['Grand Total Cost/Pc']==D('27.85')


def test_pdc_shot_scrap_melting_and_machine_minutes_graph():
    record={'Commodity':'PDC','Type':'MAKE','Supplier':'s','Qty/Veh':1,'Material Code':'fixture','RM Base':'fixture',
            'Casting Wt':2,'Part Wt':1,'PDC Machine Process':'fixture machine','PDC Machine Minutes':30,'_process_lines':[]}
    master=zero_norm_master();result=dispatch('PDC','MAKE').calculate(record,CalculationContext(master,[record]))
    assert result['SHOT WEIGHT']==D(2)/D('.65')
    assert result['PDC cost']==1
    assert result['PDC Conversion cost']==9
    assert result['Scrap Cost']==D('1.19')


def test_forging_make_hardening_is_rm_not_duplicated_process():
    record={'Commodity':'FORGING','Type':'MAKE','Supplier':'s','Qty/Veh':1,'Material Code':'fixture','RM Base':'fixture',
            'Input Wt':2,'Part Wt':1,'Casting Wt':D('1.5'),'_process_lines':[],
            '_hardening_lines':[{'name':'Forging','quantity':1}]}
    result=dispatch('FORGING','MAKE').calculate(record,CalculationContext(zero_norm_master(),[record]))
    assert result['Scrap Wt']==D('.8')
    assert result['Hardening Cost']==4
    assert result['RM Cost/Pc']==D('6.4')
    assert result['Process Cost/Pc']==0


def test_soft_assy_no_material_keeps_original_process_and_no_rm_lookup():
    record={'Commodity':'SOFT','Type':'ASSY','Supplier':'s','Qty/Veh':2,'_process_lines':[{'name':'fixture process','quantity':3}]}
    master=zero_norm_master();result=dispatch('SOFT','ASSY').calculate(record,CalculationContext(master,[record]))
    assert result['RM Cost/Pc']==0 and result['Process Cost/Pc']==6
    assert result['Grand Total Cost/Veh']==12
    assert not any(c=='material_master' for c,k,f in master.calls)


def test_pcba_landed_and_solder_formula_uses_database_only():
    record={'Commodity':'PCBA','Type':'ASSY','Supplier':'s','Qty/Veh':1,'Material Code':'fixture','RM Base':'fixture','FOB':999}
    from app.calculations.commodities.pcba.assy import calculate_component
    result=calculate_component(record,CalculationContext(zero_norm_master(),[record]))
    assert result['RM Cost/Pc']==12
    assert result['Process Cost/Pc']==6
    assert result['Grand Total Cost/Pc']==18


def test_thermal_nonmake_source_factor_and_pnf():
    record={'Commodity':'THERMAL','Type':'ASSY','Supplier':'s','Qty/Veh':1,'Material Code':'fixture','RM Base':'fixture','Part Wt':1}
    result=dispatch('THERMAL','ASSY').calculate(record,CalculationContext(zero_norm_master(),[record]))
    assert result['Gross Wt']==D('1.2')
    assert result['RM Cost/Pc']==D('2.4')
    assert result['Process Cost/Pc']==D('4.8')
    assert result['Grand Total Cost/Pc']==D('8.64')


def test_blank_existing_excel_reference_differs_from_missing_field():
    assert evaluate_expression('{width}*2',{'width':None},excel_blank_zero=True)==0
    with pytest.raises(CalculationError):evaluate_expression('{width}*2',{},excel_blank_zero=True)
    rows=[{'Con1':'width>0'},{'Con1':'width=NONE'}]
    with pytest.raises(CalculationError):first_matching_condition(rows,{},'Con1')


def test_hard_make_shift_efficiency_and_per_vehicle_formula():
    record={'Commodity':'HARD','Type':'MAKE','Supplier':'s','Qty/Veh':2,'Material Code':'fixture','RM Base':'fixture',
            'Gross Wt':2,'Part Wt':1,'Cycle Time':30,'Cavity':2,'m/c Tonnage':100}
    master=zero_norm_master({'Applicable Process':'Default','efficiency':'.8'})
    previous=master.lookup
    def lookup(code,keys,field,**kw):
        if code=='norms_shift_factor':return D('.2') if keys['shift_factor']=='Deflashing' else D(10)
        return previous(code,keys,field,**kw)
    master.lookup=lookup
    result=dispatch('HARD','MAKE').calculate(record,CalculationContext(master,[record]))
    assert result['Pc per shift']==1536
    assert result['Shift Rate']==1000
    assert result['RM Cost/Pc']==4
    assert result['Grand Total Cost/Pc']==D(4)+D(1536)/1000+D('.2')
    assert result['Grand Total Cost/Veh']==result['Grand Total Cost/Pc']*2


def test_gdc_original_absent_location_group_uses_verified_ad_metadata():
    from app.calculations.masters import MasterField
    master=zero_norm_master();master.fields=lambda code:(MasterField('rate_ad','Rate/UOM','AD','DECIMAL'),)
    record={'Commodity':'GDC','Type':'MAKE','Supplier':'s','Qty/Veh':1,'Material Code':'fixture','RM Base':'fixture',
        'Sector & Location':'FD','Casting Wt':2,'Part Wt':1,'Core Weight per Part':D('.5'),'_process_lines':[]}
    result=dispatch('GDC','MAKE').calculate(record,CalculationContext(master,[record]))
    assert result['Process Rate Location']=='AD' and record['Sector & Location']=='FD'
    assert all(field.group=='AD' for code,keys,field in master.calls if code=='process_master')


def test_gdc_location_is_first_supplier_detail_and_does_not_cross_supplier():
    from app.calculations.commodities.gdc.make import process_location
    from app.calculations.masters import MasterField
    master=zero_norm_master();master.fields=lambda code:tuple(MasterField('rate_'+x,'Rate/UOM',x,'DECIMAL') for x in ['AD','FD','GD'])
    first={'Commodity':'GDC','Type':'MAKE','Supplier':'s','_detail_row':2,'Sector & Location':'FD'}
    current={'Commodity':'GDC','Type':'PROCESS','Supplier':'s','_detail_row':8,'Sector & Location':'GD'}
    other={'Commodity':'GDC','Supplier':'other','_detail_row':1,'Sector & Location':'AD'}
    assert process_location(current,CalculationContext(master,[current,other,first]))['Sector & Location']=='FD'


def test_gdc_existing_group_missing_process_never_falls_back_to_another_rate():
    from app.calculations.masters import MasterField,MissingMasterError
    master=zero_norm_master();master.fields=lambda code:tuple(MasterField('rate_'+x,'Rate/UOM',x,'DECIMAL') for x in ['AD','FD'])
    original=master.lookup;locations=[]
    def lookup(code,keys,field,**kwargs):
        if code=='process_master':locations.append(field.group);raise MissingMasterError('No required process record')
        return original(code,keys,field,**kwargs)
    master.lookup=lookup
    record={'Commodity':'GDC','Type':'MAKE','Supplier':'s','Qty/Veh':1,'Material Code':'fixture','RM Base':'fixture',
        'Sector & Location':'FD','Casting Wt':2,'Part Wt':1,'Core Weight per Part':D('.5'),'_process_lines':[]}
    with pytest.raises(CalculationError) as error:dispatch('GDC','MAKE').calculate(record,CalculationContext(master,[record]))
    assert error.value.code=='master_lookup_blocked' and locations==['FD']
