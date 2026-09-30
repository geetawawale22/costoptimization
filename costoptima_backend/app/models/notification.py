from sqlalchemy import Column, Integer, String, Boolean, ForeignKey
from app.database import Base
from app.models.mixins import AuditMixin


class Notification(Base, AuditMixin):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    message = Column(String(500), nullable=False)
    is_read = Column(Boolean, default=False)
