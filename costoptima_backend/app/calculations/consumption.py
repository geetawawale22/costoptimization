"""Named consumption-master execution: PCM part1/part1_stamp/make_hvac.

Original findcond narrows rows one ConN at a time by the FIRST satisfying
condition text, retaining equal-condition rows; it stops when one row remains.
This is deliberately not an AND filter over every row or a SQL last-row guess.
"""
from decimal import Decimal
import re
from .common import CalculationError, first_matching_condition, evaluate_expression, get, empty, number, normalized
from .masters import FieldRef, MasterLookupError
from .consumption_rules import FORMULAS, CONDITIONS


def _field(row,name,group=None):
    try:return row.value(FieldRef(name,group))
    except MasterLookupError:
        if group is not None:return row.value(name)
        raise


def _condition_failure(error, context, master_code, result, values, name, rows):
    """Describe the exact failed named rule without guessing its input/master.

    The allowlisted source conditions identify the few safe operands to report.
    A missing material method and an absent uploaded dimension are different
    requirements; neither is silently converted to zero or another formula.
    """
    attempted=[];names=[]
    for row in rows:
        condition=str(row[name])
        record=row['_record']
        attempted.append({'condition':condition,'source_table':record.source_table,
                          'source_row_id':record.source_row_id})
        for test in condition.split('&'):
            match=re.match(r'\s*\{?([^{}<>=!]+?)\}?\s*(?:!=|<=|>=|=|<|>)',test)
            if match and match.group(1).strip() not in names:names.append(match.group(1).strip())
    inputs=[]
    for operand in names:
        present=any(normalized(key)==normalized(operand) for key in values)
        item={'field':operand,'present':present,'value':get(values,operand) if present else None}
        # Only attach a database identity where its actual named mapping exists.
        # Failure to map an input field never grants a material-field fallback.
        code=get(values,'Material Code')
        if not empty(code) and hasattr(context.master,'record'):
            try:
                record=context.master.record('material_master',{'material_code':code})
                from .masters import resolve_field
                field=resolve_field(record.fields,operand)
                item['master_source']={'master':'material_master','lookup_keys':{'material_code':str(code)},
                    'source_table':record.source_table,'source_row_id':record.source_row_id,
                    'database_column':field.column,'database_value':record.values.get(field.column)}
            except (MasterLookupError,KeyError):pass
        inputs.append(item)
    details={**error.details,'master':master_code,'master_field':result,
             'condition_field':name,'condition_inputs':inputs,'attempted_conditions':attempted}
    return CalculationError(error.code,str(error),error.field,**details)


def select_rule(context,master_code,result,values,condition_names,group=None,*,bindings=None,commodity=None):
    rows=[]; mapped_conditions=set()
    for record in context.master.records(master_code):
        if commodity is not None:
            try:rule_commodity=record.value('Commodity')
            except MasterLookupError:continue
            if str(rule_commodity or '').strip().casefold()!=str(commodity).strip().casefold():continue
        try: expression=_field(record,result,group)
        except MasterLookupError:continue
        if empty(expression):continue
        if str(expression).strip() not in FORMULAS.get((master_code,result),frozenset()):
            raise CalculationError("unported_consumption_formula",f"A {result} source expression is not in the static Python port",result,master=master_code)
        row={"_record":record,"_expression":expression}
        for name in condition_names:
            try:
                row[name]=_field(record,name,group)
                mapped_conditions.add(name)
            except MasterLookupError:row[name]=None
        rows.append(row)
    if not rows:raise CalculationError("master_expression_missing",f"No verified {result} expression is available",result,master=master_code)
    if condition_names and not mapped_conditions:
        raise CalculationError("master_condition_mapping_missing", "No verified named condition fields are mapped; refusing a first-formula guess", result, master=master_code)
    for name in condition_names:
        if len(rows)==1:break
        populated=[r for r in rows if not empty(r.get(name))]
        if any(str(r[name]).strip() not in CONDITIONS for r in populated):
            raise CalculationError("unported_consumption_condition","A master condition is not in the static Python source vocabulary",name)
        if not populated:continue
        try:selected=first_matching_condition(populated,values,name)
        except CalculationError as error:
            if error.code in {'missing_condition','unknown_operand'}:
                raise _condition_failure(error,context,master_code,result,values,name,populated) from error
            raise
        condition=selected[name]
        rows=[row for row in rows if row.get(name)==condition]
    if not rows:raise CalculationError("missing_condition",f"No matching condition for {result}",result)
    # Original resolver selects the first resulting row when conditions tie.
    chosen=rows[0]
    if bindings is not None:
        # create_indexformula2:1425-1445 distinguishes Material Master,
        # Input and Output helpers even where their named operands coincide.
        # Bind selected material operands by their real semantic field, before
        # the calculated L/W output can shadow material sheet-size L/W.
        record=chosen["_record"]
        material=None
        expression_fields=set(re.findall(r"\{([^{}]+)\}",str(chosen["_expression"])))
        for field in record.fields:
            if not str(field.header or "").startswith("Material Master"):
                continue
            if master_code=="gross_consumption_base" and field.group!=(group or result):
                continue
            declaration=record.values.get(field.column)
            if not isinstance(declaration,str):continue
            for name in re.findall(r"\{([^{}]+)\}",declaration):
                name=name.strip()
                if name not in expression_fields:continue
                if material is None:
                    code=get(values,"Material Code")
                    if empty(code):raise CalculationError("material_binding_missing","Consumption rule requires a named Material Code",name)
                    material=context.master.record("material_master",{"material_code":code})
                bindings[name]=material.value(name)
    context.provenance.append({"master":master_code,"source_table":chosen["_record"].source_table,
                               "source_row_id":chosen["_record"].source_row_id,"field":result,
                               "duplicate_candidates":len(rows)})
    return next(expression for expression in FORMULAS[(master_code,result)] if expression==str(chosen["_expression"]).strip())


def calculate_consumption(record,context,family):
    values=dict(record);results={}
    # part1:1649-1655 and 3361 link these known input fields into output
    # cells. Excel evaluates an existing empty linked numeric cell as zero.
    # An absent header stays absent and still raises unknown_operand.
    numeric_inputs=("Pipe Dia", "L / Unfold", "W / Unfold", "T", "GSM/ Density",
                    "Net RM consumption", "Blank Length", "Blank Width",
                    "Blank Consumption", "No of parts / Blank",
                    "No of parts /sheet", "Actual Gross Consumption")
    for field in numeric_inputs:
        for name in tuple(values):
            if name.strip().casefold()==field.casefold() and empty(values[name]):
                values[name]=Decimal(0)
    # These are source-defined names of semantic condition fields, not offsets.
    def apply(master,output,start,end,group=None):
        bindings={}
        expression=select_rule(context,master,output,values,[f"Con{i}" for i in range(start,end+1)],group,bindings=bindings)
        result=evaluate_expression(expression,{**values,**bindings},excel_blank_zero=True)
        values[output]=results[output]=number(result,output)
        if hasattr(context,'checkpoint'):context.checkpoint(record,results)
        return result
    net=get(record,"Net RM consumption")
    # Primary1670-1687 derives AD independently even when input AE is supplied.
    apply("net_consumption_base","Cal RM cons",1,9)
    # Supplied dimensions are kept. Derived dimensions need named master rules.
    for output in ("Blank Length","Blank Width"):
        value=get(record,output)
        if not empty(value) and number(value,output)>0:
            values[output]=results[output]=number(value)
            if hasattr(context,'checkpoint'):context.checkpoint(record,results)
        else:apply("blank_consumption_base",output,1,10)
    apply("blank_consumption_base","Cal Blank Cons",11,18)
    apply("blank_consumption_base","Cal Parts Per Blank",19,23)
    for output in ("L","W"):
        apply("gross_consumption_base",output,1,9)
    apply("gross_consumption_base","Cal parts / Sheet",10,16)
    actual=get(record,"Actual Gross Consumption")
    # AU is calculated independently; AW selects positive uploaded AV over AU.
    # They are separate source fields, not interchangeable display aliases.
    apply("gross_consumption_base","Cal Gross Consumption",17,21,"Cal Gross Consumption")
    results["Effective Gross Consumption"]=number(actual) if not empty(actual) and number(actual)>0 else results["Cal Gross Consumption"]
    if hasattr(context,'checkpoint'):context.checkpoint(record,results)
    gross=number(results["Effective Gross Consumption"])
    if gross==0:raise CalculationError("division_by_zero","Gross consumption is zero; yield cannot be calculated")
    blank=number(results["Cal Blank Cons"]);parts=number(results["Cal Parts Per Blank"])
    calculated_net=number(results["Cal RM cons"])
    net_value=number(net) if not empty(net) and number(net)>0 else calculated_net
    if net_value<=0:
        if parts==0:raise CalculationError("division_by_zero","Cal Parts Per Blank is zero")
        net_value=blank/parts
    # Primary3363 AX uses calculated net or input blank/parts and calculated AU;
    # AY uses supplied net, calculated net, or calculated blank/parts over AW.
    calculated_gross=number(results["Cal Gross Consumption"])
    input_blank=get(record,"Blank Consumption");input_parts=get(record,"No of parts / Blank")
    cal_numerator=calculated_net
    if cal_numerator<=0:
        cal_numerator=(number(input_blank or 0)/number(input_parts)) if not empty(input_parts) and number(input_parts)!=0 else None
    results["Cal Yield"]=cal_numerator/calculated_gross if cal_numerator is not None and calculated_gross!=0 else None
    results["Actual Yield"]=net_value/gross
    result_yield=results["Actual Yield"] if results["Actual Yield"]>0 else results["Cal Yield"]
    if result_yield is None:raise CalculationError("yield_unresolved","Neither original yield formula has a valid calculated result")
    sheet_parts=get(record,"No of parts /sheet")
    area_parts=number(sheet_parts) if not empty(sheet_parts) and number(sheet_parts)>0 else number(results["Cal parts / Sheet"])
    if area_parts==0:raise CalculationError("division_by_zero","Parts per Sheet is zero")
    length,width=number(results["L"]),number(results["W"])
    diameter=number(get(record,"Pipe Dia",0) or 0);thickness=number(get(record,"T",0) or 0)
    surface=diameter*length*Decimal('3.14') if diameter else 2*((length*width)+(width*thickness)+(thickness*length))
    b_surface=diameter*length*Decimal('3.14') if diameter else length*width
    results["Total Surface Area"]=surface*Decimal('0.000001')/area_parts*result_yield
    results["B' Surface area"]=b_surface*Decimal('0.000001')/area_parts*result_yield
    results["Effective Net RM consumption"]=number(net) if not empty(net) and number(net)>0 else results["Cal RM cons"]
    return results
