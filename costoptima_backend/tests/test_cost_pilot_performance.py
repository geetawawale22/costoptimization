"""Snapshot-local optimization must preserve exact master semantics."""
from decimal import Decimal
from types import MappingProxyType
import pytest
from app.calculations.masters import SemanticMasterResolver, MasterRecord, MasterField, MasterMappingError
from app.calculations.context import CalculationContext
from app.calculations.common import CalculationError


def test_match_resolution_once_per_schema_preserves_order_case_and_ambiguity(monkeypatch):
    import app.calculations.masters as module
    fields=(MasterField('code','Code',None,'VARCHAR(255)'),MasterField('rate','Rate',None,'DECIMAL(20,6)'))
    rows=tuple(MasterRecord('fixture','src_fixture','Family',n,MappingProxyType({'code':c,'rate':Decimal(str(n))}),fields) for n,c in [(1,'A'),(2,'a'),(3,'B')])
    resolver=SemanticMasterResolver(None)
    monkeypatch.setattr(resolver,'records',lambda *a,**k:rows)
    original=module.resolve_field;calls=[]
    def resolve(*args):calls.append(args);return original(*args)
    monkeypatch.setattr(module,'resolve_field',resolve)
    assert [x.source_row_id for x in resolver.matches('fixture',{'Code':'a'})]==[1,2]
    assert [x.source_row_id for x in resolver.matches('fixture',{'Code':'a'},case_sensitive=True)]==[2]
    assert resolver.matches('fixture',{'Code':'X'})==()
    assert len(calls)==1
    assert resolver.record('fixture',{'Code':'a'},duplicate_policy='first').source_row_id==1
    assert resolver.record('fixture',{'Code':'a'},duplicate_policy='last').source_row_id==2
    with pytest.raises(module.DuplicateMasterError):resolver.record('fixture',{'Code':'a'},duplicate_policy='error')


def test_source_schema_identity_and_separate_snapshots_do_not_borrow_values(monkeypatch):
    import app.calculations.masters as module
    a=(MasterField('code','Code',None,'VARCHAR(255)'),)
    b=(MasterField('other','Different header',None,'VARCHAR(255)'),)
    rows=(MasterRecord('fixture','src_first','A',1,{'code':'001'},a),MasterRecord('fixture','src_second','B',1,{'other':'001'},b))
    resolver=SemanticMasterResolver(None);monkeypatch.setattr(resolver,'records',lambda *args,**kwargs:rows)
    assert resolver.matches('fixture',{'Code':'001'})==rows[:1]
    fresh=SemanticMasterResolver(None)
    assert fresh._match_columns=={} and fresh._record_cache=={}
    assert fresh._match_columns is not resolver._match_columns


def test_lookup_failure_has_exact_named_business_key_and_period():
    from app.calculations.masters import FieldRef,MissingMasterError
    class Missing:
        def lookup(self,*args,**kwargs):raise MissingMasterError('No matching master')
    context=CalculationContext(Missing(),[])
    with pytest.raises(CalculationError) as caught:
        context.require_master('non_std_bop',{'customer no':'0313ABH00301N_017'},FieldRef('RM COST','Jan#24'))
    issue=caught.value.as_dict()
    assert issue['lookup_keys']=={'customer no':'0313ABH00301N_017'}
    assert issue['master']=='non_std_bop' and issue['master_field']=='RM COST' and issue['master_group']=='Jan#24'


def test_output_style_reuse_preserves_independent_cell_overrides():
    from openpyxl import Workbook
    from app.calculations.output.workbook import table
    from openpyxl.styles import Font
    book=Workbook();book.remove(book.active)
    sheet=table(book,'Fixture',['Name','Amount'],[['0017',Decimal('1.25')],['a long description that wraps to a second line',Decimal('2.5')]])
    assert sheet['A2'].font.name=='Calibri' and sheet['A2'].font.sz==9
    assert sheet['A2'].value=='0017' and sheet['A2'].data_type=='s'
    assert sheet['B2'].number_format=='#,##0.000' and sheet['A3'].alignment.wrap_text
    sheet['B2'].number_format='0.000000'
    assert sheet['B3'].number_format=='#,##0.000'
    sheet['A1'].font=Font(bold=False)
    assert sheet['B1'].font.bold
