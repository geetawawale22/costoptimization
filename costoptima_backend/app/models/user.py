from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, JSON, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base
from app.models.mixins import AuditMixin


class Role(Base, AuditMixin):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False)
    description = Column(String(500), nullable=True)

    users = relationship("User", back_populates="role")
    role_permissions = relationship("RolePermission", back_populates="role", cascade="all, delete-orphan")


class Permission(Base, AuditMixin):
    """One row per (page/model, action). 'resource' names the page or model being
    protected (masters, commodity_config, calculations, users, roles, outputs,
    history, settings). 'action' is create / view / update / delete."""
    __tablename__ = "permissions"
    __table_args__ = (UniqueConstraint("resource", "action", name="uq_permission_resource_action"),)

    id = Column(Integer, primary_key=True, index=True)
    resource = Column(String(255), nullable=False)
    action = Column(String(50), nullable=False)
    description = Column(String(500), nullable=True)

    role_permissions = relationship("RolePermission", back_populates="permission", cascade="all, delete-orphan")


class RolePermission(Base, AuditMixin):
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),)

    id = Column(Integer, primary_key=True, index=True)
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False)
    permission_id = Column(Integer, ForeignKey("permissions.id"), nullable=False)

    role = relationship("Role", back_populates="role_permissions")
    permission = relationship("Permission", back_populates="role_permissions")


class User(Base, AuditMixin):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    employee_id = Column(String(255), unique=True, index=True, nullable=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    job_title = Column(String(255), nullable=True)
    department = Column(String(255), nullable=True)
    company = Column(String(255), nullable=True)
    mobile = Column(String(50), nullable=True)
    role_id = Column(Integer, ForeignKey("roles.id"))
    status = Column(String(255), default="Active")
    deactivation_reason = Column(String(255), nullable=True)

    role = relationship("Role", back_populates="users")
    sessions = relationship("Session", back_populates="user", cascade="all, delete-orphan")


class Session(Base, AuditMixin):
    """Server-side session store for cookie-based (non-JWT) authentication."""
    __tablename__ = "sessions"

    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    last_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="sessions")


class NotificationRule(Base, AuditMixin):
    __tablename__ = "notification_rules"

    id = Column(Integer, primary_key=True, index=True)
    commodity = Column(String(255), nullable=False)
    to_emails = Column(JSON, default=list)
    cc_emails = Column(JSON, default=list)
