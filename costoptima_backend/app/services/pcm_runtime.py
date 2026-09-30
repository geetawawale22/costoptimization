"""Database-only PCM interpreter. Business expressions live in PCM_ENGINE.definition.

No legacy script, Excel evaluation, master generation or default business rates
are executed here. Snapshots contain only the explicitly configured master sources.
"""
import ast
import hashlib
import json
import math
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP

import simpleeval
from sqlalchemy.orm import selectinload

from app.models.workbook import WorkbookType, WorkbookTab
from app.services.pcm_legacy_output_style import apply_legacy_output_template_styles
from app.services.app_config_service import get_config_value
from app.services.diagnostics import DiagnosticTrace


class PcmError(ValueError):
    def __init__(self, message, code="CALCULATION_ERROR", **details):
        super().__init__(message)
        self.code = code
        self.details = details


def norm(value):
    return " ".join(str(value if value is not None else "").replace("_", " ").split()).casefold()


def number(value):
    if isinstance(value, bool):
        return int(value)
    if value is None or value == "":
        raise PcmError("Required numeric value is blank", "MISSING_INPUT")
    try:
        text = str(value).strip().replace(",", "")
        result = float(text[:-1]) / 100 if text.endswith("%") else float(text)
    except (ValueError, TypeError) as exc:
        raise PcmError(f"Invalid number: {value!r}", "INVALID_INPUT") from exc
    if not math.isfinite(result):
        raise PcmError("Non-finite numeric value", "INVALID_INPUT")
    return result


def field(record, key, *default):
    found = [v for k, v in record.items() if norm(k) == norm(key)]
    found = [v for v in found if v is not None and v != ""]
    if len(found) > 1 and any(v != found[0] for v in found[1:]):
        raise PcmError(f"Ambiguous field {key!r}")
    if found:
        if isinstance(found[0], str) and found[0].startswith("="):
            raise PcmError(f"Unevaluated Excel formula in {key!r}")
        return found[0]
    if default:
        return default[0]
    raise PcmError(f"Missing field {key!r}", "MISSING_INPUT", field=key)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def load_definition(db):
    definition = get_config_value(db, "PCM_ENGINE", "definition")
    if definition is not None:
        validate_definition(definition)
    return definition


def validate_definition(definition):
    if not isinstance(definition, dict) or definition.get("schema_version") != 1:
        raise PcmError("PCM_ENGINE.definition requires schema_version 1")
    for key in ("profiles", "dispatch", "sources", "input", "output", "process_units"):
        if key not in definition:
            raise PcmError(f"Missing definition section: {key}")
    for dispatch in definition["dispatch"]:
        if dispatch["profile"] not in definition["profiles"]:
            raise PcmError(f"Unknown profile {dispatch['profile']}")
    sheet_names = [definition["output"][key] for key in ("master_sheet", "rollup_sheet")]
    sheet_names += ["Errors", "Provenance", "Formula Trace", "Analysis"]
    if definition["output"].get("sbc_sheet"):
        sheet_names.append(definition["output"]["sbc_sheet"])
    sheet_names += [item["sheet"] for item in definition["output"].get("related_sheets", [])]
    for profile in definition["profiles"].values():
        if profile["scope"] not in definition["sources"]:
            raise PcmError(f"Unknown source scope {profile['scope']}")
        sheet_names.append(profile["sheet"])
        if "Total" not in profile["outputs"]:
            raise PcmError("Every profile must define a Total output")
        for step in profile["steps"]:
            if not step["name"].isidentifier() or step["name"].startswith("_"):
                raise PcmError("Step names must be public identifiers")
            for expr in (step["expression"], step.get("condition")):
                if expr:
                    validate_expression(expr)
    if len({name.casefold() for name in sheet_names}) != len(sheet_names):
        raise PcmError("Output sheet names must be unique")
    if any(not name or len(name) > 31 or any(c in name for c in "[]:*?/\\") for name in sheet_names):
        raise PcmError("Invalid output sheet name")


def validate_expression(expr):
    if not isinstance(expr, str) or len(expr) > 10000:
        raise PcmError("Expression must be text of at most 10000 characters")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise PcmError(f"Invalid expression: {expr}") from exc
    forbidden = (ast.Attribute, ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp,
                 ast.GeneratorExp, ast.NamedExpr, ast.Pow)
    if any(isinstance(node, forbidden) for node in ast.walk(tree)):
        raise PcmError("Expression contains a prohibited operation")


def snapshot_masters(db, definition, trace: DiagnosticTrace | None = None):
    codes = sorted({code for sources in definition["sources"].values() for code in sources})
    if trace:
        trace.section("Reading master data from database", workbook_codes=codes)
    masters = []
    workbooks = (db.query(WorkbookType).options(selectinload(WorkbookType.tabs).selectinload(WorkbookTab.rows))
                 .filter(WorkbookType.code.in_(codes), WorkbookType.is_deleted.is_(False)).all())
    for workbook in workbooks:
        for tab in workbook.tabs:
            if tab.is_deleted:
                continue
            for row in tab.rows:
                if not row.is_deleted:
                    masters.append({"workbook": workbook.code, "table": tab.tab_name,
                                    "row_id": row.id, "version": row.version,
                                    "section_id": row.section_id, "data": row.row_data})
    from app.pcm.master_data.views import materialize_views
    masters.extend(materialize_views(db, definition.get("master_views", [])))
    masters = sorted(masters, key=lambda r: (r["workbook"], r["table"], r["row_id"]))
    if trace:
        counts = {}
        for record in masters:
            key = f"{record['workbook']}::{record['table']}"
            counts[key] = counts.get(key, 0) + 1
        trace.add("MASTER_SNAPSHOT", "Master rows loaded from database", total_rows=len(masters), tables=counts)
    return masters


class Interpreter:
    def __init__(self, definition, masters, trace: DiagnosticTrace | None = None):
        validate_definition(definition)
        self.definition = definition
        self.masters = masters
        self.trace = trace
        self.index = {}
        for record in masters:
            self.index.setdefault((record["workbook"], norm(record["table"])), []).append(record)
        if self.trace:
            self.trace.add("ENGINE_INIT", "PCM interpreter initialized",
                           profiles=sorted(definition["profiles"]),
                           dispatch_rules=len(definition["dispatch"]),
                           master_records=len(masters))

    def profile_for(self, row):
        # Some commodity detail sheets carry sub-rows that are NOT independently costed
        # parts. GDC estimation inputs list the part once (Type=Make) followed by its
        # machining operations (Type=Process, Customer No "OP 10".."OP 80"), which hold no
        # Material Code or Casting Wt because they describe an operation, not a part.
        # Dispatching them to a part-costing profile produced a MISSING_INPUT failure per
        # operation and marked a sound calculation "Needs Review".
        #
        # They are skipped here rather than costed. How legacy folds an operation row into
        # the parent part's machining cost is NOT reproduced - that is unverified business
        # logic and is left as Needs Confirmation rather than guessed. Skipping is the
        # conservative choice: it removes a false error without inventing a cost.
        skip = self.definition.get("dispatch_skip", [])
        for entry in skip:
            if all(norm(field(row, k, "")) in [norm(v) for v in values]
                   for k, values in entry.get("match", {}).items()):
                if self.trace:
                    self.trace.add("DISPATCH_SKIP", "Row matched a configured skip rule",
                                   match=entry.get("match", {}),
                                   customer_no=field(row, "Customer No", ""),
                                   type=field(row, "Type", ""),
                                   commodity=field(row, "Commodity", ""))
                return None
        for entry in self.definition["dispatch"]:
            if all(norm(field(row, k, "")) in [norm(v) for v in values]
                   for k, values in entry.get("match", {}).items()):
                if self.trace:
                    self.trace.add("DISPATCH", "Row matched calculation profile",
                                   profile=entry["profile"], match=entry.get("match", {}),
                                   customer_no=field(row, "Customer No", ""),
                                   type=field(row, "Type", ""),
                                   commodity=field(row, "Commodity", ""))
                return entry["profile"]
        raise PcmError(f"No configured dispatch for Type={field(row, 'Type', '')!r}, Commodity={field(row, 'Commodity', '')!r}", "NOT_IMPLEMENTED")

    def calculate(self, row):
        context, trace, selected = {}, [], []
        if self.trace:
            self.trace.section("Calculating input row",
                               customer_no=field(row, "Customer No", ""),
                               end_item=field(row, "End Item", ""),
                               type=field(row, "Type", ""),
                               commodity=field(row, "Commodity", ""),
                               quantity=field(row, self.definition["output"]["quantity_field"], ""))
        if row.get("_input_issues"):
            issue = row["_input_issues"][0]
            raise PcmError(issue["message"], issue["code"], **issue.get("details", {}))
        number(field(row, self.definition["output"]["quantity_field"], 1))
        for variant in self.definition["output"].get("variants", []):
            number(field(row, variant, 0))
        profile_name = self.profile_for(row)
        if profile_name is None:
            # Non-costed sub-row (see profile_for). Reported as Skipped so it is visible
            # in the output and never silently dropped.
            return {"input": row, "profile": None, "values": {}, "trace": [],
                    "status": "Skipped",
                    "issues": [{"code": "NOT_A_COSTED_ROW",
                                "message": "Operation/sub-row carried by the detail sheet; "
                                           "not costed as a part. Its contribution to the "
                                           "parent part is Needs Confirmation.",
                                "customer_no": field(row, "Customer No", None),
                                "type": field(row, "Type", None)}]}
        profile = self.definition["profiles"][profile_name]
        if profile.get("not_implemented"):
            raise PcmError(profile["not_implemented"], "NOT_IMPLEMENTED", profile=profile_name)
        scope = profile["scope"]
        scope = profile.get("scope_by_commodity", {}).get(norm(field(row, "Commodity", "")), scope)
        if self.trace:
            self.trace.add("PROFILE", "Using profile and master-data scope",
                           profile=profile_name, scope=scope, sheet=profile.get("sheet"))
        material_record_cache = {"loaded": False, "record": None}

        def records(table):
            # Ordered sources are explicit precedence tiers, never mixed globally.
            tables = self.definition.get("table_aliases", {}).get(table, [table])
            for source in self.definition["sources"][scope]:
                rows = [r for t in tables for r in self.index.get((source, norm(t)), [])]
                if rows:
                    yield rows

        def match_records(table, criteria):
            for rows in records(table):
                matches = [r for r in rows if all(norm(field(r["data"], k, "")) == norm(v)
                                                 for k, v in criteria.items())]
                if matches:
                    if self.trace:
                        self.trace.add("MASTER_MATCH", "Matched master records",
                                       table=table, criteria=criteria, matches=len(matches))
                    return matches
            if self.trace:
                self.trace.add("MASTER_MISS", "No master rows matched criteria",
                               table=table, criteria=criteria)
            return []
        def pf_code():
            project = field(row, "Project")

            return lookup(
                "PF",
                "PF Code",
                {"Project": project}
            )
        def lookup(table, column, criteria, *default):
            matches = match_records(table, criteria)
            if not matches and default:
                if self.trace:
                    self.trace.add("LOOKUP_DEFAULT", "Using configured lookup default",
                                   table=table, column=column, criteria=criteria, default=default[0])
                return default[0]
            if len(matches) != 1:
                available = any(True for _ in records(table))
                code = "AMBIGUOUS_MASTER" if matches else ("MISSING_MASTER" if available else "MASTER_NOT_AVAILABLE")
                raise PcmError(f"{table}: expected one match for {criteria}, found {len(matches)}", code, table=table, lookup_keys=criteria)
            selected.append({k: matches[0][k] for k in ("workbook", "table", "row_id", "version")})
            try:
                value = field(matches[0]["data"], column, *default)
                if self.trace:
                    self.trace.add("LOOKUP_VALUE", "Read value from master data",
                                   table=table, column=column, criteria=criteria,
                                   value=value, row_id=matches[0]["row_id"])
                return value
            except PcmError as exc:
                raise PcmError(str(exc), "MISSING_MASTER_FIELD", table=table, field=column, lookup_keys=criteria) from exc

        def lookup_first(table, column, criteria, *default):
            matches = match_records(table, criteria)
            if not matches and default:
                if self.trace:
                    self.trace.add("LOOKUP_DEFAULT", "Using configured first-match lookup default",
                                   table=table, column=column, criteria=criteria, default=default[0])
                return default[0]
            if not matches:
                available = any(True for _ in records(table))
                code = "MISSING_MASTER" if available else "MASTER_NOT_AVAILABLE"
                raise PcmError(f"{table}: expected at least one match for {criteria}, found 0",
                               code, table=table, lookup_keys=criteria)
            selected.append({k: matches[0][k] for k in ("workbook", "table", "row_id", "version")})
            try:
                value = field(matches[0]["data"], column, *default)
                if self.trace:
                    self.trace.add("LOOKUP_VALUE", "Read first matching value from master data",
                                   table=table, column=column, criteria=criteria,
                                   value=value, row_id=matches[0]["row_id"])
                return value
            except PcmError as exc:
                raise PcmError(str(exc), "MISSING_MASTER_FIELD", table=table, field=column, lookup_keys=criteria) from exc

        def material_value(key):
            if not material_record_cache["loaded"]:
                material_record_cache["loaded"] = True
                material_code = field(row, "Material Code", None)
                if material_code:
                    matches = match_records("Material Master", {"Material Code": material_code})
                    if len(matches) == 1:
                        material_record_cache["record"] = matches[0]
                    elif len(matches) > 1:
                        raise PcmError(f"Material Master: expected one match for Material Code {material_code!r}, found {len(matches)}",
                                       "AMBIGUOUS_MASTER", table="Material Master",
                                       lookup_keys={"Material Code": material_code})
            record = material_record_cache["record"]
            if not record:
                return None
            try:
                return field(record["data"], key, None)
            except PcmError as exc:
                if exc.code == "MISSING_INPUT":
                    return None
                raise

        def band(table, value, column, criteria):
            matches = [r for r in match_records(table, criteria)
                       if number(field(r["data"], "Min")) <= number(value)
                       and (field(r["data"], "Max", None) is None or number(value) < number(field(r["data"], "Max")))]
            if len(matches) != 1:
                raise PcmError(f"{table}: expected one weight/tonnage band for {value}, found {len(matches)}")
            selected.append({k: matches[0][k] for k in ("workbook", "table", "row_id", "version")})
            return field(matches[0]["data"], column)

        def evaluate(expr):
            validate_expression(expr)
            value = simpleeval.EvalWithCompoundTypes(names=context, functions=functions).eval(expr)
            if self.trace:
                self.trace.add("FORMULA_EVAL", "Evaluated expression",
                               expression=expr, context=dict(context), result=value)
            return value

        def configured_rule(table, output):
            # Normalized Con/Head rules use Condition/Expression, preserving priority.
            candidates = match_records(table, {"Output": output})
            matches = [r for r in candidates if evaluate(field(r["data"], "Condition", "True"))]
            if not matches:
                raise PcmError(f"No translated {table} rule for {output}", "RULE_NOT_AVAILABLE" if candidates else "NOT_IMPLEMENTED", table=table, output=output)
            matches.sort(key=lambda r: number(field(r["data"], "Priority", 0)))
            if len(matches) > 1 and field(matches[0]["data"], "Priority", 0) == field(matches[1]["data"], "Priority", 0):
                raise PcmError(f"Ambiguous {table} priority for {output}")
            record = matches[0]
            selected.append({k: record[k] for k in ("workbook", "table", "row_id", "version")})
            return evaluate(field(record["data"], "Expression"))

        def derived(key, *default):
            """Resolve a formula placeholder from calculated values, input, or a rule."""
            for name, value in reversed(list(context.items())):
                if norm(name) == norm(key):
                    if self.trace:
                        self.trace.add("FORMULA_INPUT", "Resolved derived value from prior calculated step",
                                       key=key, source="calculated_context", value=value)
                    return value
            value = field(row, key, None)
            if value is not None:
                if self.trace:
                    self.trace.add("FORMULA_INPUT", "Resolved derived value from uploaded input row",
                                   key=key, source="uploaded_input", value=value)
                return value
            value = material_value(key)
            if value is not None:
                if self.trace:
                    self.trace.add("FORMULA_INPUT", "Resolved derived value from Material Master",
                                   key=key, source="database_material_master", value=value)
                return value
            matches = []
            for table in profile.get("rule_tables", self.definition.get("rule_tables", [])):
                if match_records(table, {"Output": key}):
                    matches.append(table)
            if len(matches) == 1:
                value = configured_rule(matches[0], key)
                if self.trace:
                    self.trace.add("FORMULA_INPUT", "Resolved derived value from configured rule table",
                                   key=key, source=matches[0], value=value)
                return value
            if default:
                if self.trace:
                    self.trace.add("FORMULA_INPUT", "Using default derived value",
                                   key=key, source="formula_default", value=default[0])
                return default[0]
            if len(matches) > 1:
                raise PcmError(f"Derived value {key!r} exists in multiple rule tables", "AMBIGUOUS_MASTER", field=key)
            raise PcmError(f"Missing derived/input value {key!r}", "MISSING_INPUT", field=key)

        def operations(group):
            if group not in profile.get("operations", {}):
                raise PcmError(f"Missing process mapping: profiles.{profile_name}.operations.{group}", "NOT_IMPLEMENTED")
            total = 0
            for operation in profile.get("operations", {}).get(group, []):
                quantity = evaluate(operation["quantity"])
                if not quantity:
                    continue
                rate = lookup("Process Master", operation.get("rate_column", "Rate"),
                              {"Process": operation["process"], "Sector & Location": field(row, "Sector & Location")})
                unit = operation["unit"]
                divisor = self.definition["process_units"][unit]
                total += number(quantity) * number(rate) / number(divisor)
            return total

        def process_columns(rate_column, category=None, auto=False):
            """Sum input process columns against Process Master rates.

            Old PCM commodity sheets store many process names directly as input
            columns. The legacy formulas multiply each nonblank process column by
            the matching Process Master rate for the active sector/location. When
            `auto` is true, every input column that matches a Process Master row is
            considered; otherwise the profile's configured process_columns list is
            used for stricter sheet-specific behavior.
            """
            total = 0
            configured = profile.get("parameters", {}).get("process_columns", [])
            processes = list(row.keys()) if auto and not configured else configured
            used = False
            for process in processes:
                if str(process).startswith("_"):
                    continue
                raw_quantity = field(row, process, None)
                if raw_quantity is None or raw_quantity == "":
                    continue
                criteria = {"Process": process}
                matches = match_records("Process Master", criteria)
                if category is not None:
                    filtered = [r for r in matches if norm(field(r["data"], "Category", "")) == norm(category)]
                    if filtered:
                        matches = filtered
                if not matches:
                    if auto:
                        continue
                    available = any(True for _ in records("Process Master"))
                    code = "MISSING_MASTER" if available else "MASTER_NOT_AVAILABLE"
                    raise PcmError(f"Process Master: expected match for {criteria}, found 0", code, table="Process Master", lookup_keys=criteria)
                quantity = number(raw_quantity)
                if not quantity:
                    continue
                rated = []
                for match in matches:
                    try:
                        rated.append((match, field(match["data"], rate_column)))
                    except PcmError:
                        continue
                if not rated:
                    if auto:
                        continue
                    raise PcmError(f"Missing field {rate_column!r}", "MISSING_MASTER_FIELD", table="Process Master", field=rate_column, lookup_keys=criteria)
                selected.append({k: rated[0][0][k] for k in ("workbook", "table", "row_id", "version")})
                uom = norm(field(rated[0][0]["data"], "UOM", ""))
                divisor = 60 if "hr" in uom or "mhr" in uom else 1
                total += quantity * number(rated[0][1]) / divisor
                used = True
            if auto and not used:
                raise PcmError("No input process columns matched Process Master", "MISSING_INPUT", table="Process Master")
            return total

        def auto_process_cost(category=None):
            return process_columns("Rate/UOM_" + str(field(row, "Sector & Location")), category, True)

        def power(base, exponent):
            # validate_expression() forbids ast.Pow (blocks unbounded/negative
            # exponent DoS via **); this is the safe, bounded replacement used
            # by pcm_master_normalize.convert_template() for legacy '(...)^2'
            # style master formulas (e.g. Net Consumption base "Cal RM cons").
            base_n = number(base)
            exp_n = number(exponent)
            if exp_n != int(exp_n) or abs(exp_n) > 12:
                raise PcmError(f"power() exponent must be an integer in [-12, 12], got {exponent!r}")
            exp_i = int(exp_n)
            result = 1.0
            for _ in range(abs(exp_i)):
                result *= base_n
            return (1.0 / result) if exp_i < 0 else result

        def rounded(value, places):
            return float(Decimal(str(number(value))).quantize(Decimal(1).scaleb(-int(places)), rounding=ROUND_HALF_UP))

        def rounddown(value, places=0):
            factor = 10 ** int(number(places))
            return math.trunc(number(value) * factor) / factor

        def basis(table, column, criteria):
            label = lookup(table, column, criteria)
            expression = profile.get("basis_fields", {}).get(norm(label))
            if expression is None:
                raise PcmError(f"Markup basis {label!r} is not implemented", "NOT_IMPLEMENTED", table=table)
            return evaluate(expression)

        def basis_first(table, column, criteria):
            label = lookup_first(table, column, criteria)
            expression = profile.get("basis_fields", {}).get(norm(label))
            if expression is None:
                raise PcmError(f"Markup basis {label!r} is not implemented", "NOT_IMPLEMENTED", table=table)
            return evaluate(expression)

        def detail(group, key):
            children = row.get("_related", {}).get(group, [])
            values = [field(child, key) for child in children if field(child, key, None) is not None]
            if not values:
                raise PcmError(f"Missing {group} detail {key}", "MISSING_INPUT", field=key)
            if any(value != values[0] for value in values):
                raise PcmError(f"Conflicting {group} values for {key}", "AMBIGUOUS_INPUT", field=key)
            return values[0]

        def sum_detail(group, expression):
            nonlocal row
            parent = row
            children = parent.get("_related", {}).get(group, [])
            if not children:
                raise PcmError(f"Required {group} rows are missing", "MISSING_INPUT", sheet=group)
            total = 0
            try:
                for child in children:
                    row = child
                    total += number(evaluate(expression))
            finally:
                row = parent
            return total

        def input_value(key, *default):
            value = field(row, key, *default)
            if self.trace:
                self.trace.add("FORMULA_INPUT", "Read value from uploaded input row",
                               function="v", key=key, value=value,
                               source="original_uploaded_workbook")
            return value

        def parameter_value(key):
            value = profile.get("parameters", {})[key]
            if self.trace:
                self.trace.add("FORMULA_INPUT", "Read value from PCM profile parameters",
                               function="p", key=key, value=value,
                               source="database_PCM_ENGINE.definition")
            return value

        from app.services.pcm_legacy_conditions import legacy_eq, legacy_ne
        functions = {
            "n": number,
            "text": norm,
            "legacy_eq": legacy_eq,
            "legacy_ne": legacy_ne,
            "v": input_value,
            "p": parameter_value,
            "lookup": lookup,
            "lookup_first": lookup_first,
            "pf_code": pf_code,
            "band": band,
            "rule": configured_rule,
            "operations": operations,
            "process_columns": process_columns,
            "auto_process_cost": auto_process_cost,
            "min": min,
            "max": max,
            "abs": abs,
            "round": rounded,
            "power": power,
            "basis": basis,
            "basis_first": basis_first,
            "detail": detail,
            "sum_detail": sum_detail,
            "d": derived,
            "integer": lambda value: math.floor(number(value)),
            "rounddown": rounddown,
            "sum_values": lambda *values: sum(number(value) for value in values),
        }
        # Recursion through rule() is bounded independently of simpleeval's AST limits.
        depth = 0
        raw_evaluate = evaluate

        def evaluate(expr):
            nonlocal depth
            depth += 1
            try:
                if depth > 20:
                    raise PcmError("Recursive rule dependency exceeds 20 levels")
                return raw_evaluate(expr)
            finally:
                depth -= 1

        for step in profile["steps"]:
            try:
                if step.get("condition"):
                    condition_value = evaluate(step["condition"])
                    if self.trace:
                        self.trace.add("FORMULA_CONDITION", "Checked step condition",
                                       profile=profile_name, calc_step=step["name"],
                                       condition=step["condition"], result=condition_value)
                    if not condition_value:
                        if self.trace:
                            self.trace.add("FORMULA_SKIP", "Skipped step because condition was false",
                                           profile=profile_name, calc_step=step["name"])
                        continue
                value = evaluate(step["expression"])
                if isinstance(value, (int, float)) and not math.isfinite(value):
                    raise PcmError("Non-finite result")
                context[step["name"]] = value
                trace.append({"name": step["name"], "expression": step["expression"], "value": value})
                if self.trace:
                    self.trace.add("FORMULA_STEP", "Calculated formula step",
                                   profile=profile_name, calc_step=step["name"],
                                   formula=step["expression"], result=value,
                                   available_values=dict(context))
            except Exception as exc:
                if self.trace:
                    self.trace.error("Formula step failed",
                                     profile=profile_name, calc_step=step["name"],
                                     formula=step.get("expression"),
                                     reason=str(exc))
                raise PcmError(f"{profile_name}.{step['name']}: {exc}", getattr(exc, "code", "DIVISION_BY_ZERO" if isinstance(exc, ZeroDivisionError) else "FORMULA_ERROR"),
                               **{**getattr(exc, "details", {}), "profile": profile_name, "step": step["name"]}) from exc
        outputs = {label: context[key] for label, key in profile["outputs"].items()}
        number(outputs["Total"])
        if self.trace:
            self.trace.section("Finished input row calculation",
                               profile=profile_name, outputs=outputs, master_refs=selected)
        return {"profile": profile_name, "input": row, "values": outputs, "trace": trace,
                "master_refs": selected, "status": "Completed"}

    def run(self, rows):
        results = []
        for index, row in enumerate(rows, 1):
            try:
                results.append(self.calculate(row))
            except (ValueError, KeyError, TypeError) as exc:
                issue = {"code": getattr(exc, "code", "CALCULATION_ERROR"), "message": str(exc),
                         "line": index, "customer_no": field(row, "Customer No", ""), **getattr(exc, "details", {})}
                if self.trace:
                    self.trace.error("Input row needs review",
                                     line=index, customer_no=issue["customer_no"],
                                     code=issue["code"], error_message=issue["message"])
                results.append({"input": row, "values": {}, "status": "Needs Review", "error": str(exc),
                                "profile": issue.get("profile", "unconfigured"), "issues": [issue]})
        return results


def resolve_sheet_name(configured, aliases, available):
    """Match a configured detail-sheet name to the sheet this workbook actually uses.

    Real legacy inputs do not agree on detail-sheet naming: the reference S302 workbook
    names its die-casting detail sheet "GDC", while the GDC estimation inputs name the
    same sheet "GDC_Input". An exact string match silently skipped the enrichment join,
    so every GDC row lost Casting Wt, Material Code and the rest of its detail fields and
    failed with MISSING_INPUT - a wrong-looking calculation caused purely by a sheet name.

    Resolution order, first hit wins:
      1. exact configured name
      2. any configured alias, in order
      3. case/whitespace/underscore-insensitive match on the configured name
      4. same, on each alias

    Aliases come from configuration (input.sheet_aliases), so a new naming convention is
    a config edit rather than a code change. Nothing is guessed by fuzzy substring match:
    an unknown sheet still yields no join, which surfaces as an explicit issue.
    """
    if configured in available:
        return configured
    for alias in aliases or []:
        if alias in available:
            return alias
    lookup = {norm(name).replace("_", " "): name for name in available}
    for candidate in [configured, *(aliases or [])]:
        hit = lookup.get(norm(candidate).replace("_", " "))
        if hit:
            return hit
    return None


def read_inputs(path, definition):    """BOM drives dispatch; enrichment joins preserve supplier/end-item identity."""
    from app.services.bom_reader import read_bom_sheet as _read_bom_sheet
    from openpyxl import load_workbook
    config = definition["input"]
    def read_sheet(sheet):
        if sheet not in config.get("headers", {}):
            return _read_bom_sheet(path, sheet)
        from app.services.bom_reader import json_safe_value as _json_safe_value
        raw_book = load_workbook(path, read_only=True, data_only=False)
        cached_book = load_workbook(path, read_only=True, data_only=True)
        result = []
        try:
            header = config["headers"][sheet]
            titles = next(raw_book[sheet].iter_rows(min_row=header, max_row=header, values_only=True))
            for raw, cached in zip(raw_book[sheet].iter_rows(min_row=header + 1), cached_book[sheet].iter_rows(min_row=header + 1)):
                row, issues = {}, []
                for name, source, value in zip(titles, raw, cached):
                    if name is None:
                        continue
                    row[str(name).strip()] = _json_safe_value(value.value)
                    if source.data_type == "f" and value.value is None:
                        issues.append({"code": "INPUT_FORMULA_NOT_AVAILABLE", "message": f"{sheet}!{source.coordinate}: input formula has no cached value; recalculate the input workbook", "details": {"sheet": sheet, "cell": source.coordinate}})
                if any(value is not None for value in row.values()):
                    if issues:
                        row["_input_issues"] = issues
                    result.append(row)
        finally:
            raw_book.close()
            cached_book.close()
        return result
    wb = load_workbook(path, read_only=True, data_only=False)
    names = wb.sheetnames
    wb.close()
    if config["bom_sheet"] not in names:
        raise PcmError(f"Missing required sheet {config['bom_sheet']}")
    rows = read_sheet(config["bom_sheet"])
    if config.get("variant_markers") and rows:
        headers = list(rows[0])
        start, end = config["variant_markers"]
        if start in headers and end in headers:
            definition["output"]["variants"] = headers[headers.index(start) + 1:headers.index(end)]
    sheet_aliases = config.get("sheet_aliases", {})
    for spec in config.get("enrichment", []):
        resolved = resolve_sheet_name(spec["sheet"], spec.get("aliases") or sheet_aliases.get(spec["sheet"]), names)
        if resolved is None:
            continue
        details = read_sheet(resolved)
        for row in rows:
            if not all(norm(field(row, k, "")) in [norm(v) for v in values] for k, values in spec.get("match", {}).items()):
                continue
            candidates = [detail for detail in details if all(
                field(row, k, None) is not None and norm(field(row, k)) == norm(field(detail, k, "")) for k in spec["keys"])]
            if len(candidates) > 1:
                raise PcmError(f"Ambiguous input join to {resolved} for {[field(row, k, '') for k in spec['keys']]}")
            if candidates:
                for key, value in candidates[0].items():
                    # Blank BOM variant flags mean "not fitted", not missing data.
                    if key in definition["output"].get("variants", []) or key in spec.get("preserve_fields", []):
                        continue
                    if field(row, key, None) is None:
                        row[key] = value
    for spec in config.get("inline_relations", []):
        group = spec["group"]
        keys = spec.get("keys", [])
        inherit_fields = spec.get("inherit_fields", [])
        parents = [
            row for row in rows
            if all(norm(field(row, k, "")) in [norm(v) for v in values]
                   for k, values in spec.get("parent_match", {}).items())
        ]
        children = [
            row for row in rows
            if all(norm(field(row, k, "")) in [norm(v) for v in values]
                   for k, values in spec.get("child_match", {}).items())
        ]
        for parent in parents:
            related = []
            for child in children:
                if not all(field(parent, key, None) is not None
                           and norm(field(parent, key)) == norm(field(child, key, ""))
                           for key in keys):
                    continue
                child_copy = dict(child)
                for inherited in inherit_fields:
                    if field(child_copy, inherited, None) in (None, ""):
                        child_copy[inherited] = field(parent, inherited, None)
                related.append(child_copy)
            if related:
                parent.setdefault("_related", {})[group] = related
    for spec in config.get("relations", []):
        resolved_rel = resolve_sheet_name(spec["sheet"], spec.get("aliases") or sheet_aliases.get(spec["sheet"]), names)
        if resolved_rel is None:
            continue
        children = read_sheet(resolved_rel)
        if spec.get("header_values"):
            header_book = load_workbook(path, read_only=True, data_only=True)
            try:
                for child in children:
                    for key, address in spec["header_values"].items():
                        child[key] = header_book[spec["sheet"]][address].value
            finally:
                header_book.close()
        for row in rows:
            if all(norm(field(row, k, "")) in [norm(v) for v in values] for k, values in spec.get("match", {}).items()):
                row.setdefault("_related", {})[spec["sheet"]] = [child for child in children if all(
                    field(row, k, None) is not None and norm(field(row, k)) == norm(field(child, k, "")) for k in spec["keys"])]
    return rows


def write_snapshot_output(snapshot, path):
    """Export saved values only; changing live masters cannot change historical output."""
    import xlsxwriter
    definition, results = snapshot.definition, snapshot.results
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with xlsxwriter.Workbook(str(path), {"strings_to_formulas": False, "strings_to_urls": False}) as wb:
        def sheet(name, rows):
            ws = wb.add_worksheet(name)
            headers = list(dict.fromkeys(k for row in rows for k in row))
            ws.write_row(0, 0, headers)
            for idx, row in enumerate(rows, 1):
                ws.write_row(idx, 0, [json.dumps(row.get(k), ensure_ascii=False) if isinstance(row.get(k), (dict, list)) else row.get(k) for k in headers])
            ws.freeze_panes(1, 0)
            if headers:
                ws.autofilter(0, 0, len(rows), len(headers) - 1)
        def legacy_aliases(record):
            result = dict(record)
            aliases = {
                "RM Cost": "RM Cost/Pc",
                "Process Cost": "Process Cost/Pc",
                "O&H Cost": "OH Cost",
                "PROFIT Cost": "Profit Cost",
                "Markup Cost/Pc": "Total Markup",
                "Total Cost/Pc": "Total",
                "Special RM Cost": "RM Cost/Pc",
                "Special Process Cost": "Process Cost/Pc",
                "Special Markup": "Total Markup",
                "Assy CT(in sec)": "CT(sec)",
                "Assy MHR": "MHR",
                "RM Rate/UOM": "Rate Kg",
            }
            for legacy, source in aliases.items():
                if result.get(legacy) in (None, "") and source in result:
                    result[legacy] = result[source]
            if result.get("Commodity") in (None, ""):
                result["Commodity"] = result.get("Parent Commodity") or result.get("profile")
            return result

        flat = [
            legacy_aliases({
                **{k: v for k, v in r["input"].items() if not k.startswith("_")},
                **r["values"],
                "profile": r.get("profile"),
                "Calculation Status": r["status"],
                "Error": r.get("error", ""),
            })
            for r in results
        ]
        master_columns = definition["output"].get("master_output_columns") or []
        if master_columns:
            master_rows = [{column: row.get(column) for column in master_columns} for row in flat]
            sheet(definition["output"]["master_sheet"], master_rows)
        else:
            sheet(definition["output"]["master_sheet"], flat)
        for name, profile in definition["profiles"].items():
            selected = [flat[i] for i, r in enumerate(results) if r.get("profile") == name]
            if selected:
                sheet(profile["sheet"], selected)
        sheet("Errors", [issue for r in results for issue in r.get("issues", [])])
        sheet("Provenance", [{"Input SHA256": snapshot.input_sha256, "Definition SHA256": snapshot.definition_sha256}])
        sheet("Formula Trace", [{"Line": i + 1, **step} for i, r in enumerate(results) for step in r.get("trace", [])])
        # Roll up only successful lines; show incomplete status for each affected group.
        groups = {}
        for result in results:
            row = result["input"]
            key = tuple(field(row, k, "") for k in definition["output"]["group_by"])
            group = groups.setdefault(key, {**dict(zip(definition["output"]["group_by"], key)), "Total": 0, "Status": "Completed"})
            if result["status"] != "Completed":
                group["Status"] = "Needs Review"
                continue
            qty = number(field(row, definition["output"]["quantity_field"], 1))
            group["Total"] += number(result["values"]["Total"]) * qty
            for variant in definition["output"].get("variants", []):
                group[variant] = group.get(variant, 0) + number(result["values"]["Total"]) * qty * number(field(row, variant, 0))
        sheet(definition["output"]["rollup_sheet"], list(groups.values()))
        if definition["output"].get("sbc_sheet"):
            end_items = {}
            for result in results:
                source = result["input"]
                if norm(field(source, "Type", "")) == "end item":
                    key = tuple(field(source, k, "") for k in definition["output"]["group_by"])
                    end_items[key] = source
            sbc_rows = []
            for key, group in groups.items():
                source = end_items.get(key, {})
                sbc_rows.append({"End Item": field(source, "End Item", group.get("End Item", "")),
                                 "Customer No": field(source, "Customer No", ""),
                                 "Desc": field(source, "Desc", ""),
                                 **{variant: group.get(variant, 0) for variant in definition["output"].get("variants", [])},
                                 "Status": group["Status"]})
            sheet(definition["output"]["sbc_sheet"], sbc_rows)

        # Expand related input rows (for example each Paint material layer) while
        # retaining calculated group values and the exact snapshotted master rate.
        for spec in definition["output"].get("related_sheets", []):
            detail_rows = []
            master_rows = [record for record in getattr(snapshot, "masters", [])
                           if norm(record.get("table")) == norm(spec["rate_table"])]
            for result in results:
                if result.get("profile") != spec["profile"]:
                    continue
                parent = result["input"]
                children = parent.get("_related", {}).get(spec["relation"], [])
                for child_index, child in enumerate(children):
                    detail_row = {k: v for k, v in child.items() if not k.startswith("_")}
                    for variant in definition["output"].get("variants", []):
                        detail_row[variant] = field(parent, variant, 0)
                    material = field(child, spec["material_field"], None)
                    period = field(child, spec["period_field"], None)
                    if material is not None and period is not None:
                        matches = [record for record in master_rows
                                   if norm(field(record["data"], spec["material_field"], "")) == norm(material)]
                        if len(matches) == 1:
                            rate = field(matches[0]["data"], period, None)
                            detail_row[spec["rate_output"]] = rate
                            consumption = field(child, spec["consumption_field"], None)
                            if rate is not None and consumption is not None:
                                places = int(spec.get("rounding_places", 3))
                                quantum = Decimal(1).scaleb(-places)
                                rounded_rate = Decimal(str(number(rate))).quantize(quantum, rounding=ROUND_HALF_UP)
                                rounded_consumption = Decimal(str(number(consumption))).quantize(quantum, rounding=ROUND_HALF_UP)
                                detail_row[spec["rate_output"]] = float(rounded_rate)
                                detail_row[spec["cost_output"]] = float(rounded_rate * rounded_consumption)
                    if child_index == 0:
                        detail_row.update(result.get("values", {}))
                        detail_row["Calculation Status"] = result["status"]
                        detail_row["Error"] = result.get("error", "")
                    detail_rows.append(detail_row)
            if detail_rows:
                sheet(spec["sheet"], detail_rows)
        analysis = []
        analysis_specs = definition["output"].get("analysis_rows") or [{"label": label, "field": label} for label in definition["output"].get("analysis_components", [])]
        end_analysis = []
        for spec in analysis_specs:
            label = spec["label"]
            component = {"Cost Driver": label, "Total": 0, "Status": "Completed"}
            end_components = {}
            for result in results:
                if result["status"] != "Completed":
                    component["Status"] = "Needs Review"
                    continue
                row = result["input"]
                if spec.get("profiles") and result.get("profile") not in spec["profiles"]:
                    continue
                value = number(result["values"].get(spec["field"], 0)) * number(field(row, definition["output"]["quantity_field"], 1))
                component["Total"] += value
                group_key = tuple(field(row, k, "") for k in definition["output"]["group_by"])
                end_component = end_components.setdefault(group_key, {**dict(zip(definition["output"]["group_by"], group_key)), "Cost Driver": label})
                for variant in definition["output"].get("variants", []):
                    component[variant] = component.get(variant, 0) + value * number(field(row, variant, 0))
                    end_component[variant] = end_component.get(variant, 0) + value * number(field(row, variant, 0))
            analysis.append(component)
            end_analysis.extend(end_components.values())
        sheet("Analysis", analysis)
        if definition["output"].get("end_analysis_sheet"):
            sheet(definition["output"]["end_analysis_sheet"], end_analysis)
    legacy_output = definition.get("output", {}) if isinstance(definition, dict) else {}
    if legacy_output.get("apply_legacy_template_styles", True):
        apply_legacy_output_template_styles(path, legacy_output.get("legacy_template_path"))
    # Re-project onto the legacy sheet/column layout. This reads the workbook just
    # written and rewrites presentation only - values are never recalculated, so the
    # verified numbers are preserved exactly. Disable with
    # definition.output.render_legacy_layout = false.
    if legacy_output.get("render_legacy_layout", True):
        try:
            from app.services.pcm_output_layout import render_legacy_output, load_layout
            if load_layout(legacy_output.get("output_layout_path")):
                render_legacy_output(path, path, load_layout(legacy_output.get("output_layout_path")))
        except Exception as exc:  # presentation must never fail a calculation
            import logging
            logging.getLogger(__name__).warning("legacy layout rendering skipped: %s", exc)
    # Apply the legacy FORMAT contract (Calibri 8, accounting number format, amber total
    # fill, 3DX annotation blocks) transcribed from the legacy source into
    # data/pcm_legacy_format.json. Values are untouched.
    if legacy_output.get("apply_legacy_formats", True):
        try:
            from openpyxl import load_workbook as _lw
            from app.services.pcm_legacy_format import apply_legacy_formats, load_format_spec
            spec = load_format_spec(legacy_output.get("legacy_format_path"))
            if spec:
                book = _lw(path)
                apply_legacy_formats(book, spec, skip_sheets=set(
                    legacy_output.get("diagnostic_sheets",
                                      ["Errors", "Provenance", "Formula Trace", "End Item Input"])))
                book.save(path)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("legacy formatting skipped: %s", exc)
    should_render_template = (
        legacy_output.get("render_legacy_template") is True
        or bool(legacy_output.get("legacy_template_path") or legacy_output.get("sbc_template_path"))
        or definition.get("revision") == "published-master-v2"
    )
    if should_render_template:
        try:
            from app.pcm.output.template_clone import render_legacy_workbook_from_template
            applied = render_legacy_workbook_from_template(
                path,
                legacy_output.get("legacy_template_path") or legacy_output.get("sbc_template_path"),
                definition,
            )
            if not applied:
                import logging
                logging.getLogger(__name__).warning("legacy template rendering skipped: template not found")
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("legacy template rendering skipped: %s", exc)
    return str(path)


