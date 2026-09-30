from sqlalchemy import Column, Integer, String, ForeignKey, JSON
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.mixins import AuditMixin


class Bom(Base, AuditMixin):
    __tablename__ = "boms"

    id = Column(Integer, primary_key=True, index=True)
    project_name = Column(String(255), nullable=True)
    customer = Column(String(255), nullable=True)
    revision = Column(String(255), nullable=True)
    file_path = Column(String(500), nullable=False)
    extra_input_file_path = Column(String(500), nullable=True)
    status = Column(String(255), default="Uploaded")

    tabs = relationship("BomTab", back_populates="bom", cascade="all, delete-orphan")
    lines = relationship("BomLine", back_populates="bom", cascade="all, delete-orphan")


class BomTab(Base, AuditMixin):
    __tablename__ = "bom_tabs"

    id = Column(Integer, primary_key=True, index=True)
    bom_id = Column(Integer, ForeignKey("boms.id"))
    commodity_code = Column(String(255), nullable=True)
    detected_sheet_name = Column(String(255), nullable=False)

    bom = relationship("Bom", back_populates="tabs")


class BomLine(Base, AuditMixin):
    __tablename__ = "bom_lines"

    id = Column(Integer, primary_key=True, index=True)
    bom_id = Column(Integer, ForeignKey("boms.id"))
    commodity_code = Column(String(255), nullable=True)
    row_data = Column(JSON, default=dict)
    validation_status = Column(String(255), default="Pending")
    validation_message = Column(String(500), nullable=True)

    bom = relationship("Bom", back_populates="lines")
