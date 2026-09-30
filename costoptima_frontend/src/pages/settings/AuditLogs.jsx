import { useEffect, useState } from 'react'
import * as miscApi from '../../api/misc'
import Pagination from '../../components/Pagination'
import { usePageCrumb } from '../../components/Layout'
import { useToast } from '../../context/ToastContext'
import StatusChip from '../../components/StatusChip'

const ACTIONS = ['created', 'updated', 'deleted']

export default function AuditLogs() {
  usePageCrumb('Settings / <b>Audit Logs</b>')
  const showToast = useToast()
  const [meta, setMeta] = useState({ items: [], total: 0, page: 1, page_size: 50, total_pages: 1, entities: [] })
  const [loading, setLoading] = useState(true)
  const [q, setQ] = useState('')
  const [entity, setEntity] = useState('')
  const [action, setAction] = useState('')
  const [page, setPage] = useState(1)

  async function load(nextPage = page, overrides = {}) {
    setLoading(true)
    try {
      const res = await miscApi.auditLog({ q, entity, action, ...overrides, page: nextPage, pageSize: 50 })
      setMeta(res)
      setPage(nextPage)
    } catch (err) {
      showToast(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load(1) }, []) // eslint-disable-line react-hooks/exhaustive-deps

  function applyFilters() { load(1) }
  function resetFilters() {
    setQ('')
    setEntity('')
    setAction('')
    load(1, { q: '', entity: '', action: '' })
  }
  function changeEntity(value) {
    setEntity(value)
    load(1, { entity: value })
  }
  function changeAction(value) {
    setAction(value)
    load(1, { action: value })
  }

  return (
    <div className="audit-page">
      <div className="settings-page-header">
        <h1 className="page-title">Audit Logs</h1>
        <p className="page-sub">Application-wide activity trail — every create, update, and delete across users, roles, permissions, workbooks, commodities, calculations, and uploads.</p>
      </div>

      <div className="card">
        <div className="toolbar config-toolbar">
          <div className="toolbar-left config-filterbar">
            <input placeholder="Search details, entity, or actor..." value={q} onChange={(e) => setQ(e.target.value)} />
            <select value={entity} onChange={(e) => changeEntity(e.target.value)}>
              <option value="">All entities</option>
              {(meta.entities || []).map((ent) => <option key={ent} value={ent}>{ent.replaceAll('_', ' ')}</option>)}
            </select>
            <select value={action} onChange={(e) => changeAction(e.target.value)}>
              <option value="">All actions</option>
              {ACTIONS.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
            <button className="btn" onClick={applyFilters} disabled={loading}>{loading ? 'Loading...' : 'Search'}</button>
            <button className="btn" onClick={resetFilters}>Reset</button>
          </div>
        </div>

        {loading ? (
          <div className="loading">Loading audit logs...</div>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Entity</th>
                  <th>Action</th>
                  <th>Details</th>
                  <th>Status</th>
                  <th>Actor</th>
                  <th>Timestamp</th>
                </tr>
              </thead>
              <tbody>
                {meta.items.length === 0 && (
                  <tr><td colSpan={6} className="empty-row">No entries match the current filters.</td></tr>
                )}
                {meta.items.map((r, i) => (
                  <tr key={`${r.entity}-${r.created_at}-${i}`}>
                    <td className="resource-cell">{r.entity?.replaceAll('_', ' ') || '—'}</td>
                    <td><span className={`action-badge ${r.action}`}>{r.action || 'created'}</span></td>
                    <td>{r.details || '—'}</td>
                    <td>{r.status && r.status !== '—' ? <StatusChip value={r.status} /> : '—'}</td>
                    <td>{r.actor || '—'}</td>
                    <td>{r.created_at ? new Date(r.created_at).toLocaleString() : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Pagination page={meta.page} pageSize={meta.page_size} total={meta.total} totalPages={meta.total_pages} onPageChange={load} />
      </div>
    </div>
  )
}
