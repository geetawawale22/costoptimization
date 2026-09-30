"""Configuration-driven calculation engine.

Legacy business formulas are represented by CalcRule rows. Forging keeps the
legacy worked-example fallback for backward compatibility, but no commodity
uses a random/illustrative result anymore: an unconfigured commodity is a
validation error rather than fabricated cost data.
"""
from typing import Dict, Any, Iterable, Optional
import re
import simpleeval
from app.services.pcm_hard import calculate_hard_line


def _num(value, default=0.0):
    try:
        if isinstance(value, str):
            text = value.strip()
            if text.endswith("%"):
                return float(text[:-1].replace(",", "") or default) / 100.0
        return float(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return default


def calculate_forging_line(row: Dict[str, Any], master_lookup: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    part_wt = _num(row.get("Part Wt"))
    input_wt = _num(row.get("Input Wt"), part_wt or 1.0)
    calc_type = str(row.get("Type", "")).strip().upper()
    material_code = row.get("Material Code")

    material = master_lookup.get("material_master", {}).get(str(material_code), {}) if material_code else {}
    rate_per_uom = _num(material.get("Rate/UOM"), _num(row.get("Rate/UOM")))
    scrap_rate = _num(material.get("Scrap Rate"), _num(row.get("Scrap Rate")))

    yield_pct = (part_wt / input_wt) if input_wt else 0.0
    scrap_wt = max(input_wt - part_wt, 0.0) * 0.8
    scrap_recovery = scrap_rate * scrap_wt
    heat_treatment_cost = _num(row.get("Heat Treatment Cost"))
    rm_cost_pc = (rate_per_uom * input_wt) + heat_treatment_cost - scrap_recovery if calc_type == "MAKE" else (rate_per_uom * input_wt) - scrap_recovery
    process_cost = _num(row.get("Process Cost"))
    basic_cost_pc = rm_cost_pc + process_cost

    norms = master_lookup.get("norms", {}).get(str(row.get("Supplier")), {})
    icc = _num(norms.get("ICC"), _num(row.get("ICC")))
    rej = _num(norms.get("REJ"), _num(row.get("REJ")))
    oh = _num(norms.get("OH"), _num(row.get("OH")))
    profit = _num(norms.get("Profit"), _num(row.get("Profit")))
    total_markup = icc + rej + oh + profit
    total = basic_cost_pc + _num(row.get("Tool Cost")) + _num(row.get("Packing")) + _num(row.get("Freight")) + total_markup
    return {
        "Yield (%)": round(yield_pct, 6), "Scrap Wt": round(scrap_wt, 6),
        "Scrap Recovery": round(scrap_recovery, 6), "RM Cost/Pc": round(rm_cost_pc, 6),
        "Process Cost": round(process_cost, 6), "Basic Cost/Pc": round(basic_cost_pc, 6),
        "Total Markup": round(total_markup, 6), "Total": round(total, 6),
    }


PCM_FIELD_ALIASES = {
    "qty_veh": ["Qty/Veh", "Qty Veh", "Quantity/Veh"],
    "basic_cost": ["Basic Cost", "Basic"],
    "basic_cost_pc": ["Basic Cost/Pc", "Basic Cost Pc", "Basic Cost per Pc", "Basic Cost"],
    "rm_cost_pc": ["RM Cost/Pc", "RM cost/Pc", "RM Cost Pc", "RM cost"],
    "process_cost": ["Process Cost", "Process cost", "Process Cost/Pc"],
    "icc": ["ICC", "ICC %"],
    "rej": ["REJ", "Rejection", "REJ %"],
    "oh": ["OH", "O&H", "O and H", "Overhead"],
    "profit": ["Profit", "PROFIT", "Profit %"],
    "markup": ["Markup", "Total Markup", "Paint Markup"],
    "type": ["Type"],
    "commodity": ["Commodity"],
    "material_code": ["Material Code"],
}


def _rule_context(row: Dict[str, Any]) -> Dict[str, Any]:
    context = dict(row)
    normalized = {}
    for key, value in row.items():
        alias = re.sub(r"[^A-Za-z0-9_]", "_", str(key).strip()).strip("_").lower()
        if alias:
            normalized.setdefault(alias, value)
            context.setdefault(alias, value)
    for alias, names in PCM_FIELD_ALIASES.items():
        for name in names:
            generated = re.sub(r"[^A-Za-z0-9_]", "_", str(name).strip()).strip("_").lower()
            if name in row and row.get(name) not in (None, ""):
                context.setdefault(alias, row.get(name))
                break
            if generated in normalized and normalized.get(generated) not in (None, ""):
                context.setdefault(alias, normalized.get(generated))
                break
        context.setdefault(alias, 0 if alias not in {"type", "commodity", "material_code"} else "")
    # Apply defaults after normalization: otherwise a synthetic input_wt=0
    # masks the real 'Input Wt' column before rules ever see it.
    context.setdefault("rate_per_uom", context.get("rate_uom", 0))
    for key in ("scrap_rate", "heat_treatment_cost", "tool_cost", "packing", "freight", "input_wt", "part_wt"):
        context.setdefault(key, 0)
    return context


def evaluate_calc_rules(rules: Iterable, row: Dict[str, Any]) -> Dict[str, Any]:
    context = _rule_context(row)
    result = {}
    for rule in sorted((r for r in rules if not r.is_deleted), key=lambda r: (r.priority or 0, r.id or 0)):
        try:
            if rule.condition_expr and not simpleeval.simple_eval(rule.condition_expr, names=context):
                continue
            value = simpleeval.simple_eval(rule.formula_expr, names=context)
            result[rule.output_field] = value
            context[rule.output_field] = value
            alias = re.sub(r"[^A-Za-z0-9_]", "_", str(rule.output_field).strip()).strip("_").lower()
            if alias:
                context[alias] = value
        except Exception as exc:
            raise ValueError(f"Formula '{rule.output_field}' failed: {exc}") from exc
    return result


def run_synchronous_calculation(bom_lines, master_lookup=None, rules_by_commodity=None):
    master_lookup = master_lookup or {}
    rules_by_commodity = rules_by_commodity or {}
    by_commodity: Dict[str, Dict[str, Any]] = {}
    for line in bom_lines:
        code = line.commodity_code or "unclassified"
        bucket = by_commodity.setdefault(code, {"records": 0, "total_cost": 0.0, "lines": []})
        bucket["records"] += 1
        row = line.row_data or {}
        rules = rules_by_commodity.get(code, [])
        calc_row = dict(row)
        material = master_lookup.get("material_master", {}).get(str(row.get("Material Code")), {})
        norms = master_lookup.get("norms", {}).get(str(row.get("Supplier")), {})
        for key, value in material.items():
            calc_row.setdefault(str(key), value)
        for key, value in norms.items():
            calc_row.setdefault(str(key), value)
        if rules:
            try:
                result = evaluate_calc_rules(rules, calc_row)
            except ValueError as exc:
                result = {"Total": 0.0, "calculation_status": "Needs Review", "calculation_error": str(exc)}
        elif code.lower() in ("trim", "hard"):
            # BOM rows resolve to CommodityType code "trim" (see
            # app/routers/boms.py::COMMODITY_ALIASES for the "Hard" sheet/value
            # -> "trim" mapping), which is also what HardLookup's master-data
            # lookups (trim_self/trim_master_user/trim_pce workbook codes in
            # seed.py) are keyed to. "hard" is kept here too in case a
            # CommodityType literally coded "hard" is ever created by hand.
            result = calculate_hard_line(row, master_lookup)
        elif code.lower() == "forging":
            result = calculate_forging_line(row, master_lookup)
        else:
            result = {"Total": 0.0, "calculation_status": "Not Configured",
                      "calculation_error": f"No calculation rules configured for commodity '{code}'"}
        bucket["total_cost"] += _num(result.get("Total"))
        bucket["lines"].append(result)
    for bucket in by_commodity.values():
        bucket["total_cost"] = round(bucket["total_cost"], 6)
    return by_commodity
