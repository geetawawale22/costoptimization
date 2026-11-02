"""Read-only visibility into the existing Python calculation implementation.

This is not a formula repository or evaluator. Database master values remain in
Step 6. Editing a legacy database formula cannot change this Python engine.
"""
import ast
import hashlib
import re
from pathlib import Path
from fastapi import APIRouter, Depends, Query
from app.security import require_permission
from app.calculations.dispatcher import BRANCHES
from app.calculations.consumption_rules import FORMULAS

router = APIRouter(prefix='/api/formulas', tags=['Formula Management'],
    dependencies=[Depends(require_permission('settings','view'))])
ROOT = Path(__file__).resolve().parents[1] / 'calculations'
READ_ONLY_REASON = ('Cost calculations currently use reviewed Python expressions. '
    'Database masters supply rates, conditions and exact allowlisted consumption expressions. Formula editing, activation and '
    'version changes are unavailable here because database formula records are not consumed by this engine.')


def _call_name(node):
    return node.func.attr if isinstance(node.func,ast.Attribute) else node.func.id if isinstance(node.func,ast.Name) else ''


def _constant(node):
    return node.value if isinstance(node,ast.Constant) and isinstance(node.value,str) else None


def _references(function):
    inputs=set(); masters=set()
    for node in ast.walk(function):
        if not isinstance(node,ast.Call): continue
        name=_call_name(node)
        if name in {'get','required','optional','number'} and len(node.args)>1:
            value=_constant(node.args[1])
            if value: inputs.add(value)
        if name in {'require_master','lookup','rows','require_record'} and node.args:
            value=_constant(node.args[0])
            if value: masters.add(value)
    return sorted(inputs),sorted(masters)


def _output_name(node,parents):
    current=node
    for _ in range(8):
        parent=parents.get(current)
        if parent is None: break
        if isinstance(parent,(ast.Assign,ast.AnnAssign)):
            target=parent.targets[0] if isinstance(parent,ast.Assign) else parent.target
            if isinstance(target,ast.Subscript):
                key=_constant(target.slice)
                if key:return key
            return ast.unparse(target)
        if isinstance(parent,ast.Dict):
            for key,value in zip(parent.keys,parent.values):
                if value is current:
                    label=_constant(key)
                    if label:return label
        if isinstance(parent,ast.Return):return 'Result'
        current=parent
    return 'Intermediate expression'


def inventory():
    """Parse trusted installed source only; never execute uploaded/saved expressions."""
    items=[]
    paths=sorted(path for path in ROOT.rglob('*.py') if (
        path.relative_to(ROOT).parts[0] in {'commodities','types','merging'} or
        path.name in {'consumption.py','common.py','context.py'}))
    for path in paths:
        source=path.read_text(encoding='utf-8-sig'); tree=ast.parse(source)
        relative=path.relative_to(ROOT).as_posix(); module=relative[:-3].replace('/','.')
        parents={child:parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        branch=[b for b in BRANCHES.values() if b.module==module]
        version=hashlib.sha256(source.encode('utf-8')).hexdigest()[:16]
        for function in (node for node in ast.walk(tree) if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))):
            inputs,masters=_references(function)
            for expression in ast.walk(function):
                if not isinstance(expression,(ast.BinOp,ast.IfExp)):continue
                # Include each complete arithmetic expression once, not every suboperator.
                parent=parents.get(expression)
                if isinstance(parent,(ast.BinOp,ast.IfExp)):continue
                # String/list concatenation belongs to representation, not cost formula visibility.
                if isinstance(expression,ast.BinOp) and any(isinstance(v,(ast.List,ast.Tuple,ast.JoinedStr)) or
                    (isinstance(v,ast.Constant) and isinstance(v.value,str)) for v in (expression.left,expression.right)):continue
                text=ast.get_source_segment(source,expression) or ast.unparse(expression)
                if len(text)>5000:continue
                conditions=[];ancestor=parents.get(expression)
                while ancestor is not None and ancestor is not function:
                    if isinstance(ancestor,ast.If):conditions.append(ast.unparse(ancestor.test))
                    ancestor=parents.get(ancestor)
                label=_output_name(expression,parents)
                key=hashlib.sha256((relative+':'+function.name+':'+str(expression.lineno)+':'+text).encode()).hexdigest()[:24]
                items.append({'key':key,'name':label,'kind':'python_expression','expression':text,
                    'commodity':', '.join(dict.fromkeys(b.commodity or 'Type only' for b in branch)) or 'Shared',
                    'type':', '.join(dict.fromkeys(b.type or 'Blank' for b in branch)) or 'Shared',
                    'inputs':inputs,'master_dependencies':masters,'conditions':conditions,
                    'where_used':[module+'::'+function.name], 'source_file':'app/calculations/'+relative,
                    'source_function':function.name,'source_line':expression.lineno,
                    'source_end_line':expression.end_lineno,'version':version,
                    'versions':[{'version':version,'description':'Current installed source'}],
                    'editable':False,'active':None,'dependency_scope':'Enclosing source function; shared helpers may add lookups',
                    'activation':'Selected by input conditions',
                    'verification':'Source visibility; numerical parity is recorded separately'})
    for (master,field),expressions in sorted(FORMULAS.items()):
        for expression in sorted(expressions):
            key=hashlib.sha256((master+':'+field+':'+expression).encode()).hexdigest()[:24]
            items.append({'key':key,'name':field,'kind':'reviewed_consumption_expression','expression':expression,
                'commodity':'Shared','type':'Shared','inputs':sorted(set(re.findall(r'\{([^{}]+)\}',expression))),
                'master_dependencies':[master],'conditions':['Selected by matching database master conditions'],
                'where_used':['consumption::select_rule'], 'source_file':'app/calculations/consumption_rules.py',
                'source_function':'FORMULAS','source_line':None,'source_end_line':None,'version':'reviewed-source',
                'versions':[{'version':'reviewed-source','description':'Current reviewed source expression'}],
                'editable':False,'active':None,'activation':'Selected by matching master conditions',
                'verification':'Reviewed expression allowlist; rates remain database values'})
    return items


@router.get('')
def formulas(q: str = Query('',max_length=200), commodity: str = Query('',max_length=80),
             kind: str = Query('',max_length=50), page: int = Query(1,ge=1),
             page_size: int = Query(100,ge=1,le=2000)):
    items=inventory()
    if commodity:items=[row for row in items if commodity.casefold() in row['commodity'].casefold()]
    if kind:items=[row for row in items if row['kind']==kind]
    if q.strip():
        needle=q.strip().casefold()
        items=[row for row in items if needle in ' '.join(str(row.get(key,'')) for key in
            ('name','expression','commodity','type','inputs','master_dependencies','where_used')).casefold()]
    total=len(items);start=(page-1)*page_size
    return {'items':items[start:start+page_size],'total':total,'page':page,'page_size':page_size,
        'read_only':True,'runtime_source':'python','database_formulas_active':False,
        'reason':READ_ONLY_REASON,'read_only_reason':READ_ONLY_REASON}
