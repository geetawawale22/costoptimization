"""Source-formula Thermal tests, isolated from production rows and rates."""
from decimal import Decimal as D
from types import SimpleNamespace
import pytest
from app.calculations.context import CalculationContext
from app.calculations.common import CalculationError
from app.calculations.masters import FieldRef
from app.calculations.commodities.thermal.make import calculate,supplier_has_make


class Record:
    def __init__(self,values,id_):self.values=values;self.source_table='fixture';self.source_row_id=id_;self.fields=[]
    def value(self,name):return self.values.get(name.name if isinstance(name,FieldRef) else name)


class Masters:
    def __init__(self):self.calls=[];self.trace=[]
    def records(self,name):
        if name=='blank_consumption_base':
            return [Record({'Commodity':commodity,'Con1':material,'Con2':'Flowtype = '+flow,'Blank Length':length,'Blank Width':'{H}+{Blank Width Allowance}'},i) for i,(commodity,material,flow,length) in enumerate([
                ('Soft','Material Code != SMALTB0003','Crossflow','0'),
                ('Thermal','Material Code = SMALTB0003','Crossflow','{L}+{Blank Width Allowance}'),
                ('Thermal','Material Code = SMALTB0003','Downflow','{W}+{Blank Width Allowance}'),
                ('Thermal','Material Code != SMALTB0003','Crossflow','{W}+{Blank Length Allowance}'),
                ('Thermal','Material Code != SMALTB0003','Downflow','{L}+{Blank Length Allowance}')])]
        if name=='net_consumption_base':
            return [Record({'Commodity':'Thermal','Con1':'Material Code = '+code,'Cal RM cons':expression},i) for i,(code,expression) in enumerate([
                ('SMALTB0001','{thk}*{Input Blank Length}*({H}*2+{Tube Height (Post Forming)}*2)*(2.78*10^-6)'),
                ('SMALTB0002','{H}*{thk}*{Fin Length}*(2.78*10^-6)'),
                ('SMALTB0003','{thk}*{Input Blank Length}*{Input Blank Width}*(2.78*10^-6)'),
                ('SMALTB0004','{thk}*{Input Blank Length}*{Input Blank Width}*(2.78*10^-6)')])]
        raise AssertionError('Thermal must not use SOFT gross/parts/yield masters: '+name)
    def lookup(self,name,keys,field,**kwargs):
        key=field.name if isinstance(field,FieldRef) else field
        self.calls.append((name,keys,key,kwargs))
        if name=='material_master':return {'Material Family':'Tube','Material Sub Family':'Aluminium','Material Grade':'fixture grade','Blank Length Allowance':D(5),'Blank Width Allowance':D(2),'Rate/UOM':D(2)}[key]
        if name=='process_master':
            assert keys=={'Process Code':'SMALTB0001'} or str(keys.get('Process Code','')).startswith('SMALTB')
            assert isinstance(field,FieldRef) and field.name=='Rate/UOM' and field.group=='AD'
            return D(3)
        if name=='norms_make':return D(0)
        raise AssertionError((name,keys,field))


def record(code='SMALTB0001',type_='MAKE',flow='Crossflow'):
    return {'Commodity':'THERMAL','Type':type_,'Supplier':'fixture','Customer No':'0001','End Item':'E','Material Code':code,'RM Base':'Period','Qty/Veh':10,'L':500,'W':300,'H':20,'thk':1,'Tube Height (Post Forming)':2,'FPDM':10,'Flow type':flow,'Part Wt':1}


def test_make_tube_geometry_and_no_nonmake_pnf_or_soft_consumption():
    row=record();ctx=CalculationContext(Masters(),[row]);values=calculate(row,ctx)
    assert values['Input Blank Length']==305 and values['Input Blank Width']==22
    assert values['Tube Pitch']==50 and values['Height/100']==D('3.05')
    assert values['Fin Height']==48 and values['Fin Qty']==D('30.50') and values['Fin Length']==1464
    assert values['Gross Wt']==D('.0373076')
    assert values['RM Cost/Pc']==D('.0746152')
    assert values['Conversion Cost']==D('1.1192280')
    assert values['Process Cost/Pc']==D('.2238456')
    assert values['Grand Total Cost/Pc']==D('.2984608')
    assert values['PnF/Pc']==0 and values['Grand Total Cost/Veh']==D('2.9846080')
    assert values['_output_sheet_prefix']=='make_'
    assert all(p['master'] in {'blank_consumption_base','net_consumption_base'} for p in ctx.provenance)


@pytest.mark.parametrize('code,flow,blank_length,gross',[
    ('SMALTB0002','Crossflow',305,D('.0813984')),
    ('SMALTB0003','Crossflow',502,D('.03070232')),
    ('SMALTB0003','Downflow',302,D('.01847032')),
    ('SMALTB0004','Downflow',505,D('.03088580')),
])
def test_original_material_and_flow_branches(code,flow,blank_length,gross):
    row=record(code,flow=flow);values=calculate(row,CalculationContext(Masters(),[row]))
    assert values['Input Blank Length']==blank_length and values['Gross Wt']==gross


def test_supplier_any_make_routes_other_type_through_make_geometry():
    make=record();assy=record(type_='ASSY');assy['Customer No']='second'
    values=calculate(assy,CalculationContext(Masters(),[make,assy]))
    assert values['_thermal_supplier_path']=='make' and values['PnF/Pc']==0
    # The original classifier inspects Thermal detail Type, not BOM ownership.
    bom=record(type_='BUY NON STD');bom['_details']=[{'_sheet':'Thermal','Type':'MAKE'}]
    assert supplier_has_make(bom,CalculationContext(Masters(),[bom])) is True


def test_nonmake_does_not_need_make_geometry_and_ignores_another_supplier_make():
    row=record(type_='ASSY');other=record();other['Supplier']='different'
    for key in ['L','W','H','thk','Tube Height (Post Forming)','FPDM','Flow type']:row.pop(key)
    values=calculate(row,CalculationContext(Masters(),[row,other]))
    assert values['Gross Wt']==D('1.2') and values['Basic Cost/Pc']==D('9.6')
    assert values['PnF/Pc']==D('1.92') and values['Grand Total Cost/Pc']==D('11.52')
    assert values['_output_sheet_prefix']=='Th_'


def test_zero_quantity_and_unknown_required_geometry_block_instead_of_defaulting():
    row=record();row['Qty/Veh']=0
    with pytest.raises(CalculationError,match='Tube Pitch'):calculate(row,CalculationContext(Masters(),[row]))
    row=record();row.pop('FPDM')
    with pytest.raises(CalculationError,match='absent'):calculate(row,CalculationContext(Masters(),[row]))
