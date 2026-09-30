from typing import List, Dict, Any
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.deps import get_current_user, require_permission
from app.models.mixins import stamp_create, stamp_update
from app.models.calculation import OutputTemplate, Calculation, CalculationResult
from app.services.pcm_sheet_metal import TEMPLATE_NAME, DEFAULT_TEMPLATE
from app.models.notification import Notification
from app.models.bom import Bom
from app.models.user import User
from app.schemas.user import UserOut  # noqa: F401 (kept for potential response reuse)
from app.schemas.calculation import CalculationOut  # noqa: F401

router = APIRouter(tags=["Outputs, Dashboard & Notifications"])


class OutputTemplateIn(BaseModel):
    name: str
    format_type: str = "xlsx"
    layout: Dict[str, Any] = {}


class OutputTemplateOut(OutputTemplateIn):
    id: int
    model_config = {"from_attributes": True}


class NotificationOut(BaseModel):
    id: int
    message: str
    is_read: bool
    model_config = {"from_attributes": True}


# ---------- Output Templates ----------
@router.get("/output-templates", response_model=List[OutputTemplateOut])
def list_templates(db: Session = Depends(get_db), _=Depends(require_permission("settings", "view"))):
    return db.query(OutputTemplate).filter(OutputTemplate.is_deleted.is_(False)).all()


@router.post("/output-templates", response_model=OutputTemplateOut)
def create_template(payload: OutputTemplateIn, db: Session = Depends(get_db),
                     user=Depends(require_permission("settings", "update"))):
    t = OutputTemplate(**payload.model_dump())
    stamp_create(t, user.id)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@router.put("/output-templates/{template_id}", response_model=OutputTemplateOut)
def update_template(template_id: int, payload: OutputTemplateIn, db: Session = Depends(get_db),
                     user=Depends(require_permission("settings", "update"))):
    t = db.query(OutputTemplate).filter(OutputTemplate.id == template_id, OutputTemplate.is_deleted.is_(False)).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    for f, v in payload.model_dump().items():
        setattr(t, f, v)
    stamp_update(t, user.id)
    db.commit()
    db.refresh(t)
    return t


@router.post("/output-templates/pcm-sheet-metal-forging/reset", response_model=OutputTemplateOut)
def reset_pcm_sheet_metal_template(db: Session = Depends(get_db),
                                    user=Depends(require_permission("settings", "update"))):
    template = db.query(OutputTemplate).filter(OutputTemplate.name == TEMPLATE_NAME).first()
    if not template:
        template = OutputTemplate(name=TEMPLATE_NAME, format_type="xlsx", layout=DEFAULT_TEMPLATE)
        stamp_create(template, user.id)
        db.add(template)
    else:
        template.format_type = "xlsx"
        template.layout = DEFAULT_TEMPLATE
        template.is_deleted = False
        stamp_update(template, user.id)
    db.commit()
    db.refresh(template)
    return template


# ---------- Dashboard ----------
@router.get("/dashboard/summary")
def dashboard_summary(db: Session = Depends(get_db), _=Depends(require_permission("history", "view"))):
    base = db.query(Calculation).filter(Calculation.is_deleted.is_(False))
    total = base.count()
    completed = base.filter(Calculation.status == "Completed").count()
    failed = base.filter(Calculation.status == "Failed").count()
    avg_cost = (db.query(func.avg(CalculationResult.total_cost))
                  .filter(CalculationResult.is_deleted.is_(False)).scalar() or 0)
    return {"total_calculations": total, "completed": completed, "failed": failed,
            "avg_total_cost": round(avg_cost, 2)}


@router.get("/dashboard/commodity-distribution")
def commodity_distribution(db: Session = Depends(get_db), _=Depends(require_permission("history", "view"))):
    results = (db.query(CalculationResult.breakdown)
                 .filter(CalculationResult.is_deleted.is_(False)).all())
    totals: Dict[str, float] = {}
    for (breakdown,) in results:
        for commodity, cost in (breakdown or {}).items():
            totals[commodity] = totals.get(commodity, 0) + cost
    return totals


@router.get("/reports/audit-log")
def audit_log(
    db: Session = Depends(get_db),
    _=Depends(require_permission("settings", "view")),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    q: str = "",
    entity: str = "",
    action: str = "",
):
    """Read-only audit trail from real audited tables.

    There is no separate event table yet, so this endpoint exposes the audit
    fields that already exist on persisted records and returns the paged shape
    expected by the Audit Logs page.
    """
    users = {
        user.id: (user.name or user.email or f"User {user.id}")
        for user in db.query(User).filter(User.is_deleted.is_(False)).all()
    }

    def actor(user_id):
        return users.get(user_id) or (f"User {user_id}" if user_id else "System")

    rows = []
    for bom in db.query(Bom).filter(Bom.is_deleted.is_(False)).all():
        rows.append({
            "entity": "bom",
            "action": "created",
            "details": bom.file_path,
            "status": bom.status,
            "actor": actor(bom.created_by),
            "created_at": bom.created_at,
        })
        if bom.modified_at and bom.modified_at != bom.created_at:
            rows.append({
                "entity": "bom",
                "action": "updated",
                "details": bom.file_path,
                "status": bom.status,
                "actor": actor(bom.modified_by),
                "created_at": bom.modified_at,
            })

    for calculation in db.query(Calculation).filter(Calculation.is_deleted.is_(False)).all():
        rows.append({
            "entity": "calculation",
            "action": "created",
            "details": f"Calculation {calculation.id}",
            "status": calculation.status,
            "actor": actor(calculation.created_by),
            "created_at": calculation.created_at,
        })
        if calculation.modified_at and calculation.modified_at != calculation.created_at:
            rows.append({
                "entity": "calculation",
                "action": "updated",
                "details": f"Calculation {calculation.id}",
                "status": calculation.status,
                "actor": actor(calculation.modified_by),
                "created_at": calculation.modified_at,
            })

    for template in db.query(OutputTemplate).filter(OutputTemplate.is_deleted.is_(False)).all():
        rows.append({
            "entity": "output_template",
            "action": "created",
            "details": template.name,
            "status": template.format_type,
            "actor": actor(template.created_by),
            "created_at": template.created_at,
        })
        if template.modified_at and template.modified_at != template.created_at:
            rows.append({
                "entity": "output_template",
                "action": "updated",
                "details": template.name,
                "status": template.format_type,
                "actor": actor(template.modified_by),
                "created_at": template.modified_at,
            })

    entities = sorted({row["entity"] for row in rows})
    if entity:
        rows = [row for row in rows if row["entity"] == entity]
    if action:
        rows = [row for row in rows if row["action"] == action]
    if q:
        needle = q.casefold()
        rows = [
            row for row in rows
            if any(needle in str(row.get(key) or "").casefold()
                   for key in ("entity", "action", "details", "status", "actor"))
        ]

    rows.sort(key=lambda row: row["created_at"] or datetime.min, reverse=True)
    total = len(rows)
    start = (page - 1) * page_size
    items = rows[start:start + page_size]
    total_pages = max(1, (total + page_size - 1) // page_size)
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "entities": entities,
    }


# ---------- Notifications ----------
@router.get("/notifications", response_model=List[NotificationOut])
def list_notifications(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return (db.query(Notification)
              .filter(Notification.user_id == user.id, Notification.is_deleted.is_(False))
              .order_by(Notification.created_at.desc()).all())


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    n = db.query(Notification).filter(Notification.id == notification_id, Notification.is_deleted.is_(False)).first()
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    n.is_read = True
    stamp_update(n, user.id)
    db.commit()
    db.refresh(n)
    return n
