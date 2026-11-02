"""Source-backed seat classification/aggregation/layout; no production fixtures."""
from decimal import Decimal as D
from io import BytesIO
from openpyxl import load_workbook
from app.calculations.context import RowResult,CalculationRun
from app.calculations.merging.seat import consolidate_seat
from app.calculations.output import build_workbook


class Record:
    def __init__(self,values):self.values=values
    def value(self,key):return self.values.get(key)


class Masters:
    def __init__(self,families=None):self.families=families or {};self.calls=[];self.trace=[]
    def records(self,name):
        assert name=='seat_output_matrix'
        bases=['Sheetmetal','Foam','Trim','Plastic','Mechanism','Welding','Coating','Trim Process','Assembly','Others','Sub total']
        return tuple(Record({'Base':base,'Type':'Material Family' if i<4 else 'Function' if i<9 else '-', 'Selection Priority':'2' if i<4 else '1' if i<7 else '3' if i<9 else '-'}) for i,base in enumerate(bases))
    def lookup(self,name,keys,field,**kw):
        assert name=='material_master' and field=='Material Family'
        assert kw=={'duplicate_policy':'last','missing':'none'}
        self.calls.append(keys['material_code']);return self.families.get(keys['material_code'])


def row(id_,function=None,code=None,*,type_='MAKE',commodity='HARD',qty=1,variant=1,total=10,existing=None,parent='SEAT'):
    inputs={'Customer No':id_,'End Item':'E','Supplier':'s','Type':type_,'Commodity':commodity,'Parent Commodity':parent,'Material Code':code,'Function':function,'Qty/Veh':qty,'_variants':{'Pack 1':variant}}
    if existing is not None:inputs['Seat_Function']=existing
    return RowResult(id_,commodity,type_,inputs,values={'Total':D(total),'Total Cost/Pc':D(total),'Grand Total Cost/Pc':D(total)},status='success')


def test_seat_uses_original_broadcast_and_later_row_precedence():
    first=row('first','Mechanism')
    both=row('both','Mechanism','F')
    last=row('last',None,'F')
    summary=consolidate_seat([first,both,last],Masters({'F':'Foam'}))
    assert first.values['Seat_Function']=='Mechanism'
    assert both.values['Seat_Function']=='Foam' and last.values['Seat_Function']=='Foam'
    assert summary['issues'][0]['code']=='seat_broadcast_override'
    assert summary['issues'][0]['record_id']=='both'


def test_both_populated_does_not_trigger_but_function_rule_includes_priority3_bases():
    both=row('both','Mechanism','F')
    assembly=row('assembly','Assembly')
    consolidate_seat([both,assembly],Masters({'F':'Foam'}))
    assert both.values['Seat_Function'] is None
    assert assembly.values['Seat_Function']=='Assembly'


def test_existing_bom_classification_wins_and_costs_are_owned_once():
    hard=row('hard','Mechanism',qty=2,total=10,existing='Assembly')
    soft=row('soft',None,'F',commodity='SOFT',qty=3,variant=2,total=5)
    root=row('root',type_='END ITEM',qty=99,total=999)
    summary=consolidate_seat([hard,soft,root],Masters({'F':'Foam'}))
    values=summary['blocks'][0]['rows'][0]['values']
    assert hard.values['Seat_Function']=='Assembly'
    assert values['Assembly']==20 and values['Foam']==30 and values['Sub total']==50
    assert values['Mechanism']==0
    assert any(issue['code']=='seat_bom_classification_preserved' for issue in summary['issues'])


def test_seat_gate_requires_actual_parent_and_failed_contribution_is_not_zero():
    unchanged=row('notseat',parent='')
    assert consolidate_seat([unchanged],object())=={'enabled':False,'blocks':[],'issues':[]}
    failed=row('failed');failed.status='blocked';failed.values={}
    summary=consolidate_seat([failed],Masters())
    output=summary['blocks'][0]['rows'][0]
    assert output['status']=='incomplete' and output['values']=={}
    assert output['blocked_records']==['failed']


def test_output_format_named_positions_labels_and_master_classification():
    item=row('0012','Assembly',total=7)
    metadata={'sbc_layout':{'identity_headers':['End Item','Customer No','Desc'],'variants':[{'key':'Pack 1','label':'Base'}]}}
    summary=consolidate_seat([item],Masters(),metadata)
    run=CalculationRun([item],summaries={'seat':summary},metadata=metadata)
    w=load_workbook(BytesIO(build_workbook(run)),data_only=False)
    assert 'Output_Format' in w
    output=w['Output_Format']
    assert output['C3'].value=='Base' and output['C4'].value=='E'
    assert output['L3'].value=='Assembly' and output['L4'].value==7
    assert output['N3'].value=='Sub total' and output['N4'].value==7
    master=w['Master_Output'];headers=[cell.value for cell in master[1]]
    assert master.cell(2,headers.index('Seat_Function')+1).value=='Assembly'
    # Seat cells preserve their trusted value projection; other sheets now have
    # source-backed verified SUM/SUMPRODUCT footer formulas.
    assert not any(cell.data_type=='f' for row_ in output for cell in row_)


def test_blank_seat_classification_retains_master_field_and_others():
    item=row('blank',total=4)
    summary=consolidate_seat([item],Masters())
    assert summary['blocks'][0]['rows'][0]['values']['Others']==4
    w=load_workbook(BytesIO(build_workbook(CalculationRun([item],summaries={'seat':summary}))),data_only=False)
    assert 'Seat_Function' in [cell.value for cell in w['Master_Output'][1]]
