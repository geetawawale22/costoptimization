"""Source-schema output checks; synthetic amounts are not legacy parity proof."""
from decimal import Decimal as D
from io import BytesIO
from openpyxl import load_workbook
from app.calculations.context import RowResult,CalculationRun
from app.calculations.output import build_workbook
from app.calculations.output.layouts import MASTER_MATRIX


def rendered(rows,metadata=None,summaries=None):
    return load_workbook(BytesIO(build_workbook(CalculationRun(rows,metadata=metadata or {},summaries=summaries or {}))),data_only=False)


def named_row(sheet,row=2):
    return {cell.value:sheet.cell(row,cell.column).value for cell in sheet[1] if cell.value is not None}


def test_original_master_matrix_order_and_amount_not_percentage():
    inputs={'PF Code':'001','Project':'P','End Item':'E','Supplier':'s','Customer No':'0007','Desc':'fixture','Commodity':'SOFT','Type':'MAKE','Qty/Veh':2,'Material Code':'M','_variants':{'V':1}}
    values={'RM Cost/Pc':D(10),'Process Cost/Pc':D(20),'Basic Cost/Pc':D(30),'ICC':D('.02'),'ICC COST':D('.6'),'REJ COST':D('.3'),'OH COST':D('.4'),'PROFIT COST':D('.5'),'MARKUP/Pc':D('1.8'),'Total':D('31.8')}
    row=RowResult('one','SOFT','MAKE',inputs,values=values,status='success')
    w=rendered([row]);ws=w['Master_Output'];headers=[c.value for c in ws[1]]
    assert headers[:2]==['PF Code','Project']
    expected=[]
    prefix=True
    for item in MASTER_MATRIX:
        if item['mappings'].get('SOFT') is None:continue
        if not item['bom']:prefix=False
        if prefix and item['header'] not in inputs and item['header']!='HARD / SOFT':continue
        expected.append('Commodity' if item['header']=='HARD / SOFT' and prefix else item['header'])
    assert headers[:-2]==expected
    # The original output contract links the computed amount, never the percentage.
    content=build_workbook(CalculationRun([row]))
    cached=load_workbook(BytesIO(content),data_only=True)
    assert named_row(ws)['Markup Cost/Pc']=="='ST_s'!BP3"
    assert named_row(cached['Master_Output'])['Markup Cost/Pc']==1.8
    assert named_row(cached['Master_Output'])['Markup Cost/Pc']!=float(values['ICC'])
    assert 'ICC Cost' not in named_row(ws)  # Removed by the current authoritative matrix.
    assert named_row(ws)['Customer No']=='0007'
    assert named_row(cached['Master_Output'])['RM Cost']==10
    assert headers[-2:]==['V','Execution status']


def test_sbc_preserves_input_variant_labels_and_nonzero_owner_selection():
    def root(identifier,flag,amount):
        return RowResult(identifier,'','END ITEM',{'End Item':'E','Customer No':identifier,'Desc':'=unsafe','Type':'END ITEM','Qty/Veh':1,'_variants':{'Pack 1':flag}},values={'Pack 1':D(amount)},status='success')
    w=rendered([root('0001',1,0),root('0002',0,9)],metadata={'sbc_layout':{'identity_headers':['End Item','Customer No','Desc'],'variants':[{'key':'Pack 1','label':'Base'}]}})
    ws=w['SBC']
    assert ws['D1'].value=='Pack 1' and ws['D2'].value=='Base'
    assert ws['B3'].value=='0001' and ws['D3'].value==0
    assert ws['B4'].value is None
    assert ws['C3'].value=='=unsafe' and ws['C3'].data_type=='s'
    assert ws['A10'].value=='Reference Commercial'
    assert ws['A20'].value=='Revision'
    end=w['End Item'];assert [c.value for c in end[1]][:6]==['End Item','Supplier','Customer No','Desc','Type','Qty/Veh']
    assert end['G2'].value==0


def test_analysis_legacy_categories_are_distinct_from_generic_summary():
    row=RowResult('one','','PKG',{'Customer No':'1','Qty/Veh':1,'_variants':{'V':1}},values={},status='success')
    w=rendered([row],summaries={'Analysis':[{'variant':'V','values':{'RM cost':D(999)},'status':'success'}], 'legacy_analysis':[{'variant':'V','values':{'RM cost':D(3),'Buy Std':D(7),'ICC Cost':D('.2'),'Total':D(10)},'status':'success'}]})
    ws=w['Analysis'];assert ws['A1'].value=='Cost Driver' and ws['B1'].value=='V'
    assert ws['A4'].value=='Buy Std' and ws['B4'].value==7
    assert ws['A9'].value=='RM cost Soft' and ws['A16'].value=='Total'
    assert ws['B2'].value==3 and ws['B25'].value==.2
    assert w['Calculation Overview']['B2'].value==999
    fallback=rendered([row],summaries={'Analysis':[{'variant':'V','values':{'RM cost':D(999)},'status':'success'}]})
    assert fallback['Analysis']['B2'].value is None
    assert any('Legacy category' in str(c.value) or 'category producers' in str(c.value) for r in fallback['Diagnostics'] for c in r)


def test_pcba_board_and_components_remain_distinct_outputs():
    component_values={'FOB':D(2),'Landed Cost/Pc':D(3),'Process Cost':D(1),'Basic Cost/Pc':D(4),'ICC':D('.1'),'ICC COST':D('.4'),'Grand Total Cost':D('4.4')}
    inputs={'Supplier':'s','End Item':'E','Customer No':'BOARD001','Supplier No':'009','Qty/Veh':1,'Commodity':'PCBA','Type':'ASSY','RM Base':'Period','_variants':{'V':1},'_pcba_quantity_headers':['Pack A','Pack B'],'_pcba_components':[{'Material Code':'M01','Pack A':2,'Pack B':3}]}
    row=RowResult('b','PCBA','ASSY',inputs,values={'Process / Assy Name':'Buy PCBA','RM Cost/Pc':D(6),'Process Cost/Pc':D(2),'Basic Cost/Pc':D('8.8'),'Total Cost/Pc':D('8.8'),'_pcba_component_results':[{'Material Code':'M01','quantity_field':'Pack A','quantity':D(2),'values':component_values}]},status='success')
    content=build_workbook(CalculationRun([row]));w=load_workbook(BytesIO(content),data_only=False)
    cached=load_workbook(BytesIO(content),data_only=True)
    assert 'PCBA' in w and 'PCBA_OP' in w
    board=named_row(w['PCBA']);assert board['Sr']=='009' and board['Process / Assy Name']=='Buy PCBA'
    component=named_row(w['PCBA_OP']);assert component['Customer No']=='BOARD001'
    values=named_row(cached['PCBA_OP'])
    assert component['Material Code']=='M01' and values['Pack A Cost/Veh']==8.8
    assert values['Pack B Cost/Veh']==13.2
    assert component['ICC']==.1 and component['ICC COST']==.4
    columns={cell.value:cell.column_letter for cell in w['PCBA_OP'][1]}
    assert component['Pack A Cost/Veh']=='='+columns['Pack A']+'2*'+columns['Grand Total Cost']+'2'
    assert component['Pack B Cost/Veh']=='='+columns['Pack B']+'2*'+columns['Grand Total Cost']+'2'
    assert not w._external_links


def test_bns_producer_detail_and_purchasing_wrapper_both_export_without_double_master_rows():
    inputs={'Supplier':'s','End Item':'E','Customer No':'B001','Type':'BUY NON STD','Commodity':'HARD',
        'Qty/Veh':1,'_ownership_type':'BUY NON STD','_variants':{'V':1}}
    producer=RowResult('p','HARD','BUY NON STD PRODUCER',inputs,values={'Grand Total Cost/Pc':D(10)},status='success',dependency_only=True)
    consumer=RowResult('c','HARD','BUY NON STD',inputs,values={'Basic Cost/Pc':D(10),'MARKUP/Pc':D(2),'Total/Pc':D(12)},status='success')
    run=CalculationRun([consumer],producer_rows=[producer])
    book=load_workbook(BytesIO(build_workbook(run)))
    assert 'Make_Buy Non Std' in book.sheetnames
    assert 'Buy Non Std' in book.sheetnames
    assert book['Master_Output'].max_row==2
    assert run.as_dict()['producer_rows'][0]['dependency_only'] is True


def test_hard_make_displays_resolved_batch_period_and_location_but_preserves_input():
    from app.calculations.output.workbook import result_value
    row=RowResult('owner','HARD','MAKE',{'RM Base':None,'Sector & Location':'other'},
        values={'RM Base':'first period','Sector & Location':'AD'},status='success')
    assert result_value(row,'RM Base','source','HARD_MAKE')=='first period'
    assert result_value(row,'Sector & Location','source','HARD_MAKE')=='AD'
    assert row.inputs['RM Base'] is None and row.inputs['Sector & Location']=='other'
    assert result_value(row,'Sector & Location','source','GDC')=='other'
