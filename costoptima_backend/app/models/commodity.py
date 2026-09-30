from sqlalchemy import Column, Integer, String, ForeignKey, Boolean, Text
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.mixins import AuditMixin


class CommodityType(Base, AuditMixin):
    __tablename__ = "commodity_types"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(255), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(String(500), nullable=True)
    is_active = Column(Boolean, default=True)

    fields = relationship("FieldDefinition", back_populates="commodity_type", cascade="all, delete-orphan")
    rules = relationship("CalcRule", back_populates="commodity_type", cascade="all, delete-orphan")


class FieldDefinition(Base, AuditMixin):
    __tablename__ = "field_definitions"

    id = Column(Integer, primary_key=True, index=True)
    commodity_type_id = Column(Integer, ForeignKey("commodity_types.id"))
    field_name = Column(String(255), nullable=False)
    data_type = Column(String(255), default="string")
    source_type = Column(String(255), default="input")
    position = Column(Integer, default=0)

    commodity_type = relationship("CommodityType", back_populates="fields")


class CalcRule(Base, AuditMixin):
    __tablename__ = "calc_rules"

    id = Column(Integer, primary_key=True, index=True)
    commodity_type_id = Column(Integer, ForeignKey("commodity_types.id"))
    condition_expr = Column(Text, nullable=True)
    formula_expr = Column(Text, nullable=False)
    output_field = Column(String(255), nullable=False)
    priority = Column(Integer, default=0)

    commodity_type = relationship("CommodityType", back_populates="rules")
    versions = relationship("CalcRuleVersion", back_populates="rule", cascade="all, delete-orphan")


class CalcRuleVersion(Base, AuditMixin):
    __tablename__ = "calc_rule_versions"

    id = Column(Integer, primary_key=True, index=True)
    rule_id = Column(Integer, ForeignKey("calc_rules.id"))
    version = Column(Integer, nullable=False)
    expr_snapshot = Column(Text, nullable=False)

    rule = relationship("CalcRule", back_populates="versions")
