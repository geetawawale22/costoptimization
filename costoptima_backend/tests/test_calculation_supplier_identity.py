"""Two supplier roles, unique sourcing and owner-safe dependency regressions."""
from decimal import Decimal as D
from types import SimpleNamespace
from io import BytesIO
from pathlib import Path
import pytest
from openpyxl import Workbook
from app.calculations.input import parse_workbook
from app.calculations.engine import execute
from app.calculations.context import CalculationContext,RowResult
from app.calculations.common import CalculationError
from app.calculations.merging.dependencies import plan
from test_calculation_engine import zero_norm_master

HEADERS=['PF Code','Project','0 / 1 / 2 / 3','Base','Supplier','End Item','Customer No','Commodity','Type','Qty/Veh','Material Code','RM Base','Part Wt','Cycle Time','Cavity','m/c Tonnage']
def workbook(type_='BUY NON STD',*,suppliers=('costing',),formula=False,second_owner=False,detail_commodity='HARD'):
    w=Workbook();b=w.active;b.title='BOM';b.append(HEADERS)
    root=['P','project',0,1,'owner','root','root','','END ITEM',1]
    component=['P','project',1,1,'owner','root','part','HARD',type_,1,'fixture','fixture']
    b.append(root);b.append(component)
    if second_owner:b.append(['P','project',1,1,'other owner','root','part','HARD',type_,1,'fixture','fixture'])
    h=w.create_sheet('Hard');h.append(HEADERS)
    for s in suppliers:h.append(['P','project',1,1,s,'root','part',detail_commodity,type_,1,'fixture','fixture','=1+1' if formula else .045,480,4,'Rubber Molding 250T'])
    out=BytesIO();w.save(out);return out.getvalue()

def masters():
    master=zero_norm_master({'Applicable Process':'Blow Molding','efficiency':'.8','factor':'.06','icc':'0','rej':'0','handling':'0','profit':'0','markup':'0'})
    master.records=lambda code:[SimpleNamespace(value=lambda f:{'criteria':'rest all','net_wt':None}.get(f),decimal=lambda f:D('1.2'))] if code=='norms_gross_wt' else []
    return master

@pytest.mark.parametrize('type_',['MAKE','BUY NON STD'])
def test_costing_view_keeps_final_bom_supplier_and_single_cost_attribution(type_):
    master=masters();run=execute(workbook(type_),master,development=True)
    component=next(r for r in run.rows if r.inputs['Customer No']=='part');root=next(r for r in run.rows if r.type=='END ITEM')
    assert component.status=='success',component.issues
    assert component.inputs['Supplier']=='owner'
    assert component.inputs['Costing Supplier']=='costing' and component.inputs['Purchasing Supplier']=='owner'
    assert component.inputs['Part Wt']==.045
    assert all(keys=={'supplier':'costing'} for code,keys,field in master.calls if code=='norms_make')
    assert root.status=='success'
    assert root.values['Base']==component.values['Grand Total Cost/Pc']
    assert len(run.producer_rows)==(type_=='BUY NON STD')
    if run.producer_rows:
        producer=run.producer_rows[0]
        assert producer.inputs['Supplier']=='costing' and producer.inputs['Purchasing Supplier']=='owner'
        assert producer.dependency_only and producer.values['Gross Wt']==D('.054')
        assert [p['producer_record_id'] for p in component.provenance if 'producer_record_id' in p]==[producer.record_id]

@pytest.mark.parametrize('kwargs',[{'suppliers':('one','two')},{'second_owner':True},{'suppliers':('',)},{'detail_commodity':'SOFT'}])
def test_unverified_costing_identity_is_rejected(kwargs):
    parsed=parse_workbook(workbook(**kwargs))
    assert any(i['code']=='ambiguous_costing_supplier' and i['severity']=='error' for i in parsed.issues)
    run=execute(workbook(**kwargs),masters(),development=True)
    assert not any(r.status=='success' for r in run.rows if r.commodity=='HARD')
    assert next(r for r in run.rows if r.type=='END ITEM').status=='blocked'


def test_uncached_matched_detail_formula_blocks_owner_not_zero_cost():
    run=execute(workbook(formula=True),masters(),development=True)
    r=next(r for r in run.rows if r.commodity=='HARD')
    assert r.status=='blocked' and any(issue['code']=='input_validation_failed' for issue in r.issues)
    assert any(issue['code']=='detail_validation_failed' for issue in r.issues)
    assert any(i['code']=='detail_validation_failed' for i in run.issues)


def test_supplier_validation_still_rejects_different_end_item_or_wrong_owner_producer():
    record={'_record_id':'owner1','Customer No':'part','End Item':'E','Supplier':'purchasing'}
    context=CalculationContext(masters(),[])
    context.result_records=[RowResult('owner2:producer','HARD','BUY NON STD PRODUCER',{'_record_id':'owner2','Customer No':'part','End Item':'E','Supplier':'costing'},values={'Total':D(10)},status='success',dependency_only=True)]
    with pytest.raises(CalculationError,match='absent'):
        context.require_result(record,'HARD','BUY NON STD PRODUCER','Total',supplier='costing',owner_record_id='owner1')


def test_invalid_paint_dependency_does_not_block_independent_valid_package():
    records=[{'_record_id':'paint','Commodity':'HARD','Type':'SPECIAL PROCESS','End Item':'E','Make Part no':'missing'},
             {'_record_id':'root','Commodity':'','Type':'END ITEM','End Item':'E'},
             {'_record_id':'pkg','Commodity':'','Type':'PKG','End Item':'other'}]
    order,edges=plan(records,isolate=True)
    assert order[0]=='pkg'
    assert records[0]['_dependency_blocked'] and records[1]['_dependency_blocked']
    assert not records[2].get('_dependency_blocked')
    with pytest.raises(CalculationError):plan([dict(r) for r in records])

@pytest.mark.parametrize('name,key,weight',[('S302 SBC CSD','S302_CSD_010',.045),('S302 SBC CSD','S302_CSD_011',.010),('Z115 AIS 185HP','0313ABH00301N_012',.001)])
def test_actual_supplied_hard_weight_is_attached_without_changing_owner(name,key,weight):
    folder=Path(r'C:\Users\Admin\Downloads\CostOptima_PCM_updated (1)\CostOptima\InputFiles')
    files=[p for p in folder.rglob('*.xlsx') if p.name.startswith(name)]
    if not files:pytest.skip('Original supplied reference unavailable')
    assert len(files)==1
    parsed=parse_workbook(files[0]);rows=[r for r in parsed.records if r.get('Customer No')==key]
    assert len(rows)==1
    row=rows[0]
    assert row['Supplier']=='Mahle_AIS' and row['Costing Supplier']=='Others Air Filter'
    assert row['Part Wt']==weight and len(row['_details'])==1
    assert not any(i['severity']=='error' and i['sheet']==row['_sheet'] and i['row']==row['_row'] for i in parsed.issues)


def test_uploaded_supplier_metadata_cannot_choose_costing_identity():
    w=Workbook();b=w.active;b.title='BOM'
    b.append(['Supplier','End Item','Customer No','Commodity','Type','Qty/Veh','_costing_supplier','Costing Supplier','Purchasing Supplier'])
    b.append(['owner','E','part','HARD','MAKE',1,'unverified','unverified','unverified'])
    out=BytesIO();w.save(out)
    row=parse_workbook(out.getvalue()).records[0]
    assert row['_costing_supplier']==row['Costing Supplier']=='owner'
    assert row['Supplier']==row['Purchasing Supplier']=='owner'


def test_bad_paint_component_blocks_its_group_without_invalidating_another():
    w=Workbook();b=w.active;b.title='BOM'
    b.append(['Supplier','End Item','Customer No','Commodity','Type','Qty/Veh','Special Process'])
    for name in ('bad','good'):b.append(['owner','E',name,'HARD','SPECIAL PROCESS',1,'Buy Paint'])
    p=w.create_sheet('Paint')
    p.append([None,None,None,None,None,None,None,None,'RM Base','JUN#22'])
    p.append(['Sr','Supplier','End Item','Make Part no','Customer No','Material Code','Consumption (in Litre)','No of parts / Jig'])
    p.append([1,'tier2','E','make1','bad','A','=1+1',4])
    p.append([2,'tier2','E','make2','good','A',2,4])
    out=BytesIO();w.save(out);parsed=parse_workbook(out.getvalue())
    failed=[i for i in parsed.issues if i['code']=='paint_component_validation_failed']
    assert len(failed)==1 and failed[0]['row']==2
    assert len(parsed.records[1]['_paint_components'])==1
    assert not any(i['severity']=='error' and i['sheet']=='BOM' and i['row']==3 for i in parsed.issues)


def test_guarded_workflow_keeps_independent_owner_running_after_dependency_failure():
    w=Workbook();b=w.active;b.title='BOM'
    b.append(['0 / 1 / 2 / 3','Base','Supplier','End Item','Customer No','Commodity','Type','Qty/Veh','Special Process','Make Part no'])
    b.append([0,1,'owner','bad E','bad root','','END ITEM',1])
    b.append([1,1,'owner','bad E','paint','HARD','SPECIAL PROCESS',1,'Make Paint','absent make'])
    b.append([0,1,'other','good E','good root','','END ITEM',1])
    b.append([1,1,'other','good E','package','','PKG',1])
    out=BytesIO();w.save(out);run=execute(out.getvalue(),masters(),development=True)
    results={r.inputs['Customer No']:r for r in run.rows}
    assert results['paint'].status==results['bad root'].status=='blocked'
    assert results['package'].status==results['good root'].status=='success'
    assert results['good root'].values['Base']==0  # PKG source initializes zero
    assert not results['bad root'].values
    assert any(i['code']=='missing_or_duplicate_paint_producer' for i in run.issues)
