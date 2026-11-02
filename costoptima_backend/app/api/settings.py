from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import AppConfig, User, stamp
from app.security import current_user, require_permission, management_lock

router = APIRouter(prefix="/api/settings", tags=["Settings"])
GROUP = "COSTOPTIMA_NEW"
DEFINITIONS = {"app.display_name": {"default": "Cost Pilot", "type": "string"}, "ui.page_size": {"default": 25, "type": "number"}}


def validate_value(key, value):
    if key not in DEFINITIONS:
        raise HTTPException(404, "Unknown setting")
    if key == "app.display_name":
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 80:
            raise HTTPException(422, "Display name must be text of 1 to 80 characters")
        return value.strip()
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise HTTPException(422, "Page size must be an integer from 10 to 100")
    try:
        number = int(value)
    except (ValueError, TypeError):
        raise HTTPException(422, "Page size must be an integer from 10 to 100")
    if not 10 <= number <= 100 or (isinstance(value, str) and str(number) != value.strip()):
        raise HTTPException(422, "Page size must be an integer from 10 to 100")
    return number


def verify_metadata(row, key):
    if key not in DEFINITIONS or row.group != GROUP or row.key != key or row.is_deleted or not row.is_active or row.is_secret or row.type != DEFINITIONS[key]["type"] or row.content not in (None, ""):
        raise HTTPException(409, "Setting metadata conflicts with the approved scalar setting; no record was changed")


def read_settings(db):
    result = {key: item["default"] for key, item in DEFINITIONS.items()}
    rows = db.query(AppConfig).filter(AppConfig.group == GROUP, AppConfig.key.in_(DEFINITIONS)).all()
    for row in rows:
        if row.is_deleted or not row.is_active:
            continue
        verify_metadata(row, row.key)
        try:
            result[row.key] = validate_value(row.key, row.value)
        except HTTPException:
            raise HTTPException(409, "Stored setting value is invalid; no record was changed")
    return result


@router.get("/appearance")
def appearance(db: Session = Depends(get_db), _=Depends(current_user)):
    # Both approved values are non-sensitive application behavior for every signed-in user.
    return read_settings(db)





from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import distinct
from sqlalchemy.orm import Session

from app.security import require_permission
from app.api.users import DEFAULT_PAGE_SIZE, PaginatedResponse, paginate_query
from app.database import get_db
from app.models import AppConfig
from app.models import stamp_create, stamp_update
from app.schemas import (
    AppConfigCreate, AppConfigOptionsOut, AppConfigOut, AppConfigUpdate, AppConfigValueOut,
    PREDEFINED_CONFIG_GROUPS,
)
from app.app_config import (
    general_query, general_group, MASKED_SECRET, config_to_dict, discover_config_keys, exposed_value, get_config_value,
    normalize_group, stored_value,
)

config_router = APIRouter(prefix="/api/app-config", tags=["Application Configuration"])


def _is_output_template_group(group: str | None) -> bool:
    return normalize_group(group or "") == "OUTPUT_TEMPLATE"


def _protect_output_template(*, existing: AppConfig | None = None, target_group: str | None = None):
    """Keep template writes behind /calculations/output-template validation/versioning."""
    if (existing is not None and not general_group(existing.group)) or (target_group is not None and not general_group(target_group)):
        raise HTTPException(status_code=409, detail=(
            "This business configuration belongs to an excluded module."
        ))


def _find_by_id(db: Session, config_id: int) -> AppConfig:
    row = general_query(db).filter(AppConfig.id == config_id, AppConfig.is_deleted.is_(False)).first()
    if not row:
        raise HTTPException(status_code=404, detail="Configuration entry not found")
    return row


def _duplicate_exists(db: Session, group: str, key: str, exclude_id: int | None = None) -> bool:
    q = general_query(db).filter(AppConfig.group == group, AppConfig.key == key, AppConfig.is_deleted.is_(False))
    if exclude_id:
        q = q.filter(AppConfig.id != exclude_id)
    return db.query(q.exists()).scalar()


@config_router.get("/options", response_model=AppConfigOptionsOut)
def app_config_options(db: Session = Depends(get_db), _=Depends(require_permission("settings", "view"))):
    db_groups = [g for (g,) in general_query(db).with_entities(AppConfig.group).distinct().filter(AppConfig.is_deleted.is_(False)).all() if g]
    db_keys = [f"{row.group}.{row.key}" for row in general_query(db).filter(AppConfig.is_deleted.is_(False)).order_by(AppConfig.group, AppConfig.key).all()]
    detected = discover_config_keys()
    return {
        "groups": sorted(set(PREDEFINED_CONFIG_GROUPS + db_groups)),
        "keys": sorted(set(db_keys + detected)),
        "detected_keys": detected,
        "predefined_groups": PREDEFINED_CONFIG_GROUPS,
    }


@config_router.get("", response_model=PaginatedResponse[AppConfigOut])
def list_app_config(page: int = 1, page_size: int = DEFAULT_PAGE_SIZE, q: str = "",
                    group: str = "", key: str = "", type: str = "", active: str = Query("", description="true/false/blank"),
                    db: Session = Depends(get_db), _=Depends(require_permission("settings", "view"))):
    query = general_query(db).filter(AppConfig.is_deleted.is_(False))
    if q:
        like = f"%{q}%"
        query = query.filter((AppConfig.key.ilike(like)) | (AppConfig.group.ilike(like)) |
                             (AppConfig.description.ilike(like)) | (AppConfig.content.ilike(like)))
    if group:
        query = query.filter(AppConfig.group == normalize_group(group))
    if key:
        query = query.filter(AppConfig.key.ilike(f"%{key}%"))
    if type:
        query = query.filter(AppConfig.type == type)
    if active.lower() in {"true", "false"}:
        query = query.filter(AppConfig.is_active.is_(active.lower() == "true"))
    query = query.order_by(AppConfig.group, AppConfig.key)
    items, total, page, page_size, total_pages = paginate_query(query, page, page_size)
    items = [config_to_dict(item, reveal_secret=False) for item in items]
    return PaginatedResponse[AppConfigOut](items=items, total=total, page=page, page_size=page_size, total_pages=total_pages)


@config_router.post("", response_model=AppConfigOut)
def create_app_config(payload: AppConfigCreate, db: Session = Depends(get_db),
                      user=Depends(require_permission("settings", "update"))):
    management_lock(db, user, "settings", "update")
    if (payload.is_secret or payload.type == "secret") and payload.content:
        raise HTTPException(422, "Secret content must be empty; only the value is encrypted")
    group = normalize_group(payload.group)
    key = payload.key.strip()
    _protect_output_template(target_group=group)
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
    if row.group == GROUP and row.key in DEFINITIONS:
        value = validate_value(row.key, payload.value)
        if row.type != DEFINITIONS[row.key]["type"] or row.is_secret or row.content:
            raise HTTPException(422, "Known setting metadata is fixed")
        row.value = str(value)
    stamp_create(row, user.id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return config_to_dict(row, reveal_secret=False)


@config_router.get("/value", response_model=AppConfigValueOut)
def get_app_config_value(group: str = Query("GENERAL"), key: str = Query(...),
                         db: Session = Depends(get_db), _=Depends(require_permission("settings", "view"))):
    row = (general_query(db)
             .filter(AppConfig.group == normalize_group(group), AppConfig.key == key,
                     AppConfig.is_deleted.is_(False), AppConfig.is_active.is_(True))
             .first())
    if not row:
        raise HTTPException(status_code=404, detail="Active configuration key not found")
    if row.is_secret:
        raise HTTPException(403, "Secret values cannot be revealed through the configuration API")
    return {"group": row.group, "key": row.key, "value": exposed_value(row, reveal_secret=False),
            "content": row.content, "type": row.type, "is_secret": row.is_secret, "is_active": row.is_active}


@config_router.get("/{config_id}", response_model=AppConfigOut)
def get_app_config(config_id: int, db: Session = Depends(get_db), _=Depends(require_permission("settings", "view"))):
    return config_to_dict(_find_by_id(db, config_id), reveal_secret=False)


@config_router.put("/{config_id}", response_model=AppConfigOut)
def update_app_config(config_id: int, payload: AppConfigUpdate, db: Session = Depends(get_db),
                      user=Depends(require_permission("settings", "update"))):
    management_lock(db, user, "settings", "update")
    row = _find_by_id(db, config_id)
    data = payload.model_dump(exclude_unset=True)
    new_group = normalize_group(data.get("group", row.group))
    _protect_output_template(existing=row, target_group=new_group)
    new_key = (data.get("key", row.key) or "").strip()
    if _duplicate_exists(db, new_group, new_key, exclude_id=row.id):
        raise HTTPException(status_code=400, detail="Configuration group + key already exists")
    new_type = data.get("type", row.type)
    new_is_secret = data.get("is_secret", row.is_secret) or new_type == "secret"
    if new_is_secret and data.get("content", row.content):
        raise HTTPException(422, "Secret content must be empty; only the value is encrypted")
    if new_is_secret != row.is_secret or new_type != row.type:
        if "value" not in data or (row.is_secret and data["value"] == MASKED_SECRET):
            data["value"] = exposed_value(row, reveal_secret=True)
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
    if row.group == GROUP and row.key in DEFINITIONS:
        if row.type != DEFINITIONS[row.key]["type"] or row.is_secret or row.content:
            raise HTTPException(422, "Known setting metadata is fixed")
        row.value = str(validate_value(row.key, row.value))
    stamp_update(row, user.id)
    db.commit()
    db.refresh(row)
    return config_to_dict(row, reveal_secret=False)


@config_router.post("/{config_id}/toggle", response_model=AppConfigOut)
def toggle_app_config(config_id: int, db: Session = Depends(get_db), user=Depends(require_permission("settings", "update"))):
    management_lock(db, user, "settings", "update")
    row = _find_by_id(db, config_id)
    _protect_output_template(existing=row)
    row.is_active = not row.is_active
    if row.group == GROUP and row.key in DEFINITIONS:
        if row.type != DEFINITIONS[row.key]["type"] or row.is_secret or row.content:
            raise HTTPException(422, "Known setting metadata is fixed")
        row.value = str(validate_value(row.key, row.value))
    stamp_update(row, user.id)
    db.commit()
    db.refresh(row)
    return config_to_dict(row, reveal_secret=False)


@config_router.delete("/{config_id}")
def delete_app_config(config_id: int, db: Session = Depends(get_db), user=Depends(require_permission("settings", "delete"))):
    management_lock(db, user, "settings", "delete")
    row = _find_by_id(db, config_id)
    _protect_output_template(existing=row)
    row.is_deleted = True
    stamp_update(row, user.id)
    db.commit()
    return {"message": "Configuration deleted", "id": config_id, "group": row.group, "key": row.key}


# Example application code:
