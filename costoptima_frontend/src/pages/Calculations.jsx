import React, {useEffect, useState} from 'react'
import {Link} from 'react-router-dom'
import {request} from '../services/api'
import {useAuth} from '../auth/AuthContext'
import {usePageCrumb} from '../layouts/ProtectedLayout'
import {CALCULATION_BASE as BASE, dateBoundary, downloadCalculation, localDate, runCounts, safeError, statusLabel} from '../services/calculations'
import Pagination from '../components/Pagination'
import StatusChip from '../components/StatusChip'
const EMPTY={q:'',status:'',date_from:'',date_to:'',sort:'created_desc'}
function CalculationActionIcon({name}){
 const paths={
  results:<><path d="M2 10s2.9-5 8-5 8 5 8 5-2.9 5-8 5-8-5-8-5Z"/><circle cx="10" cy="10" r="2.1"/></>,
  preview:<><path d="M5 2.8h6l4 4v10.4H5z"/><path d="M11 2.8v4h4M7.5 10h5M7.5 12.8h5M7.5 15.5h5"/></>,
  download:<><path d="M10 2.8v9.1m-3.4-3.4 3.4 3.4 3.4-3.4"/><path d="M3.5 13.8v3.4h13v-3.4"/></>,
  issues:<><path d="m10 2.8 7.3 13H2.7z"/><path d="M10 7.4v4.2m0 2.3h.01"/></>,
 }
 return <svg className="calculation-action-icon" aria-hidden="true" viewBox="0 0 20 20">{paths[name]}</svg>
}
export default function Calculations(){
 usePageCrumb('Workspace / <b>Calculations</b>')
 const {can,user}=useAuth(),[filters,setFilters]=useState(EMPTY),[applied,setApplied]=useState(EMPTY),[page,setPage]=useState(1),[history,setHistory]=useState(null),[busy,setBusy]=useState(true),[error,setError]=useState(''),[downloading,setDownloading]=useState('')
 async function load(nextPage=page,values=applied){setBusy(true);setError('');try{const data=await request(BASE+'/runs',{params:{...values,date_from:dateBoundary(values.date_from),date_to:dateBoundary(values.date_to,true),page:nextPage,page_size:20}});setHistory(data);setPage(nextPage);setApplied(values)}catch(e){setError(safeError(e))}finally{setBusy(false)}}
 useEffect(()=>{load(1,EMPTY)},[])
 function update(key,value){setFilters(current=>({...current,[key]:value}))}
 async function download(item){setDownloading(item.run_id);setError('');try{await downloadCalculation(item.run_id,item.input_name)}catch(e){setError(safeError(e))}finally{setDownloading('')}}
 return <div className="page-stack calculations-history"><header className="page-heading"><div><h1 className="page-title">Calculations</h1><p className="page-sub">Review previous calculations, explore results and download your Excel reports.</p></div>{can('boms','create')&&<Link className="btn primary" to="/calculations/new"><span aria-hidden="true">+</span> New Calculation</Link>}</header>
  {error&&<p className="notice error" role="alert">{error}</p>}
  <section className="card"><form className="filter-bar" onSubmit={e=>{e.preventDefault();load(1,filters)}}><label className="grow">Search<input aria-label="Search calculations" placeholder="Calculation ID, filename, project or part" value={filters.q} onChange={e=>update('q',e.target.value)}/></label><label>Status<select value={filters.status} onChange={e=>update('status',e.target.value)}><option value="">All statuses</option><option value="calculated">Calculated</option><option value="partial">Partial</option><option value="unverified">Needs review</option></select></label><label>From<input type="date" value={filters.date_from} onChange={e=>update('date_from',e.target.value)}/></label><label>To<input type="date" min={filters.date_from||undefined} value={filters.date_to} onChange={e=>update('date_to',e.target.value)}/></label><label>Sort by<select value={filters.sort} onChange={e=>update('sort',e.target.value)}><option value="created_desc">Newest first</option><option value="created_asc">Oldest first</option><option value="name_asc">Filename A-Z</option><option value="name_desc">Filename Z-A</option><option value="status_asc">Status</option></select></label><div className="action-row"><button className="btn" disabled={busy}>Apply</button><button type="button" className="btn secondary" disabled={busy} onClick={()=>{setFilters(EMPTY);load(1,EMPTY)}}>Reset</button></div></form>
  <div className="list-header-row"><span className="list-meta">{history?.total??0} calculations</span><button className="text-button" disabled={busy} onClick={()=>load()}>Refresh</button></div>
  {busy?<div className="loading" role="status">Loading calculations...</div>:<div className="table-scroll"><table className="business-table history-table"><thead><tr><th>Calculation ID</th><th>Input / project</th><th>Uploaded</th><th>Uploaded by</th><th>Total</th><th>Calculated</th><th>Partial</th><th>Incomplete</th><th>Status</th><th>Actions</th></tr></thead><tbody>
   {history?.items.map(item=>{const counts=runCounts(item);return <tr key={item.run_id}><td><Link className="table-link" to={`/calculations/${item.run_id}`} title={item.run_id}>{item.run_id.slice(0,12)}</Link></td><td className="filename-cell"><strong>{item.input_name}</strong><small className="cell-sub">{item.project||item.part_name||'Not specified'}</small></td><td className="nowrap">{localDate(item.created_at)}</td><td>{item.uploaded_by||user?.name||'Your account'}</td><td>{counts.total}</td><td>{counts.calculated}</td><td>{counts.partial}</td><td>{counts.blocked+counts.unverified+counts.error}</td><td><StatusChip value={statusLabel(item.status)}/></td><td><div className="table-actions calculation-row-actions"><Link className="row-action-btn calculation-action" to={`/calculations/${item.run_id}`}><CalculationActionIcon name="results"/><span>View Results</span></Link><Link className="row-action-btn calculation-action" to={`/calculations/${item.run_id}?tab=preview`}><CalculationActionIcon name="preview"/><span>Preview Excel</span></Link><button className="row-action-btn calculation-action" disabled={downloading===item.run_id} aria-busy={downloading===item.run_id} onClick={()=>download(item)}><CalculationActionIcon name="download"/><span>{downloading===item.run_id?'Downloading...':'Download Excel'}</span></button><Link className="row-action-btn calculation-action" to={`/calculations/${item.run_id}?tab=issues`}><CalculationActionIcon name="issues"/><span>View Issues</span></Link></div></td></tr>})}
   {!history?.items.length&&<tr><td colSpan={10} className="empty-row"><div className="empty-state-box"><strong>No calculations found</strong><span>{applied.q||applied.status||applied.date_from||applied.date_to?'Try different filters.':'Start a new calculation to review your costs and create an Excel report.'}</span>{can('boms','create')&&<Link className="btn primary" to="/calculations/new">+ New Calculation</Link>}</div></td></tr>}
  </tbody></table></div>}
  <Pagination page={page} pageSize={20} total={history?.total||0} totalPages={Math.ceil((history?.total||0)/20)} onPageChange={next=>load(next)}/>
 </section></div>
}
