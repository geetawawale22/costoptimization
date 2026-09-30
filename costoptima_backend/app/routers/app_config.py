from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import distinct
from sqlalchemy.orm import Session

from app.core.deps import require_permission
from app.core.pagination import DEFAULT_PAGE_SIZE, PaginatedResponse, paginate_query
from app.database import get_db
from app.models.app_config import AppConfig
from app.models.mixins import stamp_create, stamp_update
from app.schemas.app_config import (
    AppConfigCreate, AppConfigOptionsOut, AppConfigOut, AppConfigUpdate, AppConfigValueOut,
    PREDEFINED_CONFIG_GROUPS,
)
from app.services.app_config_service import (
    MASKED_SECRET, config_to_dict, discover_config_keys, exposed_value, get_config_value,
    normalize_group, stored_value,
)

router = APIRouter(prefix="/app-config", tags=["Application Configuration"])


def _find_by_id(db: Session, config_id: int) -> AppConfig:
    row = db.query(AppConfig).filter(AppConfig.id == config_id, AppConfig.is_deleted.is_(False)).first()
    if not row:
        raise HTTPException(status_code=404, detail="Configuration entry not found")
    return row


def _duplicate_exists(db: Session, group: str, key: str, exclude_id: int | None = None) -> bool:
    q = db.query(AppConfig).filter(AppConfig.group == group, AppConfig.key == key, AppConfig.is_deleted.is_(False))
    if exclude_id:
        q = q.filter(AppConfig.id != exclude_id)
    return db.query(q.exists()).scalar()


@router.get("/options", response_model=AppConfigOptionsOut)
def app_config_options(db: Session = Depends(get_db), _=Depends(require_permission("settings", "view"))):
    db_groups = [g for (g,) in db.query(distinct(AppConfig.group)).filter(AppConfig.is_deleted.is_(False)).all() if g]
    db_keys = [f"{row.group}.{row.key}" for row in db.query(AppConfig).filter(AppConfig.is_deleted.is_(False)).order_by(AppConfig.group, AppConfig.key).all()]
    detected = discover_config_keys()
    return {
        "groups": sorted(set(PREDEFINED_CONFIG_GROUPS + db_groups)),
        "keys": sorted(set(db_keys + detected)),
        "detected_keys": detected,
        "predefined_groups": PREDEFINED_CONFIG_GROUPS,
    }


@router.get("", response_model=PaginatedResponse[AppConfigOut])
def list_app_config(page: int = 1, page_size: int = DEFAULT_PAGE_SIZE, q: str = "",
                    group: str = "", key: str = "", active: str = Query("", description="true/false/blank"),
                    db: Session = Depends(get_db), _=Depends(require_permission("settings", "view"))):
    query = db.query(AppConfig).filter(AppConfig.is_deleted.is_(False))
    if q:
        like = f"%{q}%"
        query = query.filter((AppConfig.key.ilike(like)) | (AppConfig.group.ilike(like)) |
                             (AppConfig.description.ilike(like)) | (AppConfig.content.ilike(like)))
    if group:
        query = query.filter(AppConfig.group == normalize_group(group))
    if key:
        query = query.filter(AppConfig.key.ilike(f"%{key}%"))
    if active.lower() in {"true", "false"}:
        query = query.filter(AppConfig.is_active.is_(active.lower() == "true"))
    query = query.order_by(AppConfig.group, AppConfig.key)
    items, total, page, page_size, total_pages = paginate_query(query, page, page_size)
    items = [config_to_dict(item, reveal_secret=False) for item in items]
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size, total_pages=total_pages)


@router.post("", response_model=AppConfigOut)
def create_app_config(payload: AppConfigCreate, db: Session = Depends(get_db),
                      user=Depends(require_permission("settings", "update"))):
    group = normalize_group(payload.group)
    key = payload.key.strip()
    if _duplicate_exists(db, group, key):
        raise HTTPException(status_code=400, detail="Configuration group + key already exists")
    try:
        row = AppConfig(
            group=group,
            key=key,
            value=stored_value(payload.value, payload.type, payload.is_secret or payload.type == "secret"),
            content=payload.content,
            type=payload.type,
            description=payload.description,
            is_secret=bool(payload.is_secret or payload.type == "secret"),
            is_active=payload.is_active,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    stamp_create(row, user.id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return config_to_dict(row, reveal_secret=False)


@router.get("/value", response_model=AppConfigValueOut)
def get_app_config_value(group: str = Query("GENERAL"), key: str = Query(...),
                         db: Session = Depends(get_db), _=Depends(require_permission("settings", "view"))):
    row = (db.query(AppConfig)
             .filter(AppConfig.group == normalize_group(group), AppConfig.key == key,
                     AppConfig.is_deleted.is_(False), AppConfig.is_active.is_(True))
             .first())
    if not row:
        raise HTTPException(status_code=404, detail="Active configuration key not found")
    return {"group": row.group, "key": row.key, "value": exposed_value(row, reveal_secret=True),
            "content": row.content, "type": row.type, "is_secret": row.is_secret, "is_active": row.is_active}


@router.get("/{config_id}", response_model=AppConfigOut)
def get_app_config(config_id: int, db: Session = Depends(get_db), _=Depends(require_permission("settings", "view"))):
    return config_to_dict(_find_by_id(db, config_id), reveal_secret=False)


@router.put("/{config_id}", response_model=AppConfigOut)
def update_app_config(config_id: int, payload: AppConfigUpdate, db: Session = Depends(get_db),
                      user=Depends(require_permission("settings", "update"))):
    row = _find_by_id(db, config_id)
    data = payload.model_dump(exclude_unset=True)
    new_group = normalize_group(data.get("group", row.group))
    new_key = (data.get("key", row.key) or "").strip()
    if _duplicate_exists(db, new_group, new_key, exclude_id=row.id):
        raise HTTPException(status_code=400, detail="Configuration group + key already exists")
    new_type = data.get("type", row.type)
    new_is_secret = data.get("is_secret", row.is_secret) or new_type == "secret"
    if "value" in data and not (row.is_secret and data["value"] == MASKED_SECRET):
        try:
            row.value = stored_value(data["value"], new_type, new_is_secret)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if "group" in data:
        row.group = new_group
    if "key" in data:
        row.key = new_key
    if "type" in data:
        row.type = new_type
    if "content" in data:
        row.content = data["content"]
    if "description" in data:
        row.description = data["description"]
    if "is_secret" in data or new_type == "secret":
        if new_is_secret and not row.is_secret and "value" not in data:
            try:
                row.value = stored_value(exposed_value(row, reveal_secret=True), new_type, True)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        row.is_secret = bool(new_is_secret)
    if "is_active" in data:
        row.is_active = bool(data["is_active"])
    stamp_update(row, user.id)
    db.commit()
    db.refresh(row)
    return config_to_dict(row, reveal_secret=False)


@router.post("/{config_id}/toggle", response_model=AppConfigOut)
def toggle_app_config(config_id: int, db: Session = Depends(get_db), user=Depends(require_permission("settings", "update"))):
    row = _find_by_id(db, config_id)
    row.is_active = not row.is_active
    stamp_update(row, user.id)
    db.commit()
    db.refresh(row)
    return config_to_dict(row, reveal_secret=False)


@router.delete("/{config_id}")
def delete_app_config(config_id: int, db: Session = Depends(get_db), user=Depends(require_permission("settings", "delete"))):
    row = _find_by_id(db, config_id)
    row.is_deleted = True
    stamp_update(row, user.id)
    db.commit()
    return {"message": "Configuration deleted", "id": config_id, "group": row.group, "key": row.key}


# Example application code:
# from app.services.app_config_service import ConfigAccessor, get_config_value
# storage_root = get_config_value(db, "FILE_STORAGE", "OUTPUT_ROOT", default="./uploads/outputs")
# vendor_key = ConfigAccessor(db).get("API.VENDOR_KEY", required=True)
