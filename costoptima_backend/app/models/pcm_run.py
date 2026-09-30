"""Immutable inputs, configuration, master data and results used by a PCM run."""
from sqlalchemy import Column, Integer, ForeignKey, JSON, String
from app.database import Base
from app.models.mixins import AuditMixin


class PcmRunSnapshot(Base, AuditMixin):
    __tablename__ = "pcm_run_snapshots"
    id = Column(Integer, primary_key=True)
    calculation_id = Column(Integer, ForeignKey("calculations.id"), unique=True, nullable=False)
    input_sha256 = Column(String(64), nullable=False)
    definition_sha256 = Column(String(64), nullable=False)
    definition = Column(JSON, nullable=False)
    masters = Column(JSON, nullable=False)
    results = Column(JSON, nullable=False)
