"""One read-only audit API using existing global settings:view permission."""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, or_, and_
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import AuditEvent, User
from app.security import require_permission, permission_pairs
from app.audit import verify_integrity
from app.schemas import AuditEventOut
from uuid import UUID

router = APIRouter(prefix="/api/reports/audit-log", tags=["Audit Logs"], dependencies=[Depends(require_permission("settings", "view"))])
T = AuditEvent.__table__


def item(row, detail=False):
    data = dict(row)
    data["integrity"] = verify_integrity(data)
    for key in ("occurred_at", "recorded_at"):
        data[key] = data[key].replace(tzinfo=timezone.utc).isoformat()
    if not detail:
        for key in ("changes", "metadata", "user_agent", "client_ip", "integrity_mac"):
            data.pop(key, None)
    return AuditEventOut.model_validate(data).model_dump(mode="json")


def utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


@router.get("/options")
def options(db: Session = Depends(get_db)):
    actors = db.execute(select(User.id, User.name).where(User.id.in_(select(T.c.actor_user_id).where(T.c.actor_user_id.is_not(None)))).order_by(User.name)).all()
    return {"actors":[{"id":r.id,"name":r.name} for r in actors],
            "resources":db.execute(select(T.c.resource).distinct().order_by(T.c.resource)).scalars().all(),
            "actions":db.execute(select(T.c.action).distinct().order_by(T.c.action)).scalars().all(),
            "outcomes":["attempt","success","failure","denied","observed"]}


@router.get("")
def events(page: int = Query(1, ge=1, le=1000), page_size: int = Query(50, ge=1, le=200),
           actor_user_id: int | None = None, session_ref: str = Query("", max_length=64), resource: str = Query("", max_length=100),
           action: str = Query("", max_length=100), outcome: str = Query("", max_length=16), q: str = Query("", max_length=200),
           date_from: datetime | None = None, date_to: datetime | None = None,
           anchor: int | None = Query(None, ge=1), before_id: int | None = Query(None, ge=1),
           before_time: datetime | None = None, db: Session = Depends(get_db)):
    if outcome and outcome not in {"attempt","success","failure","denied","observed"}:
        raise HTTPException(422, "Invalid audit outcome")
    if date_from and date_to and utc(date_from) > utc(date_to):
        raise HTTPException(422, "Date range is reversed")
    if q and not (date_from and date_to):
        raise HTTPException(422, "Search requires both date range limits")
    if bool(before_id) != bool(before_time):
        raise HTTPException(422, "Cursor requires both before_id and before_time")
    anchor = anchor or db.execute(select(func.max(T.c.id))).scalar() or 0
    conditions = [T.c.id <= anchor]
    for column, value in [(T.c.actor_user_id,actor_user_id),(T.c.session_ref,session_ref),(T.c.resource,resource),(T.c.action,action),(T.c.outcome,outcome)]:
        if value is not None and value != "":
            conditions.append(column == value)
    if date_from: conditions.append(T.c.occurred_at >= utc(date_from))
    if date_to: conditions.append(T.c.occurred_at <= utc(date_to))
    if q:
        pattern = "%"+q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")+"%"
        conditions.append(or_(T.c.summary.like(pattern, escape="\\"),T.c.target_id.like(pattern,escape="\\"),T.c.action.like(pattern,escape="\\")))
    count = db.execute(select(func.count()).select_from(T).where(*conditions)).scalar()
    if before_id:
        conditions.append(or_(T.c.occurred_at < utc(before_time), and_(T.c.occurred_at == utc(before_time), T.c.id < before_id)))
    query = select(T).where(*conditions).order_by(T.c.occurred_at.desc(),T.c.id.desc()).limit(page_size)
    if not before_id: query = query.offset((page-1)*page_size)
    rows = db.execute(query).mappings().all()
    return {"items":[item(row) for row in rows], "page":page,"page_size":page_size,"total":count,"total_pages":max(1,(count+page_size-1)//page_size),"anchor":anchor,
            "next_cursor":{"before_time":rows[-1]["occurred_at"].replace(tzinfo=timezone.utc).isoformat(),"before_id":rows[-1]["id"]} if len(rows)==page_size else None}


def _related_calculation(row, user, can_view):
    """A ledger entry never grants access to another account's calculation files."""
    metadata = row.get('metadata') or {}
    if not isinstance(metadata, dict): metadata = {}
    raw_id = metadata.get('run_id') or (row.get('target_id') if row.get('resource') == 'calculations' else None)
    try: run_id = str(UUID(str(raw_id)))
    except (ValueError, TypeError, AttributeError): run_id = None
    result = {'run_id': run_id, 'input_name': str(metadata.get('input_name') or '')[:255],
              'calculation_status': metadata.get('calculation_status'), 'related_result_url': None}
    if not run_id or not can_view: return result
    # Import the existing single artifact owner/root, including test overrides.
    from app.api import calculations
    from app.calculations import development as dev
    try:
        manifest = dev.history_item(dev.read_manifest(dev.run_folder(calculations.ROOT, user.id, run_id), user.id))
    except (ValueError, OSError): return result
    result.update(input_name=manifest['input_name'], calculation_status=manifest['status'],
                  related_result_url='/calculations/'+run_id)
    return result


@router.get('/activity')
def activity(page: int = Query(1, ge=1, le=1000), page_size: int = Query(25, ge=1, le=100),
             q: str = Query('', max_length=200), action: str = Query('', max_length=100),
             actor_user_id: int | None = Query(None,ge=1), resource: str = Query('',max_length=100),
             session_ref: str = Query('',max_length=64), status: str = Query('', max_length=20), date_from: datetime | None = None,
             date_to: datetime | None = None, db: Session = Depends(get_db),
             user=Depends(require_permission('settings', 'view'))):
    """Business-facing projection of the existing immutable centralized ledger."""
    if date_from and date_to and utc(date_from) > utc(date_to):
        raise HTTPException(422, 'The start date must precede the end date')
    if status and status not in {'calculated','partial','blocked','unverified','error','attempt','success','failure','denied','observed'}:
        raise HTTPException(422, 'Select a valid activity status')
    conditions = []
    if actor_user_id is not None: conditions.append(T.c.actor_user_id == actor_user_id)
    if resource: conditions.append(T.c.resource == resource)
    if session_ref: conditions.append(T.c.session_ref == session_ref)
    if action: conditions.append(T.c.action == action)
    if date_from: conditions.append(T.c.occurred_at >= utc(date_from))
    if date_to: conditions.append(T.c.occurred_at <= utc(date_to))
    if status:
        conditions.append(or_(T.c.outcome == status, T.c.metadata['calculation_status'].as_string() == status))
    if q.strip():
        pattern = '%'+q.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        conditions.append(or_(T.c.summary.like(pattern,escape='\\'),T.c.action.like(pattern,escape='\\'),
            T.c.target_id.like(pattern,escape='\\'), T.c.metadata['input_name'].as_string().like(pattern,escape='\\'),
            User.name.like(pattern,escape='\\')))
    joined = T.outerjoin(User, User.id == T.c.actor_user_id)
    total = db.execute(select(func.count()).select_from(joined).where(*conditions)).scalar() or 0
    rows = db.execute(select(T, User.name.label('actor_name')).select_from(joined).where(*conditions)
        .order_by(T.c.occurred_at.desc(),T.c.id.desc()).limit(page_size).offset((page-1)*page_size)).mappings().all()
    can_view = ('boms','view') in permission_pairs(db,user)
    output = []
    for row in rows:
        original = dict(row)
        actor_name = original.pop('actor_name')
        data = item(original)
        data.update(actor_name=actor_name or ('System' if row['actor_user_id'] is None else 'User '+str(row['actor_user_id'])),
                    status=row['outcome'], description=row['summary'], **_related_calculation(original,user,can_view))
        output.append(data)
    return {'items': output, 'total': total, 'page':page,'page_size':page_size,
            'total_pages':max(1,(total+page_size-1)//page_size)}


@router.get("/{event_id}")
def detail(event_id: str, db: Session = Depends(get_db)):
    row = db.execute(select(T).where(T.c.event_id == event_id)).mappings().first()
    if not row:
        raise HTTPException(404, "Audit event not found")
    return item(row, detail=True)
