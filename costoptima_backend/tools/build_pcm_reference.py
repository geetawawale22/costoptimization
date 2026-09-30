"""Build database configuration for the supplied published-master layout.

Only describes source structure and formulas. Never reads expected results.
"""
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def table(name, sheet, columns, start=3, keys=("Supplier",), **extra):
    return {"table": name, "sheet": sheet, "columns": columns, "data_start": start, "required_keys": list(keys), **extra}


tables = [
    table("Material Master", "Material Master", None, keys=("Material Code",), header_row=2, group_row=1, merge_columns=["Rate/UOM", "Scrap Rate"]),
    table("Process Master", "Process Master", None, keys=("Process",), header_row=2, group_row=1, merge_columns=["Rate/UOM"]),
    table("Std BOP", "BOP", None, keys=("Customer No",), header_row=2),
    table("Non Std BOP", "Buy NonStd", None, keys=("Customer No",), header_row=2),
    table("Norms Make", "Norms", {"Supplier": "A", "ICC": "B", "REJ": "C", "OH": "D", "PROFIT": "E", "EFFICIENCY": "F"}, blank_as_zero=["ICC", "REJ", "OH", "PROFIT"]),
    table("Norms Applicability", "Norms", {"Supplier": "L", "ICC": "M", "REJ": "N", "OH": "O", "PROFIT": "P"}),
    table("Norms Assy", "Norms", {"Supplier": "AO", "ICC": "AP", "REJ": "AQ", "OH": "AR", "PROFIT": "AS"}, blank_as_zero=["ICC", "REJ", "OH", "PROFIT"]),
    table("Norms Buy Std", "Norms", {"Supplier": "AZ", "ICC": "BA", "REJ": "BB", "OH": "BC", "PROFIT": "BD", "Royalty": "BF"}, blank_as_zero=["ICC", "REJ", "OH", "PROFIT", "Royalty"]),
    table("Norms PnF", "Norms", {"Type": "CI", "Factor": "CJ"}, keys=("Type",)),
    table("Shift Bands", "Norms", {"Supplier": "CQ"}, unpivot=[{"column": c, "fields": {"Min": lo, "Max": hi}} for c, lo, hi in [("CR", 0, 401), ("CS", 401, 1000), ("CT", 1000, 2001), ("CU", 2001, 5001)]], value_field="Rate"),
    table("Special RM", "Special RM", {"Material Code": "C", "Material": "D", "APR#18": "E", "OCT#18": "F", "APR#19": "G", "OCT#19": "H", "APR#21": "I", "DEC#21": "J", "JUN#22": "K"}, keys=("Material Code",)),
    table("Special norms", "Special norms", {"Supplier": "B", "Hanger Cost": "C", "ICC": "D", "REJ ON INJ MOLDED PART": "E", "REJ ON MATERIAL & PROCESS": "F", "OVERALL REJECTION": "G", "OVERHEAD": "H", "PROFIT": "I", "PnF": "X"}),
    table("Paint Applicability", "Special norms", {"Supplier": "K", "ICC": "L", "REJ ON INJ MOLDED PART": "M", "REJ ON MATERIAL & PROCESS": "N", "OVERALL REJECTION": "O", "OVERHEAD": "P", "PROFIT": "Q"}),
    # Remaining published-master domains. These are stored as database rows as
    # well, even when a commodity still needs a site-specific formula mapping.
    table("eBom", "eBom", None, keys=("Material Code",), header_row=2,
          group_row=1, merge_columns=["Rate/UOM", "Scrap Rate"]),
    table("Scrap Stamping", "Scrap Stamping", None, start=2,
          keys=("Stamping",), header_row=1),
    table("Consumable", "Consumable", None, start=3,
          keys=("Base Part",), header_row=2),
    table("Seat Output Matrix", "Seat_Output_Matrix", None, start=2,
          keys=("Base",), header_row=1),
    table("Casting Complexity Master", "Casting Complexity Master", None, start=4,
          keys=(), header_row=3),
    table("Casting Recipe Master", "Casting Recipie Master", None, start=3,
          keys=("Addition in kg",), header_row=2),
    {"table": "Net Consumption base", "sheet": "Net Consumption base", "normalizer": "consumption_rules"},
    {"table": "Blank Consumption base", "sheet": "Blank Consumption base", "normalizer": "consumption_rules"},
    {"table": "Gross Consumption base", "sheet": "Gross Consumption base", "normalizer": "consumption_rules"},
]
# Assy is three side-by-side matrices sharing operation/category headers.
for label, supplier_col, start_col in [("Assy MHR", "A", 2), ("Assy CT", "BX", 77), ("Assy Handling", "ES", 150)]:
    tables.append(table(label, "Assy", {"Supplier": supplier_col}, start=4, unpivot=[
        {"column": start_col + i, "fields": {"Operation Index": i}} for i in range(21)], value_field="Value"))
layout = {"workbook": "pcm_published", "tables": tables, "cell_tables": [
    {"table": "Hard Parameters", "sheet": "Norms", "keys": {"Name": "HRS"}, "cells": {"Factor": "AC4"}}]}

definition = json.loads((ROOT / "data/pcm_definition.json").read_text(encoding="utf-8"))
definition["revision"] = "published-master-v2"
definition["master_views"] = [{"table": s["table"], "header_mode": "Single" if s["sheet"] in ("Norms", "Assy", "Special norms") else "0,1" if s.get("group_row") else "1", "unique": s.get("required_keys", []), "sources": [{"workbook": "pcm_published", "table": s["table"]}]} for s in tables]
definition["master_views"].append({"table": "Hard Parameters", "header_mode": "Single", "sources": [{"workbook": "pcm_published", "table": "Hard Parameters"}]})
definition["sources"] = {key: ["pcm_merged"] for key in definition["sources"]}
definition["table_aliases"] = {}
definition["rule_tables"] = ["Net Consumption base", "Blank Consumption base", "Gross Consumption base"]
definition["input"]["variant_markers"] = ["0 / 1 / 2 / 3", "Supplier"]
definition["input"]["headers"] = {"BOM": 1, "Hard": 2, "Paint": 2}
definition["input"]["enrichment"][0]["match"] = {"Type": ["MAKE", "ASSY", "SPECIAL PROCESS", "BUY NON STD"]}
definition["input"]["relations"] = [{"sheet": "Paint", "keys": ["Customer No"], "match": {"Type": ["SPECIAL PROCESS"]}, "header_values": {"RM Base": "T1"}}]


def steps(items):
    return [{"name": k, "expression": v} for k, v in items]


def markups(norms, use_basis=False):
    return [(key + "_cost", f'n(lookup("{norms}", "{col}", {{"Supplier": v("Supplier")}})) * ' +
             (f'basis("Norms Applicability", "{col}", {{"Supplier": v("Supplier")}})' if use_basis else 'basic_cost'))
            for key, col in [("icc", "ICC"), ("rej", "REJ"), ("oh", "OH"), ("profit", "PROFIT")]] + [
                ("markup", "icc_cost + rej_cost + oh_cost + profit_cost"), ("total", "basic_cost + markup")]


p = definition["profiles"]
# Do not disable a complete profile merely because it is outside the supplied
# parity workbook. Each profile now runs its configured database expressions;
# a genuinely missing operation/rule/master is reported at the exact step.
for profile in p.values():
    profile.pop("not_implemented", None)
    profile.setdefault("basis_fields", {}).update({
        "rm cost": "rm_cost", "rm cost / pc": "rm_cost",
        "basic cost": "basic_cost", "basic cost/pc": "basic_cost",
        "process cost": "process_cost", "process cost/pc": "process_cost",
    })
    for step in profile.get("steps", []):
        step["expression"] = step["expression"].replace(
            'v("RM Base", "Rate/UOM")', '"Rate/UOM_" + v("RM Base")')
        step["expression"] = step["expression"].replace(
            'lookup("Material Master", "Scrap Rate",',
            'lookup("Material Master", "Scrap Rate_" + v("RM Base"),')
        for cost, column in [("ICC Base", "ICC"), ("REJ Base", "REJ"),
                             ("OH Base", "OH"), ("PROFIT Base", "PROFIT")]:
            step["expression"] = step["expression"].replace(
                f'rule("Norms Make Applicability", "{cost}")',
                f'basis("Norms Applicability", "{column}", {{"Supplier": v("Supplier")}})')
p["hard"]["steps"] = steps([
    ("rate", 'n(lookup("Material Master", "Rate/UOM_" + v("RM Base"), {"Material Code": v("Material Code")}))'),
    ("gross_wt", 'n(v("Gross Wt")) if v("Gross Wt", None) != None else n(v("Part Wt")) * (n(lookup("Hard Parameters", "Factor", {"Name": "HRS"})) if text(v("HRS Y or N")) == "y" else n(rule("Norms Gross Wt", "Factor")))'),
    ("rm_cost", "rate * gross_wt"),
    ("shift_factor", 'n(band("Shift Bands", n(v("m/c Tonnage")), "Rate", {"Supplier": v("Supplier")}))'),
    ("shift_rate", 'n(v("m/c Tonnage")) * shift_factor'),
    ("efficiency", 'n(lookup("Norms Make", "EFFICIENCY", {"Supplier": v("Supplier")}))'),
    ("pc_per_shift", 'p("shift_seconds") / n(v("Cycle Time")) * n(v("Cavity")) * efficiency'),
    ("molding", "shift_rate / pc_per_shift"),
    ("deflashing", 'n(band("Shift Bands", n(v("m/c Tonnage")), "Rate", {"Supplier": "Deflashing"}))'),
    ("process_cost", "molding + deflashing"), ("basic_cost", "rm_cost + process_cost"),
] + markups("Norms Make", True))
p["hard"]["outputs"].update({"Rate Kg": "rate", "Total Cost/Pc": "total"})
p["hard"]["basis_fields"] = {"rm cost": "rm_cost", "rm cost / pc": "rm_cost", "basic cost/pc": "basic_cost", "process cost": "process_cost"}
p["soft"]["parameters"]["process_columns"] = [
    "PU Foaming",
    "PU Trimming",
    "PU Molding LHR",
    "EPP Moudling _Paracoat",
    "Diecutting in roller diecut press",
    "Diecutting in Heat sealing press_Ukay",
    "RM Blank Cutting_Ukay",
    "Preheating",
    "Thermoforming",
    "2 Robot Waterjet Trimming_Ukay",
    "BOP Manual Assy_Ukay",
    "Final Inspection_Ukay",
    "LHR Skilled_Ukay",
    "LHR Skilled_Others ST",
]
p["soft"]["steps"] = steps([
    ("has_material", 'v("Material Code", None) != None'),
    ("rate", 'n(lookup("Material Master", "Rate/UOM_" + v("RM Base"), {"Material Code": v("Material Code")})) if has_material else 0'),
    ("gross_consumption", 'n(v("Actual Gross Consumption")) if v("Actual Gross Consumption", None) != None else (n(rule("Gross Consumption base", "Gross Consumption")) if has_material else 0)'),
    ("rm_cost", "rate * gross_consumption"),
    ("process_cost", 'n(v("Process Cost")) if v("Process Cost", None) != None else process_columns("Rate/UOM_" + v("Sector & Location"), "Soft trim")'),
    ("basic_cost", "rm_cost + process_cost"),
    ("icc_cost", 'n(lookup("Norms Assy" if text(v("Type")) == "assy" else "Norms Make", "ICC", {"Supplier": v("Supplier")})) * (basic_cost if text(v("Type")) == "assy" else n(basis("Norms Applicability", "ICC", {"Supplier": v("Supplier")})))'),
    ("rej_cost", 'n(lookup("Norms Assy" if text(v("Type")) == "assy" else "Norms Make", "REJ", {"Supplier": v("Supplier")})) * (basic_cost if text(v("Type")) == "assy" else n(basis("Norms Applicability", "REJ", {"Supplier": v("Supplier")})))'),
    ("oh_cost", 'n(lookup("Norms Assy" if text(v("Type")) == "assy" else "Norms Make", "OH", {"Supplier": v("Supplier")})) * (basic_cost if text(v("Type")) == "assy" else n(basis("Norms Applicability", "OH", {"Supplier": v("Supplier")})))'),
    ("profit_cost", 'n(lookup("Norms Assy" if text(v("Type")) == "assy" else "Norms Make", "PROFIT", {"Supplier": v("Supplier")})) * (basic_cost if text(v("Type")) == "assy" else n(basis("Norms Applicability", "PROFIT", {"Supplier": v("Supplier")})))'),
    ("markup", "icc_cost + rej_cost + oh_cost + profit_cost"),
    ("total", 'basic_cost + markup + n(v("Tool Cost", 0)) + n(v("Packing", 0)) + n(v("Freight", 0))'),
    ("net_rm_cons", 'n(rule("Net Consumption base", "Cal RM cons")) if has_material else 0'),
    ("blank_length", 'n(rule("Blank Consumption base", "Blank Length")) if has_material else 0'),
    ("blank_width", 'n(rule("Blank Consumption base", "Blank Width")) if has_material else 0'),
])
definition["dispatch"].insert(3, {"match": {"Type": ["ASSY"], "Commodity": ["SOFT", "SOFT TRIM"]}, "profile": "soft"})
p["buy_std"]["steps"] = steps([("rm_cost", 'n(lookup("Std BOP", "Cost/Pc", {"Customer No": v("Customer No")}))'), ("process_cost", "0"), ("basic_cost", "rm_cost")] + markups("Norms Buy Std"))
p["buy_std"]["outputs"].pop("Royalty", None)
p["assy"]["parameters"]["operations"] = {name.lower(): i for i, name in enumerate(["Fix", "Snap", "Screw", "Pasting", "Inspection", "USW", "Vibration", "Punching", "HeatStake", "Scoring", "Milling", "Robo", "JPH-Manual", "JPH-Inspection", "JPH-USW", "JPH-Vibration", "JPH-Punching", "JPH-HeatStake", "JPH-Scoring", "JPH-Milling", "JPH-Robo"])}
p["assy"]["steps"] = steps([
    ("operation", 'p("operations")[text(v("Operation Process"))]'),
    ("ct_point", 'n(lookup("Assy CT", "Value", {"Supplier": v("Supplier"), "Operation Index": operation}))'),
    ("handling", 'n(lookup("Assy Handling", "Value", {"Supplier": v("Supplier"), "Operation Index": operation}))'),
    ("ct", 'n(v("CT(sec)")) if v("CT(sec)", None) != None else ct_point * n(v("No of Points")) + handling'),
    ("mhr", 'n(lookup("Assy MHR", "Value", {"Supplier": v("Supplier"), "Operation Index": operation}))'),
    ("rm_cost", "0"), ("process_cost", 'ct / p("seconds_per_hour") * mhr'), ("basic_cost", "process_cost"),
] + markups("Norms Assy"))
p["assy"]["outputs"].update({"CT Per Point": "ct_point", "Handling Time": "handling"})

# Legacy PCM process-column sheets calculate conversion cost by multiplying each
# nonblank process column by Process Master Rate/UOM_<Sector & Location>. This
# replaces placeholder operations("conversion") calls with a database-backed
# interpreter of the old sheet behavior.
for code, category in {
    "stamping": "Sheet Metal",
    "forging": "Forging",
    "sand": "Casting",
    "gdc": "GDC",
    "pdc": "PDC",
    "thermal_make": None,
}.items():
    if code in p:
        for step in p[code].get("steps", []):
            if step["name"] == "process_cost" and 'operations("conversion")' in step["expression"]:
                step["expression"] = 'n(v("Process Cost")) if v("Process Cost", None) != None else auto_process_cost(' + (repr(category) if category else '') + ')'
        if code == "forging":
            for step in p[code].get("steps", []):
                if step["name"] == "heat" and 'operations("heat")' in step["expression"]:
                    step["expression"] = '(n(v("Heat Treatment Cost")) if v("Heat Treatment Cost", None) != None else 0) if text(v("Type")) == "make" else 0'

paint = p["special_process"]
paint["parameters"] = {"rounding_places": 3}
paint["basis_fields"] = {"paint rm cost": "paint_rm", "basic cost": "paint_basic", "paint process cost": "paint_process", "make part cost": "make_cost"}
paint["steps"] = steps([
    ("paint_supplier", 'detail("Paint", "Supplier")'),
    ("paint_rm", 'sum_detail("Paint", \'0 if v("Material Code", None) == None and n(v("Consumption (in Litre)", 0)) == 0 else round(n(v("Consumption (in Litre)")), p("rounding_places")) * round(n(lookup("Special RM", v("RM Base"), {"Material Code": v("Material Code")})), p("rounding_places"))\')'),
    ("paint_process", 'n(lookup("Special norms", "Hanger Cost", {"Supplier": paint_supplier})) / n(detail("Paint", "No of parts / Jig"))'),
    ("make_cost", '0 if n(lookup("Special norms", "REJ ON INJ MOLDED PART", {"Supplier": paint_supplier})) == 0 else n(rule("Paint Dependency", "Make Part Cost"))'),
    ("paint_basic", "paint_rm + paint_process"),
])
for key, col in [("paint_icc", "ICC"), ("paint_rej_part", "REJ ON INJ MOLDED PART"), ("paint_rej", "REJ ON MATERIAL & PROCESS"), ("paint_overall", "OVERALL REJECTION"), ("paint_oh", "OVERHEAD"), ("paint_profit", "PROFIT")]:
    paint["steps"] += steps([(key, f'n(lookup("Special norms", "{col}", {{"Supplier": paint_supplier}})) * basis("Paint Applicability", "{col}", {{"Supplier": paint_supplier}})')])
paint["steps"] += steps([
    ("paint_markup", "paint_icc + paint_rej_part + paint_rej + paint_overall + paint_oh + paint_profit"),
    ("paint_pnf", '(paint_basic + paint_markup) * n(lookup("Special norms", "PnF", {"Supplier": paint_supplier}))'),
    ("rm_cost", "paint_rm"), ("process_cost", "paint_process"),
    ("basic_cost", "paint_basic + paint_markup + paint_pnf"),
] + markups("Norms Buy Std"))
paint["outputs"] = {"Paint RM Cost": "paint_rm", "Paint Process Cost": "paint_process", "Paint Basic Cost": "paint_basic", "Paint Markup": "paint_markup", "Paint PnF": "paint_pnf", "RM Cost/Pc": "rm_cost", "Process Cost/Pc": "process_cost", "Basic Cost/Pc": "basic_cost", "Total Markup": "markup", "Total": "total"}
paint["outputs"].update({"ICC Cost": "icc_cost", "REJ Cost": "rej_cost", "OH Cost": "oh_cost", "Profit Cost": "profit_cost"})
definition["output"]["analysis_rows"] = [
    {"label": "RM cost", "field": "RM Cost/Pc", "profiles": ["hard"]},
    {"label": "Process cost", "field": "Process Cost/Pc", "profiles": ["hard"]},
    {"label": "Buy Std", "field": "Basic Cost/Pc", "profiles": ["buy_std"]},
    {"label": "Buy Non Std", "field": "Total", "profiles": ["buy_non_std"]},
    {"label": "Assy", "field": "Basic Cost/Pc", "profiles": ["assy"]},
    {"label": "Special Process", "field": "Basic Cost/Pc", "profiles": ["special_process"]},
    {"label": "Markup", "field": "Total Markup"},
    {"label": "PnF", "field": "Total", "profiles": ["pkg", "frt"]},
    {"label": "Total", "field": "Total"},
    *[{"label": label, "field": key} for label, key in [("ICC Cost", "ICC Cost"), ("REJ Cost", "REJ Cost"), ("O&H Cost", "OH Cost"), ("PROFIT Cost", "Profit Cost")]],
]
definition["output"]["end_analysis_sheet"] = "End Item Analysis"
definition["output"]["sbc_sheet"] = "SBC"
definition["output"]["related_sheets"] = [{
    "relation": "Paint", "sheet": "Paint", "profile": "special_process",
    "rate_table": "Special RM", "material_field": "Material Code",
    "period_field": "RM Base", "consumption_field": "Consumption (in Litre)",
    "rate_output": "Rate / Litre", "cost_output": "RM cost / Pc", "rounding_places": 3,
}]

if __name__ == "__main__":
    for filename, value in [("pcm_master_import.json", layout), ("pcm_reference_definition.json", definition)]:
        (ROOT / "data" / filename).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        print(filename)
