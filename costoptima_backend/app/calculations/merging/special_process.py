"""Original SOFT special-process grouping over named Sr/Qty fields.

Primary source: soft884 defines Sr independently of Supplier No; merge19518
selects the first Customer No match;19520-19525 sums every supplier row sharing
that Sr, including nonadjacent rows. No Excel coordinates are used here.
"""
from decimal import Decimal
from contextlib import nullcontext
from numbers import Number

from ..common import CalculationError, empty, get, normalized, number, required
from ..context import RowResult
from ..masters import MasterLookupError


def _group_key(value):
    if empty(value):
        return ("blank", "")
    if isinstance(value, Number) and not isinstance(value, bool):
        return ("numeric", number(value, "Sr"))
    return ("text", str(value).strip().casefold())


def select_soft_group(owner, details):
    """Select original first-match Sr group, retaining source order and values.

    Matching Supplier alone reflects the original supplier sheet. Cross-EndItem
    collisions are rejected instead of silently borrowing another BOM owner's
    costs. Sr may be blank only when its original header actually exists.
    """
    supplier = normalized(required(owner, "Supplier"))
    customer = normalized(required(owner, "Customer No"))
    end_item = normalized(required(owner, "End Item"))
    candidates = [dict(row) for row in details if normalized(get(row, "Supplier")) == supplier]
    anchors = [row for row in candidates if normalized(get(row, "Customer No")) == customer]
    if not anchors:
        raise CalculationError("soft_special_process_anchor_missing", "No SOFT detail Customer No anchors this special-process group", "Customer No")
    anchor = anchors[0]
    if not any(normalized(key) == "sr" for key in anchor):
        raise CalculationError("soft_special_process_sr_missing", "The original SOFT Sr field is required; Supplier No is not a substitute", "Sr")
    if normalized(get(anchor, "End Item")) != end_item:
        raise CalculationError("soft_special_process_owner_conflict", "The first SOFT Customer No match belongs to another End Item")
    key = _group_key(get(anchor, "Sr"))
    group = [row for row in candidates if any(normalized(name) == "sr" for name in row) and _group_key(get(row, "Sr")) == key]
    if any(normalized(get(row, "End Item")) != end_item for row in group):
        raise CalculationError("soft_special_process_owner_conflict", "A supplier/Sr group crosses End Item ownership; no costs were borrowed")
    if not group:
        raise CalculationError("soft_special_process_group_missing", "The selected SOFT Sr group has no members")
    return group


def _source_id(record):
    value = record.get("_record_id")
    if not value:
        raise CalculationError("soft_special_process_source_identity_missing", "A SOFT group component lacks verified source identity")
    return str(value)


def _same_source(owner_record, component):
    component_id = _source_id(component)
    if str(owner_record.get("_record_id", "")) == component_id:
        return True
    return any(str(row.get("_record_id", "")) == component_id for row in owner_record.get("_details", ()))


def _checked_components(record, context):
    details = record.get("_soft_components")
    if details is None:
        # Compatibility for already-normalized runs: use actual retained detail
        # rows, never fabricate a special-process group from Supplier No.
        details = [row for owner in context.rows if normalized(get(owner, "Commodity")) == "soft"
                   for row in owner.get("_details", ())]
    if not isinstance(details, list):
        raise CalculationError("soft_special_process_group_missing", "SOFT special process requires parsed detail components")
    unique = []
    seen = set()
    for row in details:
        identity = _source_id(row)
        if identity not in seen:
            unique.append(row)
            seen.add(identity)
    group = select_soft_group(record, unique)
    owner_id = str(record.get("_record_id", ""))
    for component in group:
        for owner in context.rows:
            if str(owner.get("_record_id", "")) == owner_id or not _same_source(owner, component):
                continue
            if normalized(owner.get("_ownership_type", get(owner, "Type"))) == "end item":
                continue
            flags = owner.get("_variants", {})
            positive = not flags or any(number(value or 0, name) != 0 for name, value in flags.items())
            qty = get(owner, "Qty/Veh")
            if positive and (empty(qty) or number(qty, "Qty/Veh") != 0):
                raise CalculationError("soft_special_process_duplicate_ownership", "A grouped SOFT component also has a separate BOM cost owner", component_record_id=_source_id(component), other_owner=owner.get("_record_id"))
    return group


def calculate_soft_group(record, context):
    """Calculate producer-only SOFT rows then project their Sr-weighted costs."""
    from ..commodities.soft.make import calculate as calculate_component
    group = _checked_components(record, context)
    cache = getattr(context, "_soft_special_process_producers", None)
    if cache is None:
        context._soft_special_process_producers = cache = {}
    amounts = {"Total RM Cost": Decimal(0), "Process Cost": Decimal(0),
               "Tier 2 Markup+PnF": Decimal(0), "Basic cost": Decimal(0)}
    provenance = []
    first_surface = None
    anchor_customer = normalized(required(record, "Customer No"))
    failures=[];completed=0
    for raw in group:
        identity = _source_id(raw)
        if identity not in cache:
            data = dict(raw)
            data["Commodity"] = "SOFT"
            # Original details own their Type and Qty; the final BOM owner is
            # deliberately separate from this dependency-only component.
            if empty(get(data, "RM Base")) and not empty(get(record, "RM Base")):
                data["RM Base"] = get(record, "RM Base")
            producer = RowResult(identity + ":soft-special-producer", "SOFT", "SOFT SPECIAL PROCESS PRODUCER", data,
                                 branch="commodities.soft.make", dependency_only=True)
            scope=context.intermediate_scope() if hasattr(context,'intermediate_scope') else nullcontext()
            with scope:
                try:
                    producer.values = calculate_component(data, context)
                    producer.status = "success"
                except (CalculationError, MasterLookupError) as error:
                    producer.status = "blocked"
                    producer.values=context.partial_values(error.details.get('partial_values',{})) if isinstance(error,CalculationError) and hasattr(context,'partial_values') else context.partial_values() if hasattr(context,'partial_values') else {}
                    producer.issues = [error.as_dict() if isinstance(error, CalculationError) else {"code": error.code, "message": str(error)}]
                except Exception as error:
                    producer.status='error';producer.values=context.partial_values() if hasattr(context,'partial_values') else {}
                    producer.issues=[{'code':'calculation_error','message':str(error),'error_type':type(error).__name__}]
            cache[identity] = producer
            context.result_records.append(producer)
        producer = cache[identity]
        if producer.status != "success" or producer.calculation_status!='CALCULATED':
            failures.append({'component_record_id':identity,'producer_issues':producer.issues})
            continue
        completed+=1
        qty = number(get(raw, "Qty/Veh") or 0, "Qty/Veh")
        for target, source in (("Total RM Cost", "RM Cost/Pc"), ("Process Cost", "Process Cost/Pc"),
                               ("Tier 2 Markup+PnF", "MARKUP/Pc"), ("Basic cost", "Total")):
            amounts[target] += number(required(producer.values, source), source) * qty
        if first_surface is None and normalized(get(raw, "Customer No")) == anchor_customer:
            surface = get(producer.values, "B' Surface area")
            # part1 creates this output field even for no-material rows.
            first_surface = Decimal(0) if empty(surface) and empty(get(raw, "Material Code")) else number(surface, "B' Surface area")
        provenance.append({"producer_record_id": producer.record_id, "quantity": qty})
    if failures:
        if completed and hasattr(context,'checkpoint'):
            context.checkpoint(record,{'Completed grouped RM Cost':amounts['Total RM Cost'],
                'Completed grouped Process Cost':amounts['Process Cost'],'Completed grouped component count':completed})
        raise CalculationError('soft_special_process_producer_failed','One or more SOFT group components could not calculate; grouped total is unavailable.',
            producer_issues=[issue for failure in failures for issue in failure['producer_issues']],components=failures)
    amounts.update({"Sr": get(group[0], "Sr"), "Surface Area (in SQM)": first_surface,
                    "Consumable / Masking Cost": Decimal(0), "RM Cost": amounts["Total RM Cost"]})
    context.provenance.extend(provenance)
    return amounts
