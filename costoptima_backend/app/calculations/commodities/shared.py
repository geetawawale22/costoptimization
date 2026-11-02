"""Shared named-field arithmetic; no Excel coordinates or embedded master rates.

Primary reference: PCM_5/Sheet_Metal_Forging_with summary_chk_Chnages.py,
icc_check 1355-1388, make_new 14935-14996, process loops 3240-3295,
forging_data 13064-13239 and casting process loops 8950-9025.
"""
from decimal import Decimal
import re
from ..common import CalculationError, number, required, get
from ..masters import FieldRef

ZERO = Decimal(0)


def checkpoint(context, record, values):
    """Retain only explicitly resolved same-run values for partial output."""
    if hasattr(context,'checkpoint'):context.checkpoint(record,values)
    return values


def value(record, field, default=None):
    raw = get(record, field, default)
    if raw is None or raw == '':
        raise CalculationError('missing_input', f'Required numeric input {field} is missing.', field=field)
    return number(raw, field)


def material(context, record, field='Rate/UOM'):
    period = required(record, 'RM Base')
    return context.require_master('material_master', {'material_code': required(record, 'Material Code')},
                                  FieldRef(field, group=str(period)))



def material_details(context, record):
    """Retain named master labels for output without using uploaded rates.

    Arithmetic-only adapters may expose lookup alone. The database resolver
    always exposes the same matched record and verified metadata fields.
    """
    code=get(record,'Material Code')
    if code in (None,'') or not hasattr(context.master,'record'):return {}
    metadata=context.master.record('material_master',{'material_code':code})
    return {field:metadata.value(field) for field in ('Material Family','Material Sub Family','Material Grade')}


def process_rate(context, record, process, *, location=None, key='process'):
    # AD is the explicit legacy location fallback, not a fabricated rate.
    location = location or get(record, 'Sector & Location') or 'AD'
    rate=context.require_master('process_master', {key: process}, FieldRef('Rate/UOM', group=str(location)))
    context.provenance.append({'kind':'master_rate','name':str(process),'rate':rate,'location':str(location)})
    return rate


def process_lines(context, record, *, key='_process_lines', labor=False, grade=None, material_cost=None, location=None):
    """Resolve every supplied process by its business name, preserving order.

    SOFT/STAMPING final overwrite multiplies labour by the first positive
    process quantity and converts hourly rates from minutes. CB Foam uses
    RM Cost*0.3 (legacy part1/part1_stamp), not the initial SUMPRODUCT.
    """
    lines = record.get(key, record.get('Process Lines'))
    if lines is None:
        lines = context.process_lines(record) if hasattr(context, 'process_lines') else None
    if lines is None:
        raise CalculationError('process_mapping_missing', 'Named process-column mapping is required before this branch can run.')
    if not isinstance(lines, list):
        raise CalculationError('invalid_process_lines', 'Process Lines must be a list of named quantities.')
    if str(grade or '').strip().casefold() == 'cb foam' and labor:
        return number(material_cost, 'RM Cost / Pc') * Decimal('0.3')
    first_positive = next((number(line.get('quantity') or 0, str(line.get('name')))
                           for line in lines if number(line.get('quantity') or 0, str(line.get('name'))) > 0), ZERO)
    total = ZERO
    for line in lines:
        name = str(required(line, 'name'))
        quantity = number(line.get('quantity') or 0, name)
        if quantity == 0:
            # A supplied zero operation has zero contribution without inventing
            # a rate. It must not demand a missing price for an unused process.
            context.provenance.append({'kind':'process_line','name':name,'quantity':quantity,
                                       'rate':None,'cost':ZERO,'uom':None,'rate_required':False})
            continue
        # Rates in uploads are deliberately ignored: all rates come from DB.
        rate = process_rate(context, record, name, location=location)
        uom = str(context.master.lookup('process_master', {'process': name}, 'uom') or '')
        amount = quantity * rate
        if labor and any(token in name.upper() for token in ('LHR_SKILLED', 'LHR_SEMISKILLED', 'LHR_UNSKILLED')):
            amount *= first_positive
        if any(token in uom.casefold() for token in ('/hr', 'mhr', 'lhr')):
            amount /= 60
        context.provenance.append({'kind':'process_line','name':name,'quantity':quantity,
                                   'rate':rate,'cost':amount,'uom':uom})
        total += amount
        checkpoint(context,record,{'Completed Process Cost/Pc':total,
            '_process_costs':[dict(item) for item in context.provenance if item.get('kind')=='process_line']})
    return total


def cost_base(label, values):
    """Original pipe-separated applicability labels, resolved by named outputs.

    icc_check creates addition over the selected output headers. Unsupported
    labels are blocked; they never silently become Basic Cost or zero.
    """
    if label is None or str(label).strip() == '':
        raise CalculationError('applicability_missing', 'A markup applicability expression is missing.')
    if str(label).strip() in ('0', '0.0'):
        return ZERO
    aliases = {
        'RM COST': 'RM Cost/Pc', 'RM COST/PC': 'RM Cost/Pc',
        'PROCESS COST': 'Process Cost/Pc', 'PROCESS COST/PC': 'Process Cost/Pc',
        'BASIC COST': 'Basic Cost/Pc', 'BASIC COST/PC': 'Basic Cost/Pc',
        'TOTAL COST': 'Total Cost/Pc', 'TOTAL COST/PC': 'Total Cost/Pc',
        'ICC COST': 'ICC COST', 'REJ COST': 'REJ COST',
        'OH COST': 'OH COST', 'PROFIT COST': 'PROFIT COST',
    }
    if '{' in str(label):
        from ..common import evaluate_expression
        return number(evaluate_expression(str(label), values), 'applicability base')
    total = ZERO
    for token in str(label).split('|'):
        token = token.strip()
        matched = next((name for name in values if name.casefold() == token.casefold()), None)
        matched = matched or aliases.get(token.upper())
        if matched not in values:
            raise CalculationError('applicability_field_missing', f'Applicability field {token!r} has no calculated producer.', field=token)
        total += number(values[matched], matched)
    return total


def markups(context, record, values, *, fields=('ICC', 'REJ', 'OH', 'PROFIT'), blank_applicability_zero=False, base_overrides=None):
    """Source rates and named bases from the existing database Norms masters."""
    supplier = required(record, 'Supplier')
    result = dict(values)
    checkpoint(context,record,result)
    for name in fields:
        rate = context.require_master('norms_make', {'supplier': supplier}, name, blank_zero=True)
        result[name] = rate
        checkpoint(context,record,{name:rate})
        if rate == 0:
            result[name + ' COST'] = ZERO
        elif base_overrides is not None and name in base_overrides:
            result[name+' COST']=rate*number(base_overrides[name],name+' applicability base')
        else:
            if blank_applicability_zero:
                row=context.master.record('norms_make_applicability', {'supplier':supplier},duplicate_policy='last')
                label=row.value(name)
                if label is None or str(label).strip()=='':
                    # make_new14939-14984 and part13831-3915 write0 when
                    # selected blank applicability cannot be constructed. A missing
                    # supplier record still fails before this point.
                    result[name+' COST']=ZERO
                    result.setdefault('_legacy_fallbacks',[]).append({'field':name+' COST','reason':'selected blank applicability; explicit original branch fallback'})
                    checkpoint(context,record,result)
                    continue
            else:label = context.master.lookup('norms_make_applicability', {'supplier': supplier}, name, duplicate_policy='last')
            result[name + ' COST'] = rate * cost_base(label, result)
        checkpoint(context,record,result)
    result['MARKUP/Pc'] = sum((result[name + ' COST'] for name in fields), ZERO)
    return result


def finish(record, values, *, pnf=ZERO, total_key='Total Cost/Pc'):
    result = dict(values)
    if total_key not in result:
        result[total_key] = result['Basic Cost/Pc'] + result.get('MARKUP/Pc', ZERO)
    result['PnF/Pc'] = number(pnf, 'PnF/Pc')
    result['Grand Total Cost/Pc'] = result[total_key] + result['PnF/Pc']
    qty = value(record, 'Qty/Veh')
    for field in ('RM Cost/Pc', 'Process Cost/Pc', 'Basic Cost/Pc', 'MARKUP/Pc', total_key, 'PnF/Pc', 'Grand Total Cost/Pc'):
        if field in result:
            result[field.replace('/Pc', '/Veh')] = result[field] * qty
    return result
