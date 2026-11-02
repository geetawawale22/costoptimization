import base64
import hashlib
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AppConfig

SECRET_PREFIX = "enc:v1:"
MASKED_SECRET = "********"
CODE_CONFIG_PATTERN = re.compile(r"(?:config\.get|get_config_value)\(\s*[\"']([A-Za-z0-9_.:-]+)[\"'](?:\s*,\s*[\"']([A-Za-z0-9_.:-]+)[\"'])?")


def _fernet_key() -> bytes:
    raw = get_settings().app_config_encryption_key
    if not raw:
        raise ValueError("APP_CONFIG_ENCRYPTION_KEY is required for secret configuration")
    try:
        decoded = base64.urlsafe_b64decode(raw.encode("utf-8"))
        if len(decoded) == 32:
            return raw.encode("utf-8")
    except Exception:
        pass
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    return Fernet(_fernet_key())


def encrypt_value(value: str) -> str:
    return SECRET_PREFIX + _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_value(value: str | None) -> str | None:
    if value is None:
        return None
    if not value.startswith(SECRET_PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(SECRET_PREFIX):].encode("utf-8")).decode("utf-8")
    except InvalidToken:
        raise ValueError("Could not decrypt app configuration value. Check APP_CONFIG_ENCRYPTION_KEY/SECRET_KEY.")


def normalize_group(group: str | None) -> str:
    return (group or "GENERAL").strip().upper()


def split_group_key(group_or_key: str, key: str | None = None) -> tuple[str, str]:
    if key is not None:
        return normalize_group(group_or_key), key.strip()
    text = (group_or_key or "").strip()
    if "." in text:
        group, key_part = text.split(".", 1)
        return normalize_group(group), key_part.strip()
    return "GENERAL", text


def serialize_config_value(value: Any, value_type: str) -> str:
    value_type = (value_type or "string").lower()
    if value is None:
        return ""
    if value_type == "json":
        if isinstance(value, str):
            json.loads(value)
            return value
        return json.dumps(value, ensure_ascii=False)
    if value_type == "boolean":
        if isinstance(value, bool):
            return "true" if value else "false"
        text = str(value).strip().lower()
        if text not in {"true", "false", "1", "0", "yes", "no", "y", "n"}:
            raise ValueError("Boolean config value must be true/false")
        return "true" if text in {"true", "1", "yes", "y"} else "false"
    if value_type == "number":
        import math
        if not math.isfinite(float(value)):
            raise ValueError("Number config value must be finite")
        float(value)
        return str(value)
    return str(value)


def parse_config_value(value: str | None, value_type: str) -> Any:
    value_type = (value_type or "string").lower()
    value = "" if value is None else value
    if value_type == "json":
        return json.loads(value) if value else None
    if value_type == "boolean":
        return str(value).strip().lower() in {"true", "1", "yes", "y"}
    if value_type == "number":
        if value == "":
            return None
        number = float(value)
        return int(number) if number.is_integer() else number
    return value


def stored_value(value: Any, value_type: str, is_secret: bool) -> str:
    serialized = serialize_config_value(value, value_type)
    return encrypt_value(serialized) if is_secret else serialized


def exposed_value(row: AppConfig, reveal_secret: bool = False) -> Any:
    if row.is_secret and not reveal_secret:
        return MASKED_SECRET if row.value else ""
    raw = decrypt_value(row.value) if row.is_secret else row.value
    return parse_config_value(raw, row.type)


def config_to_dict(row: AppConfig, reveal_secret: bool = False) -> dict:
    return {
        "id": row.id,
        "group": row.group or "GENERAL",
        "key": row.key,
        "value": exposed_value(row, reveal_secret=reveal_secret),
        "content": None if row.is_secret else row.content,
        "type": row.type,
        "description": row.description,
        "is_secret": row.is_secret,
        "is_active": row.is_active,
        "created_at": row.created_at,
        "modified_at": row.modified_at,
    }


def get_config_value(db: Session, group_or_key: str, key: str | None = None, default: Any = None, required: bool = False) -> Any:
    group, key_name = split_group_key(group_or_key, key)
    row = (db.query(AppConfig)
             .filter(AppConfig.group == group, AppConfig.key == key_name,
                     AppConfig.is_deleted.is_(False), AppConfig.is_active.is_(True))
             .first())
    if not row:
        if required:
            raise KeyError(f"Missing active app configuration key: {group}.{key_name}")
        return default
    return exposed_value(row, reveal_secret=True)


def discover_config_keys(root: Path | None = None) -> list[str]:
    return ["COSTOPTIMA_NEW.app.display_name", "COSTOPTIMA_NEW.ui.page_size"]

# Business namespaces are owned by their unimplemented feature modules.
BUSINESS_GROUPS = {"CALCULATION_POLICY", "OUTPUT_TEMPLATE", "OUTPUT_TEMPLATE_HISTORY",
                   "PCM_CONFIGURATION_BACKUP", "PCM_MASTER_SOURCE", "PCM_OUTPUT_MAPPING"}

def general_group(group):
    value = normalize_group(group)
    return value not in BUSINESS_GROUPS and not value.startswith(("PCM_", "CALCULATION_", "OUTPUT_TEMPLATE"))

def general_query(db):
    return db.query(AppConfig).filter(~AppConfig.group.in_(BUSINESS_GROUPS),
        ~AppConfig.group.like(r"PCM\_%", escape="\\"),
        ~AppConfig.group.like(r"CALCULATION\_%", escape="\\"),
        ~AppConfig.group.like(r"OUTPUT\_TEMPLATE%", escape="\\"))
