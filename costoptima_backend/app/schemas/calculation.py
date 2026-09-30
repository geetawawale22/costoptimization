from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime


class CommodityResultOut(BaseModel):
    id: int
    commodity_code: str
    status: str
    records_found: int
    total_cost: float
    model_config = {"from_attributes": True}


class CalculationLogOut(BaseModel):
    id: int
    created_at: datetime
    level: str
    process: Optional[str]
    message: Optional[str]
    model_config = {"from_attributes": True}


class CalculationResultOut(BaseModel):
    total_cost: float
    savings_opportunity: float
    confidence_pct: float
    breakdown: Dict[str, Any]
    output_file_path: Optional[str]
    email_sent: str
    model_config = {"from_attributes": True}


class CalculationOut(BaseModel):
    """Returned synchronously by POST /boms/{id}/calculate — everything is already
    complete by the time this response is sent, so there is no separate polling step."""
    id: int
    bom_id: int
    status: str
    created_by: Optional[int] = None
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    commodity_results: List[CommodityResultOut] = []
    logs: List[CalculationLogOut] = []
    result: Optional[CalculationResultOut] = None
    issues: List[Dict[str, Any]] = []
    model_config = {"from_attributes": True}
