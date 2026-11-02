"""Original recipe charge boundaries, supplier/grade fields and database pricing."""
from decimal import Decimal as D
from types import SimpleNamespace
import pytest
from app.calculations.context import CalculationContext
from app.calculations.common import CalculationError
from app.calculations.masters import FieldRef
from app.calculations.commodities.sand_casting.make import calculate_recipe_charge,calculate


def fixture():
    rows=[('Pig Iron',10),('C.I Return / RR',5),('Total',999),('Ferro',2),('Sub Total',999),('Filter',3),('Grand Total',999)]
    master=SimpleNamespace()
    def value(label,qty,field):
        if field=='Addition in kg':return label
        assert field==FieldRef('FG fixture','supplier fixture')
        return qty
    master.records=lambda code:tuple(SimpleNamespace(value=lambda field,label=l,qty=q:value(label,qty,field)) for l,q in rows)
    master.record=lambda code,keys,**kw:SimpleNamespace(value=lambda field:keys['Material Grade'])
    def lookup(code,keys,field,**kw):
        if code=='material_master':return {'Pig Iron':D(2),'C.I Return / RR':D(1),'Ferro':D(3),'Filter':D(4)}[keys['Material Code']]
        return D('.1') if field=='Burning Loss' else D(5)
    master.lookup=lookup
    row={'Supplier':'supplier fixture','Material Grade':'FG fixture','RM Base':'fixture period'}
    return row,CalculationContext(master,[row])


def test_recipe_excludes_aggregate_rows_and_separates_consumables():
    row,ctx=fixture();values=calculate_recipe_charge(row,ctx)
    assert values['Total Charge Kg']==17
    assert values['Total RM cost Rs/Charge']==31
    assert values['Cost Of Consumable']==12
    assert values['Power cost']==85
    assert values['Sub Total Cost']==128
    assert values['Total with burning loss cost']==D('140.8')
    assert [r['Material Grade'] for r in values['_recipe_ingredients']]==['Pig Iron','C.I Return / RR','Ferro','Filter']


def test_unmapped_final_make_rate_is_blocked_with_partial_charge_values():
    row,ctx=fixture()
    with pytest.raises(CalculationError) as error:calculate(row,ctx)
    assert error.value.code=='casting_scrap_reference_unresolved'
    assert error.value.details['partial_values']['Total Charge Kg']==17
