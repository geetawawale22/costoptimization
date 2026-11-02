"""
AuditMixin — adds created_by, created_at, modified_by, modified_at, is_deleted
to every table in the system, per the standard audit/soft-delete convention.

created_by / modified_by store the acting user's id (not a FK constraint, so the
mixin can be reused on the users table itself without a circular dependency).
is_deleted implements soft-delete: routers filter it out rather than issuing a
real DELETE, so history/audit trails are never destroyed.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, DateTime, Boolean


class AuditMixin:
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    modified_by = Column(Integer, nullable=True)
    modified_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)


def stamp_create(obj, user_id):
    obj.created_by = user_id
    obj.modified_by = user_id


def stamp_update(obj, user_id):
    obj.modified_by = user_id
    obj.modified_at = datetime.utcnow()


from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, JSON, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base



class Role(Base, AuditMixin):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False)
    description = Column(String(500), nullable=True)

    users = relationship("User", back_populates="role")
    role_permissions = relationship("RolePermission", back_populates="role", cascade="all, delete-orphan")


class Permission(Base, AuditMixin):
    """One row per (page/model, action). 'resource' names the page or model being
    protected (masters, commodity_config, boms, users, roles, converters,
    settings). 'action' is create / view / update / delete."""
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


from sqlalchemy import Boolean, Column, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import LONGTEXT
from app.database import Base



class AppConfig(Base, AuditMixin):
    __tablename__ = "app_config"
    __table_args__ = (UniqueConstraint("group", "key", name="uq_app_config_group_key"),)

    id = Column(Integer, primary_key=True, index=True)
    group = Column("group", String(100), nullable=False, default="GENERAL", index=True, quote=True)
    key = Column(String(255), nullable=False, index=True)
    value = Column(Text().with_variant(LONGTEXT(), "mysql", "mariadb"), nullable=True)
    content = Column(Text().with_variant(LONGTEXT(), "mysql", "mariadb"), nullable=True)
    type = Column(String(50), nullable=False, default="string")
    description = Column(Text, nullable=True)
    is_secret = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)



SessionModel = Session
LoginSession = Session

def stamp(row, actor_id, *, new=False):
    (stamp_create if new else stamp_update)(row, actor_id)


# Immutable application ledger; deliberately does not inherit AuditMixin.
from sqlalchemy import BigInteger, SmallInteger, Index
from sqlalchemy.dialects.mysql import BIGINT, DATETIME
from sqlalchemy import CHAR


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id = Column(BigInteger().with_variant(BIGINT(unsigned=True), "mysql").with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True)
    event_id = Column(CHAR(36), nullable=False, unique=True)
    occurred_at = Column(DateTime().with_variant(DATETIME(fsp=6), "mysql"), nullable=False)
    recorded_at = Column(DateTime().with_variant(DATETIME(fsp=6), "mysql"), nullable=False)
    actor_user_id = Column(Integer, nullable=True)
    session_ref = Column(CHAR(64), nullable=True)
    request_id = Column(CHAR(36), nullable=True)
    resource = Column(String(100), nullable=False)
    action = Column(String(100), nullable=False)
    target_type = Column(String(100), nullable=True)
    target_id = Column(String(128), nullable=True)
    outcome = Column(String(16), nullable=False)
    http_method = Column(String(10), nullable=True)
    route = Column(String(255), nullable=True)
    http_status = Column(SmallInteger, nullable=True)
    client_ip = Column(String(45), nullable=True)
    user_agent = Column(String(512), nullable=True)
    summary = Column(String(500), nullable=False)
    changes = Column(JSON, nullable=True)
    metadata_json = Column("metadata", JSON, nullable=True)
    error_category = Column(String(100), nullable=True)
    integrity_key_id = Column(String(32), nullable=False)
    integrity_mac = Column(CHAR(64), nullable=False)
    __table_args__ = (
        Index("ix_audit_time", "occurred_at", "id"),
        Index("ix_audit_actor_time", "actor_user_id", "occurred_at", "id"),
        Index("ix_audit_session_time", "session_ref", "occurred_at", "id"),
        Index("ix_audit_request", "request_id"),
        Index("ix_audit_resource_action_time", "resource", "action", "occurred_at", "id"),
        Index("ix_audit_outcome_time", "outcome", "occurred_at", "id"),
        Index("ix_audit_target_time", "target_type", "target_id", "occurred_at", "id"),
    )
