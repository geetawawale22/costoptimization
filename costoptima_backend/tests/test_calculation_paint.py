"""Primary merge19362-19550 Paint arithmetic with isolated named masters."""
from decimal import Decimal as D
from io import BytesIO
from types import SimpleNamespace
import openpyxl
import pytest
from app.calculations.context import CalculationContext,RowResult,CalculationRun
from app.calculations.common import CalculationError
from app.calculations.masters import FieldRef,MasterRecord,MasterField
from app.calculations.merging.paint import calculate_paint_group
from app.calculations.commodities.hard.special_process import calculate
from app.calculations.input import parse_workbook
from app.calculations.output import build_workbook


def named_row(values,groups=None):
    fields=tuple(MasterField('field_'+str(i),name,(groups or {}).get(name),'TEXT',True) for i,name in enumerate(values))
    return MasterRecord('fixture','fixture','fixture',1,{field.column:value for field,value in zip(fields,values.values())},fields)


class Masters:
    def records(self,code):
        assert code=='special_rm'
        return (named_row({'Material Code':None,'Landed Price/ltr (June 2022)':'JUN#22'}),)
    def record(self,code,keys,**kwargs):
        if code=='norms_special_process_norms':return SimpleNamespace(value=lambda _:1)
        if code=='norms_buy_non_std':return SimpleNamespace(value=lambda _:0)
        assert code=='special_norms'
        group=next(iter(keys)).group
        values=({'Hanger Cost':40,'ICC':'.1','REJ ON INJ MOLDED PART':'.2','REJ ON MATERIAL & PROCESS':'.1','OVERALL REJECTION':'.05','OVERHEAD':'.1','PROFIT':'.1'} if group=='Rates' else
            {'ICC':'Paint RM Cost','REJ ON INJ MOLDED PART':'Make Part Cost','REJ ON MATERIAL & PROCESS':'Basic Cost','OVERALL REJECTION':'Basic Cost','OVERHEAD':'Paint Process Cost','PROFIT':'Basic Cost','PnF':'.05'})
        return named_row(values,{name:group for name in values})
    def lookup(self,code,keys,field,**kwargs):
        if code=='special_rm':
            if field=='Product Descrption':return 'Verified paint '+keys['material_code']
            assert isinstance(field,FieldRef) and field.name=='Landed Price/ltr (June 2022)'
            return {'A':D(10),'B':D(20)}[keys['material_code']]
        if code=='norms_buy_non_std':return D('.1') if field=='icc' else D(0)
        raise AssertionError(code)


def fixture(mode='Buy Paint'):
    owner={'_record_id':'owner','Supplier':'final supplier','End Item':'E','Customer No':'painted','Commodity':'HARD','Type':'SPECIAL PROCESS','Qty/Veh':2,'Special Process':mode}
    anchor={'_record_id':'paint:1','Sr':'001','Supplier':'tier 2','End Item':'E','Customer No':'painted','Make Part no':'unpainted','RM Base':'JUN#22','Material Code':'A','Consumption (in Litre)':2,'No of parts / Jig':4,'Painting Area (in SQM)':D('.4'),'Make Part Cost':99999,'Consumable / Masking Cost':99999}
    owner['_paint_components']=[anchor,{**anchor,'_record_id':'paint:2','Sr':None,'Material Code':'B','Consumption (in Litre)':3,'No of parts / Jig':None}]
    producer_record={'_record_id':'make','Customer No':'unpainted','End Item':'E','Commodity':'HARD','Type':'MAKE'}
    context=CalculationContext(Masters(),[producer_record,owner])
    context.result_records=[RowResult('make','HARD','MAKE',producer_record,{'Basic Cost/Pc':D(100)},status='success')]
    return owner,context


def test_paint_group_rates_quantities_and_named_applicability():
    owner,context=fixture();paint=calculate_paint_group(owner,context)
    assert paint['Paint RM Cost']==80 and paint['Paint Process Cost']==10
    assert paint['ICC Cost']==8 and paint['REJ ON INJ MOLDED PART Cost']==20
    assert paint['REJ ON MATERIAL & PROCESS Cost']==9 and paint['OVERALL REJECTION Cost']==D('4.5')
    assert paint['OVERHEAD Cost']==1 and paint['PROFIT Cost']==9
    assert paint['Paint Markup']==D('51.5') and paint['Paint PnF']==D('7.075')
    assert paint['Paint Total Cost/Pc']==D('148.575')
    assert paint['Consumable / Masking Cost']==0
    assert calculate_paint_group(owner,context)==paint


def test_buy_paint_tier2_and_outer_markup_attributed_once():
    owner,context=fixture();values=calculate(owner,context)
    assert values['Tier 2 Markup+PnF']==D('58.575')
    assert values['Basic Cost/Pc']==D('148.575')
    assert values['ICC COST']==D('14.8575')
    assert values['Grand Total Cost/Pc']==D('163.4325')
    assert values['Grand Total Cost/Veh']==D('326.8650')


def test_make_paint_uses_six_amounts_without_tier2_pnf_duplication():
    owner,context=fixture('Make Paint');values=calculate(owner,context)
    assert values['Tier 2 Markup+PnF']==0 and values['Basic Cost/Pc']==90
    assert values['REJ COST']==D('33.5')
    assert values['Grand Total Cost/Pc']==D('141.5')


def test_paint_blocks_unverified_period_owner_and_producer():
    owner,context=fixture();owner['_paint_components'][1]['End Item']='another'
    with pytest.raises(CalculationError,match='ownership'):calculate_paint_group(owner,context)
    owner,context=fixture();
    for line in owner['_paint_components']:line['RM Base']='unknown'
    with pytest.raises(CalculationError,match='period'):calculate_paint_group(owner,context)
    owner,context=fixture();context.result_records=[]
    with pytest.raises(CalculationError):calculate_paint_group(owner,context)


def test_original_paint_input_headers_continuation_and_independent_supplier():
    book=openpyxl.Workbook();bom=book.active;bom.title='BOM'
    bom.append(['Supplier','End Item','Customer No','Commodity','Type','Qty/Veh','Special Process'])
    bom.append(['final','E','painted','HARD','SPECIAL PROCESS',1,'Buy Paint'])
    paint=book.create_sheet('Paint')
    paint.append([None,None,None,None,None,None,None,None,'RM Base','JUN#22'])
    paint.append(['Sr','Supplier','End Item','Make Part no','Customer No','Material Code','Consumption (in Litre)','No of parts / Jig'])
    paint.append([1,'tier2','E','make','painted','A',2,4]);paint['A3'].number_format='000'
    paint.append([None,'tier2',None,None,'painted','B',3,None])
    stream=BytesIO();book.save(stream)
    parsed=parse_workbook(stream.getvalue());owner=parsed.records[0]
    assert len(owner['_paint_components'])==2
    assert owner['_paint_components'][0]['Sr']=='001'
    assert owner['_paint_components'][1]['End Item']=='E'
    assert owner['_paint_components'][1]['RM Base']=='JUN#22'
    assert owner['Supplier']=='final' and owner['_paint_components'][0]['Supplier']=='tier2'
    assert not any(i['severity']=='error' for i in parsed.issues)


def test_final_purchasing_and_thermal_costs_reach_export_cells():
    purchase=RowResult('p','','BUY NON STD',{'Supplier':'s','Customer No':'001','Qty/Veh':1},
        {'Basic Cost/Pc':D(10),'ICC':D('.1'),'ICC COST':D(1),'MARKUP/Pc':D(1),'Total Cost/Pc':D(11)},status='success')
    thermal=RowResult('t','THERMAL','MAKE',{'Supplier':'tube','Qty/Veh':1,'Flow type':'Crossflow'},
        {'_thermal_supplier_path':'make','Flowtype':'Crossflow','Rate Kg':D(100),'Tube Pitch':D(25),'Fin Length':D(50),'RM Cost/Pc':D(3),'Process Cost/Pc':D(2),'Basic Cost/Pc':D(5),'MARKUP/Pc':D(1),'Total Cost/Pc':D(6),'Total Cost/Veh':D(6)},status='success')
    book=openpyxl.load_workbook(BytesIO(build_workbook(CalculationRun([purchase,thermal]))))
    ws=book['Buy Non Std'];columns={c.value:c.column for c in ws[1]}
    assert ws.cell(2,columns['Basic Cost/Pc']).value==10
    assert ws.cell(2,columns['ICC Cost']).value==1 and ws.cell(2,columns['Total/Pc']).value==11
    ws=book['make_tube'];columns={c.value:c.column for c in ws[2]}
    assert ws.cell(3,columns['Tube Pitch']).value==25 and ws.cell(3,columns['Fin Length']).value==50
    assert ws.cell(3,columns['BASIC Cost/Pc']).value==5 and ws.cell(3,columns['Total Cost']).value==6
    assert 'PnF' not in columns
    assert ws.cell(3,columns['Rate Kg']).value==100
    assert ws.cell(3,columns['Flowtype']).value=='Crossflow'


def test_paint_component_sheet_preserves_group_rows_and_anchor_totals():
    owner,context=fixture();values=calculate(owner,context)
    result=RowResult('owner','HARD','SPECIAL PROCESS',owner,values,status='success')
    book=openpyxl.load_workbook(BytesIO(build_workbook(CalculationRun([result]))))
    ws=book['Paint'];cols={c.value:c.column for c in ws[1]}
    assert ws.max_row==3
    assert ws.cell(2,cols['RM cost / Pc']).value==20 and ws.cell(3,cols['RM cost / Pc']).value==60
    assert ws.cell(2,cols['Material']).value=='Verified paint A'
    assert ws.cell(2,cols['Basic Cost']).value==90 and ws.cell(3,cols['Basic Cost']).value is None
    assert ws.cell(2,cols['Pnf']).value==7.075
    assert ws.cell(2,cols['Total Cost/Pc']).value==148.575
    final=book['Special Process'];columns={c.value:c.column for c in final[1]}
    assert final.cell(2,columns['Tier 2 Markup+PnF']).value==58.575
    assert final.cell(2,columns['Total cost /PC']).value==163.4325
