from pydantic import Field
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime


class PermissionOut(BaseModel):
    id: int
    resource: str = Field(min_length=1, max_length=255)
    action: str = Field(min_length=1, max_length=50)
    description: Optional[str] = None
    model_config = {"from_attributes": True}


class PermissionCreate(BaseModel):
    resource: str = Field(min_length=1, max_length=255)
    action: str = Field(min_length=1, max_length=50)
    description: Optional[str] = None


class PermissionUpdate(BaseModel):
    resource: Optional[str] = None
    action: Optional[str] = None
    description: Optional[str] = None


class RoleBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None


class RoleCreate(RoleBase):
    pass


class RoleOut(RoleBase):
    id: int
    model_config = {"from_attributes": True}


class RoleWithPermissions(RoleOut):
    permissions: List[PermissionOut] = []


class RolePermissionSet(BaseModel):
    permission_ids: List[int]


class UserBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    employee_id: Optional[str] = Field(default=None, max_length=255)
    email: EmailStr
    job_title: Optional[str] = Field(default=None, max_length=255)
    department: Optional[str] = Field(default=None, max_length=255)
    company: Optional[str] = Field(default=None, max_length=255)
    mobile: Optional[str] = Field(default=None, max_length=50)
    role_id: Optional[int] = None


class UserCreate(UserBase):
    password: str = Field(min_length=15, max_length=128)


class UserUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    employee_id: Optional[str] = Field(default=None, max_length=255)
    job_title: Optional[str] = Field(default=None, max_length=255)
    department: Optional[str] = Field(default=None, max_length=255)
    company: Optional[str] = Field(default=None, max_length=255)
    mobile: Optional[str] = Field(default=None, max_length=50)
    role_id: Optional[int] = None
    status: Optional[str] = None
    deactivation_reason: Optional[str] = None


class UserOut(UserBase):
    id: int
    status: str
    created_at: datetime
    role: Optional[RoleOut] = None
    model_config = {"from_attributes": True}


class MeOut(UserOut):
    """Returned by /auth/login and /auth/me — includes the resolved permission list
    for the current user's role so the frontend can gate the UI from one call."""
    permissions: List[PermissionOut] = []


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
    group: Optional[str] = Field(default=None, min_length=2, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    key: Optional[str] = Field(default=None, min_length=2, max_length=255, pattern=r"^[A-Za-z0-9_.:-]+$")
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


class AuthOut(MeOut):
    csrf_token: str


class AuditEventOut(BaseModel):
    model_config = {"extra": "allow"}
    event_id: str
    id: int
    occurred_at: datetime
    actor_user_id: int | None
    session_ref: str | None
    request_id: str | None
    resource: str
    action: str
    target_type: str | None
    target_id: str | None
    outcome: str
    summary: str
    changes: dict | None = None
    metadata: dict | None = None
    integrity: str
