"""Original footer formula strings and independently computed same-run caches.

These are presentation/cache checks, not full original runtime parity. No SQL,
legacy workbook costs, uploaded expressions or arbitrary evaluator is used.
"""
from decimal import Decimal as D
from io import BytesIO
from types import SimpleNamespace as NS
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import pytest
from openpyxl import Workbook, load_workbook

from app.calculations.output.layouts import LAYOUTS
from app.calculations.output.workbook import (analysis_output, end_item_output,
    detail_footer, detail_value, safe_text)
from app.calculations.output.formulas import (persist_numeric_caches,
    restore_trusted_numeric_caches, rounded_equal, numeric_cell, sumproducts)


def encoded(book):
    data=BytesIO();book.save(data)
    return persist_numeric_caches(data.getvalue(),book)


def decoded(content, numbers=False):
    return load_workbook(BytesIO(content),data_only=numbers)


def nonstd(complete=True):
    book=Workbook();ws=book.active;ws.title='Buy Non Std'
    columns=LAYOUTS['BUY_NON_STD']['columns']
    rows=[]
    for index,(commodity,basic,qty,flag) in enumerate([('',D(10),2,1),('PCBA',D(20),3,2)]):
        values={'Basic Cost/Pc':basic,'PCBA Basic Cost/Pc':D(4) if commodity else None,
            'ICC':D('.02'),'REJ':D('.03'),'OH':D('.05'),'PROFIT':D('.1'),
            'ICC COST':basic*D('.02'),'REJ COST':basic*D('.03'),
            'OH COST':basic*D('.05'),'PROFIT COST':basic*D('.1'),
            'MARKUP/Pc':basic*D('.2'),'Total/Pc':basic*D('1.2'),
            'Total Cost/Pc':basic*D('1.2'),'End Item Supplier':'Other Owner',
            'Purchasing Markup Rate':D('.2')}
        row=NS(commodity=commodity,type='BUY NON STD',values=values,
            calculation_status='CALCULATED' if complete else 'PARTIAL',status='success',dependency_only=False,
            inputs={'Supplier':'Purchaser','Customer No':'New '+str(index),'Qty/Veh':qty,'_variants':{'Variant':flag}})
        rows.append(row)
    for column,item in enumerate(columns,1):safe_text(ws.cell(1,column),item[0])
    safe_text(ws.cell(1,31),'Variant')
    for index,row in enumerate(rows,2):
        for column,(_,field,source,_) in enumerate(columns,1):
            safe_text(ws.cell(index,column),detail_value(row,field,source,'BUY_NON_STD'))
        ws.cell(index,31,row.inputs['_variants']['Variant'])
    detail_footer(ws,columns,rows,['Variant'],'BUY_NON_STD',[])
    return book


def test_nonstd_exact_body_footer_offsets_pcba_deduction_and_boolean_cache():
    content=encoded(nonstd());formulas=decoded(content)['Buy Non Std'];values=decoded(content,True)['Buy Non Std']
    assert formulas['AD15'].value=='Extra Information for 3DX  format suitability , already included in above table.'
    assert formulas['AD1'].value=='Total/Pc' and formulas['AE1'].value=='Variant'
    assert formulas['S6'].value=='End Item Supplier' and formulas['T6'].value=='Other Owner'
    assert formulas['S7'].value=='Markup' and D(str(values['T7'].value))==D('.2')
    assert formulas['AE6'].value=='=SUMPRODUCT(S2:S3,H2:H3,AE2:AE3)-AE9'
    assert formulas['AE7'].value=='=SUMPRODUCT(AC2:AC3,H2:H3,AE2:AE3)'
    assert formulas['AE8'].value=='=SUM(AE6:AE7)'
    assert formulas['AE9'].value=='=SUMPRODUCT(T2:T3,H2:H3,AE2:AE3)'
    assert formulas['AE11'].value=='=SUMPRODUCT(AD2:AD3,H2:H3,AE2:AE3)-AE9'
    assert formulas['AE12'].value=='=ROUND(AE8,2)=ROUND(AE11,2)'
    assert [values[cell].value for cell in ['AE6','AE7','AE8','AE9','AE11','AE12']]==[116,28,144,24,144,True]
    for name,column,expected in [('ICC Cost','Y',D('2.8')),('REJ Cost','Z',D('4.2')),('O&H Cost','AA',D(7)),('PROFIT Cost','AB',D(14))]:
        offset=['ICC Cost','REJ Cost','O&H Cost','PROFIT Cost'].index(name)+16
        assert formulas[f'AD{offset}'].value==name
        assert formulas[f'AE{offset}'].value==f'=SUMPRODUCT({column}2:{column}3,H2:H3,AE2:AE3)'
        assert D(str(values[f'AE{offset}'].value))==expected
    assert formulas['Y2'].value=='=S2*U2' and D(str(values['Y2'].value))==D('.2')
    assert formulas['AD3'].value=='=S3+AC3' and values['AD3'].value==24
    with ZipFile(BytesIO(content)) as archive:
        root=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
        cell=next(c for c in root.iter('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c') if c.attrib['r']=='AE12')
        assert cell.attrib['t']=='b' and cell.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v').text=='1'
    restored=decoded(content);cached=decoded(content,True)
    assert restore_trusted_numeric_caches(restored,cached)>=18
    assert decoded(encoded(restored),True)['Buy Non Std']['AE12'].value is True


def test_partial_nonstd_never_synthesizes_footer_zero_or_boolean():
    content=encoded(nonstd(False));ws=decoded(content,True)['Buy Non Std']
    assert all(ws[name].value is None for name in ['AE6','AE7','AE8','AE9','AE11','AE12','AE16','AE19'])


@pytest.mark.parametrize('key',['PKG','FRT'])
def test_logistics_pnf_exact_footer_formula_and_current_run_cache(key):
    columns=LAYOUTS[key]['columns'];book=Workbook();ws=book.active
    row=NS(calculation_status='CALCULATED',commodity='',type=key,dependency_only=False,values={'Basic Cost/Pc':D('2.5')},inputs={'Qty/Veh':3,'_variants':{'Variant':2}})
    for index,(label,field,source,_) in enumerate(columns,1):
        safe_text(ws.cell(1,index),label)
        safe_text(ws.cell(2,index),detail_value(row,field,source,key))
    ws.cell(1,len(columns)+1,'Variant');ws.cell(2,len(columns)+1,2)
    detail_footer(ws,columns,[row],['Variant'],key,[])
    formula=ws.cell(5,len(columns)+1)
    amount=next(i for i,column in enumerate(columns,1) if column[0]=='Basic Cost')
    qty=next(i for i,column in enumerate(columns,1) if column[0]=='Qty/Veh')
    assert formula.value==f'=SUMPRODUCT({ws.cell(2,amount).coordinate}:{ws.cell(2,amount).coordinate},{ws.cell(2,qty).coordinate}:{ws.cell(2,qty).coordinate},{ws.cell(2,len(columns)+1).coordinate}:{ws.cell(2,len(columns)+1).coordinate})'
    assert decoded(encoded(book),True).active[formula.coordinate].value==15


def test_analysis_exact_sum_enditem_reference_round_and_cached_boolean():
    root=NS(type='END ITEM',calculation_status='CALCULATED',status='success',values={'V':D('1.005')},
        inputs={'Customer No':'new','_variants':{'V':1}})
    run=NS(rows=[root],summaries={'legacy_analysis':[{'variant':'V','status':'success',
        'values':{'RM cost':D('1.005'),**{key:D(0) for key in ['Process cost','Buy Std','Buy Non Std','Assy','Special Process','Markup','RM cost Soft','Process cost Soft','Markup Soft','PCBA','Machine Scrap Recovery','Other6','PnF']},'Total':D('1.005')}}]})
    book=Workbook();end_item_output(book,[root],['V']);ws=analysis_output(book,run,['V'],[])
    assert ws['A18'].value is None and ws['A19'].value is None
    assert ws['B16'].value=='=SUM(B2:B15)'
    assert ws['B18'].value=="='End Item'!G5"
    assert ws['B19'].value=='=ROUND(B16,2)=ROUND(B18,2)'
    content=encoded(book);cache=decoded(content,True)
    assert cache['Analysis']['B19'].value is True and D(str(cache['Analysis']['B18'].value))==D('1.005')
    assert restore_trusted_numeric_caches(decoded(content),cache)==4


@pytest.mark.parametrize('left,right,expected', [('1.005','1.004',False),('-1.005','-1.004',False),('1.005','1.006',True)])
def test_boolean_cache_uses_excel_rounding_and_is_not_numeric_operand(left,right,expected):
    book=Workbook();ws=book.active;ws['A1']=D(left);ws['B1']=D(right)
    assert rounded_equal(ws['C1'],ws['A1'],ws['B1'],expected)
    assert numeric_cell(ws['C1']) is None
    assert decoded(encoded(book),True).active['C1'].value is expected
    assert restore_trusted_numeric_caches(decoded(encoded(book)),decoded(encoded(book),True))==1


def test_formula_restore_rejects_tampered_boolean_cache_and_text_in_product():
    content=encoded(nonstd());formulas=decoded(content);cache=decoded(content,True)
    cache['Buy Non Std']['AE12']=False
    with pytest.raises(ValueError,match='outside the trusted writer'):
        restore_trusted_numeric_caches(formulas,cache)
    book=Workbook();ws=book.active;ws.append(['2',3,1])
    assert not sumproducts(ws['D2'],[1],2,3,1,1,D(6),blank_columns=(1,))


def test_final_summary_order_precedes_detail_sheets_and_diagnostics_remain_last():
    from app.calculations.context import CalculationRun, RowResult
    from app.calculations.output.workbook import build_workbook
    child=RowResult('part','', 'BUY STD', {'Supplier':'Other','Customer No':'Changed',
        'End Item':'Root','Qty/Veh':1,'_variants':{'V':1}},
        values={'Basic Cost/Pc':D(5),'Total Cost/Pc':D(5),'Total/Pc':D(5)},status='success')
    owner=RowResult('owner','','END ITEM',{'Supplier':'Other','Customer No':'Root',
        'End Item':'Root','Qty/Veh':1,'_variants':{'V':1}},values={'V':D(5)},status='success')
    book=decoded(build_workbook(CalculationRun([child,owner],summaries={'create_end_item_analysis':True})))
    assert book.sheetnames[:5]==['SBC','End Item','Master_Output','Analysis','End Item Analysis']
    assert book.sheetnames.index('Buy Std')>4
    assert book.sheetnames[-1]=='Diagnostics'


def _linked_nonstd(identifier,supplier,basic,status='success'):
    from app.calculations.context import RowResult
    return RowResult(identifier,'','BUY NON STD',{
        'Customer No':'other-common-part','Supplier':supplier,'Purchasing Supplier':supplier,
        'End Item':'Different owner','Qty/Veh':1,'Type':'BUY NON STD','_variants':{'Alt variant':1}},
        values={'Basic Cost/Pc':D(basic),'MARKUP/Pc':D(basic)*D('.2'),
                'Total Cost/Pc':D(basic)*D('1.2'),'ICC COST':D(basic)*D('.02'),
                'REJ COST':D(0),'OH COST':D(0),'PROFIT COST':D(0)},status=status)


def test_master_output_detail_formulas_bind_exact_owners_when_customer_keys_repeat():
    from app.calculations.context import CalculationRun
    from app.calculations.output import build_workbook
    owners=[_linked_nonstd('z-owner','Supplier Z',10),_linked_nonstd('a-owner','Supplier A',20)]
    data=build_workbook(CalculationRun(owners))
    formulas=decoded(data);cached=decoded(data,True)
    sheet=formulas['Master_Output'];headers={cell.value:cell.column for cell in sheet[1]}
    # Master sorting differs from the actual detail order. Same Customer No must
    # never cause a first-match or supplier-swapped result reference.
    expected=[('Supplier A',3,20),('Supplier Z',2,10)]
    for line,(supplier,source_row,basic) in enumerate(expected,2):
        assert sheet.cell(line,headers['Supplier']).value==supplier
        assert sheet.cell(line,headers['Basic Cost/Pc']).value==f"='Buy Non Std'!S{source_row}"
        assert sheet.cell(line,headers['Markup Cost/Pc']).value==f"='Buy Non Std'!AC{source_row}"
        assert sheet.cell(line,headers['Total Cost/Pc']).value==f"='Buy Non Std'!AD{source_row}"
        assert cached['Master_Output'].cell(line,headers['Basic Cost/Pc']).value==basic
        assert cached['Master_Output'].cell(line,headers['Total Cost/Pc']).value==basic*1.2
    assert restore_trusted_numeric_caches(formulas,cached)>0
    restored=decoded(encoded(formulas),True)
    assert restored['Master_Output'].cell(2,headers['Total Cost/Pc']).value==24


def test_partial_master_output_links_available_components_without_fabricating_final_cost():
    from app.calculations.context import CalculationRun
    from app.calculations.output import build_workbook
    row=_linked_nonstd('partial-owner','Supplier Partial',13,'blocked')
    row.values.pop('Total Cost/Pc');row.values.pop('MARKUP/Pc')
    data=build_workbook(CalculationRun([row]));f=decoded(data);v=decoded(data,True)
    headers={cell.value:cell.column for cell in f['Master_Output'][1]}
    assert f['Master_Output'].cell(2,headers['Basic Cost/Pc']).value=="='Buy Non Std'!S2"
    assert v['Master_Output'].cell(2,headers['Basic Cost/Pc']).value==13
    assert f['Master_Output'].cell(2,headers['Total Cost/Pc']).value is None
    assert v['Master_Output'].cell(2,headers['Total Cost/Pc']).value is None


def test_master_output_duplicate_projection_identity_is_never_selected_arbitrarily():
    from app.calculations.context import CalculationRun
    from app.calculations.output import build_workbook
    data=build_workbook(CalculationRun([_linked_nonstd('same','Supplier A',7),_linked_nonstd('same','Supplier B',11)]))
    book=decoded(data);header={cell.value:cell.column for cell in book['Master_Output'][1]}
    assert book['Master_Output'].cell(2,header['Basic Cost/Pc']).data_type!='f'
    assert book['Master_Output'].cell(3,header['Basic Cost/Pc']).data_type!='f'
    assert any('duplicate source-record identity' in str(cell.value) for row in book['Diagnostics'] for cell in row)


def test_master_output_requires_unique_named_source_column_and_equal_computed_value():
    from app.calculations.context import CalculationRun
    from app.calculations.output.workbook import master_output
    row=_linked_nonstd('independent','Supplier A',7)
    book=Workbook();book.remove(book.active);source=book.create_sheet('Different detail')
    source['A2']=99;source['B2']=7;source['C2']=7
    columns=[('Basic Cost/Pc','Basic Cost/Pc','result','General')]
    sheet=master_output(book,CalculationRun([row]),['Alt variant'],[],{row.record_id:[(source,2,columns)]})
    header={cell.value:cell.column for cell in sheet[1]};cell=sheet.cell(2,header['Basic Cost/Pc'])
    assert cell.value==7 and cell.data_type!='f'  # Unequal same-run source is not linked.
    book.remove(sheet)
    duplicates=[('Basic Cost/Pc','x','result','General'),('Basic Cost/Pc','y','result','General')]
    sheet=master_output(book,CalculationRun([row]),['Alt variant'],[],{row.record_id:[(source,2,duplicates)]})
    header={cell.value:cell.column for cell in sheet[1]}
    assert sheet.cell(2,header['Basic Cost/Pc']).data_type!='f'
