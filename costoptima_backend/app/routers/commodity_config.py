"""
Commodity Config engine — the "no re-coding for new functionality" core.
Onboarding a new commodity (or changing a formula) is a data change here,
never a new endpoint or a code deploy.
"""
from typing import List
import copy
import json
from pathlib import Path
import simpleeval
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.deps import get_current_user, require_permission
from app.models.mixins import stamp_create, stamp_update
from app.models.app_config import AppConfig
from app.models.commodity import CommodityType, FieldDefinition, CalcRule, CalcRuleVersion
from app.schemas.commodity import (
    CommodityTypeOut, CommodityTypeCreate, FieldDefinitionOut, FieldDefinitionCreate,
    CalcRuleOut, CalcRuleCreate, CalcRuleTestRequest, CalcRuleTestResult,
    PcmFormulaOut, PcmFormulaUpdate,
)
from app.pcm.engine import validate_definition

router = APIRouter(prefix="/commodity-types", tags=["Commodity Config"])
RESOURCE = "commodity_config"
PCM_GROUP = "PCM_ENGINE"
PCM_KEY = "definition"
PCM_DEFAULT_DEFINITION = Path(__file__).resolve().parents[2] / "data" / "pcm_reference_definition.json"


def _get_commodity(db: Session, commodity_id: int) -> CommodityType:
    c = db.query(CommodityType).filter(CommodityType.id == commodity_id, CommodityType.is_deleted.is_(False)).first()
    if not c:
        raise HTTPException(status_code=404, detail="Commodity type not found")
    return c


def _pcm_config_row(db: Session) -> AppConfig | None:
    return (db.query(AppConfig)
              .filter(AppConfig.group == PCM_GROUP, AppConfig.key == PCM_KEY,
                      AppConfig.is_deleted.is_(False), AppConfig.is_active.is_(True))
              .first())


def _pcm_definition(db: Session) -> tuple[AppConfig, dict]:
    row = _pcm_config_row(db)
    if not row:
        raise HTTPException(status_code=404, detail={
            "code": "PCM_DEFINITION_NOT_AVAILABLE",
            "message": "PCM_ENGINE.definition is not seeded. Use Seed Original PCM Formulas first.",
        })
    try:
        definition = json.loads(row.value or "{}")
        validate_definition(definition)
        return row, definition
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"PCM_ENGINE.definition is invalid: {exc}") from exc


def _pcm_formula_id(profile: str, index: int) -> str:
    return f"{profile}:{index}"


def _parse_pcm_formula_id(formula_id: str) -> tuple[str, int]:
    try:
        profile, raw_index = formula_id.rsplit(":", 1)
        return profile, int(raw_index)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="PCM formula id must look like '<profile>:<index>'") from exc


def _definition_to_formula_rows(definition: dict) -> list[PcmFormulaOut]:
    rows = []
    for profile_name, profile in sorted((definition.get("profiles") or {}).items()):
        output_by_step = {}
        for output, step_name in (profile.get("outputs") or {}).items():
            if step_name not in output_by_step or output == "Total":
                output_by_step[step_name] = output
        for index, step in enumerate(profile.get("steps") or []):
            rows.append(PcmFormulaOut(
                id=_pcm_formula_id(profile_name, index),
                profile=profile_name,
                profile_sheet=profile.get("sheet"),
                step_name=step.get("name") or "",
                formula_expr=step.get("expression") or "",
                output_field=output_by_step.get(step.get("name")),
                priority=index,
            ))
    return rows


def _save_pcm_definition(db: Session, row: AppConfig, definition: dict, user):
    validate_definition(definition)
    row.value = json.dumps(definition, ensure_ascii=False)
    row.type = "json"
    row.description = row.description or "PCM formulas, dispatch, source bindings, parameters and output configuration"
    stamp_update(row, user.id)
    db.commit()
    db.refresh(row)


@router.get("", response_model=List[CommodityTypeOut])
def list_commodities(db: Session = Depends(get_db), _=Depends(require_permission(RESOURCE, "view"))):
    return db.query(CommodityType).filter(CommodityType.is_deleted.is_(False)).all()


@router.get("/pcm-formulas", response_model=List[PcmFormulaOut])
def list_pcm_formulas(db: Session = Depends(get_db), _=Depends(require_permission(RESOURCE, "view"))):
    _, definition = _pcm_definition(db)
    return _definition_to_formula_rows(definition)


@router.post("/pcm-formulas/seed-default")
def seed_pcm_formulas(replace: bool = False, db: Session = Depends(get_db),
                      user=Depends(require_permission(RESOURCE, "update"))):
    definition = json.loads(PCM_DEFAULT_DEFINITION.read_text(encoding="utf-8"))
    validate_definition(definition)
    existing = (db.query(AppConfig)
                  .filter(AppConfig.group == PCM_GROUP, AppConfig.key == PCM_KEY,
                          AppConfig.is_deleted.is_(False))
                  .first())
    if existing and not replace:
        raise HTTPException(status_code=409, detail={
            "code": "PCM_DEFINITION_EXISTS",
            "message": "PCM_ENGINE.definition already exists. Pass replace=true to publish the bundled original PCM formulas.",
        })
    if existing:
        existing.value = json.dumps(definition, ensure_ascii=False)
        existing.type = "json"
        existing.is_active = True
        existing.description = "PCM formulas, dispatch, source bindings, parameters and output configuration"
        stamp_update(existing, user.id)
    else:
        existing = AppConfig(group=PCM_GROUP, key=PCM_KEY, value=json.dumps(definition, ensure_ascii=False),
                             type="json",
                             description="PCM formulas, dispatch, source bindings, parameters and output configuration",
                             is_active=True, is_secret=False)
        stamp_create(existing, user.id)
        db.add(existing)
    db.commit()
    return {"message": "PCM formulas seeded", "profiles": len(definition["profiles"]),
            "formulas": len(_definition_to_formula_rows(definition)), "replaced": bool(replace)}


@router.put("/pcm-formulas/{formula_id}", response_model=PcmFormulaOut)
def update_pcm_formula(formula_id: str, payload: PcmFormulaUpdate, db: Session = Depends(get_db),
                       user=Depends(require_permission(RESOURCE, "update"))):
    row, definition = _pcm_definition(db)
    profile_name, index = _parse_pcm_formula_id(formula_id)
    profile = (definition.get("profiles") or {}).get(profile_name)
    if not profile:
        raise HTTPException(status_code=404, detail="PCM profile not found")
    steps = profile.get("steps") or []
    if index < 0 or index >= len(steps):
        raise HTTPException(status_code=404, detail="PCM formula not found")
    updated = copy.deepcopy(definition)
    profile = updated["profiles"][profile_name]
    old_name = profile["steps"][index].get("name")
    new_name = (payload.step_name or old_name or "").strip()
    profile["steps"][index]["name"] = new_name
    profile["steps"][index]["expression"] = payload.formula_expr
    outputs = profile.setdefault("outputs", {})
    output_field = payload.output_field.strip() if payload.output_field else ""
    for output, step_name in list(outputs.items()):
        if step_name == old_name:
            outputs[output] = new_name
    if output_field:
        outputs[output_field] = new_name
    _save_pcm_definition(db, row, updated, user)
    return next(item for item in _definition_to_formula_rows(updated) if item.id == formula_id)


@router.post("", response_model=CommodityTypeOut)
def create_commodity(payload: CommodityTypeCreate, db: Session = Depends(get_db),
                      user=Depends(require_permission(RESOURCE, "create"))):
    if db.query(CommodityType).filter(CommodityType.code == payload.code, CommodityType.is_deleted.is_(False)).first():
        raise HTTPException(status_code=400, detail="A commodity with this code already exists")
    c = CommodityType(**payload.model_dump())
    stamp_create(c, user.id)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@router.get("/{commodity_id}", response_model=CommodityTypeOut)
def get_commodity(commodity_id: int, db: Session = Depends(get_db), _=Depends(require_permission(RESOURCE, "view"))):
    return _get_commodity(db, commodity_id)


@router.put("/{commodity_id}", response_model=CommodityTypeOut)
def update_commodity(commodity_id: int, payload: CommodityTypeCreate, db: Session = Depends(get_db),
                      user=Depends(require_permission(RESOURCE, "update"))):
    c = _get_commodity(db, commodity_id)
    for field, value in payload.model_dump().items():
        setattr(c, field, value)
    stamp_update(c, user.id)
    db.commit()
    db.refresh(c)
    return c


@router.delete("/{commodity_id}")
def delete_commodity(commodity_id: int, db: Session = Depends(get_db),
                      user=Depends(require_permission(RESOURCE, "delete"))):
    c = _get_commodity(db, commodity_id)
    c.is_deleted = True
    stamp_update(c, user.id)
    db.commit()
    return {"message": "Commodity type deleted", "commodity_id": commodity_id}


# ---------- Field definitions ----------
@router.get("/{commodity_id}/fields", response_model=List[FieldDefinitionOut])
def list_fields(commodity_id: int, db: Session = Depends(get_db), _=Depends(require_permission(RESOURCE, "view"))):
    c = _get_commodity(db, commodity_id)
    return [f for f in c.fields if not f.is_deleted]


@router.post("/{commodity_id}/fields", response_model=FieldDefinitionOut)
def add_field(commodity_id: int, payload: FieldDefinitionCreate, db: Session = Depends(get_db),
              user=Depends(require_permission(RESOURCE, "create"))):
    _get_commodity(db, commodity_id)
    field = FieldDefinition(commodity_type_id=commodity_id, **payload.model_dump())
    stamp_create(field, user.id)
    db.add(field)
    db.commit()
    db.refresh(field)
    return field


@router.put("/fields/{field_id}", response_model=FieldDefinitionOut)
def update_field(field_id: int, payload: FieldDefinitionCreate, db: Session = Depends(get_db),
                  user=Depends(require_permission(RESOURCE, "update"))):
    field = db.query(FieldDefinition).filter(FieldDefinition.id == field_id, FieldDefinition.is_deleted.is_(False)).first()
    if not field:
        raise HTTPException(status_code=404, detail="Field not found")
    for f, v in payload.model_dump().items():
        setattr(field, f, v)
    stamp_update(field, user.id)
    db.commit()
    db.refresh(field)
    return field


@router.delete("/fields/{field_id}")
def delete_field(field_id: int, db: Session = Depends(get_db), user=Depends(require_permission(RESOURCE, "delete"))):
    field = db.query(FieldDefinition).filter(FieldDefinition.id == field_id, FieldDefinition.is_deleted.is_(False)).first()
    if not field:
        raise HTTPException(status_code=404, detail="Field not found")
    field.is_deleted = True
    stamp_update(field, user.id)
    db.commit()
    return {"message": "Field deleted", "field_id": field_id}


# ---------- Calc rules (formulas) ----------
@router.get("/{commodity_id}/formulas", response_model=List[CalcRuleOut])
def list_formulas(commodity_id: int, db: Session = Depends(get_db), _=Depends(require_permission(RESOURCE, "view"))):
    c = _get_commodity(db, commodity_id)
    return [r for r in c.rules if not r.is_deleted]


@router.post("/{commodity_id}/formulas", response_model=CalcRuleOut)
def add_formula(commodity_id: int, payload: CalcRuleCreate, db: Session = Depends(get_db),
                 user=Depends(require_permission(RESOURCE, "create"))):
    _get_commodity(db, commodity_id)
    rule = CalcRule(commodity_type_id=commodity_id, **payload.model_dump())
    stamp_create(rule, user.id)
    db.add(rule)
    db.commit()
    db.refresh(rule)
    version = CalcRuleVersion(rule_id=rule.id, version=1, expr_snapshot=rule.formula_expr)
    stamp_create(version, user.id)
    db.add(version)
    db.commit()
    return rule


@router.put("/formulas/{rule_id}", response_model=CalcRuleOut)
def update_formula(rule_id: int, payload: CalcRuleCreate, db: Session = Depends(get_db),
                    user=Depends(require_permission(RESOURCE, "update"))):
    rule = db.query(CalcRule).filter(CalcRule.id == rule_id, CalcRule.is_deleted.is_(False)).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Formula not found")
    for f, v in payload.model_dump().items():
        setattr(rule, f, v)
    stamp_update(rule, user.id)
    db.commit()
    db.refresh(rule)
    next_version = len(rule.versions) + 1
    version = CalcRuleVersion(rule_id=rule.id, version=next_version, expr_snapshot=rule.formula_expr)
    stamp_create(version, user.id)
    db.add(version)
    db.commit()
    return rule


@router.delete("/formulas/{rule_id}")
def delete_formula(rule_id: int, db: Session = Depends(get_db), user=Depends(require_permission(RESOURCE, "delete"))):
    rule = db.query(CalcRule).filter(CalcRule.id == rule_id, CalcRule.is_deleted.is_(False)).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Formula not found")
    rule.is_deleted = True
    stamp_update(rule, user.id)
    db.commit()
    return {"message": "Formula deleted", "rule_id": rule_id}


@router.post("/formulas/{rule_id}/test", response_model=CalcRuleTestResult)
def test_formula(rule_id: int, payload: CalcRuleTestRequest, db: Session = Depends(get_db),
                  _=Depends(require_permission(RESOURCE, "view"))):
    """Dry-run a formula against a sample row WITHOUT touching any real data.
    Uses simpleeval so admin-authored expressions can never execute arbitrary code."""
    rule = db.query(CalcRule).filter(CalcRule.id == rule_id, CalcRule.is_deleted.is_(False)).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Formula not found")
    try:
        if rule.condition_expr:
            condition_ok = simpleeval.simple_eval(rule.condition_expr, names=payload.sample_row)
            if not condition_ok:
                return CalcRuleTestResult(output_field=rule.output_field, result=None,
                                           error="Condition not met for this sample row")
        result = simpleeval.simple_eval(rule.formula_expr, names=payload.sample_row)
        return CalcRuleTestResult(output_field=rule.output_field, result=result)
    except Exception as exc:
        return CalcRuleTestResult(output_field=rule.output_field, result=None, error=str(exc))
