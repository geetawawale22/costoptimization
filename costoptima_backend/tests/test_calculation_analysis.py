"""Primary Analysis categories and PCBA basic-cost deduction, isolated rows."""
from decimal import Decimal as D
from app.calculations.context import RowResult
from app.calculations.merging.rollup import legacy_analysis


def result(identifier,commodity,type_,**values):
    base={'RM Cost/Pc':D(10),'Process Cost/Pc':D(3),'Basic Cost/Pc':D(13),'MARKUP/Pc':D(2),
          'ICC COST':D('.1'),'REJ COST':D('.2'),'OH COST':D('.3'),'PROFIT COST':D('.4')}
    base.update(values)
    return RowResult(identifier,commodity,type_,{'Type':type_,'Supplier':'S','Qty/Veh':2,'_variants':{'V':3}},values=base,status='success')


def test_analysis_separates_soft_hard_and_overhead_handling():
    hard=result('hard','HARD','MAKE');soft=result('soft','SOFT','ASSY',**{'Handling COST':D('.5')})
    values=legacy_analysis([hard,soft])[0]['values']
    assert values['RM cost']==60 and values['Process cost']==18 and values['Markup']==12
    assert values['RM cost Soft']==60 and values['Process cost Soft']==18 and values['Markup Soft']==12
    assert values['O&H Cost']==D('6.6') and values['Other6'] is None
    assert values['Total']==180


def test_pcba_purchase_cost_attributed_once_and_purchase_markup_retained():
    row=result('board','PCBA','BUY NON STD',**{'Basic Cost/Pc':D(100),'PCBA Basic Cost/Pc':D(100),'MARKUP/Pc':D(5)})
    values=legacy_analysis([row])[0]['values']
    assert values['Buy Non Std']==0 and values['PCBA']==600
    assert values['Markup']==30 and values['Total']==630


def test_single_pcba_component_cannot_masquerade_as_board():
    summary=legacy_analysis([result('component','PCBA','ASSY')])[0]
    assert summary['status']=='incomplete' and summary['values']=={}
    assert summary['issues'][0]['code']=='analysis_pcba_board_missing'


def test_missing_breakup_is_not_zero_filled():
    row=result('hard','HARD','MAKE');row.values.pop('ICC COST')
    summary=legacy_analysis([row])[0]
    assert summary['status']=='incomplete' and summary['values']=={}
    assert summary['issues'][0]['code']=='analysis_value_missing'


def test_sand_machine_scrap_original_negative_sign():
    row=result('sand','SAND CASTING','MAKE',**{'Machining Scrap Recovery':D(4)})
    values=legacy_analysis([row])[0]['values']
    assert values['Machine Scrap Recovery']==-24 and values['Total']==66


def test_undefined_multi_supplier_scrap_precedence_is_blocked():
    rows=[result('one','SAND CASTING','MAKE',**{'Machining Scrap Recovery':D(4)}),result('two','SAND CASTING','MAKE',**{'Machining Scrap Recovery':D(1)})]
    rows[1].inputs['Supplier']='Other'
    summary=legacy_analysis(rows)[0]
    assert summary['status']=='incomplete'
    assert any(i['code']=='analysis_sand_precedence_unresolved' for i in summary['issues'])


def test_end_item_analysis_uses_owner_and_component_variant_without_root_variant():
    from app.calculations.merging.rollup import end_item_analysis
    hard=result('hard','HARD','MAKE');hard.inputs['End Item']='ownerA'
    soft=result('soft','SOFT','ASSY',**{'Handling COST':D('.5')});soft.inputs['End Item']='ownerB'
    root=RowResult('root','', 'END ITEM',{'Type':'END ITEM','End Item':'ownerA','Qty/Veh':1,'_variants':{'V':99}},status='success')
    series=end_item_analysis([hard,soft,root])
    a=next(item for item in series if item['End Item']=='ownerA')['values']
    b=next(item for item in series if item['End Item']=='ownerB')['values']
    assert a['RM cost']==60 and a['Process cost']==18 and a['Total']==90
    assert b['RM cost']==60 and b['Process cost']==18 and b['Total']==90
