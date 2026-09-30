import { useEffect, useState } from 'react'
import { downloadBlob } from '../api/client'
import * as calcApi from '../api/calculations'
import { downloadConverterJob, listConverterJobs } from '../api/pdfConverter'
import { usePageCrumb } from '../components/Layout'
import { useAuth } from '../context/AuthContext'
import { useConfirm } from '../context/ConfirmContext'
import StatusChip from '../components/StatusChip'

export default function History() {
  usePageCrumb('History')
  const { can } = useAuth()
  const confirm = useConfirm()
  const [historyType, setHistoryType] = useState('pcm')
  const [calculations, setCalculations] = useState([])
  const [converterJobs, setConverterJobs] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [logs, setLogs] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    async function load() {
      setLoading(true)
      try {
        if (historyType === 'ftg') {
          const rows = await listConverterJobs({ flow_type: 'ftg' })
          setConverterJobs(rows)
          setSelectedId(null)
          setLogs([])
        } else {
          const rows = await calcApi.listCalculations()
          setCalculations(rows)
        }
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [historyType])

  async function viewLogs(calcId) {
    setSelectedId(calcId)
    const detail = await calcApi.getCalculation(calcId)
    setLogs(detail.logs)
  }

  async function handleDeleteAll() {
    if (!(await confirm({ title: 'Delete All History', message: 'Delete all calculation history? This cannot be undone.' }))) return
    await calcApi.deleteAllCalculations()
    setCalculations([])
    setSelectedId(null)
    setLogs([])
  }

  async function downloadSaved(job) {
    const blob = await downloadConverterJob(job.id)
    downloadBlob(blob, job.output_filename || `converter_v${job.version}.xlsx`)
  }

  const canViewPcmHistory = can('calculations', 'view')
  const canViewFtgHistory = can('outputs', 'view')

  useEffect(() => {
    if (historyType === 'pcm' && !canViewPcmHistory && canViewFtgHistory) {
      setHistoryType('ftg')
    }
  }, [historyType, canViewPcmHistory, canViewFtgHistory])

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h1 className="page-title">History &amp; Logs</h1>
          <p className="page-sub">Separate history for CostOptima PCM calculations and FTG converter outputs.</p>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <select value={historyType} onChange={(event) => setHistoryType(event.target.value)}>
            {canViewPcmHistory && <option value="pcm">CostOptima PCM</option>}
            {canViewFtgHistory && <option value="ftg">FTG</option>}
          </select>
          {historyType === 'pcm' && (
            <button className="btn" disabled={!can('calculations', 'delete') || calculations.length === 0}
                    title={!can('calculations', 'delete') ? 'Not permitted for your role' : ''}
                    onClick={handleDeleteAll}>
              Delete All
            </button>
          )}
        </div>
      </div>

      {historyType === 'pcm' && (
      <div className="card" style={{ marginBottom: 18 }}>
        <h3>Calculation History</h3>
        {loading && <div className="loading">Loading...</div>}
        {!loading && (
          <div className="table-scroll">
            <table>
              <thead><tr><th>Calculation</th><th>Status</th><th>Started</th><th>Completed</th><th>Actions</th></tr></thead>
              <tbody>
                {calculations.map((c) => (
                  <tr key={c.id}>
                    <td>CALC-{String(c.id).padStart(4, '0')}</td>
                    <td><StatusChip value={c.status} /></td>
                    <td>{c.started_at ? new Date(c.started_at).toLocaleString() : '—'}</td>
                    <td>{c.completed_at ? new Date(c.completed_at).toLocaleString() : '—'}</td>
                    <td><button className="btn" onClick={() => viewLogs(c.id)}>View Logs</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      )}

      {historyType === 'ftg' && (
        <div className="card" style={{ marginBottom: 18 }}>
          <h3>FTG Converter History</h3>
          {loading && <div className="loading">Loading...</div>}
          {!loading && (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Converter</th>
                    <th>Input File</th>
                    <th>Version</th>
                    <th>Status</th>
                    <th>Created</th>
                    <th>Output</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {converterJobs.length === 0 && (
                    <tr>
                      <td colSpan={7} style={{ textAlign: 'center', padding: 24, color: 'var(--text-muted)' }}>
                        No FTG converter history yet.
                      </td>
                    </tr>
                  )}
                  {converterJobs.map((job) => (
                    <tr key={job.id}>
                      <td>{job.converter_type === 'pdf_converter' ? 'PDF Converter' : job.converter_type}</td>
                      <td>{job.source_name}</td>
                      <td>v{job.version}</td>
                      <td><StatusChip value={job.status} /></td>
                      <td>{job.created_at ? new Date(job.created_at).toLocaleString() : '-'}</td>
                      <td>{job.output_filename}</td>
                      <td><button className="btn" onClick={() => downloadSaved(job)}>Download</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {historyType === 'pcm' && selectedId && (
        <div className="card">
          <h3>Execution Logs — CALC-{String(selectedId).padStart(4, '0')}</h3>
          <div className="table-scroll">
            <table>
              <thead><tr><th>Timestamp</th><th>Level</th><th>Process</th><th>Message</th></tr></thead>
              <tbody>
                {logs.length === 0 && (
                  <tr><td colSpan={4} style={{ textAlign: 'center', padding: 24, color: 'var(--text-muted)' }}>No logs yet.</td></tr>
                )}
                {logs.map((l) => (
                  <tr key={l.id}>
                    <td>{new Date(l.created_at).toLocaleString()}</td>
                    <td><StatusChip value={l.level} /></td>
                    <td>{l.process}</td>
                    <td>{l.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  )
}
