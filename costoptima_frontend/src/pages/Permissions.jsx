import { useEffect, useState } from 'react'
import * as miscApi from '../../api/misc'
import { usePageCrumb } from '../../components/Layout'
import { useAuth } from '../../context/AuthContext'
import { useToast } from '../../context/ToastContext'

/**
 * Manages the raw Permission catalogue itself (one row per resource+action grant),
 * separate from "Roles & Permissions" which only assigns existing permissions to a
 * role. Use this page first to define a new permission (e.g. protecting a newly
 * added page/model), then go assign it to roles on the Roles & Permissions page.
 */
export default function Permissions() {
  usePageCrumb('Settings / <b>Permissions</b>')
  const { can } = useAuth()
  const showToast = useToast()

  const [permissions, setPermissions] = useState([])
  const [loading, setLoading] = useState(true)
  const [editingId, setEditingId] = useState(null)
  const [form, setForm] = useState({ resource: '', action: 'view', description: '' })

  const canCreate = can('roles', 'create')
  const canUpdate = can('roles', 'update')
  const canDelete = can('roles', 'delete')

  function refresh() {
    setLoading(true)
    miscApi.listPermissions().then(setPermissions).finally(() => setLoading(false))
  }

  useEffect(refresh, [])

  function startEdit(p) {
    setEditingId(p.id)
    setForm({ resource: p.resource, action: p.action, description: p.description || '' })
  }

  function startCreate() {
    setEditingId('new')
    setForm({ resource: '', action: 'view', description: '' })
  }

  async function handleSave() {
    if (!form.resource.trim()) {
      showToast('Resource is required')
      return
    }
    try {
      if (editingId === 'new') {
        await miscApi.createPermission(form)
        showToast('Permission created.')
      } else {
        await miscApi.updatePermission(editingId, form)
        showToast('Permission updated.')
      }
      setEditingId(null)
      refresh()
    } catch (err) {
      showToast(err.message)
    }
  }

  async function handleDelete(id) {
    if (!window.confirm('Delete this permission? It will be removed from every role that has it.')) return
    try {
      await miscApi.deletePermission(id)
      showToast('Permission deleted.')
      refresh()
    } catch (err) {
      showToast(err.message)
    }
  }

  async function handleDeleteAll() {
    if (!window.confirm('Delete all permissions? They will be removed from every role.')) return
    try {
      await miscApi.deleteAllPermissions()
      showToast('Permissions deleted.')
      refresh()
    } catch (err) {
      showToast(err.message)
    }
  }

  return (
    <>
      <h1 className="page-title">Settings — Permissions</h1>
      <p className="page-sub">
        Define individual (resource, action) permissions here. Assign a permission to a role on the
        <b> Roles &amp; Permissions</b> page once it exists.
      </p>

      <div className="card" style={{ marginBottom: 18 }}>
        <div className="toolbar">
          <div className="toolbar-left" />
          <div className="toolbar-right">
            <button className="btn" disabled={!canDelete || permissions.length === 0}
                    title={!canDelete ? 'Not permitted for your role' : ''}
                    onClick={handleDeleteAll}>
              Delete All
            </button>
            <button className="btn primary" disabled={!canCreate}
                    title={!canCreate ? 'Not permitted for your role' : ''} onClick={startCreate}>
              ＋ Add Permission
            </button>
          </div>
        </div>

        {loading && <div className="loading">Loading...</div>}
        {!loading && (
          <div className="table-scroll">
            <table>
              <thead>
                <tr><th>Resource</th><th>Action</th><th>Description</th><th>Actions</th></tr>
              </thead>
              <tbody>
                {permissions.map((p) => (
                  <tr key={p.id}>
                    <td>{p.resource}</td>
                    <td style={{ textTransform: 'capitalize' }}>{p.action}</td>
                    <td>{p.description || '—'}</td>
                    <td>
                      <button className="kebab" disabled={!canUpdate} title="Edit" onClick={() => startEdit(p)}>✎</button>
                      <button className="kebab" disabled={!canDelete} title="Delete" onClick={() => handleDelete(p.id)}>🗑</button>
                    </td>
                  </tr>
                ))}
                {permissions.length === 0 && (
                  <tr><td colSpan={4} style={{ textAlign: 'center', padding: 24, color: 'var(--text-muted)' }}>No permissions defined yet.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {editingId && (
        <div className="card">
          <h3>{editingId === 'new' ? 'Add Permission' : 'Edit Permission'}</h3>
          <div className="row">
            <div className="form-field" style={{ flex: 1 }}>
              <label>Resource (page or model)</label>
              <input value={form.resource} onChange={(e) => setForm((f) => ({ ...f, resource: e.target.value }))}
                     placeholder="e.g. reports, audit_logs, dashboard" />
            </div>
            <div className="form-field" style={{ flex: 1 }}>
              <label>Action</label>
              <select value={form.action} onChange={(e) => setForm((f) => ({ ...f, action: e.target.value }))}>
                <option value="create">create</option>
                <option value="view">view</option>
                <option value="update">update</option>
                <option value="delete">delete</option>
              </select>
            </div>
          </div>
          <div className="form-field">
            <label>Description</label>
            <input value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} />
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn" onClick={() => setEditingId(null)}>Cancel</button>
            <button className="btn primary" onClick={handleSave}>Save</button>
          </div>
        </div>
      )}
    </>
  )
}
