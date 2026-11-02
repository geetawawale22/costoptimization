"""Source-backed part estimates: named values, original cells, safe ownership."""
from decimal import Decimal as D
from io import BytesIO
from copy import copy
import pytest
from openpyxl import load_workbook
from app.calculations.context import CalculationRun, RowResult
from app.calculations.output import build_workbook


def result(customer,commodity,type_='MAKE',end='Owned',**extra):
    values={'Rate/UOM':D(5),'RM Cost/Pc':D(10),'Process Cost/Pc':D(3),'Basic Cost/Pc':D(13),
            'MARKUP/Pc':D(2),'Total Cost/Pc':D(15),'Grand Total Cost/Pc':D(15),
            'ICC':D('.01'),'REJ':D('.02'),'OH':D('.03'),'PROFIT':D('.04'),'Handling':D('.05'),
            'ICC COST':D('.1'),'REJ COST':D('.2'),'OH COST':D('.3'),'PROFIT COST':D('.4'),
            'Handling COST':D(1),'GDC Cost':D(2),'GDC Process cost':D(1),'RM Cost with out scrap cost':D(11),
            'Gross Wt':D('1.1'),'GROSS WEIGHT':D('1.1'),'Scrap Cost':D(1),'RM':D(11),'RM Cost':D(10),
            'Casting Cost':D(15),'Total Casting rate /Kg':D(14),'Machining Scrap Recovery':D('.2')}
    values.update(extra.pop('values',{}))
    inputs={'End Item':end,'Supplier':'Foundry','Customer No':customer,'Desc':'=raw label',
            'Type':type_,'Qty/Veh':1,'_variants':{'Variant':1},'Material Grade':'Alloy',
            'Part Wt':D(1),'Casting Wt':D(1),'RM Base':'APR#22','_process_lines':[],**extra}
    return RowResult(customer,commodity,type_,inputs,values,status='success')


def workbook(rows,producers=()):
    return load_workbook(BytesIO(build_workbook(CalculationRun(rows,producer_rows=list(producers)))))


@pytest.mark.parametrize('commodity,suffix',[('GDC','_gdc'),('PDC','_pdc'),('SAND CASTING','_sand')])
def test_original_part_layout_is_connected_and_uses_named_calculated_values(commodity,suffix):
    w=workbook([result('0007',commodity)])
    s=w['0007'+suffix]
    assert s['B2'].value=='COST ESTIMATION SHEET'
    assert 'B2:K2' in s.merged_cells
    assert s['D4'].value=='0007' and s['D4'].data_type=='s'
    assert s['H5'].value==1
    assert s['B2'].fill.fgColor.rgb=='00C00000'
    assert s.column_dimensions['B'].width==10
    if commodity!='SAND CASTING':
        assert s['D5'].value=='=raw label' and s['D5'].data_type=='s'
        assert s['J11'].value==15 and s['K11'].value==1
        assert s['D10'].value==5
    else:
        assert s['D5'].value=='Owned' and s['J9'].value==15
        assert s['H7'].value==14 and s['H9'].value==15


def test_part_summary_is_scoped_to_end_item_and_never_counts_producer_twice():
    base=result('001','GDC');other=result('002','GDC',end='Other',values={'Grand Total Cost/Pc':D(900)})
    w=workbook([base,other])
    assert w['001_gdc']['J11'].value==15
    assert w['002_gdc']['J11'].value==900
    producer=result('bns','GDC','BUY NON STD');producer.dependency_only=True
    owner=result('bns','GDC','BUY NON STD',values={'Grand Total Cost/Pc':D(18)})
    w=workbook([owner],[producer])
    assert w['bns_gdc']['J11'].value==18
    assert w['bns_gdc']['K19'].value==2  # source producer markup occurs once
    assert any('scoped to this End Item' in str(cell.value) for row in w['Diagnostics'] for cell in row)


def test_process_rows_keep_first_nonzero_source_display_and_full_calculated_cost():
    base=result('001','GDC')
    process=result('002','GDC','PROCESS',values={'_process_costs':[
        {'name':'Cutting','quantity':D(2),'rate':D(3),'cost':D(6)},
        {'name':'Drilling','quantity':D(1),'rate':D(4),'cost':D(4)}],
        'Process Cost/Pc':D(10),'Grand Total Cost/Pc':D(12)})
    s=workbook([base,process])['001_gdc']
    assert [s[address].value for address in ('B14','E14','F14','G14','H14')]==['002','Cutting',2,3,10]
    assert s['H19'].value==10 and s['J11'].value==27


def test_blocked_owned_component_leaves_final_estimate_blank():
    base=result('001','GDC');failed=result('002','','BUY STD');failed.status='blocked';failed.values={}
    s=workbook([base,failed])['001_gdc']
    assert s['J11'].value is None and s['K11'].value is None
    assert s['J8'].value is None


def test_ambiguous_casting_bases_are_not_arbitrarily_projected():
    w=workbook([result('001','GDC'),result('002','GDC')])
    assert not any(name.endswith('_gdc') for name in w.sheetnames)
    assert any('more than one MAKE/producer' in str(cell.value) for row in w['Diagnostics'] for cell in row)


def test_original_fixed_process_total_does_not_hide_more_than_five_process_rows():
    processes=[result('p'+str(i),'GDC','PROCESS') for i in range(7)]
    s=workbook([result('001','GDC'),*processes])['001_gdc']
    assert s['B20'].value=='p6' and s['H21'].value==21
    assert s['B21'].value=='Total Process Cost'


def test_normal_forging_summary_preserves_source_norms_and_avoids_second_scrap_deduction():
    base=result('0009','FORGING',values={'Rate/UOM':D(5),'Scrap Rate':D(2),'Scrap Recovery':D(1),
        'Input Wt':D(2),'Calculated Wt':D('1.9'),'_hardening_costs':[
            {'name':'Forging','quantity':D(1),'rate':D(7),'cost':D(14),'basis':D(2)}]})
    w=workbook([base]);s=w['0009']
    assert s['A1'].value=='Cost Estimation Summary'
    assert s['B3'].value=='0009' and s['B5'].value==5 and s['B6'].value==2
    assert s['A10'].value=='Forging' and s['B10'].value==7
    assert s['F10'].value==10 and s['F11'].value==1
    assert s['C20'].value=='Total Cost/ part (Rs)' and s['F20'].value==15
    assert s['A21'].value=='Operation no.' and s.column_dimensions['A'].width==20
    assert any('not deducted twice' in str(cell.value) for row in w['Diagnostics'] for cell in row)


def test_forging_process_table_preserves_named_rates_and_calculated_amounts():
    base=result('0009','FORGING',values={'Scrap Recovery':D(1)})
    process=result('p1','FORGING','PROCESS',values={'_process_costs':[
        {'name':'Drilling','quantity':D(2),'rate':D(6),'cost':D(12)}],
        'Process Cost/Pc':D(12),'Grand Total Cost/Pc':D(14),'Scrap Recovery':D(0)})
    s=workbook([base,process])['0009']
    assert [s.cell(22,i).value for i in range(1,7)]==['p1','=raw label','Drilling',2,6,12]
    assert s['F20'].value==29 and s['F23'].value==12


def test_set_of_two_cannot_silently_use_unverified_hardcoded_rate_or_density():
    w=workbook([result('0009','FORGING',end='Set of two components')])
    assert '0009' in w
    assert w['0009']['B2'].value=='Cost Estimation Sheet'
    assert w['0009']['H8'].value is None and w['0009']['H15'].value is None
    assert w['0009']['J11'].value==15
    assert any('setoftwo_format12432-12730' in str(cell.value) for row in w['Diagnostics'] for cell in row)


def test_original_detail_data_style_applies_to_all_rows_not_only_first():
    first=result('001','GDC',end='A');second=result('002','GDC',end='B')
    w=workbook([first,second]);s=w['GDC_Output_Foundry']
    for column in range(1,s.max_column+1):
        assert copy(s.cell(3,column).font)==copy(s.cell(4,column).font)
        assert copy(s.cell(3,column).border)==copy(s.cell(4,column).border)
        assert copy(s.cell(3,column).fill)==copy(s.cell(4,column).fill)
        assert s.cell(3,column).number_format==s.cell(4,column).number_format
    detail=w['001_gdc']
    assert detail['I19'].value=='Total Markups'
    assert detail['K19'].value==2
    assert detail['I21'].value=='Handling_Buy Std'


def test_set_of_two_normalising_uses_only_verified_named_rate_not_legacy_literal():
    r=result('0009','FORGING',end='Set of two components',values={'Calculated Wt':D(2),
        '_master_rates':{'Normalising':D(9)},'Scrap Recovery':D(1)})
    w=workbook([r]);s=w['0009']
    assert s['H8'].value==18
    assert s['H15'].value is None  # no fabricated geometry or hardcoded steel density
    assert s['J11'].value==15  # informational display never recosts the owned result


def test_zero_weight_casting_displays_verified_rate_without_dividing_by_weight():
    row=result('0009','GDC',values={'_master_rates':{'GDC Process cost':D(7)},
                                    'GDC Process cost':D(0)},**{'Casting Wt':0})
    assert workbook([row])['0009_gdc']['D8'].value==7
    row.values.pop('_master_rates')
    assert workbook([row])['0009_gdc']['D8'].value is None


def test_sand_recipe_and_intermediate_rate_sheets_use_same_run_values_and_original_order():
    from test_calculation_sand_complete import fixture
    from app.calculations.commodities.sand_casting.make import calculate
    from app.calculations.output.layouts import SAND_RATE_FIELDS
    row,context,_=fixture();row.update({'Customer No':'0007','End Item':'E','_variants':{'V':1}})
    values=calculate(row,context)
    r=RowResult('sand','SAND CASTING','MAKE',row,values,status='success')
    w=workbook([r]);rate=w['Casting_Sand_RM rate Calulation'];recipe=w['Casting_Recipie_estimation']
    headers={cell.value:cell.column for cell in rate[1]}
    assert [cell.value for cell in rate[1]][35:]==list(SAND_RATE_FIELDS)
    assert rate.cell(2,headers['Customer No']).value=='0007'
    assert rate.cell(2,headers['Total Charge Kg']).value==17
    assert rate.cell(2,headers['RATE/UOM']).value==float(values['RATE/UOM'])
    assert recipe['B9'].value=='Sr No' and recipe['D10'].value=='Pig Iron'
    assert recipe['E7'].value=='fixture supplier' and recipe['E8'].value=='FG fixture'
    assert [recipe.cell(10,j).value for j in (5,6,7)]==[2,10,20]
    assert recipe['G17'].value==128 and recipe['F18'].value==17
    assert recipe.tables and rate.tables
    assert not w._external_links


def test_partial_sand_graph_never_exports_uploaded_rate_as_a_computed_result():
    from test_calculation_sand_complete import fixture
    from app.calculations.commodities.sand_casting.make import calculate_recipe_charge
    row,context,_=fixture();row.update({'Customer No':'0007','End Item':'E','RATE/UOM':999,'Total Cost/Kg':999,'_variants':{'V':1}})
    partial=calculate_recipe_charge(row,context)
    r=RowResult('sand','SAND CASTING','MAKE',row,partial,status='blocked')
    w=workbook([r]);s=w['Casting_Sand_RM rate Calulation'];headers={cell.value:cell.column for cell in s[1]}
    assert s.cell(2,headers['RATE/UOM']).value is None
    assert s.cell(2,headers['Total Cost/Kg']).value is None
    assert s.cell(2,headers['Total Charge Kg']).value==17
    assert w['Casting_Recipie_estimation']['G17'].value==128


def test_set_of_two_editable_weight_formulas_use_verified_density_without_recosting():
    r=result('0009','FORGING',end='Set of two components',values={'Calculated Wt':D(2),
        '_master_rates':{'Normalising':D(9)},'GSM/ Density':D('7.85')})
    w=workbook([r]);s=w['0009']
    assert s['H15'].data_type=='f' and '*7.85/1000000' in s['H15'].value
    assert s['H21'].value=='=SUM(H15:H20)'
    assert s['H26'].value=='=SUM(H22:H25)'
    assert s['H4'].value==2 and s['H8'].value==18
    assert s['J11'].value==15  # output-editable geometry does not alter calculated ownership
    assert all(s.cell(row,col).value is None for row in [*range(15,21),*range(22,26)] for col in range(3,8))
    assert not any('positive verified material_master' in str(cell.value) for row in w['Diagnostics'] for cell in row)


def test_set_of_two_missing_density_keeps_geometry_blank_and_reports_exact_requirement():
    r=result('0009','FORGING',end='Set of two components',values={'Calculated Wt':D(2),'_master_rates':{'Normalising':D(9)}})
    w=workbook([r]);s=w['0009']
    assert s['H15'].value is None and s['H21'].value is None
    assert s['H4'].value==2 and s['H8'].value==18
    assert any('positive verified material_master' in str(cell.value) for row in w['Diagnostics'] for cell in row)
