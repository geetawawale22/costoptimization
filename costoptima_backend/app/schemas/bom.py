from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime


class BomTabOut(BaseModel):
    id: int
    commodity_code: Optional[str]
    detected_sheet_name: str
    model_config = {"from_attributes": True}


class BomOut(BaseModel):
    id: int
    project_name: Optional[str]
    customer: Optional[str]
    revision: Optional[str]
    extra_input_file_path: Optional[str] = None
    status: str
    created_at: datetime
    tabs: List[BomTabOut] = []
    model_config = {"from_attributes": True}


class BomLinePreview(BaseModel):
    id: int
    commodity_code: Optional[str]
    row_data: Dict[str, Any]
    validation_status: str
    model_config = {"from_attributes": True}


class ValidationIssue(BaseModel):
    check: str
    status: str  # Passed / Warning / Error
    message: Optional[str] = None


class ValidationResult(BaseModel):
    bom_id: int
    issues: List[ValidationIssue]
