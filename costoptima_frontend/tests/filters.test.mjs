import test from 'node:test'
import assert from 'node:assert/strict'
import {matchesSearch,matchesFormula,matchesResult} from '../src/services/filters.js'

test('search ignores surrounding spaces and case, including structured inputs',()=>{
 assert.equal(matchesSearch('  SUPPLIER  ',['Purchasing Supplier']),true)
 assert.equal(matchesSearch('   ',[null]),true)
 assert.equal(matchesFormula({name:'Cost',commodity:'Forging',inputs:[{field:'Weight'}]},' WEIGHT ','Forging'),true)
 assert.equal(matchesFormula({name:'Cost',commodity:'Forging'},'cost','Casting'),false)
})
test('result filters combine search, status and incomplete selection',()=>{
 const partial={displayStatus:'PARTIAL',inputs:{'Purchasing Supplier':'Acme'},diagnostics:[{message:'Missing rate'}]}
 assert.equal(matchesResult(partial,' acme ','PARTIAL',true),true)
 assert.equal(matchesResult(partial,'missing rate','INCOMPLETE',false),true)
 assert.equal(matchesResult(partial,'other','PARTIAL',true),false)
 assert.equal(matchesResult(partial,'','ERROR',false),false)
 for(const displayStatus of ['PARTIAL','BLOCKED','UNVERIFIED','ERROR']) assert.equal(matchesResult({...partial,displayStatus},'','INCOMPLETE',false),true)
 assert.equal(matchesResult({...partial,displayStatus:'CALCULATED'},'','INCOMPLETE',false),false)
})
test('clearing every result filter restores all rows',()=>{
 const rows=['CALCULATED','PARTIAL','BLOCKED','UNVERIFIED','ERROR'].map(displayStatus=>({displayStatus}))
 assert.equal(rows.filter(row=>matchesResult(row,'','',false)).length,rows.length)
 assert.equal([{name:'A',commodity:'Forging'},{name:'B',commodity:'Casting'}].filter(item=>matchesFormula(item,'','')).length,2)
})
