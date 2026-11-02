import test from 'node:test'
import assert from 'node:assert/strict'
import {diagnosticLevel,diagnosticsForRow,currentDiagnostics,finalCostComplete,createRequestGate,reviewedRowStatus,reviewedRunCounts} from '../src/services/calculationResults.js'
test('informational source diagnostics are separate from required failures',()=>{
 assert.equal(diagnosticLevel({code:'pcba_detail_end_item_differs',severity:'warning'}),'info')
 assert.equal(diagnosticLevel({code:'trimmed_empty_formatting',severity:'warning'}),'info')
 assert.equal(diagnosticLevel({code:'missing_detail_match',severity:'error'}),'error')
 assert.equal(diagnosticLevel({code:'missing_master',blocking:true}),'error')
})
test('diagnostics cannot leak across run or row identities',()=>{
 const run={metadata:{run_id:'new'},issues:[{code:'old',run_id:'old',record_id:'a'},{code:'b',run_id:'new',record_id:'b'},{code:'own',run_id:'new',record_id:'a'}]}
 const row={record_id:'a',inputs:{_sheet:'BOM',_row:3}}
 assert.deepEqual(diagnosticsForRow(run,row).map(i=>i.code),['own'])
 assert.equal(currentDiagnostics(run).length,2)
})
test('legacy messages attach only to the exact source row and sheet',()=>{
 const run={issues:[{code:'x',sheet:'BOM',row:3},{code:'y',sheet:'Soft',row:3}]}
 assert.deepEqual(diagnosticsForRow(run,{record_id:'a',inputs:{_sheet:'BOM',_row:3}}).map(i=>i.code),['x'])
})
test('unknown, partial and unverified totals are not called complete',()=>{
 assert.equal(finalCostComplete({status:'success',values:{'Basic Cost/Pc':2}}),false)
 assert.equal(finalCostComplete({status:'success',values:{'Grand Total Cost/Pc':0}}),true)
 assert.equal(finalCostComplete({status:'success',values:{'Grand Total Cost/Pc':null,'Total Cost/Pc':9}}),false)
 assert.equal(finalCostComplete({status:'success',calculation_status:'UNVERIFIED',values:{'Grand Total Cost/Pc':9}}),false)
 assert.equal(finalCostComplete({status:'blocked',values:{'Grand Total Cost/Pc':2}}),false)
 assert.equal(finalCostComplete({status:'success',values:{'Grand Total Cost/Pc':2,_calculation_unverified:true}}),false)
 assert.equal(finalCostComplete({status:'success',type:'END ITEM',inputs:{_variants:{V:1}},values:{V:null}}),false)
})
test('late request responses are invalidated by a new request or route cleanup',async()=>{
 const gate=createRequestGate();const old=gate.start();const latest=gate.start()
 await Promise.resolve();assert.equal(old(),false);assert.equal(latest(),true)
 gate.invalidate();assert.equal(latest(),false)
})

test('immutable old zero-filled rate results are visibly unverified, including their owner total',()=>{
 const component={record_id:'part',status:'success',calculation_status:'CALCULATED',type:'MAKE',inputs:{'End Item':'Owner'},values:{'Grand Total Cost/Pc':9},provenance:[{master:'material_master',field:'Rate/UOM',blank_numeric_zero:true}]}
 const root={record_id:'owner',status:'success',calculation_status:'CALCULATED',type:'END ITEM',inputs:{'End Item':'Owner',_variants:{V:1}},values:{V:9}}
 const independent={record_id:'other',status:'success',calculation_status:'CALCULATED',type:'MAKE',inputs:{'End Item':'Other'},values:{'Grand Total Cost/Pc':2}}
 const run={rows:[component,root,independent]}
 assert.equal(reviewedRowStatus(run,component),'UNVERIFIED')
 assert.equal(reviewedRowStatus(run,root),'UNVERIFIED')
 assert.equal(reviewedRowStatus(run,independent),'CALCULATED')
 assert.equal(finalCostComplete(root,run),false)
 assert.deepEqual(reviewedRunCounts(run),{total:3,calculated:1,partial:0,blocked:0,unverified:2,error:0})
 assert.equal(component.values['Grand Total Cost/Pc'],9)
 assert.equal(component.calculation_status,'CALCULATED')
 assert.ok(diagnosticsForRow(run,component).some(i=>i.code==='saved_rate_assumption_review'))
})
