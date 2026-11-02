"""One guarded PCM execution workflow: parse, masters, dispatch, merge."""
from decimal import localcontext
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from time import perf_counter
from .common import CalculationError,get,normalized
from .context import CalculationContext,CalculationRun,RowResult,costing_record,validate_final_values
from .dispatcher import dispatch
from .input import parse_workbook
from .masters import MasterLookupError
from .merging.dependencies import plan
from .merging.rollup import consolidate


def _retain_output_trace(values, provenance):
    """Preserve resolved line costs/rates; output never re-queries masters."""
    for kind, field in [('consumable_line','_consumable_lines'),('process_line','_process_costs')]:
        lines=[dict(item) for item in provenance if item.get('kind')==kind]
        if lines:values[field]=lines
    rates={};conflicts=set()
    for item in provenance:
        if item.get('kind')!='master_rate':continue
        name=item['name'];rate=item['rate']
        if name in rates and rates[name]!=rate:conflicts.add(name)
        rates[name]=rate
    if rates:values['_master_rates']={name:rate for name,rate in rates.items() if name not in conflicts}
    if conflicts:values['_master_rate_conflicts']=sorted(conflicts)


def _bind_diagnostics(run):
    """Bind server-derived locations/run identity; retain original messages."""
    by_location = {}
    for row in run.rows:
        locations = {(row.inputs.get('_sheet'), row.inputs.get('_row'))}
        locations.update((detail.get('_sheet'), detail.get('_row')) for detail in row.inputs.get('_details', []))
        locations.update((detail.get('_sheet'), detail.get('_row')) for detail in row.inputs.get('_pcba_components', []))
        for location in locations: by_location.setdefault(location, []).append(row)
    run_id = run.metadata['run_id']
    for issue in run.issues:
        issue['severity'] = issue.get('severity', 'error')
        issue['blocking'] = issue['severity'] == 'error'
        issue['run_id'] = run_id
        owners = by_location.get((issue.get('sheet'), issue.get('row')), [])
        issue['record_ids'] = sorted({row.record_id for row in owners})
        if len(owners) == 1: issue['record_id'] = owners[0].record_id
        for row in owners: row.input_diagnostics.append(dict(issue))
    for row in [*run.rows, *run.producer_rows]:
        for issue in row.issues:
            issue.setdefault('severity', 'error')
            issue['blocking'] = issue['severity'] == 'error'
            issue['run_id'] = run_id
            issue['record_id'] = row.record_id


def execute(source,master,*,development=False):
    if development is not True:raise CalculationError('development_guard','Costing is disabled; explicit development execution is required')
    report_time=datetime.now(timezone.utc)
    report_datetime=report_time.astimezone(timezone(timedelta(hours=5,minutes=30))).replace(tzinfo=None)
    report_date=report_datetime.date()
    started=perf_counter()
    parsed=parse_workbook(source,partial_output=True);parsed_at=perf_counter();context=CalculationContext(master,parsed.records);by_id={r['_record_id']:r for r in parsed.records}
    run=CalculationRun([],issues=list(parsed.issues),metadata={**parsed.metadata,'run_id':str(uuid4()),'development_only':True,'report_generated_at':report_time.isoformat(),'report_date':report_date.isoformat(),
        'legacy_numerically_verified':False,'coverage':'partial implementation; read per-record and branch coverage before use'})
    for record in parsed.records:
        record['_report_date']=report_date
        record['_report_datetime']=report_datetime
    try:execution_order,edges=plan(parsed.records,isolate=True)
    except CalculationError as exc:
        run.issues.append(exc.as_dict());execution_order=list(by_id);edges={}
        for record in by_id.values():record['_dependency_blocked']=True
    for record in parsed.records:
        run.issues.extend(record.get('_dependency_errors',[]))
    run.metadata['execution_order']=execution_order;run.metadata['dependencies']={k:sorted(v) for k,v in edges.items()}
    planned_at=perf_counter()
    input_errors={}
    for issue in parsed.issues:
        if issue.get('severity')=='error':input_errors.setdefault((issue.get('sheet'),issue.get('row')),[]).append(issue)
    with localcontext() as decimal_context:
        decimal_context.prec=34
        for record_id in execution_order:
            record=by_id[record_id];c=str(get(record,'Commodity') or '').upper();t=str(get(record,'Type') or '').upper()
            result=RowResult(record_id,c,t,record);context.provenance=[];context.begin_record(record_id);trace_start=len(getattr(master,'trace',[]))
            try:
                if record.get('_dependency_blocked'):
                    result.issues.extend(record.get('_dependency_errors',[]))
                    raise CalculationError('dependency_plan_blocked',record.get('_input_blocked_reason') or 'Dependency graph could not be resolved')
                if (record.get('_sheet'),record.get('_row')) in input_errors:
                    result.issues.extend(input_errors[(record.get('_sheet'),record.get('_row'))])
                    raise CalculationError('input_validation_failed','Input validation errors affect this record')
                branch_type='SPECIAL PROCESS' if str(record.get('_ownership_type',t)).upper()=='SPECIAL PROCESS' else t
                branch=dispatch(c,branch_type);result.branch=branch.module
                calculation_inputs=costing_record(record)
                if c and str(record.get('_ownership_type',t)).upper()=='BUY NON STD':
                    producer=RowResult(record_id+':producer',c,'BUY NON STD PRODUCER',calculation_inputs,branch=branch.module,dependency_only=True)
                    try:
                        producer.values=branch.calculate(calculation_inputs,context)
                        validate_final_values(record,producer.values)
                        producer.status='success'
                    except (CalculationError,MasterLookupError) as exc:
                        producer.status='blocked';producer.values=context.partial_values(exc.details.get('partial_values',{})) if isinstance(exc,CalculationError) else context.partial_values();producer.issues=[exc.as_dict() if isinstance(exc,CalculationError) else exc.as_dict()]
                    except Exception as exc:
                        producer.status='error';producer.values=context.partial_values();producer.issues=[{'code':'calculation_error','message':str(exc),'error_type':type(exc).__name__}]
                    _retain_output_trace(producer.values,context.provenance)
                    producer.provenance=list(context.provenance)+list(getattr(master,"trace",[])[trace_start:])
                    context.result_records.append(producer);run.producer_rows.append(producer)
                    context.begin_record(record_id);context.provenance=[]
                    from .types.buy_non_std import calculate as purchasing
                    result.values=purchasing(record,context)
                else:result.values=branch.calculate(calculation_inputs,context)
                validate_final_values(record,result.values)
                result.status='success';result.provenance=list(context.provenance)
            except CalculationError as exc:
                result.status='blocked';result.issues.append(exc.as_dict());result.values=context.partial_values(exc.details.get('partial_values',{}))
            except MasterLookupError as exc:
                result.status='blocked';result.values=context.partial_values();result.issues.append(exc.as_dict())
            except Exception as exc:
                result.status='error';result.values=context.partial_values();result.issues.append({'code':'calculation_error','message':str(exc),'error_type':type(exc).__name__})
            # Output receives same-run calculated line costs, never uploaded prices.
            _retain_output_trace(result.values,context.provenance)
            result.provenance=list(context.provenance)+list(getattr(master,"trace",[])[trace_start:])
            run.rows.append(result);context.result_records.append(result)
            for producer in context.result_records:
                if producer.dependency_only and all(existing.record_id!=producer.record_id for existing in run.producer_rows):run.producer_rows.append(producer)
    calculated_at=perf_counter()
    try:run.summaries=consolidate(run.rows)
    except Exception as exc:
        run.issues.append(exc.as_dict() if isinstance(exc,CalculationError) else {'code':'summary_error','message':str(exc),'error_type':type(exc).__name__});run.summaries={"Analysis":[],"incomplete":True}
    try:
        from .merging.seat import consolidate_seat
        run.summaries['seat']=consolidate_seat(run.rows,master,run.metadata)
    except Exception as exc:
        detail=exc.as_dict() if isinstance(exc,CalculationError) else {'code':getattr(exc,'code','seat_summary_error'),'message':str(exc)}
        run.issues.append(detail);run.summaries['seat']={'enabled':False,'blocked':True,'issues':[detail]}
    run.metadata['phase_seconds']={'parse_and_normalize':parsed_at-started,'dependency_planning':planned_at-parsed_at,'calculate_and_resolve_masters':calculated_at-planned_at,'merge_and_aggregate':perf_counter()-calculated_at}
    _bind_diagnostics(run)
    run.metadata['calculation_counts']=run.counts
    run.metadata['calculation_status']=run.status
    run.metadata['successful_records']=sum(r.status=='success' for r in run.rows)
    run.metadata['blocked_records']=sum(r.status!='success' for r in run.rows)
    return run
