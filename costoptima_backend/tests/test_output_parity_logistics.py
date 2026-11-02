from types import SimpleNamespace
from decimal import Decimal
import pytest
from openpyxl import Workbook
from app.calculations.output.workbook import detail_footer

@pytest.mark.parametrize('key',['PKG','FRT'])
@pytest.mark.parametrize('complete',[True,False])
def test_pnf_original_variant_quantity_summary(key,complete):
    ws=Workbook().active
    rows=[SimpleNamespace(calculation_status='CALCULATED' if complete else 'PARTIAL',values={'Basic Cost/Pc':Decimal('2.5')},inputs={'Qty/Veh':3,'_variants':{'New variant':2}})]
    columns=[('Qty/Veh','Qty/Veh','input','General'),('Basic Cost','Basic Cost/Pc','result','General'),('Markup','MARKUP/Pc','result','General')]
    diag=[]
    detail_footer(ws,columns,rows,['New variant'],key,diag)
    assert ws.cell(5,3).value=='Pnf'
    assert ws.cell(5,4).value==(Decimal('15') if complete else None)
    assert bool(diag)==(not complete)

@pytest.mark.parametrize('complete',[True,False])
def test_nonstd_footer_keeps_cost_breakup_and_missing_totals(complete):
    ws=Workbook().active
    values={'Basic Cost/Pc':Decimal('10'),'MARKUP/Pc':Decimal('2'),'Total/Pc':Decimal('12'),'ICC COST':Decimal('.2'),'REJ COST':Decimal('.3'),'OH COST':Decimal('.5'),'PROFIT COST':Decimal('1'),'End Item Supplier':'Independent owner'}
    row=SimpleNamespace(calculation_status='CALCULATED' if complete else 'PARTIAL',commodity='',values=values,inputs={'Qty/Veh':3,'_variants':{'Variant':2}})
    columns=[('field'+str(n),'field'+str(n),'input','General') for n in range(27)]
    detail_footer(ws,columns,[row],['Variant'],'BUY_NON_STD',[])
    assert ws.cell(5,19).value=='End Item Supplier'
    assert ws.cell(5,20).value=='Independent owner'
    assert ws.cell(7,28).value==(Decimal('72') if complete else None)
    assert (Decimal(str(ws.cell(15,28).value)) if complete else ws.cell(15,28).value)==(Decimal('1.2') if complete else None)
    assert ws.cell(11,28).value==(True if complete else None)

@pytest.mark.parametrize('match',[True,False,None])
def test_analysis_boolean_is_cost_reconciliation_not_bom_flag(match):
    from app.calculations.output.workbook import analysis_output
    root=SimpleNamespace(type='END ITEM',calculation_status='CALCULATED' if match is not None else 'BLOCKED',values={'Variant':Decimal('12')})
    total=Decimal('12') if match else Decimal('11')
    run=SimpleNamespace(rows=[root],summaries={'legacy_analysis':[{'variant':'Variant','values':{'Total':total},'status':'success'}]})
    ws=analysis_output(Workbook(),run,['Variant'],[])
    assert ws.cell(19,2).value is match
    assert ws.cell(18,2).value==(Decimal('12') if match is not None else None)

@pytest.mark.parametrize('left,right', [('1.005','1.004'),('-1.005','-1.004')])
def test_analysis_reconciliation_uses_excel_round_half_away_from_zero(left,right):
    from app.calculations.output.workbook import analysis_output
    root=SimpleNamespace(type='END ITEM',calculation_status='CALCULATED',values={'Variant':Decimal(right)})
    run=SimpleNamespace(rows=[root],summaries={'legacy_analysis':[{'variant':'Variant','values':{'Total':Decimal(left)},'status':'success'}]})
    ws=analysis_output(Workbook(),run,['Variant'],[])
    assert ws.cell(19,2).value is False


def test_nonstd_original_body_column_positions():
    from app.calculations.output.layouts import LAYOUTS
    labels=[c[0] for c in LAYOUTS['BUY_NON_STD']['columns']]
    assert len(labels)==30
    assert labels[16:20]==['Part Wt','Gross Wt','Basic Cost/Pc','PCBA Basic Cost/Pc']
    assert labels[24:30]==['ICC Cost','REJ Cost','O&H Cost','PROFIT Cost','Markup','Total/Pc']
