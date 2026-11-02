"""Trusted original footer formulas retain same-run values and reject input code.

Primary evidence: PCM3937-3946,6484-6494,9259-9266,15006-15026,
17556-17630(SBC) and17645-17722(End Item). These tests verify that narrow
representation contract; they do not assert full original-workbook parity.
"""
from decimal import Decimal as D
from io import BytesIO

import pytest
from openpyxl import Workbook, load_workbook

from app.calculations.context import CalculationRun, RowResult
from app.calculations.output import build_workbook
from app.calculations.output.formulas import persist_numeric_caches, sum_range, sumproducts


def decoded(content, data_only=False):
    return load_workbook(BytesIO(content), data_only=data_only)


def detail(commodity, type_='MAKE', qty=D(2), flag=D(1), **overrides):
    inputs={'Supplier':'Supplier','End Item':'Owner','Customer No':'0001',
            'Type':type_,'Commodity':commodity,'Qty/Veh':qty,'_variants':{'Variant':flag},
            'Desc':'=WEBSERVICE("https://untrusted.invalid")'}
    values={'RM Cost/Pc':D(10),'Process Cost/Pc':D(3),'MARKUP/Pc':D(2),
            'ICC COST':D('.1'),'REJ COST':D('.2'),'OH COST':D('.3'),
            'PROFIT COST':D('.4'),'Handling COST':D('.5'),'Dies COST':D('.6'),
            'Total':D(15),'Total Cost/Pc':D(15),**overrides}
    return RowResult('detail',commodity,type_,inputs,values=values,status='success')


@pytest.mark.parametrize('commodity,name,header,offset',[
    ('HARD','Make',1,5),('SOFT','ST_Supplier',2,6),
    ('STAMPING','SM_Supplier',2,6),('GDC','GDC_Output_Supplier',2,7),
    ('PDC','PDC_Output_Supplier',1,7)])
def test_source_footer_sumproduct_has_formula_and_same_run_numeric_cache(commodity,name,header,offset):
    row=detail(commodity)
    content=build_workbook(CalculationRun([row]))
    ws=decoded(content)[name];cached=decoded(content,True)[name]
    headings={c.value:c.column for c in ws[header] if c.value is not None}
    target=ws.cell(1+offset,headings['Variant'])
    assert target.data_type=='f' and target.value.startswith('=SUMPRODUCT(')
    assert cached[target.coordinate].value==20
    assert ws.cell(header+1,headings['Desc']).data_type=='s'
    assert ws.cell(header+1,headings['Desc']).value.startswith('=WEBSERVICE')
    # Numeric equality also checks we selected the amount column, not rate row 1.
    for other in ('ICC Cost','REJ Cost','O&H Cost','PROFIT Cost'):
        label_cells=[c for line in ws for c in line if c.value==other]
        if label_cells:
            cell=ws.cell(label_cells[-1].row,headings['Variant'])
            assert cell.data_type=='f'
            assert cached[cell.coordinate].value is not None


def test_soft_total_is_sum_of_cached_subtotals_not_sum_of_formula_text():
    content=build_workbook(CalculationRun([detail('SOFT')]))
    formulas=decoded(content)['ST_Supplier'];numbers=decoded(content,True)['ST_Supplier']
    variant=next(c.column for c in formulas[2] if c.value=='Variant')
    total=formulas.cell(10,variant)
    assert total.value==f'=SUM({formulas.cell(7,variant).coordinate}:{formulas.cell(9,variant).coordinate})'
    assert numbers[total.coordinate].value==30
    # SOFT overhead summary is the sum of two aligned, trusted products.
    assert formulas.cell(19,variant).value.count('SUMPRODUCT(')==2
    assert numbers.cell(19,variant).value==pytest.approx(1.6)


def test_end_item_and_sbc_sum_footers_cache_values_and_preserve_identifiers():
    roots=[RowResult(str(i),'','END ITEM',{'End Item':'Owner','Customer No':'00'+str(i),
        'Type':'END ITEM','Qty/Veh':1,'_variants':{'Variant':1}},values={'Variant':D(value)},status='success')
        for i,value in enumerate(['0','2.125'],1)]
    content=build_workbook(CalculationRun(roots))
    formulas=decoded(content);numbers=decoded(content,True)
    assert formulas['End Item']['G6'].value=='=SUM(G2:G3)'
    assert numbers['End Item']['G6'].value==2.125
    assert formulas['SBC']['D7'].value=='=SUM(D3:D4)'
    assert numbers['SBC']['D7'].value==2.125
    assert numbers['SBC']['B3'].value=='001'


def test_blocked_end_item_never_produces_zero_or_a_success_formula():
    row=RowResult('root','','END ITEM',{'End Item':'Owner','Customer No':'1','_variants':{'V':1}},status='blocked')
    content=build_workbook(CalculationRun([row]))
    assert decoded(content)['End Item']['G5'].value is None
    assert decoded(content,True)['SBC']['D6'].value is None


def test_numeric_looking_input_text_stays_text_and_prevents_wrong_sumproduct():
    content=build_workbook(CalculationRun([detail('HARD',qty='2')]))
    ws=decoded(content)['Make'];numbers=decoded(content,True)['Make']
    columns={c.value:c.column for c in ws[1] if c.value is not None}
    assert ws.cell(2,columns['Qty/Veh']).data_type=='s'
    assert ws.cell(6,columns['Variant']).data_type!='f'
    assert numbers.cell(6,columns['Variant']).value==20


def test_trusted_helper_rejects_strings_boolean_operands_and_self_reference():
    book=Workbook();ws=book.active
    ws.append(['2',True,3]);ws.append([D(2),D(1),D(3)])
    assert not sum_range(ws['A3'],1,1,2,D(4))
    assert not sum_range(ws['B3'],2,1,2,D(2))
    assert not sumproducts(ws['C3'],[1],2,3,1,2,D(12))
    assert not sum_range(ws['A2'],1,1,2,D(4))
    assert not sum_range(ws['C3'],'A1:ZZ99',1,2,D(6))
    assert not sum_range(ws['C3'],3,1,2,D(7))
    assert sum_range(ws['C3'],3,1,2,D(6))
    saved=BytesIO();book.save(saved)
    content=persist_numeric_caches(saved.getvalue(),book)
    assert decoded(content).active['C3'].value=='=SUM(C1:C2)'
    assert decoded(content,True).active['C3'].value==6


def test_formula_cache_rejects_post_registration_expression_changes():
    book=Workbook();ws=book.active;ws['A1']=2;ws['A2']=3
    assert sum_range(ws['A3'],1,1,2,D(5))
    ws['A3']='=WEBSERVICE("https://untrusted.invalid")'
    saved=BytesIO();book.save(saved)
    with pytest.raises(ValueError,match='Trusted summary formula differs'):
        persist_numeric_caches(saved.getvalue(),book)


@pytest.mark.parametrize('density',[None,'7.85',D(0),D(-1),True,D('NaN')])
def test_editable_geometry_requires_positive_numeric_database_density(density):
    from app.calculations.output.formulas import editable_geometry_block
    book=Workbook();ws=book.active
    assert not editable_geometry_block(ws,density)
    assert ws['H15'].value is None


def test_editable_geometry_keeps_supplied_weight_separate_and_blank_dimension_zero_caches():
    from app.calculations.output.formulas import editable_geometry_block
    book=Workbook();ws=book.active;ws['H4']=D('1.234')
    assert editable_geometry_block(ws,D('7.85'))
    assert ws['H15'].value=='=IF(F15,3.14*((F15/2)^2-(G15/2)^2)*E15*7.85/1000000,C15*D15*E15*7.85/1000000)'
    assert ws['H21'].value=='=SUM(H15:H20)' and ws['H26'].value=='=SUM(H22:H25)'
    assert all(ws.cell(r,c).value is None for r in [*range(15,21),*range(22,26)] for c in range(3,8))
    output=BytesIO();book.save(output)
    content=persist_numeric_caches(output.getvalue(),book)
    cached=decoded(content,True).active
    assert cached['H4'].value==1.234
    assert all(cached.cell(r,8).value==0 for r in range(15,27))


def test_editable_geometry_refuses_to_assign_false_cache_over_existing_dimensions():
    from app.calculations.output.formulas import editable_geometry_block
    book=Workbook();ws=book.active;ws['C15']=25
    assert not editable_geometry_block(ws,D('7.85'))
    assert ws['C15'].value==25 and ws['H15'].value is None


@pytest.mark.parametrize('operation,columns,expected,formula',[
    ('*',[1,2,3],D(30),'=A2*B2*C2'),
    ('+',[1,2,3],D(10),'=A2+B2+C2')])
def test_same_row_arithmetic_preserves_numeric_cache_and_can_be_restored(operation,columns,expected,formula):
    from app.calculations.output.formulas import row_arithmetic, restore_trusted_numeric_caches
    book=Workbook();ws=book.active;ws.append(['A','B','C','Result']);ws.append([D(2),D(3),D(5)])
    assert row_arithmetic(ws['D2'],columns,operation,expected)
    assert ws['D2'].value==formula
    output=BytesIO();book.save(output);content=persist_numeric_caches(output.getvalue(),book)
    assert decoded(content,True).active['D2'].value==expected
    restored=decoded(content);cached=decoded(content,True)
    assert restore_trusted_numeric_caches(restored,cached)==1
    again=BytesIO();restored.save(again)
    assert decoded(persist_numeric_caches(again.getvalue(),restored),True).active['D2'].value==expected


@pytest.mark.parametrize('columns,operation,value',[
    ([1,2],'-',D(5)),([1,1],'+',D(4)),([1,4],'+',D(2)),
    ([1],'+',D(2)),(['A2',2],'+',D(5)),([1,2],'+',D(100)),
    ([0,2],'+',D(5)),([True,2],'+',D(5))])
def test_same_row_arithmetic_refuses_untrusted_identifiers_or_wrong_values(columns,operation,value):
    from app.calculations.output.formulas import row_arithmetic
    book=Workbook();ws=book.active;ws.append([]);ws.append([D(2),D(3)])
    assert not row_arithmetic(ws['D2'],columns,operation,value)
    assert ws['D2'].data_type!='f'


def test_horizontal_sum_caches_and_dependency_on_prior_formula_are_verified():
    from app.calculations.output.formulas import row_arithmetic, sum_row, restore_trusted_numeric_caches
    book=Workbook();ws=book.active;ws.append([]);ws.append([D(2),D(3),D(5)])
    assert sum_row(ws['D2'],1,3,D(10))
    assert row_arithmetic(ws['E2'],[4,1],'*',D(20))
    assert ws['D2'].value=='=SUM(A2:C2)'
    output=BytesIO();book.save(output);content=persist_numeric_caches(output.getvalue(),book)
    restored=decoded(content);cached=decoded(content,True)
    assert cached.active['E2'].value==20
    assert restore_trusted_numeric_caches(restored,cached)==2
    cached.active['A2']=99
    with pytest.raises(ValueError,match='outside the trusted writer'):
        restore_trusted_numeric_caches(decoded(content),cached)


def test_horizontal_sum_never_coerces_numeric_looking_text_or_boolean():
    from app.calculations.output.formulas import row_arithmetic, sum_row
    book=Workbook();ws=book.active;ws.append([]);ws.append(['2',True,D(5)])
    assert not sum_row(ws['D2'],1,3,D(8))
    assert not row_arithmetic(ws['D2'],[1,3],'*',D(10))
    assert not sum_row(ws['C2'],1,3,D(8))


@pytest.mark.parametrize('expression',['=A1+B2','=A2+A2','=A2+D2','=A2+B2*C2'])
def test_restored_arithmetic_rejects_cross_row_duplicates_self_or_mixed_operators(expression):
    from app.calculations.output.formulas import restore_trusted_numeric_caches
    book=Workbook();ws=book.active;ws['A1']=2;ws['A2']=2;ws['B2']=3;ws['C2']=5;ws['D2']=expression
    cached=Workbook();cs=cached.active;cs['A1']=2;cs['A2']=2;cs['B2']=3;cs['C2']=5;cs['D2']=5
    with pytest.raises(ValueError,match='outside the trusted writer'):
        restore_trusted_numeric_caches(book,cached)
