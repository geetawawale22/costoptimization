"""Immutable run inputs, semantic masters and same-run result ownership."""
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from copy import deepcopy
from contextlib import contextmanager
from numbers import Number
from .common import CalculationError, get, required, normalized, number
from .masters import FieldRef


def owner_key(record):
    return tuple(str(record.get("_ownership_type",get(record,k,"")) if k=="Type" else get(record,k,"" ) or "").strip() for k in ("Customer No","Supplier","Type","End Item"))


def costing_supplier(record):
    """Verified detail supplier; Supplier itself remains the BOM owner outside arithmetic."""
    return record.get("_costing_supplier", get(record, "Supplier"))


def costing_record(record):
    """Scoped arithmetic view; never mutates final ownership or source details."""
    supplier = costing_supplier(record)
    return {**record, "Supplier": supplier} if supplier != get(record, "Supplier") else record


FINAL_COST_FIELDS = ('Grand Total Cost/Pc', 'Total Cost/Pc', 'Total/Pc', 'Total', 'Total Cost/Veh')


def required_final_fields(record, values):
    """One named result contract, shared by execution and result presentation."""
    if normalized(record.get('_ownership_type', get(record, 'Type'))) == 'end item':
        return tuple(record.get('_variants', {}))
    return tuple(key for key in FINAL_COST_FIELDS if key in values)[:1]


def validate_final_values(record, values):
    if values.get('_calculation_unverified') is True:
        return  # Still UNVERIFIED and unusable by consumers/rollups.
    fields = required_final_fields(record, values)
    if not fields:
        raise CalculationError('missing_final_cost', 'Calculation did not produce its required final cost.',
                               'Final cost', partial_values=values)
    for field in fields:
        try:
            if isinstance(values.get(field), bool): raise ValueError('Boolean is not a cost')
            number(values.get(field), field)
        except (CalculationError, ValueError):
            raise CalculationError('missing_final_cost', 'Required final cost is missing or invalid.',
                                   field, partial_values=values) from None


@dataclass
class RowResult:
    record_id: str
    commodity: str
    type: str
    inputs: dict
    values: dict = field(default_factory=dict)
    status: str = "pending"
    issues: list = field(default_factory=list)
    branch: str = ""
    provenance: list = field(default_factory=list)
    dependency_only: bool = False
    input_diagnostics: list = field(default_factory=list)
    @property
    def calculation_status(self):
        """Presentation status never makes an incomplete dependency executable."""
        if self.status == "error": return "ERROR"
        if self.status == "unverified" or self.values.get("_calculation_unverified") is True: return "UNVERIFIED"
        if self.status == "success": return "CALCULATED"
        meaningful = any((isinstance(value, Number) and not isinstance(value, bool))
                         or (key in {"_process_costs", "_consumable_lines"} and bool(value))
                         or (key=='_pcba_component_results' and any(any(isinstance(number,Number) and not isinstance(number,bool)
                             for number in component.get('values',{}).values()) for component in value))
                         for key, value in self.values.items() if value is not None)
        return "PARTIAL" if meaningful else "BLOCKED"
    @property
    def final_cost_complete(self):
        if self.calculation_status != "CALCULATED": return False
        fields = required_final_fields(self.inputs, self.values)
        if not fields: return False
        try:
            return all(not isinstance(self.values.get(key), bool) and number(self.values.get(key), key).is_finite() for key in fields)
        except CalculationError: return False
    def as_dict(self):
        return {"record_id":self.record_id,"commodity":self.commodity,"type":self.type,"inputs":self.inputs,
                "values":self.values,"status":self.status,"calculation_status":self.calculation_status,"issues":self.issues,"branch":self.branch,
                "provenance":self.provenance,"dependency_only":self.dependency_only,"input_diagnostics":self.input_diagnostics,"final_cost_complete":self.final_cost_complete}


@dataclass
class CalculationRun:
    rows: list[RowResult]
    issues: list = field(default_factory=list)
    summaries: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    producer_rows: list[RowResult] = field(default_factory=list)
    @property
    def complete(self): return bool(self.rows) and all(r.status=="success" for r in self.rows) and not self.issues and self.metadata.get("coverage_complete") is True
    @property
    def counts(self):
        result = {"total": len(self.rows), "calculated": 0, "partial": 0, "blocked": 0, "unverified": 0, "error": 0}
        for row in self.rows: result[row.calculation_status.casefold()] += 1
        return result
    @property
    def status(self):
        counts = self.counts
        if any(counts[key] for key in ("partial", "blocked", "error")) or any(issue.get("blocking", issue.get("severity", "error") == "error") for issue in self.issues): return "partial"
        return "unverified" if counts["unverified"] else "calculated"
    def as_dict(self):
        return {"rows":[r.as_dict() for r in self.rows],"producer_rows":[r.as_dict() for r in self.producer_rows],"issues":self.issues,"summaries":self.summaries,
                "metadata":self.metadata,"complete":self.complete,"final_cost_complete":bool(self.rows) and all(row.final_cost_complete for row in self.rows) and self.status == "calculated","counts":self.counts,"status":self.status,"production_costing_enabled":False,
                "legacy_numerically_verified":False}


class CalculationContext:
    def __init__(self,master,rows):
        self.master,self.rows=master,tuple(dict(row) for row in rows)
        self.result_records=[];self.provenance=[]
        self._checkpoint_record_id=None; self._checkpoints={}
    def begin_record(self, record_id):
        """Explicit execution phase boundary, including producer versus owner."""
        self._checkpoint_record_id=record_id; self._checkpoints={}
    @contextmanager
    def intermediate_scope(self):
        """Keep a component's arithmetic separate from its owning board."""
        previous=self._checkpoints;previous_identity=self._checkpoint_record_id
        self._checkpoints={};self._checkpoint_record_id=None
        try:yield
        finally:self._checkpoints=previous;self._checkpoint_record_id=previous_identity
    def checkpoint(self, record, values):
        """Keep explicitly completed arithmetic; never inspect frames or masters.

        Component calculations have their own output projection. Their per-unit
        numbers must not masquerade as a board's aggregate checkpoint.
        """
        expected=record.get('_record_id')
        if self._checkpoint_record_id is not None and expected not in (None,self._checkpoint_record_id):
            raise CalculationError('partial_identity_mismatch','Intermediate result belongs to another input owner')
        for key,value in values.items():
            if any(word in normalized(key) for word in ('password','token','cookie','secret','assertion','authorization','encryption')): continue
            self._checkpoints[key]=deepcopy(value)
    def partial_values(self, explicit=None):
        """Return a detached checkpoint with unresolved final totals withheld."""
        result=deepcopy(self._checkpoints)
        for key,value in (explicit or {}).items():
            if not any(word in normalized(key) for word in ('password','token','cookie','secret','assertion','authorization','encryption')):
                result[key]=deepcopy(value)
        finals={'total','total/pc','total cost/pc','total cost/veh','total cost','grand total cost',
                'grand total cost/pc','grand total cost/veh','total cost /pc','total cost / pc',
                'total cost / veh','_variant_totals','_end_item_totals','_pcba_board_aggregated'}
        retained={key:value for key,value in result.items() if normalized(key) not in finals}
        for key,value in result.items():
            if normalized(key) in finals and value is not None:
                retained['Computed incomplete '+key]=value
        return retained

    def require_master(self,master_code,keys,field,**kwargs):
        kwargs.setdefault("decimal",True)
        try: return self.master.lookup(master_code,keys,field,**kwargs)
        except CalculationError: raise
        except (KeyError,ValueError) as exc:
            raise CalculationError("master_lookup_blocked",str(exc),master=master_code,keys=[str(k) for k in keys],
                lookup_keys={(k.name if hasattr(k,'name') else str(k)):str(v) if v is not None else None for k,v in keys.items()},
                master_field=field.name if hasattr(field,'name') else str(field),
                master_group=field.group if hasattr(field,'group') else None,
                master_error={"code":getattr(exc,"code",type(exc).__name__),**getattr(exc,"details",{})}) from exc
    def batch_has_make(self,record):
        return any(normalized(costing_supplier(r))==normalized(costing_supplier(record)) and
                   normalized(get(r,"Commodity"))==normalized(get(record,"Commodity")) and
                   normalized(get(r,"Type"))=="make" for r in self.rows)
    def require_result(self,record,commodity,type_,result_field,customer_no=None,supplier=None,end_item=None,owner_record_id=None):
        identity={"Customer No":customer_no if customer_no is not None else get(record,"Customer No"),
                  "Supplier":supplier if supplier is not None else get(record,"Supplier"),
                  "End Item":end_item if end_item is not None else get(record,"End Item")}
        found=[r for r in self.result_records if normalized(r.commodity)==normalized(commodity) and
               normalized(r.type)==normalized(type_) and
               (owner_record_id is None or r.inputs.get("_record_id")==owner_record_id) and
               all(normalized(get(r.inputs,k))==normalized(v) for k,v in identity.items() if v is not None)]
        if not found: raise CalculationError("missing_producer","Required same-run producer is absent",result_field,producer_commodity=commodity,producer_type=type_)
        if len(found)>1: raise CalculationError("duplicate_producer","More than one producer owns this business key",result_field)
        producer=found[0]
        if producer.status!="success" or producer.calculation_status!="CALCULATED":raise CalculationError("failed_producer","The required producer did not calculate successfully",result_field,producer_issues=producer.issues)
        value=get(producer.values,result_field)
        if value is None:raise CalculationError("missing_producer_value","Producer does not contain the required result",result_field)
        self.provenance.append({"producer_record_id":producer.record_id,"result":result_field})
        return number(value,result_field)
    def consumption(self,record,family):
        from .consumption import calculate_consumption
        return calculate_consumption(record,self,family)

    def end_item_supplier(self,record):
        # Original Buy Std/NonStd refers to the first End Item Supplier.
        # Named owner filtering prevents borrowing a different End Item's identity.
        owners=[r for r in self.rows if normalized(get(r,"Type"))=="end item" and
                normalized(get(r,"End Item"))==normalized(get(record,"End Item"))]
        if not owners:raise CalculationError("end_item_supplier_missing","No END ITEM supplier owns this component")
        suppliers={str(required(r,"Supplier")) for r in owners}
        if len(suppliers)!=1:raise CalculationError("end_item_supplier_ambiguous","END ITEM suppliers disagree")
        return next(iter(suppliers))
    def consign_volume(self,record):
        rows=[r for r in self.rows if normalized(get(r,"Type"))=="consign"]
        variants=list(dict.fromkeys(v for r in rows for v in r.get("_variants",{})))
        if not variants:raise CalculationError("consign_variant_missing","CONSIGN handling requires the original last variant volume")
        final=variants[-1]
        # consign() initializes Actual Cost/Pc to zero (primary PCM 10754).
        # merge() computes SUMPRODUCT(last variant, Qty/Veh, Actual Cost/Pc)
        # at 19325. Uploaded purchase costs are not an authorized override of
        # that original initialization. A quantity-only total changes the
        # handling threshold and does not represent the original formula.
        actual_cost = Decimal(0)
        return sum((number(get(r,"Qty/Veh"),"Qty/Veh")*
                    number(r.get("_variants",{}).get(final,0) or 0,final)*actual_cost
                    for r in rows),Decimal(0))
    def end_item_rollup(self,record):
        from .merging.rollup import rollup_owner
        return rollup_owner(record,self.result_records)
    def process_lines(self,record):
        lines=record.get("_process_lines",record.get("Process Lines"))
        if lines is None:raise CalculationError("process_mapping_missing","No verified process header mapping exists")
        return lines

    def producer_by_customer(self,record,commodity,type_,field,customer_no):
        found=[r for r in self.result_records if normalized(r.commodity)==normalized(commodity) and
               normalized(r.type)==normalized(type_) and normalized(get(r.inputs,"Customer No"))==normalized(customer_no) and
               normalized(get(r.inputs,"End Item"))==normalized(get(record,"End Item"))]
        if len(found)!=1:raise CalculationError("producer_ownership_unresolved","A unique same-run producer is required",field)
        if found[0].status!="success" or found[0].calculation_status!="CALCULATED":raise CalculationError("failed_producer","Required producer failed or remains unverified",field,producer_record_id=found[0].record_id,producer_issues=found[0].issues)
        self.provenance.append({"producer_record_id":found[0].record_id,"field":field})
        return number(required(found[0].values,field),field)
    def consumables(self,record):
        from .common import evaluate_expression,empty
        from .consumption_rules import FORMULAS
        import re
        code=get(record,"Material Code");total=Decimal(0)
        if empty(code):return total
        for row in self.master.records("consumable"):
            # part1:3409-3411 matches the source Material Code, not a grade
            # label or a convenient Base Part fallback.
            if row.value("Material Code")!=code:continue
            name=row.value("Consumable")
            if empty(name):raise CalculationError("consumable_material_missing","Applicable consumable has no Material Grade")
            expression=str(row.value("Consumables cost calculations") or "").strip()
            if expression not in FORMULAS.get(("consumable","Consumables cost calculations"),frozenset()):
                raise CalculationError("unported_consumable_formula","Consumable formula is not in the static source port")
            operands={name.strip() for name in re.findall(r"\{([^{}]+)\}",expression)}
            bindings={};material=None
            for field in row.fields:
                if not str(field.header or "").startswith("Material Master"):continue
                declaration=row.values.get(field.column)
                if not isinstance(declaration,str):continue
                for operand in re.findall(r"\{([^{}]+)\}",declaration):
                    operand=operand.strip()
                    if operand not in operands:continue
                    # create_indexformulacon:1492-1509 binds density/wastage
                    # to the consumable Material Grade; parent values differ.
                    if operand=="Rate/UOM":
                        bindings[operand]=self.require_master("material_master",{"Material Grade":name},
                            FieldRef("Rate/UOM",str(required(record,"RM Base"))))
                    else:
                        if material is None:material=self.master.record("material_master",{"Material Grade":name})
                        bindings[operand]=material.value(operand)
            # Original static consumable expressions always declare Rate/UOM
            # as a Material Master operand. Bad metadata cannot borrow parent RM.
            for operand in operands & {"Rate/UOM","% wastage","GSM/ Density"}:
                if operand not in bindings:
                    raise CalculationError("consumable_binding_missing",f"Consumable {operand} has no verified Material Master binding",operand)
            amount=number(evaluate_expression(expression,{**record,**bindings},excel_blank_zero=True),"consumables cost")
            total+=amount
            self.provenance.append({"master":"consumable","source_table":row.source_table,
                                    "source_row_id":row.source_row_id,"field":"Consumables cost calculations",
                                    "consumable":name,"material_operands":sorted(bindings)})
            self.provenance.append({"kind":"consumable_line","name":name,
                                    "rate":bindings.get("Rate/UOM"),"cost":amount,
                                    "material_code":code,"expression":expression})
        return total
