from sqlalchemy import Column, Integer, String

from app.database import Base
from app.models.mixins import AuditMixin


class ConverterJob(Base, AuditMixin):
    __tablename__ = "converter_jobs"

    id = Column(Integer, primary_key=True, index=True)
    flow_type = Column(String(50), nullable=False, default="ftg")
    converter_type = Column(String(100), nullable=False)
    source_name = Column(String(500), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(50), nullable=False, default="Completed")
    output_filename = Column(String(255), nullable=False)
    output_file_path = Column(String(500), nullable=False)
