"""Build the reviewable database seed; never imported by the runtime engine.

Run from the backend directory: python tools/build_pcm_definition.py
"""
import json
from pathlib import Path


def step(name, expression):
    return {"name": name, "expression": expression}


def profile(scope, sheet, formulas, parameters=None, outputs=None, norms="Norms Make", finish=True):
    steps = [step(*s) for s in formulas]
    if finish:
        steps += [step("basic_cost", "rm_cost + process_cost")]
        for name, column in [("icc", "ICC"), ("rej", "REJ"), ("oh", "OH"), ("profit", "PROFIT")]:
            steps += [step(name + "_rate", f'n(lookup("{norms}", "{column}", {{"Supplier": v("Supplier")}}))'),
                      step(name + "_cost", f'{name}_rate * n(rule("Norms Make Applicability", "{column} Base"))')]
        steps += [step("markup", "icc_cost + rej_cost + oh_cost + profit_cost"),
                  step("total", 'basic_cost + markup + n(v("Tool Cost", 0)) + n(v("Packing", 0)) + n(v("Freight", 0))')]
    standard = {"RM Cost/Pc": "rm_cost", "Process Cost/Pc": "process_cost", "Basic Cost/Pc": "basic_cost",
                "ICC Cost": "icc_cost", "REJ Cost": "rej_cost", "OH Cost": "oh_cost", "Profit Cost": "profit_cost",
                "Total Markup": "markup", "Total": "total"} if finish else {"Total": "total"}
    return {"scope": scope, "sheet": sheet, "parameters": parameters or {}, "operations": {},
            "steps": steps, "outputs": {**standard, **(outputs or {})}}


material = [('rate', 'n(lookup("Material Master", v("RM Base", "Rate/UOM"), {"Material Code": v("Material Code")}))')]
process = ('process_cost', 'n(v("Process Cost")) if v("Process Cost", None) != None else operations("conversion")')
profiles = {}
profiles["forging"] = profile("forging", "Forging", material + [
    ("input_wt", 'n(v("Input Wt"))'), ("part_wt", 'n(v("Part Wt"))'),
    ("yield_ratio", 'part_wt / input_wt'),
    ("scrap_wt", '(input_wt - part_wt) * p("scrap_recovery_factor")'),
    ("scrap_rate", 'n(lookup("Material Master", "Scrap Rate", {"Material Code": v("Material Code")}))'),
    ("scrap_recovery", 'scrap_wt * scrap_rate'),
    ("heat", '(n(v("Heat Treatment Cost")) if v("Heat Treatment Cost", None) != None else operations("heat")) if text(v("Type")) == "make" else 0'),
    ("rm_cost", 'rate * input_wt + heat - scrap_recovery'), process,
], {"scrap_recovery_factor": 0.8}, {"Yield (%)": "yield_ratio", "Scrap Wt": "scrap_wt", "Scrap Recovery": "scrap_recovery"})

profiles["hard"] = profile("trim", "Make", material + [
    ("gross_wt", 'n(v("Gross Wt")) if v("Gross Wt", None) != None else n(v("Part Wt")) * (p("hrs_factor") if text(v("HRS Y or N")) == "y" else n(band("Norms Gross Wt", n(v("Part Wt")), "Factor", {})))'),
    ("rm_cost", 'rate * gross_wt'),
    ("efficiency", 'n(lookup("Norms Make", "EFFICIENCY", {"Supplier": v("Supplier")}))'),
    ("shift_factor", 'n(lookup("Norms Shift Factor", "Shift Factor", {"Process": lookup("Material Master", "Applicable Process", {"Material Code": v("Material Code")}), "Sector & Location": v("Sector & Location")}))'),
    ("shift_rate", 'n(v("m/c Tonnage")) * shift_factor'),
    ("pc_per_shift", 'p("shift_seconds") / n(v("Cycle Time")) * n(v("Cavity")) * efficiency'),
    ("molding", 'shift_rate / pc_per_shift'),
    ("deflashing", 'n(band("Norms Deflashing", n(v("m/c Tonnage")), "Cost", {})) if n(v("m/c Tonnage")) > p("deflashing_threshold") else 0'),
    ("process_cost", 'molding + deflashing'),
], {"hrs_factor": 1.03, "shift_seconds": 28800, "deflashing_threshold": 400},
    {"Gross Wt": "gross_wt", "Shift Factor": "shift_factor", "Shift Rate": "shift_rate", "Pc per shift": "pc_per_shift", "Molding Cost": "molding", "Deflashing Cost": "deflashing"})

for code, scope, sheet in [("soft", "soft_trim", "Soft"), ("stamping", "sheet_metal", "Stamping")]:
    profiles[code] = profile(scope, sheet, material + [
        ("gross_consumption", 'n(v("Actual Gross Consumption")) if v("Actual Gross Consumption", None) != None else n(rule("Gross Consumption base", "Gross Consumption"))'),
        ("rm_cost", 'rate * gross_consumption'), process,
    ], outputs={"Gross Consumption": "gross_consumption"})

profiles["sand"] = profile("forging", "Sand Casting", [
    ("rate", 'n(rule("Casting Recipe Master", "Rate/UOM")) if text(v("Type")) == "make" else n(lookup("Material Master", v("RM Base", "Rate/UOM"), {"Material Code": v("Material Code")}))'),
    ("gross_wt", 'n(v("Gross Wt"))'), ("scrap_wt", 'gross_wt - n(v("Part Wt"))'),
    ("scrap_recovery", 'scrap_wt * rate / p("scrap_divisor") * p("scrap_recovery_factor")'),
    ("rm_cost", 'rate * gross_wt'), process,
], {"scrap_divisor": 4, "scrap_recovery_factor": 0.85}, {"Machining Scrap Recovery": "scrap_recovery"})
# Legacy L7955-7957: deduct recovery AFTER markups, retaining the gross RM base.
profiles["sand"]["steps"].append(step("total", "total - scrap_recovery"))

for code in ("gdc", "pdc"):
    geometry = material + [
        ("casting_wt", 'n(v("Casting Wt"))'),
        ("burning_loss", 'casting_wt * p("burning_loss")'),
        ("gross_wt", 'casting_wt + burning_loss'),
        ("scrap_wt", 'casting_wt - n(v("Part Wt"))'),
        ("scrap_cost", 'scrap_wt * rate * p("scrap_rate_factor") * p("scrap_recovery_factor")'),
    ]
    if code == "gdc":
        geometry += [("shot_wt", 'casting_wt / p("shot_yield")'),
                     ("core_cost", 'n(v("Core Weight per Part")) * n(lookup("Process Master", "Rate", {"Process": "CORE", "Sector & Location": v("Sector & Location")}))'),
                     ("rm_cost", 'rate * gross_wt - scrap_cost + core_cost'), process]
    else:
        geometry += [("rm_cost", 'rate * gross_wt - scrap_cost'), process]
    profiles[code] = profile("gdc_pdc", code.upper(), geometry,
        {"burning_loss": 0.07, "scrap_rate_factor": 0.7, "scrap_recovery_factor": 0.85, "shot_yield": 0.65},
        {"Burning Loss": "burning_loss", "Gross Wt": "gross_wt", "Scrap Cost": "scrap_cost"})

profiles["thermal"] = profile("dac_hvac_other", "Thermal", material + [
    ("gross_wt", 'n(v("Part Wt")) * p("gross_factor")'),
    ("rm_cost", 'rate * gross_wt'),
    ("process_cost", 'rm_cost * n(lookup("Process Master", "Rate", {"Process": "Core Coversion Cost", "Sector & Location": v("Sector & Location")}))'),
], {"gross_factor": 1.2}, {"Gross Wt": "gross_wt"})
profiles["thermal_make"] = profile("dac_hvac_other", "Thermal Make", material + [
    ("tube_pitch", 'n(v(p("flow_dimensions")[text(v("Flow type"))])) / n(v("Qty/Veh"))'),
    ("fin_height", 'tube_pitch - n(v("Tube Height (Post Forming)"))'),
    ("fin_qty", 'n(v("Input Blank Length")) / p("length_divisor") * n(v("FPDM"))'),
    ("fin_length", 'fin_height * fin_qty'),
    ("consumption", 'n(rule("Net Consumption base", "Net RM Consumption"))'),
    ("rm_cost", 'rate * consumption'), process,
], {"length_divisor": 100, "flow_dimensions": {"horizontal": "L", "vertical": "W"}},
   {"Tube Pitch": "tube_pitch", "Fin Height": "fin_height", "Fin Qty": "fin_qty", "Fin Length": "fin_length"})

profiles["pcba"] = profile("ee", "PCBA", [
    ("fob", 'n(lookup("eBom", "FOB", {"Customer No": v("Customer No"), "Supplier": v("Supplier")}))'),
    ("exchange", 'n(lookup("eBom", "EXCHANGE", {"Customer No": v("Customer No"), "Supplier": v("Supplier")}))'),
    ("landing", 'n(lookup("eBom", "Landing Factor", {"Customer No": v("Customer No"), "Supplier": v("Supplier")}))'),
    ("rm_cost", 'fob * exchange * landing'),
    ("process_cost", 'n(v("Solder Points")) * n(lookup("Process Master", "Rate", {"Process": "SOLDERING", "Sector & Location": v("Sector & Location")}))'),
])

profiles["buy_std"] = profile("shared", "Buy Std", [
    ("purchase", 'n(lookup("Std BOP", "Cost/Pc", {"Customer No": v("Customer No")}))'),
    ("royalty", 'purchase * n(lookup("Norms Buy Std", "Royalty", {"Supplier": v("End Item Supplier", v("Supplier"))}))'),
    ("rm_cost", 'purchase + royalty'), ("process_cost", '0'),
], outputs={"Royalty": "royalty"}, norms="Norms Buy Std")
profiles["buy_non_std"] = profile("shared", "Buy Non Std", [
    ("total", 'n(lookup("Non Std BOP", "Cost/Pc", {"Customer No": v("Customer No")}))'),
], finish=False)
profiles["consign"] = profile("shared", "Consign", [
    ("handling", 'n(band("Norms Consign", n(v("Part Wt")), "Handling", {"Supplier": v("Supplier")}))'),
    ("total", 'n(v("Actual Cost/Pc")) + handling'),
], outputs={"Handling": "handling"}, finish=False)
profiles["assy"] = profile("trim", "Assy", [
    ("ct", 'n(v("CT(sec)")) if v("CT(sec)", None) != None else n(lookup("Assy", "CT per Point", {"Customer No": v("Customer No"), "Category": v("Category")})) * n(v("No of Points")) + n(lookup("Assy", "Handling Time", {"Customer No": v("Customer No"), "Category": v("Category")}))'),
    ("mhr", 'n(lookup("Assy", "MHR", {"Category": v("Category"), "Process": v("Operation Process")}))'),
    ("rm_cost", '0'), ("process_cost", 'ct / p("seconds_per_hour") * mhr'),
], {"seconds_per_hour": 3600}, {"CT(sec)": "ct", "MHR": "mhr"}, norms="Norms Assy")
for code in ("pkg", "frt"):
    profiles[code] = profile("shared", code.upper(), [("total", 'n(v("Basic Cost", p("basic_cost"))) * p("rate")')],
                             {"basic_cost": 0, "rate": 0.03}, finish=False)
profiles["end_item"] = profile("shared", "End Item Input", [("total", "0")], finish=False)
# This hook deliberately fails until the full multi-row Paint rule is translated.
profiles["special_process"] = profile("special_process", "Special Process", [("total", 'n(rule("Special Process Rules", "Total"))')], finish=False)
profiles["seat"] = profile("seat_mechanism", "Seat", [("total", 'n(rule("Seat Output Matrix", "Total"))')], finish=False)

dispatch = []
for typ, code in [("END ITEM", "end_item"), ("BUY STD", "buy_std"), ("CONSIGN", "consign"), ("ASSY", "assy"), ("PKG", "pkg"), ("FRT", "frt"), ("SPECIAL PROCESS", "special_process")]:
    dispatch.append({"match": {"Type": [typ]}, "profile": code})
dispatch.append({"match": {"Commodity": ["THERMAL", "HVAC"], "Type": ["MAKE"]}, "profile": "thermal_make"})
for code, names in [("hard", ["HARD", "TRIM"]), ("forging", ["FORGING"]), ("soft", ["SOFT", "SOFT TRIM"]),
                    ("stamping", ["STAMPING", "SHEET METAL"]), ("sand", ["SAND CASTING"]), ("gdc", ["GDC"]),
                    ("pdc", ["PDC"]), ("thermal", ["THERMAL", "HVAC"]), ("pcba", ["PCBA"]), ("seat", ["SEAT", "SEAT MECHANISM"])]:
    dispatch.append({"match": {"Commodity": names}, "profile": code})
dispatch.append({"match": {"Type": ["BUY NON STD"], "Commodity": ["", "GENERIC"]}, "profile": "buy_non_std"})

definition = {
    "schema_version": 1, "revision": "pcm-documented-v1",
    "evidence": ["PCM_Reverse_Engineering_Baseline.md", "PCM_Reverse_Engineering_Baseline.docx", "PCM_Project Document (1).docx", "PCM_5/PCM_5/Sheet_Metal_Forging_with summary_chk_Chnages.py"],
    "sources": {scope: [f"{scope}_self", f"{scope}_master_user", f"{scope}_pce", "pcm_master"] for scope in {p["scope"] for p in profiles.values()}},
    "table_aliases": {"Std BOP": ["Std BOP", "BOP"], "Non Std BOP": ["Non Std BOP", "BuyNonStd", "Buy NonStd"],
                      "Casting Recipe Master": ["Casting Recipe Master", "Casting Recipie Master"]},
    "process_units": {"per_hour": 60, "per_minute": 1, "per_piece": 1, "per_kg": 1},
    "profiles": profiles, "dispatch": dispatch,
    "input": {"bom_sheet": "BOM", "enrichment": [{"sheet": sheet, "keys": ["Customer No", "End Item", "Type"], "match": {"Commodity": names}} for sheet, names in [
        ("Hard", ["HARD", "TRIM"]), ("Soft", ["SOFT"]), ("Stamping", ["STAMPING", "SHEET METAL"]),
        ("Forging", ["FORGING"]), ("Sand Casting", ["SAND CASTING"]), ("GDC", ["GDC"]), ("PDC", ["PDC"]), ("Thermal", ["THERMAL"]), ("PCBA", ["PCBA"]) ]]},
    "output": {"master_sheet": "Master_Output", "rollup_sheet": "End Item", "group_by": ["End Item", "Supplier"], "quantity_field": "Qty/Veh", "variants": [],
               "analysis_components": ["RM Cost/Pc", "Process Cost/Pc", "Total Markup", "Total"]},
}
definition["sources"]["shared"] = ["pcm_master"]
definition["sources"]["trim"].insert(0, "shift_factor_rate")
for key in ("buy_std", "buy_non_std", "consign", "assy"):
    profiles[key]["scope_by_commodity"] = {"hard": "trim", "trim": "trim", "forging": "forging", "stamping": "sheet_metal", "sheet metal": "sheet_metal", "pcba": "ee", "thermal": "dac_hvac_other", "gdc": "gdc_pdc", "pdc": "gdc_pdc"}

if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "data" / "pcm_definition.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(definition, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(profiles)} profiles to {target}")
