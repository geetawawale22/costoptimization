from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any, Dict, Iterable, Mapping


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _key(value: Any) -> str:
    return _clean(value).casefold()


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        if isinstance(value, str):
            text = value.strip()
            if text.endswith("%"):
                number = float(text[:-1].replace(",", "") or default) / 100.0
                return default if math.isnan(number) else number
        number = float(value)
        return default if math.isnan(number) else number
    except (TypeError, ValueError):
        return default


def _first_number(record: Mapping[str, Any], names: Iterable[str], default: float = 0.0) -> float:
    for name in names:
        value = record.get(name)
        if value not in (None, ""):
            return _num(value, default)
    return default


# --- Dynamic "active rate period" resolution ---------------------------------
# Legacy Material Master / Special RM files publish one rate column per month
# (e.g. "JAN#25", "JUN#22") and PCM_5 was manually repointed at whichever
# column was current. Hardcoding one such column name freezes the app to that
# one month forever. Instead: if nothing overrides it, scan whatever period
# columns actually exist on the row(s) and use the most recent one so this
# keeps working after every monthly master-data refresh with zero code changes.
_MONTH_ABBR = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
               "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
_PERIOD_RE = re.compile(r"^([A-Za-z]{3})#(\d{2})$")


def _parse_period(name: Any):
    match = _PERIOD_RE.match(_clean(name))
    if not match:
        return None
    month = match.group(1).upper()
    if month not in _MONTH_ABBR:
        return None
    return (2000 + int(match.group(2)), _MONTH_ABBR[month]), _clean(name)


def _latest_period_column(records: Iterable[Mapping[str, Any]]) -> str | None:
    best = None
    for record in records:
        for column in record.keys():
            parsed = _parse_period(column)
            if parsed and (best is None or parsed[0] > best[0]):
                best = parsed
    return best[1] if best else None


def _find_rate(record: Mapping[str, Any], rm_base: Any) -> float:
    base = _clean(rm_base)
    if base and base in record and record.get(base) not in (None, ""):
        return _num(record.get(base))
    # PCM_master's Material Master files have no stable per-period column
    # NAME at all (verified against the real file: Excel/openpyxl produces
    # differently-suffixed duplicate headers per file — "Rate/UOM11325" in
    # one workbook, "Rate/UOM1132522" in another — because every "Rate/UOM"
    # + "Scrap Rate" pair is one month's published rate, appended as a new
    # column each month, and openpyxl auto-suffixes the duplicate name).
    # Hardcoding any one of those suffixes only worked for the exact file it
    # was read from. What IS reliably true across every Material Master file
    # checked: the right-most (latest-appended) non-empty "Rate/UOM"-prefixed
    # column is always the current rate. So: take the last one, by column
    # order, instead of guessing a specific suffix.
    latest_value = None
    for name, value in record.items():
        if str(name).replace(" ", "").upper().startswith("RATE/UOM") and value not in (None, ""):
            latest_value = value
    if latest_value is not None:
        return _num(latest_value)
    return 0.0


@dataclass(frozen=True)
class HardLookup:
    material_master: Mapping[str, Mapping[str, Any]]
    std_bop: Mapping[str, Mapping[str, Any]]
    assy: Mapping[str, Mapping[str, Any]]
    norms_make: Mapping[str, Mapping[str, Any]]
    norms_buy_std: Mapping[str, Mapping[str, Any]]
    norms_assy: Mapping[str, Mapping[str, Any]]
    norms_special_process: Mapping[str, Mapping[str, Any]]
    special_rm: Mapping[str, Mapping[str, Any]]
    special_norms: Mapping[str, Mapping[str, Any]]
    # PCM_master/Shift_Factor_Rate.xlsx — Applicable Process -> Shift Factor /
    # Shift Rate / Molding Cost / Deflashing Cost. Previously not wired in at
    # all; Molding/Deflashing costing used fixed numbers regardless of process.
    shift_factor_rate: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    # Admin-editable defaults (Settings > App Config, group "PCM_HARD"),
    # used only when neither the row nor a master lookup has a value. See
    # app/services/app_config_service.get_config_value / discover_config_keys —
    # this dict is populated by that same mechanism in _master_lookup(), so
    # every key referenced below shows up automatically in the Settings UI.
    config: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_master_lookup(cls, lookup: Mapping[str, Any]) -> "HardLookup":
        scoped = lookup.get("scoped", {})

        def tab(workbook_code: str, *tab_names: str) -> Mapping[str, Mapping[str, Any]]:
            """Tries each candidate tab name in turn, scoped first (commodity-correct),
            then the flat cross-commodity fallback. Accepts several spellings because
            the tab names actually seeded in app/seed.py's COMMODITY_STRUCTURE (space-
            separated, e.g. 'Norms Make', 'Special Norms') did not match the
            underscore/lowercase spellings this function was originally written
            against (e.g. 'Norms_Make', 'Special norms') — those never matched
            anything and silently returned {} even with data imported. Verified
            against seed.py's COMMODITY_STRUCTURE["trim"]["files"] directly.
            """
            for name in tab_names:
                hit = scoped.get(workbook_code, {}).get(name)
                if hit:
                    return hit
            for name in tab_names:
                hit = lookup.get(name)
                if hit:
                    return hit
            return {}

        return cls(
            material_master=tab("trim_master_user", "Material Master"),
            std_bop=tab("trim_pce", "Std BOP", "BOP"),
            assy=tab("trim_self", "Assy"),
            norms_make=tab("trim_self", "Norms Make", "Norms_Make"),
            norms_buy_std=tab("trim_self", "Norms Buy Std", "Norms_Buy Std"),
            norms_assy=tab("trim_self", "Norms Assy", "Norms_Assy"),
            norms_special_process=tab("trim_self", "Norms Special Process Norms",
                                       "Norms_Special Process Norms", "Special Norms", "Special norms"),
            special_rm=tab("special_process_master_user", "Special RM"),
            special_norms=tab("trim_self", "Special Norms", "Special norms"),
            # Two possible homes for shift-based costing data: the commodity-scoped
            # 'Norms Shift Factor' tab under trim_self (seed.py), and the standalone
            # cross-commodity 'Shift Factor Rate' workbook (PCM/Shift_Factor_Rate.xlsx).
            # Prefer the commodity-scoped one when both have data.
            shift_factor_rate=tab("trim_self", "Norms Shift Factor")
            or tab("shift_factor_rate", "Shift Factor Rate"),
            config=lookup.get("config", {}),
        )

    def by_key(self, index: Mapping[str, Mapping[str, Any]], value: Any) -> Mapping[str, Any]:
        wanted = _key(value)
        if not wanted:
            return {}
        direct = index.get(_clean(value)) or index.get(wanted)
        if direct:
            return direct
        for key, record in index.items():
            if _key(key) == wanted:
                return record
        return {}

    def cfg(self, key: str, default: float) -> float:
        return _num(self.config.get(key), default)

    def shift_row_for(self, row: Mapping[str, Any], material: Mapping[str, Any]) -> Mapping[str, Any]:
        """Best-effort match into Shift Factor Rate by 'Applicable Process'.
        Material Master's own 'Applicable Process' column (e.g. "Injection
        Molding", verified against the real Trim Material Master file) is the
        reliable join key — tried before any row-level guess, since Hard-sheet
        rows don't carry a process column themselves in real Sprint Input files.
        """
        for candidate in (material.get("Applicable Process"), row.get("Applicable Process"),
                          row.get("Process"), material.get("Material Family")):
            if candidate:
                hit = self.by_key(self.shift_factor_rate, candidate)
                if hit:
                    return hit
        return {}


def _norms(index: Mapping[str, Mapping[str, Any]], supplier: Any) -> Mapping[str, Any]:
    wanted = _key(supplier)
    for record in index.values():
        if _key(record.get("Supplier")) == wanted:
            return record
    return {}


def calculate_make(row: Mapping[str, Any], lookup: HardLookup) -> Dict[str, Any]:
    material = lookup.by_key(lookup.material_master, row.get("Material Code"))
    # The Hard sheet's own 'Supplier' column holds the estimator's name in real
    # Sprint Input files (verified: e.g. "Abhijeet"), not an RM supplier code —
    # it never matches a Norms Make row. The real supplier for costing purposes
    # is Material Master's 'RM Supplier' field (e.g. "KINGFA"), keyed off the
    # same Material Code already resolved above. Row-level 'Supplier' is kept
    # only as a fallback for input shapes where it genuinely is the RM supplier.
    rm_supplier = material.get("RM Supplier") or row.get("Supplier")
    norms = _norms(lookup.norms_make, rm_supplier)
    if not norms:
        # PCM_5 itself falls back to a generic bucket when no specific supplier
        # match is found for a Hard-commodity MAKE row — confirmed in
        # PCM_5/PCM_Phase_3_generate_input.py (~line 278): when the commodity
        # is 'HARD' it labels the row "Others Trim"/"Others Hard" rather than
        # leaving markup unset. Both exist as real rows in Norms_Make.xlsx
        # (ICC 0.01 / REJ 0.02 / OH 0.05 / PROFIT 0.1 / EFFICIENCY 0.9), which
        # is exactly why they're tried here before giving up and defaulting
        # markup to 0 — RM suppliers like "KINGFA" (a resin supplier) are
        # never going to appear in Norms_Make's Tier-1/process supplier list.
        norms = _norms(lookup.norms_make, "Others Hard") or _norms(lookup.norms_make, "Others Trim")
    shift_row = lookup.shift_row_for(row, material)

    hrs_factor = 1.0
    if _clean(row.get("HRS Y or N")).upper() == "Y":
        # PCM_master's shrink/HRS allowance was a flat rate, not a formula —
        # kept admin-editable (Settings > App Config: PCM_HARD.hrs_allowance_factor)
        # instead of buried as a literal, so a revised allowance doesn't need a deploy.
        hrs_factor = lookup.cfg("hrs_allowance_factor", 1.03)
    gross_wt = _num(row.get("Gross Wt"))
    if not gross_wt:
        gross_wt = _num(row.get("Part Wt")) * hrs_factor

    rate_period = (_clean(row.get("RM Base")) or _clean(lookup.config.get("active_rm_rate_period"))
                   or _latest_period_column([material] if material else list(lookup.material_master.values())[:5])
                   or "JAN#25")
    rate_kg = _find_rate(material, rate_period)
    rm_cost = gross_wt * rate_kg

    tonnage = _num(row.get("m/c Tonnage"))
    cycle_time = _num(row.get("Cycle Time"))
    cavity = _num(row.get("Cavity"), 1.0) or 1.0

    shift_factor = _first_number(row, ["Shift Factor"], _first_number(shift_row, ["Shift Factor"], lookup.cfg("default_shift_factor", 10.0)))
    default_shift_rate = lookup.cfg("default_shift_rate", 8500.0) if tonnage else 0.0
    shift_rate = _first_number(row, ["Shift Rate"], _first_number(shift_row, ["Shift Rate"], default_shift_rate))
    default_efficiency = lookup.cfg("default_efficiency", 0.9)
    efficiency = _first_number(norms, ["EFFICIENCY", "Efficiency"], _num(row.get("Efficiency"), default_efficiency))
    shift_seconds = lookup.cfg("shift_seconds", 8 * 3600.0)
    pc_per_shift = (shift_seconds / cycle_time * cavity * efficiency) if cycle_time else 0.0
    molding_cost = shift_rate / pc_per_shift if pc_per_shift else 0.0

    deflashing_threshold = lookup.cfg("deflashing_tonnage_threshold", 400.0)
    deflashing_flat_cost = lookup.cfg("deflashing_flat_cost", 0.5)
    deflashing_cost = (_first_number(shift_row, ["Deflashing Cost"], deflashing_flat_cost)
                        if tonnage and tonnage > deflashing_threshold else 0.0)
    process_cost = molding_cost + deflashing_cost
    basic_cost = rm_cost + process_cost
    icc = _first_number(norms, ["ICC"], _num(row.get("ICC")))
    rej = _first_number(norms, ["REJ"], _num(row.get("REJ")))
    oh = _first_number(norms, ["OH", "O&H"], _num(row.get("OH")))
    profit = _first_number(norms, ["PROFIT", "Profit"], _num(row.get("Profit")))
    icc_cost = icc * rm_cost
    rej_cost = rej * basic_cost
    oh_cost = oh * process_cost
    profit_cost = profit * basic_cost
    markup = icc_cost + rej_cost + oh_cost + profit_cost
    total = basic_cost + markup
    return {
        "Material Family": material.get("Material Family"),
        "Material Grade": material.get("Material Grade"),
        "RM Base": rate_period,
        "Rate Kg": rate_kg,
        "RM Cost/Pc": rm_cost,
        "Shift Factor": shift_factor,
        "Shift Rate": shift_rate,
        "EFFICIENCY": efficiency,
        "Pc per shift": pc_per_shift,
        "Molding Cost": molding_cost,
        "Deflashing cost": deflashing_cost,
        "Process Cost/Pc": process_cost,
        "Basic Cost/Pc": basic_cost,
        "ICC": icc,
        "REJ": rej,
        "OH": oh,
        "PROFIT": profit,
        "ICC COST": icc_cost,
        "REJ COST": rej_cost,
        "OH COST": oh_cost,
        "PROFIT COST": profit_cost,
        "MARKUP/Pc": markup,
        "Total Cost/Pc": total,
        "Total": total,
    }


def calculate_buy_std(row: Mapping[str, Any], lookup: HardLookup) -> Dict[str, Any]:
    bop = lookup.by_key(lookup.std_bop, row.get("Customer No"))
    norms = _norms(lookup.norms_buy_std, row.get("Supplier"))
    basic = _first_number(bop, ["Cost/Pc", "Basic Cost/Pc", "Basic Cost"], _num(row.get("Basic Cost/Pc")))
    icc = _first_number(norms, ["ICC"], _num(row.get("ICC")))
    rej = _first_number(norms, ["REJ"], _num(row.get("REJ")))
    oh = _first_number(norms, ["OH", "O&H", "Handling"], _num(row.get("O&H")))
    profit = _first_number(norms, ["PROFIT", "Profit"], _num(row.get("Profit")))
    markup_rate = icc + rej + oh + profit
    costs = {"ICC Cost": basic * icc, "REJ Cost": basic * rej, "O&H Cost": basic * oh, "PROFIT Cost": basic * profit}
    markup = basic * markup_rate
    total = basic + markup
    return {"Basic Cost/Pc": basic, "ICC": icc, "REJ": rej, "O&H": oh, "PROFIT": profit, **costs, "Markup": markup, "Total/Pc": total, "Total": total}


def calculate_assy(row: Mapping[str, Any], lookup: HardLookup) -> Dict[str, Any]:
    supplier_row = lookup.by_key(lookup.assy, row.get("Supplier")) or _norms(lookup.assy, row.get("Supplier"))
    norms = _norms(lookup.norms_assy, row.get("Supplier"))
    operation = _clean(row.get("Operation Process"))
    points = _num(row.get("No of Points"), 1.0)
    ct_per_point = _first_number(supplier_row, [f"CT per Point {operation}", "CT per Point"], lookup.cfg("assy_default_ct_per_point", 6.0))
    handling = _first_number(supplier_row, [f"Handling Time {operation}", "Handling Time"], lookup.cfg("assy_default_handling_time", 10.0))
    mhr_names = [f"{operation} Manual", "Manual", "JPH-Manual Manual", operation, "MHR"] if operation.upper() == "FIX" else [operation, f"{operation} Manual", "Manual", "JPH-Manual Manual", "MHR"]
    mhr = _first_number(supplier_row, mhr_names, 0.0)
    ct_sec = (points * ct_per_point) + handling
    basic = (ct_sec / 3600) * mhr
    icc = _first_number(norms, ["ICC"], _num(row.get("ICC")))
    rej = _first_number(norms, ["REJ"], _num(row.get("REJ")))
    oh = _first_number(norms, ["OH", "O&H"], _num(row.get("O&H")))
    profit = _first_number(norms, ["PROFIT", "Profit"], _num(row.get("Profit")))
    markup_rate = icc + rej + oh + profit
    markup = basic * markup_rate
    total = basic + markup
    return {
        "CT Per Point": ct_per_point,
        "Handling Time": handling,
        "MHR": mhr,
        "CT(sec)": ct_sec,
        "Basic cost/ pc": basic,
        "ICC": icc,
        "REJ": rej,
        "O&H": oh,
        "PROFIT": profit,
        "ICC Cost": basic * icc,
        "REJ Cost": basic * rej,
        "O&H Cost": basic * oh,
        "PROFIT Cost": basic * profit,
        "Markup": markup,
        "Total": total,
    }


def calculate_hard_line(row: Mapping[str, Any], master_lookup: Mapping[str, Any]) -> Dict[str, Any]:
    lookup = HardLookup.from_master_lookup(master_lookup)
    row_type = _clean(row.get("Type")).upper()
    if row_type == "MAKE":
        return calculate_make(row, lookup)
    if row_type == "BUY STD":
        return calculate_buy_std(row, lookup)
    if row_type == "ASSY":
        return calculate_assy(row, lookup)
    if row_type in {"PKG", "FRT", "END ITEM", "SPECIAL PROCESS"}:
        return {"Total": _num(row.get("Total Cost/Pc"), _num(row.get("Total"), 0.0)), "calculation_status": "Workbook Output"}
    return {"Total": 0.0, "calculation_status": "Not Applicable", "calculation_error": f"Unsupported HARD type '{row_type}'"}


def _paint_norms(lookup: HardLookup, supplier: Any) -> Mapping[str, Any]:
    return _norms(lookup.special_norms, supplier)


def calculate_paint_rows(rows: Iterable[Mapping[str, Any]], master_lookup: Mapping[str, Any], make_costs: Mapping[str, float] | None = None) -> list[Dict[str, Any]]:
    lookup = HardLookup.from_master_lookup(master_lookup)
    make_costs = make_costs or {}
    output = [dict(row) for row in rows]
    groups: list[tuple[int, int]] = []
    starts = [idx for idx, row in enumerate(output) if row.get("Sr") not in (None, "")]
    for pos, start in enumerate(starts):
        groups.append((start, starts[pos + 1] if pos + 1 < len(starts) else len(output)))

    # Same "pick whatever the current published rate period actually is"
    # approach as RM rate resolution above, scoped to whatever Special RM
    # records are available for this Paint sheet (falls back to config
    # override, then the historical "JUN#22" literal only if nothing else
    # in the data or config can tell us the active period).
    fallback_period = (_clean(lookup.config.get("active_paint_rate_period"))
                        or _latest_period_column(list(lookup.special_rm.values())[:5]) or "JUN#22")

    for row in output:
        rm = lookup.by_key(lookup.special_rm, row.get("Material Code"))
        if rm:
            row["Material"] = rm.get("Product Descrption") or rm.get("Product Description") or row.get("Material")
        rm_base = row.get("RM Base") or fallback_period
        row["RM Base"] = rm_base
        rate = _first_number(rm, [rm_base, rm_base.upper(), rm_base.replace("#", "#").upper(), fallback_period], 0.0) if rm else 0.0
        rate = round(rate, 3)
        row["Rate / Litre"] = rate
        consumption = round(_num(row.get("Consumption (in Litre)")), 3)
        row["RM cost / Pc"] = consumption * rate

    for start, end in groups:
        row = output[start]
        norms = _paint_norms(lookup, row.get("Supplier"))
        make_part_cost = _num(make_costs.get(_clean(row.get("Make Part no"))))
        no_of_parts = _num(row.get("No of parts / Jig"), 1.0) or 1.0
        hanger_cost = _first_number(norms, ["Hanger Cost"], 0.0)
        process_cost = _num(row.get("Process Cost/Pc"), lookup.cfg("paint_default_process_cost", 125.0))
        paint_rm_cost = sum(_num(output[i].get("RM cost / Pc")) for i in range(start, end))
        hanger_pc = hanger_cost / no_of_parts
        basic = paint_rm_cost + process_cost
        icc = _first_number(norms, ["ICC"], 0.0)
        rej_inj = _first_number(norms, ["REJ ON INJ MOLDED PART"], 0.0)
        rej_mat = _first_number(norms, ["REJ ON MATERIAL & PROCESS"], 0.0)
        overall_rej = _first_number(norms, ["OVERALL REJECTION"], 0.0)
        overhead = _first_number(norms, ["OVERHEAD"], 0.0)
        profit = _first_number(norms, ["PROFIT"], 0.0)
        icc_cost = paint_rm_cost * icc
        rej_inj_cost = make_part_cost * rej_inj
        rej_mat_cost = basic * rej_mat
        overall_rej_cost = basic * overall_rej
        overhead_cost = basic * overhead
        profit_cost = basic * profit
        markup = icc_cost + rej_inj_cost + rej_mat_cost + overall_rej_cost + overhead_cost + profit_cost
        pnf = (basic + markup) * _first_number(norms, ["PnF"], 0.0)
        row.update({
            "Make Part Cost": make_part_cost,
            "Consumable / Masking Cost": 0.0,
            "Hanger Cost": hanger_cost,
            "Process Cost/Pc": process_cost,
            "Paint RM Cost": paint_rm_cost,
            "Paint Process Cost": hanger_pc,
            "Basic Cost": basic,
            "ICC": icc,
            "REJ ON INJ MOLDED PART": rej_inj,
            "REJ ON MATERIAL & PROCESS": rej_mat,
            "OVERALL REJECTION": overall_rej,
            "OVERHEAD": overhead,
            "PROFIT": profit,
            "ICC Cost": icc_cost,
            "REJ ON INJ MOLDED PART Cost": rej_inj_cost,
            "REJ ON MATERIAL & PROCESS Cost": rej_mat_cost,
            "OVERALL REJECTION Cost": overall_rej_cost,
            "OVERHEAD Cost": overhead_cost,
            "PROFIT Cost": profit_cost,
            "Paint Markup": markup,
            "Pnf": pnf,
            "Total Cost/Pc": basic + markup + pnf,
        })
    return output


def calculate_special_process_from_paint(row: Mapping[str, Any], paint_by_customer: Mapping[str, Mapping[str, Any]], master_lookup: Mapping[str, Any]) -> Dict[str, Any]:
    lookup = HardLookup.from_master_lookup(master_lookup)
    paint = paint_by_customer.get(_clean(row.get("Customer No"))) or {}
    norms = _norms(lookup.norms_buy_std, row.get("Supplier"))
    total_rm = _num(paint.get("Paint RM Cost"))
    consumable = _num(paint.get("Consumable / Masking Cost"))
    rm_cost = total_rm - consumable
    process_cost = _num(paint.get("Process Cost/Pc"))
    tier_pnf = _num(paint.get("Paint Markup")) + _num(paint.get("Pnf"))
    basic = rm_cost + process_cost + tier_pnf
    icc = _first_number(norms, ["ICC"], 0.0)
    rej = _first_number(norms, ["REJ"], 0.0)
    oh = _first_number(norms, ["OH", "O&H", "Handling"], 0.0)
    profit = _first_number(norms, ["PROFIT", "Profit"], 0.0)
    costs = {"ICC Cost": basic * icc, "REJ Cost": basic * rej, "O&H Cost": basic * oh, "PROFIT Cost": basic * profit}
    markup = sum(costs.values())
    return {
        "Surface Area (in SQM)": paint.get("Painting Area (in SQM)"),
        "Process / Assy Name": "BUY PAINT",
        "Total RM Cost": total_rm,
        "Consumable / Masking Cost": consumable,
        "RM Cost": rm_cost,
        "Process Cost": process_cost,
        "Tier 2 Markup+PnF": tier_pnf,
        "Basic cost": basic,
        "ICC": icc,
        "REJ": rej,
        "O&H": oh,
        "PROFIT": profit,
        **costs,
        "MARKUP": markup,
        "Total cost /PC": basic + markup,
        "Total": basic + markup,
    }
