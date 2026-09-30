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
