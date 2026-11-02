"""Named-value arithmetic shared by the source-backed PCM branches."""
from __future__ import annotations
import ast
import operator
import re
from decimal import Decimal, InvalidOperation, localcontext, ROUND_HALF_UP


class CalculationError(ValueError):
    def __init__(self, code, message=None, field=None, **details):
        self.code, self.field, self.details = code, field, details
        super().__init__(message if message is not None else str(code))
    def as_dict(self):
        return {"code": self.code, "message": str(self), "field": self.field, **self.details}


CalculationBlocked = CalculationError


def normalized(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def get(record, field, default=None):
    if field in record: return record[field]
    found = [v for k, v in record.items() if normalized(k) == normalized(field)]
    if len(found) > 1: raise CalculationError("ambiguous_input", f"Several columns match {field}", field)
    return found[0] if found else default


def empty(value):
    return value is None or isinstance(value, str) and not value.strip()


def required(record, field):
    value = get(record, field)
    if empty(value): raise CalculationError("missing_input", f"Required input {field} is missing", field)
    return value


def number(value, label="value"):
    if empty(value): raise CalculationError("missing_number", f"{label} is required", label)
    if isinstance(value, bool): return Decimal(int(value))
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        raise CalculationError("invalid_number", f"{label} must be numeric", label)
    if not result.is_finite(): raise CalculationError("invalid_number", f"{label} must be finite", label)
    return result


def ratio(value, label="rate"):
    if isinstance(value, str) and value.strip().endswith("%"):
        return number(value.strip()[:-1], label) / Decimal(100)
    return number(value, label)


def divide(a, b, label="division"):
    numerator, denominator = number(a, label), number(b, label)
    if denominator == 0: raise CalculationError("division_by_zero", f"{label}: denominator is zero", label)
    return numerator / denominator


def supplied_consumption_scrap(record, gross):
    """PCM part1:3508 and part1_stamp:6130 full-consumption scrap.

    The fallback uses the uploaded Blank Consumption / No of parts / Blank,
    independently of the calculated blank fields. Excel's outer IFERROR maps a
    known cell arithmetic error to zero; absent source headers remain errors.
    This helper performs no master lookup and cannot hide a missing master.
    """
    marker = object()
    def source_value(field):
        raw = get(record, field, marker)
        if raw is marker:
            raise CalculationError("missing_input", f"Required input {field} is missing", field)
        return raw
    supplied_net = source_value("Net RM consumption")
    try:
        net = Decimal(0) if empty(supplied_net) else number(supplied_net, "Net RM consumption")
    except CalculationError as error:
        if error.code == "invalid_number": return Decimal(0)
        raise
    if net > 0:
        return number(gross, "Effective Gross Consumption") - net
    # Resolve header presence before applying the original arithmetic fallback.
    blank = source_value("Blank Consumption")
    parts = source_value("No of parts / Blank")
    try:
        numerator = Decimal(0) if empty(blank) else number(blank, "Blank Consumption")
        denominator = Decimal(0) if empty(parts) else number(parts, "No of parts / Blank")
        return number(gross, "Effective Gross Consumption") - divide(numerator, denominator, "Blank Consumption / No of parts / Blank")
    except CalculationError as error:
        if error.code in {"division_by_zero", "invalid_number"}: return Decimal(0)
        raise


def first_matching_condition(rows, values, condition_field="ConN"):
    """PCM findcond's ordered !=,=,<,> matching, with no last-row fallback.

    Comparisons are named-value conditions. Excel cells/ranges and code execution
    are never accepted. Unsupported malformed legacy conditions are explicit.
    """
    for row in rows:
        condition = get(row, condition_field)
        if empty(condition): continue
        tests = str(condition).split("&")
        matched = True
        for test in tests:
            match = re.fullmatch(r"\s*\{?([^{}<>=!]+?)\}?\s*(!=|<=|>=|=|<|>)\s*(.*?)\s*", test)
            if not match: raise CalculationError("unmapped_condition", f"Unsupported master condition {condition!r}")
            name, op, literal = match.groups()
            if op in ("<=", ">="):
                raise CalculationError("legacy_condition_ambiguity", "Legacy <=/>= condition parsing needs an approved semantic decision", name)
            if not any(normalized(k)==normalized(name.strip()) for k in values):
                raise CalculationError("unknown_operand",f"Condition input {name.strip()} has no verified field",name.strip())
            actual = get(values, name.strip())
            literal = literal.strip().strip('"').strip("'")
            if op in ("<", ">"):
                # findcond only accepts nonnegative digit-leading values.
                if not str(actual).split(".")[0].isdigit():
                    matched=False
                    break
                a, b = number(actual, name), number(literal, name)
            else:
                try:a,b=number(actual,name),number(literal,name)
                except CalculationError:a,b=normalized(actual),normalized(literal)
            matched = matched and {"!=": operator.ne, "=": operator.eq, "<": operator.lt, ">": operator.gt}[op](a, b)
        if matched: return row
    raise CalculationError("missing_condition", "No matching Condition")


def evaluate_expression(expression, values, *, excel_blank_zero=False):
    """Restricted named arithmetic for existing consumption-master expressions.

    This does not create a formula catalogue, table or second execution engine.
    The branch chooses the original configured expression; only named operands
    and the explicit functions below are admitted. No Excel coordinates, imports,
    attributes, arbitrary calls, subscripts or Python eval are used.
    """
    if not isinstance(expression, str): return number(expression, "master expression")
    expression = expression.strip().lstrip("=")
    if len(expression) > 8000: raise CalculationError("expression_limit", "Master expression is too long")
    refs = {}
    def placeholder(match):
        name = match.group(1).strip()
        key = f"_field_{len(refs)}"; refs[key] = name; return key
    text = re.sub(r"\{([^{}]+)\}", placeholder, expression)
    # All replacements are syntax-level, preserving quoted source values.
    text = text.replace("^", "**").replace("<>", "!=")
    text = re.sub(r"(?<![<>=!])=(?!=)", "==", text)
    try: tree = ast.parse(text, mode="eval")
    except SyntaxError: raise CalculationError("unsupported_expression", "Master expression is not supported named arithmetic")
    if len(list(ast.walk(tree))) > 500: raise CalculationError("expression_limit", "Master expression is too complex")
    def visit(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str, bool)):
            return number(node.value) if isinstance(node.value, (int, float)) and not isinstance(node.value, bool) else node.value
        if isinstance(node, ast.Name):
            if node.id in refs:
                field=refs[node.id]
                if not any(normalized(k)==normalized(field) for k in values):raise CalculationError("unknown_operand",f"Unconfigured expression operand {field}",field)
                raw=get(values,field)
                if empty(raw):
                    if excel_blank_zero:return Decimal(0)
                    raise CalculationError("missing_input",f"Required input {field} is missing",field)
                return raw
            if node.id.upper() in {"TRUE", "FALSE"}: return node.id.upper() == "TRUE"
            raise CalculationError("unknown_operand", f"Unconfigured expression operand {node.id}")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd, ast.Not)):
            v = visit(node.operand)
            return not v if isinstance(node.op, ast.Not) else -number(v) if isinstance(node.op, ast.USub) else number(v)
        if isinstance(node, ast.BinOp):
            a, b = number(visit(node.left)), number(visit(node.right))
            if isinstance(node.op, ast.Add): return a+b
            if isinstance(node.op, ast.Sub): return a-b
            if isinstance(node.op, ast.Mult): return a*b
            if isinstance(node.op, ast.Div): return divide(a,b)
            if isinstance(node.op, ast.Pow):
                if b != b.to_integral_value() or abs(b)>20: raise CalculationError("unsupported_power", "Exponent must be a bounded integer")
                if a == 0 and b < 0: raise CalculationError("division_by_zero", "Zero cannot have a negative exponent")
                return a**int(b)
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            a, b = visit(node.left), visit(node.comparators[0])
            op = node.ops[0]
            if isinstance(op,(ast.Eq,ast.NotEq)):
                result = normalized(a)==normalized(b) if isinstance(a,str) or isinstance(b,str) else a==b
                return not result if isinstance(op,ast.NotEq) else result
            a,b=number(a),number(b)
            for typ,fn in [(ast.Lt,operator.lt),(ast.LtE,operator.le),(ast.Gt,operator.gt),(ast.GtE,operator.ge)]:
                if isinstance(op,typ): return fn(a,b)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            name=node.func.id.upper()
            if name == "IF" and len(node.args)==3: return visit(node.args[1] if visit(node.args[0]) else node.args[2])
            if name in {"IFERROR","IFEMPTY"} and len(node.args)==2:
                try:
                    result=visit(node.args[0])
                    return visit(node.args[1]) if name=="IFEMPTY" and empty(result) else result
                except CalculationError as exc:
                    if name=="IFERROR" and exc.code in {"division_by_zero","invalid_number"}: return visit(node.args[1])
                    raise
            args=[number(visit(a)) for a in node.args]
            if name=="SUM": return sum(args,Decimal(0))
            if name=="MIN" and args: return min(args)
            if name=="MAX" and args: return max(args)
            if name=="ABS" and len(args)==1: return abs(args[0])
            if name=="INT" and len(args)==1: return args[0].to_integral_value(rounding="ROUND_FLOOR")
            if name=="ROUNDDOWN" and len(args)==2:
                return args[0].quantize(Decimal(1).scaleb(-int(args[1])),rounding="ROUND_DOWN")
            if name=="ROUND" and len(args)==2:
                return args[0].quantize(Decimal(1).scaleb(-int(args[1])),rounding=ROUND_HALF_UP)
        raise CalculationError("unsupported_expression", f"Unsupported expression node {type(node).__name__}")
    with localcontext() as context:
        context.prec=34
        return visit(tree.body)
