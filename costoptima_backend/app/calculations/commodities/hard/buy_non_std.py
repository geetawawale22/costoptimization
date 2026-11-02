"""HARD producer arithmetic, followed by its separate purchasing wrapper."""
from .make import calculate as make_calculation
from ..shared import finish
SOURCE={'function':'make_bns;make_bns_prev;merge','lines':'10949-11169;18407-18684'}
def calculate(record,context):
    values=make_calculation({**record,'Sector & Location':'AD'},context,buy_non_std=True)
    factor=context.require_master('norms_pnf',{'pnf':'BNS_Make'},'factor')
    return finish(record,values,pnf=values['Total Cost/Pc']*factor)
