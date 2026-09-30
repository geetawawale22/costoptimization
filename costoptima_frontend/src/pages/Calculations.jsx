import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import * as calcApi from '../api/calculations'
import { downloadBlob } from '../api/client'
import { usePageCrumb } from '../components/Layout'
import { useAuth } from '../context/AuthContext'
import { useConfirm } from '../context/ConfirmContext'
import { useToast } from '../context/ToastContext'
import StatusChip from '../components/StatusChip'

export default function Calculations() {
  usePageCrumb('Calculations')
  const { can } = useAuth()
  const confirm = useConfirm()
  const showToast = useToast()
  const [calculations, setCalculations] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  function loadCalculations() {
    setLoading(true)
    calcApi
      .listCalculations()
      .then(setCalculations)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    loadCalculations()
  }, [])

  async function handleDeleteAll() {
    if (!(await confirm({ title: 'Delete All Calculations', message: 'Delete all calculations? This cannot be undone.' }))) return
    await calcApi.deleteAllCalculations()
    loadCalculations()
  }

  async function handleDownloadAll() {
    try {
      const blob = await calcApi.exportAllCalculations()
      downloadBlob(blob, 'calculations.xlsx')
    } catch (err) {
      showToast(err.message)
    }
  }

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h1 className="page-title">Calculations</h1>
          <p className="page-sub">Every calculation runs immediately on demand — no queued/running state to wait through.</p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn" disabled={calculations.length === 0} onClick={handleDownloadAll}>
            ⬇ Download
          </button>
          <button className="btn" disabled={!can('calculations', 'delete') || calculations.length === 0}
                  title={!can('calculations', 'delete') ? 'Not permitted for your role' : ''}
                  onClick={handleDeleteAll}>
            Delete All
          </button>
        <Link
          to="/new-calculation"
          className="btn primary"
          style={!can('calculations', 'create') ? { pointerEvents: 'none', opacity: 0.45 } : {}}
          title={!can('calculations', 'create') ? 'Not permitted for your role' : ''}
        >
          ＋ New Calculation
        </Link>
        </div>
      </div>

      <div className="card">
        {loading && <div className="loading">Loading calculations...</div>}
        {error && <p className="validation-err">{error}</p>}
        {!loading && !error && (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Calc ID</th>
                  <th>BOM</th>
                  <th>Status</th>
                  <th>Started</th>
                  <th>Completed</th>
                  <th>Commodities</th>
                </tr>
              </thead>
              <tbody>
                {calculations.length === 0 && (
                  <tr>
                    <td colSpan={6} style={{ textAlign: 'center', padding: 24, color: 'var(--text-muted)' }}>
                      No calculations yet — start one from "New Calculation".
                    </td>
                  </tr>
                )}
                {calculations.map((c) => (
                  <tr key={c.id}>
                    <td>CALC-{String(c.id).padStart(4, '0')}</td>
                    <td>BOM #{c.bom_id}</td>
                    <td><StatusChip value={c.status} /></td>
                    <td>{c.started_at ? new Date(c.started_at).toLocaleString() : '—'}</td>
                    <td>{c.completed_at ? new Date(c.completed_at).toLocaleString() : '—'}</td>
                    <td>{c.commodity_results.length}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  )
}
