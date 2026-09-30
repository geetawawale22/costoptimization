from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime


class PermissionOut(BaseModel):
    id: int
    resource: str
    action: str
    description: Optional[str] = None
    model_config = {"from_attributes": True}


class PermissionCreate(BaseModel):
    resource: str
    action: str
    description: Optional[str] = None


class PermissionUpdate(BaseModel):
    resource: Optional[str] = None
    action: Optional[str] = None
    description: Optional[str] = None


class RoleBase(BaseModel):
    name: str
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
    name: str
    employee_id: Optional[str] = None
    email: EmailStr
    job_title: Optional[str] = None
    department: Optional[str] = None
    company: Optional[str] = None
    mobile: Optional[str] = None
    role_id: Optional[int] = None


class UserCreate(UserBase):
    password: str


class UserUpdate(BaseModel):
    name: Optional[str] = None
    employee_id: Optional[str] = None
    job_title: Optional[str] = None
    department: Optional[str] = None
    company: Optional[str] = None
    mobile: Optional[str] = None
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


class NotificationRuleBase(BaseModel):
    commodity: str
    to_emails: List[str] = []
    cc_emails: List[str] = []


class NotificationRuleCreate(NotificationRuleBase):
    pass


class NotificationRuleOut(NotificationRuleBase):
    id: int
    model_config = {"from_attributes": True}
