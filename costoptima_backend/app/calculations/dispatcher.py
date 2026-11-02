"""One source-backed Commodity/Type registry, separate from master merging."""
from dataclasses import dataclass
from importlib import import_module
from .common import CalculationError

@dataclass(frozen=True)
class Branch:
    commodity:str
    type:str
    module:str
    scope:str="implemented arithmetic; full legacy parity unverified"
    def calculate(self,record,context):return import_module("app.calculations."+self.module).calculate(record,context)

_SPEC=[
 ('HARD','MAKE','commodities.hard.make'),('HARD','ASSY','commodities.hard.assy'),
 ('HARD','BUY NON STD','commodities.hard.buy_non_std'),('HARD','SPECIAL PROCESS','commodities.hard.special_process'),
 ('SOFT','MAKE','commodities.soft.make'),('SOFT','ASSY','commodities.soft.assy'),
 ('SOFT','BUY NON STD','commodities.soft.buy_non_std'),('SOFT','SPECIAL PROCESS','commodities.soft.special_process'),
 ('STAMPING','MAKE','commodities.stamping.make'),('STAMPING','BUY NON STD','commodities.stamping.buy_non_std'),
 ('FORGING','MAKE','commodities.forging.make'),('FORGING','BUY NON STD','commodities.forging.buy_non_std'),
 ('FORGING','PROCESS','commodities.forging.make'),
 ('SAND CASTING','MAKE','commodities.sand_casting.make'),('SAND CASTING','BUY NON STD','commodities.sand_casting.buy_non_std'),
 ('SAND CASTING','PROCESS','commodities.sand_casting.process'),
 ('GDC','MAKE','commodities.gdc.make'),('GDC','BUY NON STD','commodities.gdc.buy_non_std'),('GDC','PROCESS','commodities.gdc.process'),
 ('PDC','MAKE','commodities.pdc.make'),('PDC','BUY NON STD','commodities.pdc.buy_non_std'),('PDC','PROCESS','commodities.pdc.process'),
 ('PCBA','ASSY','commodities.pcba.assy'),('THERMAL','MAKE','commodities.thermal.make'),
 ('THERMAL','BUY NON STD','commodities.thermal.non_make'),
 ('','BUY STD','types.buy_std'),('','BUY NON STD','types.buy_non_std'),('','CONSIGN','types.consign'),
 ('','PKG','types.pkg'),('','FRT','types.frt'),('','END ITEM','types.end_item'),('','','types.blank')]
BRANCHES={(c,t):Branch(c,t,m) for c,t,m in _SPEC}

def dispatch(commodity,type_):
    c,t=str(commodity or '').strip().upper(),str(type_ or '').strip().upper()
    if t=='END ITEM':return BRANCHES['','END ITEM']
    found=BRANCHES.get((c,t))
    if found:return found
    # Primary family loops contain an explicit generic else, including blank Type.
    generic={'SOFT':'commodities.soft.assy','STAMPING':'commodities.stamping.make',
             'GDC':'commodities.gdc.make','PDC':'commodities.pdc.make','PCBA':'commodities.pcba.assy',
             'FORGING':'commodities.forging.make',
             'THERMAL':'commodities.thermal.non_make'}
    if c in generic:return Branch(c,t,generic[c],"source family fallback; full parity unverified")
    raise CalculationError('unsupported_branch',f'No source-proven Commodity/Type branch for {c!r}/{t!r}')


def catalogue():
    return [dict(commodity=b.commodity,type=b.type,module=b.module,scope=b.scope,
                 implementation_status=('blocked_dependency' if b.module.endswith('types.blank') else 'partial_source_formula_port'),
                 dispatcher_tested=True,formula_tested=b.module in {'types.buy_std','types.pkg','types.frt','types.consign','commodities.hard.assy','commodities.hard.make','commodities.hard.buy_non_std','commodities.soft.assy','commodities.stamping.make','commodities.forging.make','commodities.gdc.make','commodities.pdc.make','commodities.pcba.assy','commodities.soft.make','commodities.soft.special_process','commodities.hard.special_process','commodities.thermal.make','commodities.thermal.non_make','commodities.sand_casting.make','commodities.sand_casting.process','commodities.sand_casting.buy_non_std'},legacy_numerically_verified=False,production_enabled=False) for b in BRANCHES.values()]
