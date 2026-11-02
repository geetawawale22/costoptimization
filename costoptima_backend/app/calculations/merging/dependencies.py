"""Deterministic producer-before-consumer ordering with explicit cycle checks."""
from ..common import CalculationError,normalized,get

def order(nodes,edges):
    nodes=list(nodes);known=set(nodes)
    if len(known)!=len(nodes):raise CalculationError('duplicate_node','Calculation record identifiers must be unique')
    pending={n:set(edges.get(n,())) for n in nodes}
    missing={d for ds in pending.values() for d in ds if d not in known}
    if missing:raise CalculationError('missing_dependency',f'Missing dependency nodes: {sorted(missing)}')
    result=[]
    while pending:
        ready=[n for n in nodes if n in pending and not pending[n]]
        if not ready:raise CalculationError('dependency_cycle','Calculation dependency cycle detected')
        for n in ready:
            result.append(n);pending.pop(n)
            for deps in pending.values():deps.discard(n)
    return result


def plan(records,*,isolate=False):
    by_id={r['_record_id']:r for r in records};edges={key:set() for key in by_id}
    for key,r in by_id.items():
        type_=normalized(r.get('_ownership_type',get(r,'Type')));end=normalized(get(r,'End Item'))
        if type_=='end item':
            edges[key].update(k for k,v in by_id.items() if k!=key and normalized(v.get('_ownership_type',get(v,'Type')))!='end item' and normalized(get(v,'End Item'))==end)
        if type_=='special process':
            # Paint groups may contain multiple verified Make Part no references.
            # SOFT Sr producers are calculated internally from their raw details;
            # no artificial Type=ASSY node or self-dependency is introduced.
            mode=normalized(get(r,'Special Process') or get(r,'Process / Assy Name'))
            if normalized(get(r,'Commodity'))!='hard' and mode!='make paint':continue
            parts=[]
            for item in (r,*r.get('_paint_components',())):
                part=get(item,'Make Part no')
                if part not in (None,'') and normalized(part) not in {normalized(p) for p in parts}:
                    parts.append(part)
            for part in parts:
                choices=[k for k,v in by_id.items() if normalized(get(v,'Commodity'))=='hard' and
                         normalized(v.get('_ownership_type',get(v,'Type')))=='make' and
                         normalized(get(v,'Customer No'))==normalized(part) and normalized(get(v,'End Item'))==end]
                if len(choices)!=1:
                    error=CalculationError('missing_or_duplicate_paint_producer','Paint must identify one same-run HARD MAKE producer',customer_no=str(part),consumer_record_id=key)
                    if not isolate:raise error
                    r.setdefault('_dependency_errors',[]).append(error.as_dict())
                    continue
                if choices[0]==key:
                    raise CalculationError('dependency_cycle','A special-process owner cannot also be its MAKE producer')
                edges[key].add(choices[0])
    if not isolate:return order(list(by_id),edges),edges
    # Preserve valid independent components. Block failed consumers and their
    # dependants; never treat an unresolved edge as an absent/zero cost.
    blocked={key for key,r in by_id.items() if r.get('_dependency_errors')}
    while True:
        expanded=blocked|{key for key,deps in edges.items() if deps&blocked}
        if expanded==blocked:break
        blocked=expanded
    pending={key:set(deps) for key,deps in edges.items() if key not in blocked}
    result=[]
    while pending:
        ready=[key for key in by_id if key in pending and not pending[key]]
        if not ready:
            # Remaining nodes are a cycle or depend on one. Other components
            # already have a valid producer-before-consumer order.
            for key in pending:
                by_id[key].setdefault('_dependency_errors',[]).append(
                    CalculationError('dependency_cycle','Calculation dependency cycle or its dependant is unresolved',record_id=key).as_dict())
            blocked.update(pending);break
        for key in ready:
            result.append(key);pending.pop(key)
            for deps in pending.values():deps.discard(key)
    for key in blocked:
        by_id[key]['_dependency_blocked']=True
        if not by_id[key].get('_dependency_errors'):
            by_id[key]['_dependency_errors']=[CalculationError('failed_dependency','A required producer cannot be scheduled',record_id=key).as_dict()]
    return result+[key for key in by_id if key in blocked],edges
