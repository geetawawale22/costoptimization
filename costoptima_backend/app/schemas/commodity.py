from pydantic import BaseModel
from typing import Optional, List, Dict, Any


class CommodityTypeBase(BaseModel):
    code: str
    name: str
    description: Optional[str] = None
    is_active: bool = True


class CommodityTypeCreate(CommodityTypeBase):
    pass


class CommodityTypeOut(CommodityTypeBase):
    id: int
    model_config = {"from_attributes": True}


class FieldDefinitionBase(BaseModel):
    field_name: str
    data_type: str = "string"
    source_type: str = "input"  # input / master_lookup / calculated
    position: int = 0


class FieldDefinitionCreate(FieldDefinitionBase):
    pass


class FieldDefinitionOut(FieldDefinitionBase):
    id: int
    commodity_type_id: int
    model_config = {"from_attributes": True}


class CalcRuleBase(BaseModel):
    condition_expr: Optional[str] = None
    formula_expr: str
    output_field: str
    priority: int = 0


class CalcRuleCreate(CalcRuleBase):
    pass


class CalcRuleOut(CalcRuleBase):
    id: int
    commodity_type_id: int
    model_config = {"from_attributes": True}


class CalcRuleTestRequest(BaseModel):
    sample_row: Dict[str, Any]


class CalcRuleTestResult(BaseModel):
    output_field: str
    result: Any
    error: Optional[str] = None


class PcmFormulaOut(BaseModel):
    id: str
    profile: str
    profile_sheet: Optional[str] = None
    step_name: str
    formula_expr: str
    output_field: Optional[str] = None
    priority: int = 0


class PcmFormulaUpdate(BaseModel):
    step_name: Optional[str] = None
    formula_expr: str
    output_field: Optional[str] = None
