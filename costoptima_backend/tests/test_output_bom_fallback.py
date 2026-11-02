"""Active legacy make_bns_prev fallback is an input projection, not new arithmetic."""
from decimal import Decimal as D
import pytest
from app.calculations.engine import execute
from test_calculation_branch_generality import RelationalFixture
from test_calculation_input_generality import HEADERS,owner,workbook

@pytest.mark.parametrize('identity,instructions',[('Alpha',1),('Beta',7)])
def test_hard_bns_bom_only_active_legacy_fallback_has_unique_owner(identity,instructions):
    f=RelationalFixture(identity)
    try:
        common={'Supplier':f.supplier,'End Item':'Assembly '+identity,'Project':'Another project '+identity,'PF Code':'New-PF-'+identity,'RM Base':f.period}
        root=owner(**{**common,'Customer No':'root-'+identity,'Type':'END ITEM','Commodity':'','Qty/Veh':1,'Version East':1,'Version West':1})
        child=owner(**{**common,'Customer No':f.part,'Type':'BUY NON STD','Commodity':'HARD','Qty/Veh':3,'Version East':2,'Version West':0,'Material Code':f.material,'Part Wt':1,'Cycle Time':30,'Cavity':2,'m/c Tonnage':100})
        headers=HEADERS+['RM Base','Material Code','Cycle Time','Cavity','m/c Tonnage']
        run=execute(workbook([root,child],headers=headers,instructions=instructions),f.resolver,development=True)
        result=next(row for row in run.rows if row.inputs['Customer No']==f.part)
        assert result.calculation_status=='CALCULATED',result.issues
        assert len(run.producer_rows)==1 and run.producer_rows[0].dependency_only
        assert result.inputs['_details']==[]
        pcs=D(8)*3600*2*D('.8')/30
        producer=(f.rate*D('1.1')+pcs/(100*D(10))+D('.2'))*D('1.03')
        expected=producer*D('1.1')
        assert result.values['Grand Total Cost/Pc']==expected
        parent=next(row for row in run.rows if row.type=='END ITEM')
        assert parent.calculation_status=='CALCULATED',parent.issues
        assert parent.values['Version East']==expected*3*2
        assert parent.values['Version West']==0
    finally:f.close()
