"""PCM7005-7082 arithmetic port, isolated semantic master fixtures.

These tests do not contact MySQL and do not assert legacy runtime parity.
Expected values are derived from independent rational expressions corresponding
to the original formula graph, not from another invocation of the port.
"""
from decimal import Decimal as D
from fractions import Fraction as F
from types import SimpleNamespace

import pytest

from app.calculations.common import CalculationError
from app.calculations.context import CalculationContext
from app.calculations.masters import FieldRef
from app.calculations.commodities.sand_casting.make import (
    calculate, calculate_casting_rate, calculate_recipe_charge, recipe_scrap_rate,
)


class SandMasters:
    def __init__(self):
        self.calls=[]
        self.recipe=[('Pig Iron',10),('C.I Return / RR',5),('Total',999),
                     ('Ferro',2),('Sub Total',999),('Filter',3),('Grand Total',999)]
        self.densities={'casting code':D(1),'Green Sand Cost':D(1)}
        self.price={'Pig Iron':D(2),'C.I Return / RR':D(1),'Ferro':D(3),'Filter':D(4)}
        self.aux={'Green Sand Cost':D(2),'Bentonite Rate':D(3),'Lustron Rate':D(4),
                  'Ceramic Filter Rate':D(5),'Chaplet Rate':D(6),'Mold Coat Rate':D(7),
                  'Shell Core Rate':D(4),'Furnace and Laddle Lining Cost /Kg':D('.5')}

    def records(self,master):
        assert master=='casting_recipie_master'
        def ingredient(label,qty,field):
            if field=='Addition in kg':return label
            assert isinstance(field,FieldRef)
            return qty if field==FieldRef('FG fixture','fixture supplier') else None
        return tuple(SimpleNamespace(value=lambda field,l=l,q=q:ingredient(l,q,field)) for l,q in self.recipe)

    def record(self,master,keys,**kwargs):
        assert master=='material_master' and kwargs.get('duplicate_policy')=='last'
        assert set(keys)=={'Material Grade'}
        return SimpleNamespace(value=lambda field: keys['Material Grade'] if field=='Material Code' else None)

    def lookup(self,master,keys,field,**kwargs):
        self.calls.append((master,keys,field,kwargs))
        if master=='material_master':
            if field=='GSM/ Density':
                key=keys.get('Material Code',keys.get('Material Grade'))
                value=self.densities.get(key)
                if value is None:raise CalculationError('master_lookup_blocked','Missing isolated density')
                return value
            assert isinstance(field,FieldRef) and field.name=='Rate/UOM'
            if 'Material Code' in keys:
                assert field.group=='fixture period'
                return self.price[keys['Material Code']]
            assert field.group=='APR#22'
            return self.aux[keys['Material Grade']]
        if master=='norms_burning_loss':
            assert keys=={'Supplier':'fixture supplier'}
            return {'Burning Loss':D('.1'),'Electricity for melting Cost/kg':D(5),
                    'Bentonite':D('.05'),'Lustron':D('.03')}[field]
        if master=='casting_complexity_master':
            assert field==FieldRef('A','Rejection %')
            pre=keys['Rejection Category']=='Casting ( Pre Machining )'
            return ({True:D('.1'),False:D('.2')} if keys['Type']=='HPML'
                    else {True:D('.3'),False:D('.4')})[pre]
        if master=='norms_casting':
            key=keys['Pericular']
            factor=D(1) if field=='HPML' else D(10)
            return factor*{'Labour Cost':D(1),'Shot Blasting ( Rs/Kg )':D(2),
                           'Fettling Cost ( Rs/Kg )':D(3)}[key]
        if master=='norms_make':return D('.1') if field=='ICC' else D(0)
        if master=='norms_make_applicability':return 'Basic Cost/Pc'
        raise AssertionError((master,keys,field))


def sand_row(**overrides):
    return {'Commodity':'SAND CASTING','Type':'MAKE','Supplier':'fixture supplier',
            'Material Grade':'FG fixture','Material Code':'casting code','RM Base':'fixture period',
            'Molding Type':'HPML','Complexity Group':'A','Box Yield (%)':D('.5'),
            'Gross Wt':D('.1'),'Part Wt':D('.08'),'Cavity':2,'No. of Cores':2,
            'Core Weight per Part':D('.01'),'Moulding Box Length':100,
            'Moulding Box Width':100,'Moulding Box Height':100,'Type of Core':'Shell Core Rate',
            'Qty/Veh':3,'Tool Cost':1,'Packing':2,'Freight':3,'_process_lines':[],**overrides}


def fixture(**overrides):
    row=sand_row(**overrides);masters=SandMasters()
    return row,CalculationContext(masters,[row]),masters


def decimal(fraction):
    fraction=F(fraction)
    return D(fraction.numerator)/D(fraction.denominator)


def assert_close(actual,expected):
    assert abs(actual-decimal(expected))<D('1e-22')


def test_complete_casting_graph_matches_independent_original_expressions():
    row,ctx,masters=fixture();result=calculate_casting_rate(row,ctx)
    # PCM7015-7025: pre-machining rejection and both distinct scrap credits.
    good=F(17,2);rejected=good/F(10)
    after=F(128)*F(11,10)+F(128,17)*rejected-rejected-(F(17)-good)
    good_rate=after/(good-rejected)
    assert result['Casting Rejection (Kg)']==D('.85')
    assert result['Rejection Credit']==D('6.4')
    assert result['Casting stage Rejection Credit']==D('.85')
    assert result['Credit Of Scrap']==D('8.5')
    assert_close(result['Good Metal Cost Rs/Kg'],good_rate)
    # PCM7027-7047, including the original percentage operator applied twice.
    green=F(14,25)
    consumed=green*F(5,100)+green*(1-F(95,100)/100)
    fresh=consumed*F(5,100)
    assert result['Total Volume of Box']==1000000
    assert result['Outer volume of component (mm3)']==200000
    assert result['Run & Risers Wt per Box']==D('.2')
    assert result['Weight of green sand/box']==D('.56')
    assert_close(result['Net Green sand weight consumed per Mould'],consumed)
    assert_close(result['Fresh Sand Addition/ MOLD'],fresh)
    # PCM7055-7082: all additives, core, lining, labour and post rejection.
    sand=fresh*2/F(1,5)
    additives=consumed*F(5,100)*3/F(1,5)+consumed*F(3,100)*4/F(1,5)+F(5+6+7)/F(1,5)
    before=F(1)+F(4,10)+F(2+3)+sand+additives+good_rate+F(1,2)
    expected=before*(1+F(1,5))-F(1,5)
    assert result['Core Cost']==D('.4')
    assert_close(result['Moulding + Core  - Total RM Cost / Kg'],before)
    assert_close(result['RATE/UOM'],expected)
    assert result['Total Cost/Kg']==result['RATE/UOM']
    assert all(not any(str(key).isdigit() for key in keys) for _,keys,_,_ in masters.calls)


def test_make_finishes_machining_and_recovery_exactly_once():
    row,ctx,masters=fixture();result=calculate(row,ctx)
    rate=result['RATE/UOM']
    rm=rate*D('.1');casting=rm*D('1.1');recovery=D('.02')*(rate/4)*D('.85')
    assert result['_casting_rate_complete'] is True
    assert result['RM Cost/Pc']==rm
    assert result['Casting Cost']==casting
    assert result['Machining Scrap Recovery']==recovery
    assert result['Total Cost/Pc']==casting+6-recovery
    assert result['Grand Total Cost/Veh']==result['Total Cost/Pc']*3
    assert not any(master=='material_master' and keys=={'material_code':'casting code'}
                   for master,keys,field,kw in masters.calls)


@pytest.mark.parametrize('molding',['HPML','ARPA'])
def test_core_and_mold_coat_conditions_follow_original(molding):
    row,ctx,_=fixture(**{'Molding Type':molding,'Core Weight per Part':0,'Type of Core':None})
    result=calculate_casting_rate(row,ctx)
    assert result['Chaplet Cost /Kg casting']==0
    assert result['Core Cost']==0
    assert result['Mold Coat Cost /Kg casting']==(0 if molding=='ARPA' else 35)
    assert result['Labour Cost']==(1 if molding=='HPML' else 10)
    assert result['Casting Rejection ( % )']==(D('.1') if molding=='HPML' else D('.3'))
    assert result['Casting Rejection ( % ) ( Post Machining )']==(D('.2') if molding=='HPML' else D('.4'))


def test_first_recipe_input_controls_shared_period_and_labour_selector():
    row,ctx,masters=fixture(**{'RM Base':'second period','Molding Type':'ARPA'})
    first=sand_row()
    ctx.rows=(first,row)
    result=calculate_casting_rate(row,ctx)
    assert result['_recipe_rm_period']=='fixture period'
    assert result['_casting_norms_molding_group']=='HPML'
    assert result['Labour Cost']==1
    assert result['Casting Rejection ( % )']==D('.3')  # Rejection uses this row's molding.


@pytest.mark.parametrize('key',['casting code','Green Sand Cost'])
def test_missing_density_blocks_only_this_graph_and_preserves_charge(key):
    row,ctx,masters=fixture();masters.densities[key]=None
    with pytest.raises(CalculationError) as error:calculate(row,ctx)
    assert error.value.code=='casting_density_missing'
    assert error.value.field=='GSM/ Density'
    assert error.value.details['partial_values']['Total Charge Kg']==17
    assert error.value.details['partial_values']['Sub Total Cost']==128


@pytest.mark.parametrize('density',[0,-1])
def test_nonpositive_density_is_rejected(density):
    row,ctx,masters=fixture();masters.densities['casting code']=D(density)
    with pytest.raises(CalculationError,match='density must be positive') as error:calculate(row,ctx)
    assert error.value.code=='casting_density_invalid'


def test_unverified_scrap_reference_cannot_fall_back_to_any_available_rate():
    row,ctx,masters=fixture()
    masters.recipe.insert(1,('Ferro',2))
    with pytest.raises(CalculationError) as error:recipe_scrap_rate(row,ctx)
    assert error.value.code=='casting_scrap_reference_unresolved'
    assert error.value.details['retained_prefix']==['Pig Iron','Ferro']


def test_missing_rejection_lookup_does_not_become_zero():
    row,ctx,masters=fixture();lookup=masters.lookup
    def missing(master,keys,field,**kwargs):
        if master=='casting_complexity_master':
            raise CalculationError('master_lookup_blocked','Missing rejection fixture')
        return lookup(master,keys,field,**kwargs)
    masters.lookup=missing
    with pytest.raises(CalculationError) as error:calculate(row,ctx)
    assert error.value.code=='master_lookup_blocked'
    assert error.value.details['partial_values']['Sub Total Cost']==128


def test_zero_box_yield_fails_instead_of_producing_zero_cost():
    row,ctx,_=fixture(**{'Box Yield (%)':0})
    with pytest.raises(CalculationError) as error:calculate(row,ctx)
    assert error.value.code=='division_by_zero'
