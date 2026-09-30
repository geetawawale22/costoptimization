"""
Calculation — replaces the earlier async 'EstimationJob' concept. A calculation now
runs synchronously inside the request that triggers it (see app/calc_engine.py and
routers/calculations.py); there is no Queued/Running state to poll for since by the
time the API responds, the calculation and its result already exist.
"""
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, JSON, Float
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.mixins import AuditMixin


class Calculation(Base, AuditMixin):
    __tablename__ = "calculations"

    id = Column(Integer, primary_key=True, index=True)
    bom_id = Column(Integer, ForeignKey("boms.id"))
    status = Column(String(255), default="Completed")
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    commodity_results = relationship("CalculationCommodityResult", back_populates="calculation", cascade="all, delete-orphan")
    logs = relationship("CalculationLog", back_populates="calculation", cascade="all, delete-orphan")
    result = relationship("CalculationResult", back_populates="calculation", uselist=False, cascade="all, delete-orphan")
    pcm_snapshot = relationship("PcmRunSnapshot", uselist=False, cascade="all, delete-orphan")

    @property
    def issues(self):
        if not self.pcm_snapshot:
            return []
        return [issue for row in self.pcm_snapshot.results for issue in row.get("issues", [])]


class CalculationCommodityResult(Base, AuditMixin):
    __tablename__ = "calculation_commodity_results"

    id = Column(Integer, primary_key=True, index=True)
    calculation_id = Column(Integer, ForeignKey("calculations.id"))
    commodity_code = Column(String(255), nullable=False)
    status = Column(String(255), default="Completed")
    records_found = Column(Integer, default=0)
    total_cost = Column(Float, default=0)

    calculation = relationship("Calculation", back_populates="commodity_results")


class CalculationLog(Base, AuditMixin):
    __tablename__ = "calculation_logs"

    id = Column(Integer, primary_key=True, index=True)
    calculation_id = Column(Integer, ForeignKey("calculations.id"))
    level = Column(String(255), default="Info")
    process = Column(String(255), nullable=True)
    message = Column(String(500), nullable=True)

    calculation = relationship("Calculation", back_populates="logs")


class CalculationResult(Base, AuditMixin):
    __tablename__ = "calculation_results"

    id = Column(Integer, primary_key=True, index=True)
    calculation_id = Column(Integer, ForeignKey("calculations.id"))
    total_cost = Column(Float, default=0)
    savings_opportunity = Column(Float, default=0)
    confidence_pct = Column(Float, default=0)
    breakdown = Column(JSON, default=dict)
    output_file_path = Column(String(500), nullable=True)
    email_sent = Column(String(255), default="No")

    calculation = relationship("Calculation", back_populates="result")


class OutputTemplate(Base, AuditMixin):
    __tablename__ = "output_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    format_type = Column(String(255), default="xlsx")
    layout = Column(JSON, default=dict)
