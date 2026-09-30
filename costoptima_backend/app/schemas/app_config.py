from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator

ALLOWED_CONFIG_TYPES = {"string", "number", "boolean", "json", "path", "url", "secret"}
PREDEFINED_CONFIG_GROUPS = ["FILE_STORAGE", "BLOCK_STORAGE", "DATABASE", "AUTH", "API", "GENERAL"]


class AppConfigBase(BaseModel):
    group: str = Field("GENERAL", min_length=2, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    key: str = Field(..., min_length=2, max_length=255, pattern=r"^[A-Za-z0-9_.:-]+$")
    value: Any = None
    content: Optional[str] = None
    type: str = "string"
    description: Optional[str] = None
    is_secret: bool = False
    is_active: bool = True

    @field_validator("group")
    @classmethod
    def normalize_group(cls, value: str) -> str:
        return (value or "GENERAL").strip().upper()

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        value = (value or "string").strip().lower()
        if value not in ALLOWED_CONFIG_TYPES:
            raise ValueError(f"type must be one of {sorted(ALLOWED_CONFIG_TYPES)}")
        return value


class AppConfigCreate(AppConfigBase):
    pass


class AppConfigUpdate(BaseModel):
    group: Optional[str] = None
    key: Optional[str] = None
    value: Any = None
    content: Optional[str] = None
    type: Optional[str] = None
    description: Optional[str] = None
    is_secret: Optional[bool] = None
    is_active: Optional[bool] = None

    @field_validator("group")
    @classmethod
    def normalize_group(cls, value: Optional[str]) -> Optional[str]:
        return value.strip().upper() if value is not None else value

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = value.strip().lower()
        if value not in ALLOWED_CONFIG_TYPES:
            raise ValueError(f"type must be one of {sorted(ALLOWED_CONFIG_TYPES)}")
        return value


class AppConfigOut(BaseModel):
    id: int
    group: str
    key: str
    value: Any = None
    content: Optional[str] = None
    type: str
    description: Optional[str] = None
    is_secret: bool
    is_active: bool
    created_at: datetime
    modified_at: datetime
    model_config = {"from_attributes": True}


class AppConfigValueOut(BaseModel):
    group: str
    key: str
    value: Any = None
    content: Optional[str] = None
    type: str
    is_secret: bool
    is_active: bool


class AppConfigOptionsOut(BaseModel):
    groups: list[str]
    keys: list[str]
    detected_keys: list[str]
    predefined_groups: list[str]
