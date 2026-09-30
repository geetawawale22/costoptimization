from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime


class JobSubTaskOut(BaseModel):
    id: int
    commodity_code: str
    status: str
    progress_pct: int
    records_found: int
    model_config = {"from_attributes": True}


class JobLogOut(BaseModel):
    id: int
    timestamp: datetime
    level: str
    process: Optional[str]
    message: Optional[str]
    model_config = {"from_attributes": True}


class JobResultOut(BaseModel):
    total_cost: float
    savings_opportunity: float
    confidence_pct: float
    breakdown: Dict[str, Any]
    output_file_path: Optional[str]
    email_sent: str
    model_config = {"from_attributes": True}


class JobOut(BaseModel):
    id: int
    bom_id: int
    status: str
    started_by: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    sub_tasks: List[JobSubTaskOut] = []
    model_config = {"from_attributes": True}


class NotificationOut(BaseModel):
    id: int
    message: str
    is_read: bool
    created_at: datetime
    model_config = {"from_attributes": True}
