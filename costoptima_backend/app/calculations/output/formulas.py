"""Trusted local summary formulas and their same-run numeric XLSX caches.

This is a serialization helper for the existing writer. It never evaluates an
uploaded expression, opens a reference workbook, or looks up a master value.
Only writer-owned cell coordinates and numeric cells can form a SUM/SUMPRODUCT.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO
from math import isclose, isfinite
from zipfile import ZipFile
from xml.etree import ElementTree as ET
import posixpath
import re

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def _registry(book):
    if not hasattr(book, "_pcm_summary_formulas"):
        book._pcm_summary_formulas = {}
    return book._pcm_summary_formulas


def _decimal(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and isfinite(float(number)) else None
    except (InvalidOperation, ValueError, OverflowError):
        return None


def numeric_cell(cell):
    """Formula operands must be numeric cells, not numeric-looking text."""
    if cell.data_type == "f":
        cached = _registry(cell.parent.parent).get((cell.parent.title, cell.coordinate))
        return _decimal(cached[1]) if cached else None
    return _decimal(cell.value) if cell.data_type == "n" else None


def same_number(actual, expected):
    actual, expected = _decimal(actual), _decimal(expected)
    return actual is not None and expected is not None and isclose(
        float(actual), float(expected), rel_tol=1e-12, abs_tol=1e-10)


def _range(sheet, column, first, last, target, blank_zero=False):
    if not all(isinstance(value, int) and not isinstance(value, bool)
               for value in (column, first, last)):
        return None
    if not (1 <= column <= 16384 and 1 <= first <= last <= 1048576):
        return None
    if column == target.column and first <= target.row <= last:
        return None
    cells = [sheet.cell(row, column) for row in range(first, last + 1)]
    values = [Decimal(0) if blank_zero and cell.value in (None, "") else numeric_cell(cell) for cell in cells]
    if any(value is None for value in values):
        return None
    return cells[0].coordinate + ":" + cells[-1].coordinate, values


def _assign(cell, expression, expected):
    value = expected if isinstance(expected, bool) else _decimal(expected)
    if value is None:
        return False
    cell.value = "=" + expression
    _registry(cell.parent.parent)[(cell.parent.title, cell.coordinate)] = (expression, value)
    return True


def sum_range(cell, column, first, last, expected):
    """Emit a same-sheet SUM only after checking its existing displayed values."""
    checked = _range(cell.parent, column, first, last, cell)
    if not checked or not same_number(sum(checked[1], Decimal(0)), expected):
        return False
    return _assign(cell, "SUM(" + checked[0] + ")", expected)


def sumproducts(cell, amount_columns, quantity_column, variant_column, first, last, expected, subtract=None, blank_columns=()):
    """Emit sums of aligned SUMPRODUCTs; no arbitrary formula text is accepted."""
    if not amount_columns or len(set(amount_columns)) != len(amount_columns):
        return False
    quantity = _range(cell.parent, quantity_column, first, last, cell)
    variant = _range(cell.parent, variant_column, first, last, cell)
    if not quantity or not variant:
        return False
    terms = []; total = Decimal(0)
    for column in amount_columns:
        amount = _range(cell.parent, column, first, last, cell, column in blank_columns)
        if not amount:
            return False
        total += sum((a * q * v for a, q, v in zip(amount[1], quantity[1], variant[1])), Decimal(0))
        terms.append("SUMPRODUCT(" + ",".join((amount[0], quantity[0], variant[0])) + ")")
    expression = "+".join(terms)
    if subtract is not None:
        if subtract.parent is not cell.parent or subtract.coordinate == cell.coordinate:
            return False
        deduction = numeric_cell(subtract)
        if deduction is None:
            return False
        total -= deduction
        expression += "-" + subtract.coordinate
    if not same_number(total, expected):
        return False
    return _assign(cell, expression, expected)



def rounded_equal(cell, left, right, expected):
    """Original two-decimal Excel Boolean reconciliation; numeric inputs only."""
    if not isinstance(expected, bool) or any(
            operand.parent is not cell.parent or operand.coordinate == cell.coordinate
            for operand in (left, right)):
        return False
    values = [numeric_cell(operand) for operand in (left, right)]
    if any(value is None for value in values):
        return False
    actual = values[0].quantize(Decimal('.01'), rounding=ROUND_HALF_UP) == values[1].quantize(
        Decimal('.01'), rounding=ROUND_HALF_UP)
    if actual is not expected:
        return False
    return _assign(cell, f"ROUND({left.coordinate},2)=ROUND({right.coordinate},2)", expected)


def cell_reference(cell, source, expected):
    """Original mapped detail or Analysis-to-End-Item link in the fresh book."""
    if source.parent.parent is not cell.parent.parent or source is cell:
        return False
    if not same_number(numeric_cell(source), expected):
        return False
    title = source.parent.title.replace("'", "''")
    return _assign(cell, "'" + title + "'!" + source.coordinate, expected)


def row_arithmetic(cell, columns, operation, expected):
    """Writer-owned same-row addition/product with already-verified operands.

    No expression text or uploaded column identifier is accepted. PCBA original
    14092-14097/14140-14152 uses these operations for component body formulas.
    """
    if operation not in {'+', '*'} or len(columns) < 2 or len(columns) != len(set(columns)):
        return False
    if any(not isinstance(column, int) or isinstance(column, bool) or
           not 1 <= column <= 16384 or column == cell.column for column in columns):
        return False
    operands = [cell.parent.cell(cell.row, column) for column in columns]
    numbers = [numeric_cell(operand) for operand in operands]
    if any(number is None for number in numbers):
        return False
    result = Decimal(0) if operation == '+' else Decimal(1)
    for number in numbers:
        result = result + number if operation == '+' else result * number
    if not same_number(result, expected):
        return False
    return _assign(cell, operation.join(operand.coordinate for operand in operands), expected)


def sum_row(cell, first, last, expected):
    """Same-row numeric SUM, used by original PCBA markup/grand-total formulas."""
    if not all(isinstance(column, int) and not isinstance(column, bool) for column in (first, last)):
        return False
    if not 1 <= first <= last <= 16384 or first <= cell.column <= last:
        return False
    operands = [cell.parent.cell(cell.row, column) for column in range(first, last + 1)]
    numbers = [numeric_cell(operand) for operand in operands]
    if any(number is None for number in numbers) or not same_number(sum(numbers, Decimal(0)), expected):
        return False
    return _assign(cell, 'SUM(' + operands[0].coordinate + ':' + operands[-1].coordinate + ')', expected)


def editable_geometry_block(sheet, density):
    """Original setoftwo12631-12636 editable estimate, not a costing producer.

    C:G remain empty input cells exactly as in the original output form. Only a
    positive same-run database density can enable the original weight formulas.
    Initial caches are zero because every dimension is blank; supplied H4
    Calculated Wt remains an independent field and is never replaced here.
    """
    number = _decimal(density)
    rows = (*range(15, 21), *range(22, 26))
    if number is None or number <= 0:
        return False
    if any(sheet.cell(row, column).value not in (None, "")
           for row in rows for column in range(3, 8)):
        return False
    literal = format(number, "f")
    for row in rows:
        expression = (f"IF(F{row},3.14*((F{row}/2)^2-(G{row}/2)^2)*E{row}*{literal}/1000000,"
                      f"C{row}*D{row}*E{row}*{literal}/1000000)")
        _assign(sheet.cell(row, 8), expression, Decimal(0))
    sum_range(sheet.cell(21, 8), 8, 15, 20, Decimal(0))
    sum_range(sheet.cell(26, 8), 8, 22, 25, Decimal(0))
    return True


def restore_trusted_numeric_caches(book, cached_book):
    """Retain this writer's formula caches when editing its own verified artifact.

    This is not a general spreadsheet evaluator: accept only our fixed same-sheet
    SUM/SUMPRODUCT, writer-owned same-row addition/products and the ten original editable-geometry expressions.
    Every numeric result must independently match cached numeric operands; no
    uploaded formula, external reference, string operand or execution is allowed.
    Callers must also verify the input artifact against its saved run manifest.
    """
    cell_range = r"[A-Z]{1,3}[1-9][0-9]*:[A-Z]{1,3}[1-9][0-9]*"
    product = r"SUMPRODUCT\((" + cell_range + r"),(" + cell_range + r"),(" + cell_range + r")\)"
    def numbers(sheet, reference, target, blank_zero=False):
        from openpyxl.utils.cell import range_boundaries
        left, first, right, last = range_boundaries(reference)
        if left == right:
            checked = _range(sheet, left, first, last, target, blank_zero)
            return checked[1] if checked else None
        if first == last and not (first == target.row and left <= target.column <= right):
            operands = [numeric_cell(sheet.cell(first, column)) for column in range(left, right + 1)]
            return operands if all(value is not None for value in operands) else None
        return None
    pending=[]
    for sheet in book:
        if sheet.title not in cached_book.sheetnames:
            raise ValueError('Cached worksheet counterpart is missing')
        cached_sheet=cached_book[sheet.title]
        for row in sheet:
            for cell in row:
                if cell.data_type != 'f':continue
                expression=cell.value[1:];cached_cell=cached_sheet[cell.coordinate]
                expected=cached_cell.value if isinstance(cached_cell.value,bool) else _decimal(cached_cell.value)
                if expected is None:
                    raise ValueError('Trusted formula has no numeric cache to retain')
                verified=False
                match=re.fullmatch(r"SUM\((" + cell_range + r")\)",expression)
                if match:
                    operands=numbers(cached_sheet,match[1],cached_cell)
                    verified=operands is not None and same_number(sum(operands,Decimal(0)),expected)
                elif re.fullmatch(r"ROUND\([A-Z]{1,3}[1-9][0-9]*,2\)=ROUND\([A-Z]{1,3}[1-9][0-9]*,2\)", expression):
                    references = re.findall(r"ROUND\(([A-Z]{1,3}[1-9][0-9]*),2\)", expression)
                    values = [numeric_cell(cached_sheet[reference]) for reference in references]
                    if isinstance(expected, bool) and cell.coordinate not in references and all(value is not None for value in values):
                        verified = (values[0].quantize(Decimal('.01'), rounding=ROUND_HALF_UP) ==
                                    values[1].quantize(Decimal('.01'), rounding=ROUND_HALF_UP)) is expected
                elif re.fullmatch(r"'(?:[^']|'')+'![A-Z]{1,3}[1-9][0-9]*", expression):
                    title, reference = expression.rsplit("!", 1)
                    title = title[1:-1].replace("''", "'")
                    verified = (title in cached_book.sheetnames and
                                not (title == sheet.title and reference == cell.coordinate) and
                                same_number(numeric_cell(cached_book[title][reference]), expected))
                elif re.fullmatch(product + r"(?:\+" + product + r")*(?:-[A-Z]{1,3}[1-9][0-9]*)?",expression):
                    total=Decimal(0);verified=True
                    calculation, separator, deduction = expression.partition('-')
                    for term in calculation.split('+'):
                        parts=re.fullmatch(product,term)
                        # SUMPRODUCT treats truly empty worksheet cells as zero.
                        # The writer only emits a product after a complete same-run
                        # result proves the expected total; no text is coerced.
                        operands=[numbers(cached_sheet,part,cached_cell, True) for part in parts.groups()]
                        if any(values is None for values in operands) or len({len(values) for values in operands}) != 1:
                            verified=False;break
                        total+=sum((a*q*v for a,q,v in zip(*operands)),Decimal(0))
                    if separator:
                        value = numeric_cell(cached_sheet[deduction])
                        verified = verified and deduction != cell.coordinate and value is not None
                        if value is not None:total -= value
                    verified=verified and same_number(total,expected)
                elif re.fullmatch(r'(?:[A-Z]{1,3}[1-9][0-9]*(?:\+[A-Z]{1,3}[1-9][0-9]*)+|[A-Z]{1,3}[1-9][0-9]*(?:\*[A-Z]{1,3}[1-9][0-9]*)+)', expression):
                    # Only one operator, references on this same row and no self
                    # reference. Every cached operand must independently be numeric.
                    operation = '+' if '+' in expression else '*'
                    references = expression.split(operation)
                    valid = ('+' not in expression or '*' not in expression)
                    valid = valid and len(references) == len(set(references))
                    values = []
                    for reference in references:
                        operand = cached_sheet[reference]
                        valid = valid and operand.row == cell.row and reference != cell.coordinate
                        value = numeric_cell(operand)
                        if value is None:valid = False
                        values.append(value)
                    if valid:
                        result = Decimal(0) if operation == '+' else Decimal(1)
                        for value in values:
                            result = result + value if operation == '+' else result * value
                        verified = same_number(result, expected)
                elif cell.column==8 and cell.row in (*range(15,21),*range(22,26)):
                    row_number=cell.row
                    prefix=(f"IF(F{row_number},3.14*((F{row_number}/2)^2-(G{row_number}/2)^2)*E{row_number}*")
                    pattern=(re.escape(prefix)+r"(?P<density>[0-9]+(?:\.[0-9]+)?)/1000000,"+
                             re.escape(f"C{row_number}*D{row_number}*E{row_number}*")+r"(?P=density)/1000000\)")
                    geometry=re.fullmatch(pattern,expression)
                    verified=bool(geometry and Decimal(geometry['density'])>0 and expected==0 and
                                  all(cached_sheet.cell(row_number,column).value in (None,'') for column in range(3,8)))
                if not verified:
                    raise ValueError('Formula/cache is outside the trusted writer summary contract: '+sheet.title+'!'+cell.coordinate)
                pending.append((cell,expression,expected))
    for cell,expression,expected in pending:_assign(cell,expression,expected)
    return len(pending)


def persist_numeric_caches(content, book):
    """Preserve trusted numeric caches erased by openpyxl's formula serializer.

    Entries retain the existing XLSX package and only registered formula cells
    receive <v> values. Cached values are the already-calculated Python totals.
    Excel can still recalculate formulas after users inspect or edit a workbook.
    """
    registry = getattr(book, "_pcm_summary_formulas", {})
    if not registry:
        return content
    output = BytesIO()
    with ZipFile(BytesIO(content)) as source, ZipFile(output, "w") as target:
        workbook = ET.fromstring(source.read("xl/workbook.xml"))
        relationships = ET.fromstring(source.read("xl/_rels/workbook.xml.rels"))
        targets = {item.attrib["Id"]: item.attrib["Target"] for item in relationships}
        paths = {}
        for sheet in workbook.find("{" + MAIN + "}sheets"):
            destination = targets[sheet.attrib["{" + REL + "}id"]]
            path = destination.lstrip("/") if destination.startswith("/") else posixpath.normpath("xl/" + destination)
            paths[path] = sheet.attrib["name"]
        for entry in source.infolist():
            data = source.read(entry.filename)
            name = paths.get(entry.filename)
            expected = {coordinate: values for (sheet, coordinate), values in registry.items() if sheet == name}
            if expected:
                root = ET.fromstring(data); found = set()
                for cell in root.iter("{" + MAIN + "}c"):
                    coordinate = cell.attrib.get("r")
                    if coordinate not in expected:
                        continue
                    formula = cell.find("{" + MAIN + "}f")
                    expression, number = expected[coordinate]
                    if formula is None or formula.text != expression:
                        raise ValueError("Trusted summary formula differs during XLSX serialization")
                    if isinstance(number, bool):cell.attrib["t"] = "b"
                    else:cell.attrib.pop("t", None)
                    cached = cell.find("{" + MAIN + "}v")
                    if cached is None:
                        cached = ET.SubElement(cell, "{" + MAIN + "}v")
                    cached.text = ("1" if number else "0") if isinstance(number,bool) else format(float(number), ".16g")
                    found.add(coordinate)
                if found != set(expected):
                    raise ValueError("A trusted summary cell was not serialized")
                data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            target.writestr(entry, data)
    return output.getvalue()
