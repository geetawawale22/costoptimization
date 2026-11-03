import { useRef } from 'react'
import ModalCloseButton from '../components/ModalCloseButton'
import React from 'react'
import { useEffect, useMemo, useState } from 'react'
import * as miscApi from '../services/api'
import Pagination from '../components/Pagination'
import { usePageCrumb } from '../layouts/ProtectedLayout'
import { useAuth } from '../auth/AuthContext'
import { useToast } from '../context/ToastContext'
import { useConfirm } from '../context/ConfirmContext'
import StatusChip from '../components/StatusChip'

function InviteModal({ roles, onClose, onSave, saving }) {
  const [draft, setDraft] = useState({ name: '', employee_id: '', email: '', password: '', role_id: '' })
  const invalid = !draft.name || !draft.email || !draft.password || !draft.role_id

  useEffect(() => {
    const closeOnEscape = (event) => event.key === 'Escape' && onClose()
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [onClose])

  return (
    <div className="config-modal-overlay" onClick={(event) => event.target === event.currentTarget && onClose()}>
      <div className="config-modal permission-definition-modal" role="dialog" aria-modal="true" aria-label="Invite User">
        <header className="config-modal-head">
          <div><h2>Invite User</h2><p>Creates an account shared with the existing application. No invitation email is sent.</p></div>
          <ModalCloseButton onClick={onClose} aria-label="Close" />
        </header>
        <div className="config-modal-body">
          <div className="config-grid">
            <div className="form-field"><label>Name</label><input autoFocus value={draft.name} onChange={(e) => setDraft((v) => ({ ...v, name: e.target.value }))} placeholder="Enter full name" /></div>
              <div className="form-field"><label>Employee ID</label><input value={draft.employee_id} onChange={(e) => setDraft((v) => ({ ...v, employee_id: e.target.value }))} placeholder="Employee ID" /></div>
            <div className="form-field"><label>Email</label><input value={draft.email} onChange={(e) => setDraft((v) => ({ ...v, email: e.target.value }))} placeholder="name@company.com" /></div>
            <div className="form-field"><label>Temporary Password</label><input type="password" value={draft.password} onChange={(e) => setDraft((v) => ({ ...v, password: e.target.value }))} placeholder="********" /></div>
            <div className="form-field">
              <label>Role</label>
              <select value={draft.role_id} onChange={(e) => setDraft((v) => ({ ...v, role_id: e.target.value }))}>
                <option value="">Select a role</option>
                {roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </select>
            </div>
          </div>
        </div>
        <footer className="config-modal-footer">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="button" className="btn primary" disabled={invalid || saving} aria-busy={saving} onClick={() => onSave(draft)}>{saving ? 'Inviting...' : 'Invite User'}</button>
        </footer>
      </div>
    </div>
  )
}

function EditUserModal({ user, roles, onClose, onSave, saving }) {
  const [draft, setDraft] = useState({
    name: user.name || '',
    employee_id: user.employee_id || '',
    job_title: user.job_title || '',
    department: user.department || '',
    role_id: user.role?.id ? String(user.role.id) : '',
  })

  useEffect(() => {
    const closeOnEscape = (event) => event.key === 'Escape' && onClose()
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [onClose])

  return (
    <div className="config-modal-overlay" onClick={(event) => event.target === event.currentTarget && onClose()}>
      <div className="config-modal permission-definition-modal" role="dialog" aria-modal="true" aria-label="Edit User">
        <header className="config-modal-head">
          <div><h2>Edit User — {user.name}</h2></div>
          <ModalCloseButton onClick={onClose} aria-label="Close" />
        </header>
        <div className="config-modal-body">
          <div className="config-grid">
            <div className="form-field wide">
              <label>Email</label>
              <input disabled value={user.email} />
              <small>Email can't be changed here.</small>
            </div>
            <div className="form-field"><label>Name</label><input autoFocus value={draft.name} onChange={(e) => setDraft((v) => ({ ...v, name: e.target.value }))} /></div>
            <div className="form-field"><label>Employee ID</label><input value={draft.employee_id} onChange={(e) => setDraft((v) => ({ ...v, employee_id: e.target.value }))} /></div>
            <div className="form-field">
              <label>Role</label>
              <select value={draft.role_id} onChange={(e) => setDraft((v) => ({ ...v, role_id: e.target.value }))}>
                <option value="">Select a role</option>
                {roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </select>
            </div>
            <div className="form-field"><label>Job Title</label><input value={draft.job_title} onChange={(e) => setDraft((v) => ({ ...v, job_title: e.target.value }))} /></div>
            <div className="form-field"><label>Department</label><input value={draft.department} onChange={(e) => setDraft((v) => ({ ...v, department: e.target.value }))} /></div>
          </div>
        </div>
        <footer className="config-modal-footer">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="button" className="btn primary" disabled={!draft.name || saving} aria-busy={saving} onClick={() => onSave(draft)}>{saving ? 'Saving...' : 'Save Changes'}</button>
        </footer>
      </div>
    </div>
  )
}

export default function Users() {
  usePageCrumb('Settings / <b>Users</b>')
  const { can, appearance } = useAuth()
  const showToast = useToast()
  const confirm = useConfirm()
  const [usersMeta, setUsersMeta] = useState({ items: [], total: 0, page: 1, page_size: 20, total_pages: 1 })
  const [allUsers, setAllUsers] = useState([]) // full set, for accurate stat counts
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [q, setQ] = useState('')
  const [kpiFilter, setKpiFilter] = useState('')
  const [roles, setRoles] = useState([])
  const [inviteOpen, setInviteOpen] = useState(false)
  const [editingUser, setEditingUser] = useState(null)
  const [saving, setSaving] = useState(false)

  const searchRequest = useRef(0)
  async function refresh() {
    const requestId = ++searchRequest.current
    setLoading(true)
    try {
      const [usersRes, rolesRes, fullRes] = await Promise.all([
        miscApi.listUsers(page, 20, q.trim(), {
          ...(kpiFilter === 'active' ? { status: 'Active' } : {}),
          ...(kpiFilter === 'inactive' ? { status: 'Inactive' } : {}),
          ...(kpiFilter === 'admins' ? { admins_only: true } : {}),
        }),
        miscApi.listRoles(1, 200),
        miscApi.userStatistics(),
      ])
      if (requestId !== searchRequest.current) return
      setUsersMeta(usersRes)
      setRoles(rolesRes.items)
      setAllUsers(fullRes)
    } catch (err) {
      if (requestId === searchRequest.current) showToast(err.message, 'error')
    } finally {
      if (requestId === searchRequest.current) setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, q, kpiFilter])

  async function handleInvite(draft) {
    setSaving(true)
    try {
      await miscApi.createUser({ ...draft, role_id: Number(draft.role_id) || undefined })
      setInviteOpen(false)
      setPage(1)
      await refresh()
      showToast('User created.')
    } catch (err) {
      showToast(err.message, 'error')
    } finally {
      setSaving(false)
    }
  }

  async function handleEditSave(draft) {
    setSaving(true)
    try {
      await miscApi.updateUser(editingUser.id, {
        name: draft.name,
        employee_id: draft.employee_id,
        job_title: draft.job_title,
        department: draft.department,
        role_id: draft.role_id ? Number(draft.role_id) : null,
      })
      setEditingUser(null)
      await refresh()
      showToast('User updated.')
    } catch (err) {
      showToast(err.message, 'error')
    } finally {
      setSaving(false)
    }
  }

  async function handleDeactivate(id) {
    if (!(await confirm({ title: 'Deactivate User', message: 'Deactivate this user? They will lose access immediately.' }))) return
    try {
      await miscApi.deactivateUser(id, 'Deactivated by admin')
      showToast('User deactivated.')
      await refresh()
    } catch (err) { showToast(err.message, 'error') }
  }

  async function handleReactivate(id) {
    try {
      await miscApi.updateUser(id, { status: 'Active' })
      showToast('User reactivated.')
      await refresh()
    } catch (err) { showToast(err.message, 'error') }
  }

  const canCreate = can('users', 'create')
  const canUpdate = can('users', 'update')
  const canDelete = can('users', 'delete')

  const stats = { total: allUsers.total || 0, active: allUsers.active || 0,
    inactive: allUsers.inactive || 0, adminCount: allUsers.admins || 0 }


  return (
    <div className="user-page">
      <div className="user-header-row">
        <div>
          <h1 className="page-title">Users</h1>
          <p className="page-sub">Manage platform access, role assignment, and user activity.</p>
        </div>
        <button className="btn primary" disabled={!canCreate} title={canCreate ? '' : "You don't have permission to invite users."} onClick={() => setInviteOpen(true)}>
          + Invite User
        </button>
      </div>

      <div className="user-stats">
        {[
          ['total', 'Total Users', stats.total],
          ['active', 'Active', stats.active],
          ['inactive', 'Inactive', stats.inactive],
          ['admins', 'Admins', stats.adminCount],
        ].map(([key, label, value]) => (
          <button type="button" key={key} className={`stat-card user-stat-card${kpiFilter === key ? ' is-active' : ''}`} aria-pressed={kpiFilter === key} onClick={() => { setKpiFilter(current => current === key ? '' : key); setPage(1) }}>
            <span className="lbl">{label}</span><strong className="val">{value}</strong><small>Click to view users</small>
          </button>
        ))}
      </div>

      <div className="card user-card">
        <div className="toolbar user-toolbar">
          <div className="toolbar-left">
            <input className="search-box" aria-label="Search users" placeholder="Search name, email, or employee ID..." value={q} onChange={(e) => { setQ(e.target.value); setPage(1) }} /><button type="button" className="btn secondary" onClick={() => { setQ(''); setKpiFilter(''); setPage(1) }}>Reset filters</button>
          </div>

        </div>

        {loading ? (
          <div className="loading">Loading users...</div>
        ) : (
          <div className="table-scroll">
            <table className="users-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Employee ID</th>
                  <th>Email</th>
                  <th>Job Title</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {usersMeta.items.length === 0 && (
                  <tr><td colSpan={7} className="empty-row">No users match the current search.</td></tr>
                )}
                {usersMeta.items.map((u) => (
                  <tr key={u.id}>
                    <td>{u.name}</td>
                    <td>{u.employee_id || 'â€”'}</td>
                    <td>{u.email}</td>
                    <td>{u.job_title || '—'}</td>
                    <td>{u.role?.name || '—'}</td>
                    <td><StatusChip value={u.status} /></td>
                    <td>
                      <div className="table-actions">
                        <button className="row-action-btn" disabled={!canUpdate} title={canUpdate ? '' : "You don't have permission to edit users."} onClick={() => setEditingUser(u)}>✎ Edit</button>
                        {u.status === 'Inactive' ? (
                          <button className="row-action-btn success" disabled={!canUpdate} title={canUpdate ? '' : "You don't have permission to edit users."} onClick={() => handleReactivate(u.id)}>▶ Reactivate</button>
                        ) : (
                          <button className="row-action-btn danger" disabled={!canDelete} title={canDelete ? '' : "You don't have permission to deactivate users."} onClick={() => handleDeactivate(u.id)}>⏸ Deactivate</button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <Pagination page={usersMeta.page} pageSize={usersMeta.page_size} total={usersMeta.total} totalPages={usersMeta.total_pages} onPageChange={setPage} />
      </div>

      {inviteOpen && <InviteModal roles={roles} saving={saving} onClose={() => setInviteOpen(false)} onSave={handleInvite} />}
      {editingUser && <EditUserModal user={editingUser} roles={roles} saving={saving} onClose={() => setEditingUser(null)} onSave={handleEditSave} />}
    </div>
  )
}
