// Presentation of current-run evidence only; never calculation arithmetic.
const INFO_CODES=new Set(['trimmed_empty_formatting','pcba_detail_end_item_differs','bom_calculation_input'])
export function diagnosticLevel(issue={}) {
 if(INFO_CODES.has(issue.code))return 'info'
 if(issue.blocking===true||issue.severity==='error')return 'error'
 return issue.severity==='info'?'info':'warning'
}
const currentRun=run=>run.metadata?.run_id||run.run_id
export function currentDiagnostics(run={}) {
 const id=currentRun(run)
 return (run.issues||[]).filter(issue=>!issue.run_id||!id||issue.run_id===id)
}
export function diagnosticsForRow(run,row) {
 const id=currentRun(run)
 const matches=issue=>issue.record_id?issue.record_id===row.record_id:issue.record_ids?.length?issue.record_ids.includes(row.record_id):
  issue.row!=null&&issue.row===(row.inputs?._bom_row??row.inputs?._row)&&issue.sheet===(row.inputs?._bom_sheet??row.inputs?._sheet)
 const seen=new Set()
 const assumptions=legacyRateAssumptions(row)
 const review=reviewedRowStatus(run,row)!==originalStatus(row)?[{code:'saved_rate_assumption_review',severity:'warning',message:assumptions.length?'Saved result used a blank master-rate assumption. Recalculate before treating this cost as complete.':'Saved End Item depends on costs requiring recalculation under current validation.',record_id:row.record_id,run_id:id}]:[]
 return [...(row.issues||[]),...(row.input_diagnostics||[]),...review,...currentDiagnostics(run).filter(matches)]
  .filter(issue=>(!issue.run_id||!id||issue.run_id===id)&&(!seen.has(JSON.stringify([issue.code,issue.sheet,issue.row,issue.field,issue.message]))&&seen.add(JSON.stringify([issue.code,issue.sheet,issue.row,issue.field,issue.message]))))
}
const numeric=value=>value!==null&&value!==undefined&&value!==''&&typeof value!=='boolean'&&Number.isFinite(Number(value))
export function legacyRateAssumptions(row={}) {
 return (row.provenance||[]).filter(item=>item.blank_numeric_zero===true&&['material_master','process_master'].includes(item.master)&&String(item.field||'').replace(/\s/g,'').toLowerCase()==='rate/uom')
}
const originalStatus=row=>row.calculation_status||({success:'CALCULATED',blocked:'BLOCKED',error:'ERROR',unverified:'UNVERIFIED',partial:'PARTIAL'}[row.status]||'BLOCKED')
export function reviewedRowStatus(run,row) {
 const status=originalStatus(row)
 if(status!=='CALCULATED')return status
 if(legacyRateAssumptions(row).length)return 'UNVERIFIED'
 if((row.inputs?._ownership_type||row.type)==='END ITEM') {
  const owner=String(row.inputs?.['End Item']||'').trim().toLowerCase()
  if((run?.rows||[]).some(child=>child!==row&&(child.inputs?._ownership_type||child.type)!=='END ITEM'&&String(child.inputs?.['End Item']||'').trim().toLowerCase()===owner&&(legacyRateAssumptions(child).length||originalStatus(child)!=='CALCULATED')))return 'UNVERIFIED'
 }
 return status
}
export function reviewedRunCounts(run) {
 if(!Array.isArray(run.rows))return null
 const counts={total:run.rows.length,calculated:0,partial:0,blocked:0,unverified:0,error:0}
 for(const row of run.rows)counts[reviewedRowStatus(run,row).toLowerCase()]+=1
 return counts
}
export function finalCostComplete(row={},run={}) {
 if(reviewedRowStatus(run,row)!=='CALCULATED')return false
 if(typeof row.final_cost_complete==='boolean')return row.final_cost_complete
 if((row.calculation_status||(row.status==='success'?'CALCULATED':row.status))!=='CALCULATED')return false
 if(row.values?._calculation_unverified===true)return false
 if((row.inputs?._ownership_type||row.type)==='END ITEM') {
  const variants=Object.keys(row.inputs?._variants||{})
  return variants.length>0&&variants.every(key=>numeric(row.values?.[key]))
 }
 const field=['Grand Total Cost/Pc','Total Cost/Pc','Total/Pc','Total','Total Cost/Veh'].find(key=>Object.hasOwn(row.values||{},key))
 return Boolean(field)&&numeric(row.values[field])
}
export function createRequestGate() {
 let version=0
 return {start(){const token=++version;return()=>token===version},invalidate(){version+=1}}
}
