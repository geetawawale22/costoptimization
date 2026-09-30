"""Translate legacy consumption-master cells into database PCM rule rows."""
import ast
import json
import re

from openpyxl import load_workbook

NOT_YET_CONVERTED = []
_BLANK = {None, "", "\xa0"}
_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
_CONDITION = re.compile(r"^\s*(.+?)\s*(<>|!=|>=|<=|=|>|<)\s*(.*?)\s*$")


class _Power(ast.NodeTransformer):
    def visit_BinOp(self, node):
        node = self.generic_visit(node)
        if isinstance(node.op, ast.Pow):
            return ast.Call(func=ast.Name(id="power", ctx=ast.Load()),
                            args=[node.left, node.right], keywords=[])
        return node

    def visit_Call(self, node):
        node = self.generic_visit(node)
        if isinstance(node.func, ast.Name) and node.func.id.casefold() == "if" and len(node.args) == 3:
            return ast.IfExp(test=node.args[0], body=node.args[1], orelse=node.args[2])
        return node


def _text(value):
    return "" if value in _BLANK else str(value).strip()


def convert_template(value):
    """Convert the restricted Excel-like formula language to runtime syntax."""
    if isinstance(value, (int, float)):
        return repr(value)
    formula = _text(value)
    if not formula:
        raise ValueError("blank formula")
    formula = formula.lstrip("=").replace("^", "**")
    formula = _PLACEHOLDER.sub(lambda m: f'd({json.dumps(m.group(1).strip())})', formula)
    formula = re.sub(r"\bROUNDDOWN\s*\(", "rounddown(", formula, flags=re.I)
    formula = re.sub(r"\bINT\s*\(", "integer(", formula, flags=re.I)
    formula = re.sub(r"\bSUM\s*\(", "sum_values(", formula, flags=re.I)
    formula = re.sub(r"\bMAX\s*\(", "max(", formula, flags=re.I)
    formula = re.sub(r"\bMIN\s*\(", "min(", formula, flags=re.I)
    # Two published surface-area expressions have an unmatched leading '('.
    # Balance that mechanical workbook defect before parsing the expression.
    if formula.count("(") > formula.count(")"):
        formula += ")" * (formula.count("(") - formula.count(")"))
    tree = ast.parse(formula, mode="eval")
    tree = ast.fix_missing_locations(_Power().visit(tree))
    return ast.unparse(tree.body)


def convert_condition(value, header=None):
    condition = _text(value)
    if not condition:
        return None
    match = _CONDITION.match(condition)
    if not match:
        if header and not str(header).casefold().startswith("con"):
            return f'text(d({json.dumps(str(header).strip())}, "")) == text({json.dumps(condition)})'
        raise ValueError(f"unsupported condition {condition!r}")
    field, operator, expected = (item.strip() for item in match.groups())
    operator = "!=" if operator == "<>" else "==" if operator == "=" else operator
    try:
        numeric = float(expected.replace("%", "")) / (100 if expected.endswith("%") else 1)
    except ValueError:
        return f'text(d({json.dumps(field)}, "")) {operator} text({json.dumps(expected)})'
    if operator in ("==", "!="):
        # A BLANK field must not equal zero. The previous form, n(d(field, 0)) == 0.0,
        # defaulted a missing value to 0 and so made "L / Unfold=0" true for any part that
        # simply has no L / Unfold - every Thermal part, whose sheet-metal fields are blank
        # because they do not apply. That selected a zero-consumption rule ahead of the
        # Thermal-specific rule and silently produced the wrong consumption and cost.
        #
        # Legacy findcond compares float(str(v)) else str(v).upper(); str(None) is "None",
        # so a blank never equals a number. legacy_eq / legacy_ne reproduce that exactly
        # (see app/services/pcm_legacy_conditions.py) and are proven equivalent to the
        # legacy oracle by tools/diff_condition_engines.py.
        fn = "legacy_eq" if operator == "==" else "legacy_ne"
        return f'{fn}(d({json.dumps(field)}, None), {numeric!r})'
    return f'n(d({json.dumps(field)}, 0)) {operator} {numeric!r}'


def _rules(ws, table, specs):
    rows = []
    for source_row in range(3, ws.max_row + 1):
        for output, formula_col, condition_cols in specs:
            formula = ws.cell(source_row, formula_col).value
            if formula in _BLANK:
                continue
            try:
                conditions = [convert_condition(ws.cell(source_row, col).value, ws.cell(2, col).value)
                              for col in condition_cols]
                conditions = [item for item in conditions if item]
                expression = convert_template(formula)
            except (SyntaxError, ValueError) as exc:
                NOT_YET_CONVERTED.append({"table": table, "row": source_row,
                                          "output": output, "reason": str(exc)})
                continue
            rows.append({"Output": output, "Condition": " and ".join(conditions) or "True",
                         "Expression": expression, "Priority": source_row,
                         "_source_sheet": ws.title, "_source_row": source_row})
    return rows


def normalize_master_workbook(path):
    NOT_YET_CONVERTED.clear()
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        return {
            "Net Consumption base": _rules(wb["Net Consumption base"], "Net Consumption base", [
                ("Cal RM cons", 20, range(1, 10)),
            ]),
            "Blank Consumption base": _rules(wb["Blank Consumption base"], "Blank Consumption base", [
                ("Blank Length", 15, range(1, 11)),
                ("Blank Width", 21, range(1, 11)),
                ("Cal Blank Cons", 41, range(25, 33)),
                ("Cal Parts Per Blank", 54, range(45, 50)),
            ]),
            "Gross Consumption base": _rules(wb["Gross Consumption base"], "Gross Consumption base", [
                ("Cal parts / Sheet", 48, range(31, 38)),
                ("Gross Consumption", 78, range(53, 58)),
                ("Total Surface Area", 109, range(84, 89)),
            ]),
        }
    finally:
        wb.close()
