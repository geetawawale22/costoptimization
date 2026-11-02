import React, { useEffect, useRef, useState } from 'react'
import { usePageCrumb } from '../layouts/ProtectedLayout'
import Pagination from '../components/Pagination'
import { downloadBlob } from '../services/api'
import { convertFtgPdfs, downloadFtgJob, ftgCapabilities, listFtgJobs, previewFtgJob } from '../services/ftg'
import './FtgConverter.css'

// Original converter navigation order and PDF form/history interactions are retained.
const tabs = [
  { id: 'pdf', label: 'PDF to Excel Converter' },
  { id: 'input-rpa', label: 'FTG Fixture - Input to RPA' },
  { id: 'ppt-input', label: 'FTG Fixture - PPT to Input Excel' },
]
function stateLabel(value) { return value === 'partial' ? 'Needs review' : value === 'completed' ? 'Completed' : 'Failed' }
function bytes(value) { return value >= 1048576 ? `${(value / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(value / 1024))} KB` }

export default function FtgConverter() {
  usePageCrumb('FTG Converter')
  const [tab, setTab] = useState('pdf')
  const [capabilities, setCapabilities] = useState(null)
  const [files, setFiles] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)
  const [history, setHistory] = useState({ items: [], total: 0 })
  const [historyLoading, setHistoryLoading] = useState(false)
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const [preview, setPreview] = useState(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [downloading, setDownloading] = useState('')
  const [previewJob, setPreviewJob] = useState(null)
  const fileInput = useRef(null)
  const pageSize = 20

  async function refreshJobs() {
    setHistoryLoading(true)
    try { setHistory(await listFtgJobs({ page, page_size: pageSize, q: query })) }
    catch (failure) { setError(failure.message || 'Unable to load converter history.') }
    finally { setHistoryLoading(false) }
  }
  useEffect(() => { ftgCapabilities().then(setCapabilities).catch(e => setError(e.message)) }, [])
  useEffect(() => { refreshJobs() }, [page, query])

  function selectFiles(selected) {
    setError(''); setResult(null)
    const next = Array.from(selected || [])
    if (next.some(file => !file.name.toLowerCase().endsWith('.pdf'))) {
      setFiles([]); setError('Please select PDF files only.'); return
    }
    if (capabilities && (next.length > capabilities.max_files || next.some(file => file.size > capabilities.max_file_bytes) || next.reduce((n, file) => n + file.size, 0) > capabilities.max_total_bytes)) {
      setFiles([]); setError('Select up to 16 PDFs, 20 MB per file and 64 MB in total.'); return
    }
    if (new Set(next.map(file => file.name.toLowerCase())).size !== next.length) {
      setFiles([]); setError('Each PDF must have a distinct filename.'); return
    }
    setFiles(next)
  }
  async function convert(event) {
    event.preventDefault()
    if (busy || !files.length) return
    setBusy(true); setError(''); setResult(null)
    try {
      const converted = await convertFtgPdfs(files)
      setResult(converted)
      await refreshJobs()
    } catch (failure) {
      const details = failure.details?.files?.map(file => `${file.filename}: ${file.reason}`).join(' ')
      setError(details || failure.message || 'Unable to convert these PDFs.')
    } finally { setBusy(false) }
  }
  async function downloadSaved(job) {
    setDownloading(job.id)
    try { downloadBlob(await downloadFtgJob(job.id), job.output_filename || 'Compiled_MSS_Report.xlsx') }
    catch (failure) { setError(failure.message || 'Unable to download converter output.') }
    finally { setDownloading('') }
  }
  async function openPreview(job, sheet, start = 1) {
    setPreviewJob(job); setPreviewBusy(true); setError('')
    try { setPreview(await previewFtgJob(job.id, { sheet, start, rows: 20, columns: 16 })) }
    catch (failure) { setError(failure.message || 'Unable to preview this workbook.') }
    finally { setPreviewBusy(false) }
  }
  const unavailable = capabilities?.tabs?.find(item => item.id === tab)

  return <div className="ftg-page">
    <div className="ftg-heading"><div><h1 className="page-title">FTG Converter</h1><p className="page-sub">Convert tooling specification documents into an editable Excel workbook.</p></div></div>
    <div className="ftg-tabs" role="tablist" aria-label="Converter operations">
      {tabs.map(item => <button type="button" role="tab" key={item.id} id={`ftg-tab-${item.id}`} aria-selected={tab === item.id} aria-controls={`ftg-panel-${item.id}`} className={tab === item.id ? 'active' : ''} onClick={() => { setTab(item.id); setError('') }}>{item.label}</button>)}
    </div>
    {tab !== 'pdf' ? <section className="card ftg-unavailable" role="tabpanel" id={`ftg-panel-${tab}`} aria-labelledby={`ftg-tab-${tab}`}>
      <h2>{tabs.find(item => item.id === tab)?.label}</h2>
      <p>{unavailable?.reason || 'The original application has no configured conversion logic for this operation. Its conversion source and template are required before files can be processed.'}</p>
      <p className="muted">PDF to Excel conversion is available in the first tab.</p>
    </section> : <section role="tabpanel" id="ftg-panel-pdf" aria-labelledby="ftg-tab-pdf">
      <div className="pdf-converter-card">
        <section className="pdf-converter-intro">
          <p className="pdf-converter-eyebrow">PDF TO EXCEL</p>
          <h2>Your PDFs. One Excel workbook.</h2>
          <p>Select your mould specification PDFs and convert them into the original MSS Excel format, including material details, dimensions, images and selection lists.</p>
        </section>
        <form onSubmit={convert} aria-busy={busy} className="pdf-converter-form">
          <label className="pdf-converter-file-label" htmlFor="ftg-pdf-files">PDF documents</label>
          <div className="ftg-drop" onDragOver={event => { event.preventDefault() }} onDrop={event => { event.preventDefault(); if (!busy) selectFiles(event.dataTransfer.files) }}>
            <input ref={fileInput} id="ftg-pdf-files" type="file" accept=".pdf,application/pdf" multiple disabled={busy} onChange={event => selectFiles(event.target.files)} aria-describedby="ftg-file-hint" />
            <p>Choose PDFs or drag them here</p>
          </div>
          <p id="ftg-file-hint" className="pdf-converter-hint">Up to 16 files. 20 MB per file, 64 MB per batch and 40 pages per document. Scanned PDFs need readable text.</p>
          {!!files.length && <ul className="pdf-converter-files">{files.map(file => <li key={file.name}><span>{file.name}</span><small>{bytes(file.size)}</small></li>)}</ul>}
          <button className="btn primary pdf-converter-submit" type="submit" disabled={busy || !files.length} aria-busy={busy}>{busy ? 'Converting...' : 'Convert to Excel'}</button>
          <div aria-live="polite" role="status">
            {busy && <p className="pdf-converter-hint">Reading your documents and preparing the workbook. This may take a few minutes.</p>}
            {result && <div className="pdf-converter-success"><p>{result.converted_files.length} PDF{result.converted_files.length === 1 ? '' : 's'} converted. Your workbook is ready.</p><button type="button" className="btn" onClick={() => openPreview(result)}>Preview Excel</button> <button type="button" className="btn primary" disabled={downloading===result.id} aria-busy={downloading===result.id} onClick={() => downloadSaved(result)}>{downloading===result.id?'Preparing download...':'Download Excel'}</button>
              {!!result.failed_files.length && <ul>{result.failed_files.map(file => <li key={file.filename}>{file.filename}: {file.reason}</li>)}</ul>}
            </div>}
          </div>
        </form>
      </div>
      <section className="card ftg-history">
        <div className="ftg-history-head"><h2>Converted Files</h2><label>Search files<input value={query} onChange={event => { setQuery(event.target.value); setPage(1) }} placeholder="Search input filename" /></label></div>
        {historyLoading ? <div className="loading-inline" role="status">Loading converted files...</div> : <div className="table-scroll"><table>
          <thead><tr><th>Input file</th><th>Version</th><th>Status</th><th>Created</th><th>Output</th><th>Actions</th></tr></thead>
          <tbody>{!history.items.length && <tr><td colSpan="6" className="ftg-empty">No converted files yet.</td></tr>}
            {history.items.map(job => <tr key={job.id}><td>{job.source_name}</td><td>v{job.version}</td><td><span className={`chip ${job.status === 'completed' ? 'green' : 'amber'}`}>{stateLabel(job.status)}</span></td><td>{new Date(job.created_at).toLocaleString()}</td><td>{job.output_filename}</td><td><div className="ftg-actions"><button className="btn" onClick={() => openPreview(job)}>Preview</button><button className="btn" disabled={downloading===job.id} aria-busy={downloading===job.id} onClick={() => downloadSaved(job)}>{downloading===job.id?'Preparing...':'Download'}</button></div></td></tr>)}
          </tbody>
        </table></div>}
        <Pagination page={page} pageSize={pageSize} total={history.total} totalPages={Math.ceil(history.total/pageSize)} onPageChange={next=>setPage(next)}/>
      </section>
    </section>}
    {error && <p className="pdf-converter-error" role="alert">{error}</p>}
    {previewJob && <section className="card ftg-preview" aria-label="Converted workbook preview">
      <div className="ftg-history-head"><h2>Excel Preview</h2><button className="btn" onClick={() => { setPreviewJob(null); setPreview(null) }}>Close preview</button></div>
      {previewBusy && <div className="loading-inline" role="status">Loading worksheet...</div>}
      {preview && <><label>Worksheet <select value={preview.sheet} onChange={event => openPreview(previewJob, event.target.value)}>{preview.sheets.map(sheet => <option key={sheet.name} value={sheet.name}>{sheet.name}</option>)}</select></label>
        <div className="table-scroll"><table><tbody>{preview.rows.map((row, index) => <tr key={index}>{row.map((value, column) => <td key={column}>{value === null || value === undefined ? '' : String(value)}</td>)}</tr>)}</tbody></table></div>
        <div className="muted">Rows {preview.start}-{preview.start + preview.rows.length - 1} of {preview.sheets.find(sheet => sheet.name === preview.sheet)?.rows || 0}</div><Pagination page={Math.ceil(preview.start/20)} pageSize={20} total={preview.sheets.find(sheet => sheet.name === preview.sheet)?.rows || 0} totalPages={Math.ceil((preview.sheets.find(sheet => sheet.name === preview.sheet)?.rows || 0)/20)} onPageChange={next=>openPreview(previewJob,preview.sheet,(next-1)*20+1)}/>
        <p className="muted">Preview shows saved cell values. Excel retains the original layout, images and selection lists.</p>
      </>}
    </section>}
  </div>
}
