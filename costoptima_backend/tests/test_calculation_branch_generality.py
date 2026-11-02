"""Generic source-contract tests using isolated relational masters, never MySQL.

The rates/identities below are invented test fixtures, not application fallbacks.
Assertions evaluate the original expressions independently of saved outputs.
"""
from decimal import Decimal as D
from io import BytesIO
from sqlalchemy import create_engine, Table, MetaData, Column, Integer, String
from sqlalchemy.orm import Session
import openpyxl
import pytest

from app.masters.metadata import metadata, masters, families, sources, columns
from app.calculations.masters import SemanticMasterResolver, FieldRef, MissingMasterError
from app.calculations.context import CalculationContext, RowResult
from app.calculations.dispatcher import BRANCHES, dispatch
from app.calculations.common import CalculationError
from app.calculations.engine import execute
from app.calculations.output import build_workbook


class RelationalFixture:
    def __init__(self, identity):
        self.identity=identity
        self.engine=create_engine('sqlite+pysqlite:///:memory:')
        metadata.create_all(self.engine)
        self.db=Session(self.engine)
        self.next_id=1
        self.supplier='Owner '+identity
        self.part='0000'+identity
        self.material='Material '+identity
        self.process='Operation '+identity
        self.period='Period '+identity
        self.rate=D('3.75') if identity=='Alpha' else D('7.125')
        self.process_rate=D('2.5') if identity=='Alpha' else D('4.25')
        self.price=D('13.25') if identity=='Alpha' else D('19.75')
        self.seed()
        self.db.flush()
        self.resolver=SemanticMasterResolver(self.db)

    def add(self,code,fields,rows):
        id_=self.next_id;self.next_id+=1
        table_name='src_contract_'+code
        self.db.execute(masters.insert().values(master_id=id_,master_code=code,master_name=code,
            sheet_name=code,header_kind='0',sort_order=id_,view_name='vw_contract_'+code))
        self.db.execute(families.insert().values(family_id=id_,folder_path='Family '+code,display_name=code,sort_order=id_))
        self.db.execute(sources.insert().values(source_id=id_,master_id=id_,family_id=id_,data_table_name=table_name,
            source_file='isolated fixture',row_count=len(rows),expected_rows=len(rows),merge_order=0))
        table=Table(table_name,MetaData(),Column('row_id',Integer,primary_key=True),
            *(Column(name,String(255)) for name,header,group in fields))
        table.create(self.db.connection())
        for position,(name,header,group) in enumerate(fields,1):
            self.db.execute(columns.insert().values(source_id=id_,position=position,column_key=name,
                header_text=header,group_text=group,sql_type='VARCHAR(255)',storage_type='VARCHAR(255)'))
        if rows:self.db.execute(table.insert(),[dict(row_id=i,**{k:str(row[k]) if row.get(k) is not None else None for k,header,group in fields}) for i,row in enumerate(rows,1)])

    def seed(self):
        f=lambda *names:[(name,name,None) for name in names]
        labels=['Material Family','Material Sub Family','Material Grade','UOM','Commodity']
        material_fields=f('material_code','Applicable Process',*labels)+[
            ('material_rate','Rate/UOM',self.period),('scrap_rate','Scrap Rate',self.period)]
        self.add('material_master',material_fields,[{'material_code':self.material,'Applicable Process':'Default',
            **{label:'Generic '+label for label in labels},'material_rate':self.rate,'scrap_rate':D('.5')}])
        process_names=[self.process,self.material,'Core Cost','De coring cost','GDC Process cost','GDC-Fettling','GDC-Shot Blasting',
            'PDC Melting rate/Kg','PDC FETTLING','SHOT BLASTING','Soldering']
        self.add('process_master',f('process','Process Code','uom','m_c_specs')+[('process_rate','Rate/UOM','AD')],
            [{'process':name,'Process Code':name,'uom':'piece','m_c_specs':'none','process_rate':self.process_rate} for name in process_names])
        norms=['ICC','REJ','OH','PROFIT','Handling','Dies & Fixture Maint. cost']
        self.add('norms_make',f('supplier','efficiency',*norms),[
            {'supplier':self.supplier,'efficiency':D('.8'),**{name:0 for name in norms}}])
        self.add('norms_shift_factor',f('shift_factor','c_0','c_401','c_1000','c_2001','c_1k','c_2k'),[
            {'shift_factor':self.supplier,**{key:10 for key in ['c_0','c_401','c_1000','c_2001','c_1k','c_2k']}},
            {'shift_factor':'Deflashing',**{key:D('.2') for key in ['c_0','c_401','c_1000','c_2001','c_1k','c_2k']}}])
        self.add('norms_gross_wt',f('criteria','net_wt','hot_runner','hot_runner2'),[
            {'criteria':'rest all','net_wt':None,'hot_runner':'1.1','hot_runner2':'1.2'}])
        self.add('norms_pnf',f('pnf','factor'),[{'pnf':'BNS_Make','factor':'.03'},{'pnf':'Thermal','factor':'.2'}])
        for code in ['std_bop','non_std_bop']:
            self.add(code,f('customer_no','cost_pc'),[{'customer_no':self.part,'cost_pc':self.price}])
        for code in ['norms_buy_std','norms_buy_non_std']:
            self.add(code,f('supplier','icc','rej','handling','profit','markup'),[
                {'supplier':self.supplier,'icc':'.01','rej':'.02','handling':'.03','profit':'.04','markup':'.1'}])
        self.add('norms_consign',f('supplier','handling_500','handling_500_2'),[
            {'supplier':self.supplier,'handling_500':'.2','handling_500_2':'.1'}])
        self.add('norms_assy',f('supplier','ICC','REJ','OH','PROFIT','markup'),[
            {'supplier':self.supplier,'ICC':0,'REJ':0,'OH':0,'PROFIT':0,'markup':'.1'}])
        self.add('assy',f('supplier_mhr','mhr_operation','supplier_ct','ct_operation','supplier_handling','handling_operation'),[
            {'supplier_mhr':'Supplier','mhr_operation':'MHR','supplier_ct':'Supplier','ct_operation':'CT per Point','supplier_handling':'Supplier','handling_operation':'Handling Time'},
            {'mhr_operation':self.process,'ct_operation':self.process,'handling_operation':self.process},
            {'mhr_operation':'Manual','ct_operation':'Manual','handling_operation':'Manual'},
            {'supplier_mhr':self.supplier,'mhr_operation':360,'supplier_ct':self.supplier,'ct_operation':2,'supplier_handling':self.supplier,'handling_operation':4}])
        self.db.execute(masters.update().where(masters.c.master_code=='assy').values(header_kind='Single'))
        self.add('ebom',f('material_code','fob','landing_factor','currency','solder_point','Material Family',
            'Material Sub Family','Material Grade','RM Supplier','UOM')+[('exchange_rate','Rate/UOM',self.period)],
            [{'material_code':self.material,'fob':self.price,'landing_factor':'1.2','currency':'Currency '+self.identity,
              'solder_point':3,'exchange_rate':2,**{key:'Generic '+key for key in ['Material Family','Material Sub Family','Material Grade','RM Supplier','UOM']}}])

    def record(self,commodity,type_,**extra):
        return {'Commodity':commodity,'Type':type_,'Customer No':self.part,'Supplier':self.supplier,
            'End Item':'Assembly '+self.identity,'Project':'Project '+self.identity,'RM Base':self.period,
            'Qty/Veh':2,'_variants':{'Vehicle '+self.identity:3},'_record_id':'contract '+self.identity,
            **extra}

    def close(self):
        self.db.rollback();self.db.close();self.engine.dispose()


@pytest.fixture(params=['Alpha','Beta'])
def relational(request):
    fixture=RelationalFixture(request.param)
    try:yield fixture
    finally:fixture.close()


def test_registry_has_no_sample_specific_dispatch():
    for commodity,type_ in BRANCHES:
        branch=dispatch('  '+commodity.lower()+'  ','  '+type_.lower()+'  ')
        assert branch.module==BRANCHES[commodity,type_].module
    for commodity in ['SOFT','STAMPING','FORGING','GDC','PDC','PCBA','THERMAL']:
        assert callable(dispatch(commodity,'ANOTHER TYPE').calculate)
    for unproven in ['HVAC','SEAT','SEAT MECHANISM','SHEET METAL']:
        with pytest.raises(CalculationError,match='No source-proven'):dispatch(unproven,'MAKE')


@pytest.mark.parametrize('type_',['MAKE','BUY NON STD'])
def test_hard_arithmetic_uses_different_relational_master_keys(relational,type_):
    f=relational
    row=f.record('HARD',type_,**{'Material Code':f.material,'Gross Wt':2,'Part Wt':1,
        'Cycle Time':30,'Cavity':2,'m/c Tonnage':100})
    result=dispatch('HARD',type_).calculate(row,CalculationContext(f.resolver,[row]))
    pcs=D(8)*3600*2*D('.8')/30
    gross=D('1.1') if type_=='BUY NON STD' else D(2)
    basic=f.rate*gross+pcs/(100*D(10))+D('.2')
    expected=basic*(D('1.03') if type_=='BUY NON STD' else 1)
    assert result['Grand Total Cost/Pc']==expected
    assert result['Grand Total Cost/Veh']==expected*2
    assert f.resolver.trace and all(t['source_table'].startswith('src_contract_') for t in f.resolver.trace)


@pytest.mark.parametrize('commodity,type_',[('SOFT','ASSY'),('SOFT','BUY NON STD'),('SOFT',''),
    ('STAMPING','MAKE'),('STAMPING','BUY NON STD'),('STAMPING','')])
def test_process_only_family_contracts(relational,commodity,type_):
    f=relational;row=f.record(commodity,type_,**{'_process_lines':[{'name':f.process,'quantity':3,'rate':999999}]})
    result=dispatch(commodity,type_).calculate(row,CalculationContext(f.resolver,[row]))
    assert result['RM Cost/Pc']==0
    assert result['Grand Total Cost/Pc']==f.process_rate*3
    assert result['Grand Total Cost/Veh']==f.process_rate*6
    assert not any(t['master']=='material_master' for t in f.resolver.trace)


@pytest.mark.parametrize('type_',['MAKE','BUY NON STD','PROCESS',''])
def test_gdc_family_original_arithmetic(relational,type_):
    f=relational;row=f.record('GDC',type_,**{'Material Code':f.material,'Casting Wt':2,'Part Wt':1,
        'Core Weight per Part':D('.1'),'_process_lines':[]})
    result=dispatch('GDC',type_).calculate(row,CalculationContext(f.resolver,[row]))
    gross=D(2)*D('1.07');scrap=(D(2)-1)*f.rate*D('.7')*D('.85')
    rm=f.rate*gross-scrap+D('.1')*f.process_rate
    process=(D('.1')+2+2+2)*f.process_rate
    assert result['Grand Total Cost/Pc']==rm+process
    assert all(t.get('group')=='AD' for t in f.resolver.trace if t['master']=='process_master')


@pytest.mark.parametrize('type_',['MAKE','BUY NON STD','PROCESS',''])
def test_pdc_family_original_arithmetic(relational,type_):
    f=relational;row=f.record('PDC',type_,**{'Material Code':f.material,'Casting Wt':2,'Part Wt':1,
        'PDC Machine Process':f.process,'PDC Machine Minutes':30,'_process_lines':[]})
    result=dispatch('PDC',type_).calculate(row,CalculationContext(f.resolver,[row]))
    rm=f.rate*D(2)*D('1.07')-f.rate*D('.7')*D('.85')
    process=f.process_rate*(D(2)/D('.65'))+f.process_rate*30/60+f.process_rate*2+f.process_rate*2
    assert result['Grand Total Cost/Pc']==rm+process


@pytest.mark.parametrize('type_',['MAKE','BUY NON STD','PROCESS'])
def test_forging_family_named_rates_and_scrap_once(relational,type_):
    f=relational;row=f.record('FORGING',type_,**{'Material Code':f.material,'Input Wt':2,'Part Wt':1,
        'Calculated Wt':D('1.5'),'_hardening_lines':[{'name':'Forging '+f.identity,'quantity':0}],
        '_process_lines':[{'name':f.process,'quantity':3}]})
    # A zero hardening input still resolves its named rate. It never borrows another process.
    row['_hardening_lines']=[]
    result=dispatch('FORGING',type_).calculate(row,CalculationContext(f.resolver,[row]))
    expected=f.rate*2-(D(2)-1)*D('.8')*D('.5')+f.process_rate*3
    assert result['Grand Total Cost/Pc']==expected
    assert result['Scrap Recovery']==D('.4')


@pytest.mark.parametrize('type_,material_present',[('BUY NON STD',True),('PROCESS',True),('PROCESS',False)])
def test_sand_range_optional_material_and_ancillary_costs(relational,type_,material_present):
    f=relational;row=f.record('SAND CASTING',type_,**{'Material Code':f.material if material_present else None,
        'Gross Wt':2,'Part Wt':1,'_process_lines':[{'name':f.process,'quantity':3}],
        'Tool Cost':1,'Packing':2,'Freight':3})
    result=dispatch('SAND CASTING',type_).calculate(row,CalculationContext(f.resolver,[row]))
    rate=f.rate if material_present else D(0)
    expected=rate*2+f.process_rate*3+1+2+3-(D(2)-1)*(rate/4)*D('.85')
    assert result['Grand Total Cost/Pc']==expected
    assert result['Machining Scrap Recovery']==rate/4*D('.85')


def test_pcba_component_board_arithmetic_uses_exact_code(relational):
    f=relational;row=f.record('PCBA','ASSY',**{'_pcba_quantity_headers':['Empty variant','Selected variant','Later variant'],
        '_pcba_components':[{'PCBA Part Number':f.part,'Material Code':f.material,
            'Empty variant':None,'Selected variant':3,'Later variant':900,'FOB':999999}]})
    result=dispatch('PCBA','ASSY').calculate(row,CalculationContext(f.resolver,[row]))
    component=f.price*2*D('1.2')+3*f.process_rate
    assert result['Grand Total Cost/Pc']==component*3
    assert result['Selected Population']=='Selected variant'
    assert result['_pcba_component_results'][0]['values']['FOB']==f.price
    row['_pcba_components'][0]['Material Code']='Unknown code '+f.identity
    with pytest.raises(CalculationError,match='components could not calculate'):
        dispatch('PCBA','ASSY').calculate(row,CalculationContext(f.resolver,[row]))


@pytest.mark.parametrize('type_',['BUY STD','BUY NON STD'])
def test_purchasing_owner_norms_and_unknown_identifier_rejection(relational,type_):
    f=relational;row=f.record('',type_);root=f.record('','END ITEM');row['Supplier']='Purchasing '+f.identity
    result=dispatch('',type_).calculate(row,CalculationContext(f.resolver,[root,row]))
    assert result['Grand Total Cost/Pc']==f.price*D('1.1')
    assert any(t['master']=='norms_'+type_.lower().replace(' ','_') for t in f.resolver.trace)
    row['Customer No']='Not '+f.part
    with pytest.raises(CalculationError) as failure:dispatch('',type_).calculate(row,CalculationContext(f.resolver,[root,row]))
    assert failure.value.code=='master_lookup_blocked'


@pytest.mark.parametrize('type_',['PKG','FRT','CONSIGN'])
def test_source_defined_zero_is_distinct_from_missing_cost(relational,type_):
    f=relational;row=f.record('',type_)
    result=dispatch('',type_).calculate(row,CalculationContext(f.resolver,[row]))
    assert result['Grand Total Cost/Pc']==0
    if type_=='CONSIGN':assert result['Handling']==D('.1')


@pytest.mark.parametrize('header_row,reversed_rows',[(1,False),(8,True)])
def test_live_input_to_output_changes_identity_header_position_and_master_snapshot(relational,header_row,reversed_rows):
    f=relational;book=openpyxl.Workbook();sheet=book.active;sheet.title='BOM'
    # Variant columns belong between Level and Supplier, as in the original contract.
    fields=['Project','End Item','Customer No','Commodity','Type','Level','Vehicle '+f.identity,'Supplier','Qty/Veh']
    for _ in range(header_row-1):sheet.append(['Workbook notes '+f.identity])
    sheet.append(fields)
    rows=[f.record('','BUY STD'),f.record('','PKG')]
    rows[1]['Customer No']='Logistics '+f.identity
    if reversed_rows:rows.reverse()
    root=f.record('','END ITEM');root['Customer No']='Final '+f.identity
    for record in [root,*rows]:
        record['Vehicle '+f.identity]=3;record['Level']=1
        sheet.append([record.get(field) for field in fields])
    data=BytesIO();book.save(data)
    run=execute(data.getvalue(),f.resolver,development=True)
    assert len(run.rows)==3
    assert run.counts['calculated']==3
    assert not any(row.calculation_status=='ERROR' for row in run.rows)
    root_result=next(row for row in run.rows if row.type=='END ITEM')
    assert root_result.values['Vehicle '+f.identity]==f.price*D('1.1')*2*3*3
    purchasing=next(row for row in run.rows if row.type=='BUY STD')
    assert purchasing.values['Grand Total Cost/Pc']==f.price*D('1.1')
    assert purchasing.inputs['Customer No']==f.part
    workbook=openpyxl.load_workbook(BytesIO(build_workbook(run)),data_only=True)
    assert {'Master_Output','Calculation_Diagnostics','End Item'}<=set(workbook.sheetnames)
    assert any(cell.value==f.part for row in workbook['Master_Output'] for cell in row)
    assert any(cell.value==float(f.price*D('1.1')) for row in workbook['Master_Output'] for cell in row)


def test_hard_assy_dynamic_semantic_blocks_and_supplied_ct(relational):
    f=relational;row=f.record('HARD','ASSY',**{'Process / Assy Name':f.process,'No of Points':3})
    result=dispatch('HARD','ASSY').calculate(row,CalculationContext(f.resolver,[row]))
    assert result['CT(sec)']==D(3)*2+4
    assert result['Grand Total Cost/Pc']==(D(3)*2+4)*360/3600*D('1.1')
    row['CT(sec)']=30
    assert dispatch('HARD','ASSY').calculate(row,CalculationContext(f.resolver,[row]))['Grand Total Cost/Pc']==D('3.3')


@pytest.mark.parametrize('type_',['ASSY','BUY NON STD',''])
def test_thermal_nonmake_supplier_path_uses_named_process_code(relational,type_):
    f=relational;row=f.record('THERMAL',type_,**{'Material Code':f.material,'Part Wt':2})
    result=dispatch('THERMAL',type_).calculate(row,CalculationContext(f.resolver,[row]))
    rm=D(2)*D('1.2')*f.rate
    expected=(rm+rm*f.process_rate)*D('1.2')
    assert result['Grand Total Cost/Pc']==expected
    assert result['_thermal_supplier_path']=='non_make'
    assert any(t['master']=='process_master' and t['group']=='AD' for t in f.resolver.trace)


def test_master_values_are_reloaded_for_a_new_snapshot_without_global_rate_cache(relational):
    f=relational
    before=f.resolver.lookup('std_bop',{'customer_no':f.part},'cost_pc',decimal=True)
    table=Table('src_contract_std_bop',MetaData(),autoload_with=f.db.connection())
    f.db.execute(table.update().where(table.c.customer_no==f.part).values(cost_pc=str(f.price*2)))
    # Changing a fixture proves a newly constructed resolver consumes its current SQL snapshot.
    # Application rates are deliberately cached only inside one resolver/run.
    fresh=SemanticMasterResolver(f.db)
    row=f.record('','BUY STD');root=f.record('','END ITEM')
    after=dispatch('','BUY STD').calculate(row,CalculationContext(fresh,[root,row]))
    assert before==f.price
    assert after['Grand Total Cost/Pc']==before*2*D('1.1')
