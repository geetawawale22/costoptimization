"""Primary-source output compatibility; no fixture values are runtime masters."""
from decimal import Decimal as D
from io import BytesIO
from openpyxl import load_workbook
from app.calculations.context import RowResult,CalculationRun
from app.calculations.output import build_workbook


def row(identifier,commodity,type_,supplier='Purchaser',values=None,**inputs):
    base={'Supplier':supplier,'End Item':'Owned item','Customer No':identifier,'Desc':'=untrusted',
          'Commodity':commodity,'Type':type_,'Qty/Veh':2,'_variants':{'V':1},**inputs}
    return RowResult(identifier,commodity,type_,base,values=values or {},status='success')


def book(rows,producers=()):
    return load_workbook(BytesIO(build_workbook(CalculationRun(rows,producer_rows=list(producers)))),data_only=True)


def named(sheet,index=2,header=1):
    return {c.value:sheet.cell(index,c.column).value for c in sheet[header] if c.value is not None}


def test_global_type_sheets_preserve_bom_supplier_without_numbered_duplicates():
    owners=[row('0001','HARD','BUY NON STD','Purchaser A',{'Basic Cost/Pc':D(10),'Total Cost/Pc':D(12)}),
            row('0002','HARD','BUY NON STD','Purchaser B',{'Basic Cost/Pc':D(20),'Total Cost/Pc':D(24)})]
    producers=[]
    for owner,costing in zip(owners,['Molder A','Molder B']):
        values={**owner.inputs,'Costing Supplier':costing,'Purchasing Supplier':owner.inputs['Supplier']}
        producer=RowResult(owner.record_id+':producer','HARD','BUY NON STD PRODUCER',values,values={'Grand Total Cost/Pc':D(10)},status='success',dependency_only=True)
        producers.append(producer)
    w=book(owners,producers)
    assert [name for name in w.sheetnames if name.startswith('Buy Non Std')]==['Buy Non Std']
    assert [name for name in w.sheetnames if name.startswith('Make_Buy Non Std')]==['Make_Buy Non Std']
    assert named(w['Buy Non Std'])['Supplier']=='Purchaser A'
    assert named(w['Make_Buy Non Std'])['Supplier']=='Molder A'
    assert w['Master_Output'].max_row==3
    assert w['Buy Non Std']['D2'].value=='0001'


def test_original_make_schema_and_name_with_dynamic_geometry_and_db_metadata():
    r=row('0007','HARD','MAKE',values={'Material Family':'Plastic','Material Grade':'PP','Gross Wt':D('1.2'),
        'Material Rate':D(3),'RM Cost/Pc':D(4),'Process Cost/Pc':D(5),'Basic Cost/Pc':D(9),
        'ICC':D('.1'),'ICC COST':D('.9'),'REJ COST':D(0),'OH COST':D(0),'PROFIT COST':D(0),
        'MARKUP/Pc':D('.9'),'Total Cost/Pc':D('9.9')},**{'Supplier No':'008','Costing Supplier':'Molder','L':25})
    w=book([r]);assert 'Make' in w and 'HARD MAKE' not in w
    headers=[c.value for c in w['Make'][1]]
    assert headers[:9]==['Supplier','End Item','Supplier No','Customer No','Desc','2D DWG/CAD Revision No','Type','Qty/Veh','Material Code']
    assert headers[9:11]==['Material Family','Material Grade']
    assert 'L' in headers and 'Color' not in headers and 'Applicable Process' not in headers
    values=named(w['Make']);assert values['Supplier']=='Molder' and values['Gross Wt']==1.2
    assert values['Material Family']=='Plastic' and values['Rate Kg']==3
    assert values['Desc']=='=untrusted' and w['Make']['E2'].data_type=='s'
    assert named(w['Master_Output'])['Supplier']=='Purchaser'
    assert named(w['Master_Output'])['Purchasing Supplier']=='Purchaser'
    assert named(w['Master_Output'])['Costing Supplier']=='Molder'
    assert w['Make'].tables


def test_full_stamping_detail_replaces_five_column_configuration_preview():
    r=row('001','STAMPING','MAKE',values={'RM Cost/Pc':D(10),'Process Cost/Pc':D(3),'Basic Cost/Pc':D(13),
        'ICC':D('.01'),'ICC COST':D('.1'),'REJ COST':D('.2'),'OH COST':D('.3'),'PROFIT COST':D('.4'),
        'Dies COST':D('.5'),'MARKUP/Pc':D('1.5'),'Total':D('14.5'),'Scrap Weight':D(2),
        'Total stroke cost':D(1),'Total welding cost':D(2)},**{'Costing Supplier':'Stamping Co','Blank Consumption':2})
    w=book([r]);ws=w['SM_Stamping Co'];headers=[c.value for c in ws[2]]
    assert len(headers)>60 and 'Cal RM cons' in headers and 'Material Grade' in headers
    assert 'Total stroke cost' in headers and 'Total welding cost' in headers
    assert 'Handling' not in headers and 'Dies & Fixture Maint. cost' in headers
    data=named(ws,3,2)
    assert data['ICC']==.1 and ws.cell(1,headers.index('ICC')+1).value==.01
    assert data['Total welding cost']==2 and data['Dies & Fixture Maint. cost']==.5
    v=headers.index('V')+1
    assert ws.cell(7,v).value==20  # n+6, quantity * RM
    assert ws.cell(19,v).value==1.6  # n+18, quantity * (OH+dies)
    assert ws.tables


def test_stamping_optional_consumption_uses_original_conditional_columns():
    r=row('1','STAMPING','MAKE',values={'_stamping_optional_consumption':True,'Actual Yield':D('.8')})
    w=book([r]);headers=[c.value for c in w['SM_Purchaser'][2]]
    assert 'Actual Yield' in headers and 'Cal Gross Consumption' not in headers and 'Blank calculation' not in headers


def test_consumable_triplets_and_process_rates_use_calculated_metadata():
    r=row('one','SOFT','MAKE',values={'_consumable_lines':[{'name':'Solvent','rate':D(5),'cost':D('.2')}],
        '_process_costs':[{'name':'Cutting','quantity':D(2),'rate':D(6),'cost':D(12)}]},
        **{'_process_lines':[{'name':'Cutting','quantity':D(2)}],'Cutting':2})
    w=book([r]);ws=w['ST_Purchaser'];data=named(ws,3,2);headers=[c.value for c in ws[2]]
    assert headers.index('Solvent Cost')<headers.index('Scrap Rate')
    assert data['Solvent']=='Solvent' and data['Solvent Rate']==5 and data['Solvent Cost']==.2
    assert ws.cell(1,headers.index('Cutting')+1).value==6


def test_gdc_casting_cost_alias_and_blocked_footer_never_zero_fill():
    a=row('a','GDC','MAKE',values={'GDC Cost':D(6),'RM Cost/Pc':D(4),'Process Cost/Pc':D(6),
        'MARKUP/Pc':D(2),'Total':D(12)})
    b=row('b','GDC','PROCESS',values={});b.status='blocked'
    w=book([a,b]);ws=w['GDC_Output_Purchaser'];data=named(ws,3,2)
    assert data['GDC Cost']==6
    headers=[c.value for c in ws[2]];v=headers.index('V')+1
    assert ws.cell(9,v).value is None  # n+7 RM footer is incomplete
    assert any('Incomplete RM Cost footer' in str(c.value) for row_ in w['Diagnostics'] for c in row_)
