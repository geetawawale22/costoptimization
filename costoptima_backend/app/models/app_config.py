from sqlalchemy import Boolean, Column, Integer, String, Text, UniqueConstraint
from app.database import Base
from app.models.mixins import AuditMixin


class AppConfig(Base, AuditMixin):
    __tablename__ = "app_config"
    __table_args__ = (UniqueConstraint("group", "key", name="uq_app_config_group_key"),)

    id = Column(Integer, primary_key=True, index=True)
    group = Column("group", String(100), nullable=False, default="GENERAL", index=True, quote=True)
    key = Column(String(255), nullable=False, index=True)
    value = Column(Text, nullable=True)
    content = Column(Text, nullable=True)
    type = Column(String(50), nullable=False, default="string")
    description = Column(Text, nullable=True)
    is_secret = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
 