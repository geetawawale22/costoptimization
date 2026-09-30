import { useEffect, useMemo, useState } from 'react'
import * as miscApi from '../../api/misc'
import Pagination from '../../components/Pagination'
import { usePageCrumb } from '../../components/Layout'
import { useAuth } from '../../context/AuthContext'
import { useToast } from '../../context/ToastContext'
import { useConfirm } from '../../context/ConfirmContext'

const ACTIONS = ['create', 'view', 'update', 'delete']
const EMPTY_PERMISSION = { resource: '', action: 'view', description: '' }
const ROLE_PALETTE = [
  { bg: '#EAF1F5', text: '#2D5470' },
  { bg: '#E7F5EC', text: '#2E8B57' },
  { bg: '#FBF3DF', text: '#B8860B' },
  { bg: '#FBEAE8', text: '#C0392B' },
  { bg: '#F1EAFB', text: '#6B3FA0' },
  { bg: '#E3F6F5', text: '#227C77' },
  { bg: '#EDEEFB', text: '#4B4FA8' },
  { bg: '#FBEAF2', text: '#A83D6E' },
]

function roleColor(index) {
  return ROLE_PALETTE[index % ROLE_PALETTE.length]
}

function Modal({ title, subtitle, children, footer, onClose, className = '' }) {
  useEffect(() => {
    const closeOnEscape = (event) => event.key === 'Escape' && onClose()
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [onClose])

  return (
    <div className="config-modal-overlay" onClick={(event) => event.target === event.currentTarget && onClose()}>
      <div className={`config-modal access-modal ${className}`} role="dialog" aria-modal="true" aria-label={title} onClick={(event) => event.stopPropagation()}>
        <header className="config-modal-head">
          <div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close">×</button>
        </header>
        <div className="config-modal-body access-modal-body">{children}</div>
        <footer className="config-modal-footer">{footer}</footer>
      </div>
    </div>
  )
}

function PermissionDefinitionModal({ initial, title, onClose, onSave, saving }) {
  const [draft, setDraft] = useState(initial || EMPTY_PERMISSION)
  const actionLabel = title === 'Edit Permission' ? 'Save Changes' : title
  return (
    <Modal title={title} subtitle="Add a reusable resource and action permission." onClose={onClose} className="permission-definition-modal" footer={<>
      <button type="button" className="btn" onClick={onClose}>Cancel</button>
      <button type="button" className="btn primary" disabled={!draft.resource.trim() || saving} onClick={() => onSave(draft)}>{saving ? 'Saving...' : actionLabel}</button>
    </>}>
      <div className="access-section-label">Permission details</div>
      <div className="access-form-grid">
        <div className="form-field"><label>Resource</label><input autoFocus value={draft.resource} onChange={(event) => setDraft({ ...draft, resource: event.target.value })} placeholder="e.g. calculations" /></div>
        <div className="form-field"><label>Action</label><select value={draft.action} onChange={(event) => setDraft({ ...draft, action: event.target.value })}>{ACTIONS.map((action) => <option key={action}>{action}</option>)}</select></div>
        <div className="form-field access-wide-field"><label>Description</label><textarea value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} placeholder="Describe what this permission allows" /></div>
      </div>
    </Modal>
  )
}

function RoleFormModal({ role, userCount, permissions, grantedIds, canDelete, onClose, onSave, onDelete, saving }) {
  const [draft, setDraft] = useState({ name: role?.name || '', description: role?.description || '' })
  const [selectedIds, setSelectedIds] = useState(() => new Set(grantedIds || []))
  const [moduleQuery, setModuleQuery] = useState('')

  const grouped = useMemo(() => {
    const groups = {}
    permissions.forEach((permission) => {
      if (!groups[permission.resource]) groups[permission.resource] = []
      groups[permission.resource].push(permission)
    })
    Object.values(groups).forEach((list) => list.sort((a, b) => ACTIONS.indexOf(a.action) - ACTIONS.indexOf(b.action)))
    return groups
  }, [permissions])
  const moduleKeys = Object.keys(grouped).sort().filter((key) => !moduleQuery || key.replaceAll('_', ' ').toLowerCase().includes(moduleQuery.toLowerCase()))

  function toggle(permissionId) {
    setSelectedIds((current) => {
      const next = new Set(current)
      next.has(permissionId) ? next.delete(permissionId) : next.add(permissionId)
      return next
    })
  }
  function toggleModule(modulePermissions) {
    const ids = modulePermissions.map((permission) => permission.id)
    const allSelected = ids.every((id) => selectedIds.has(id))
    setSelectedIds((current) => {
      const next = new Set(current)
      ids.forEach((id) => (allSelected ? next.delete(id) : next.add(id)))
      return next
    })
  }

  return (
    <Modal
      title={role ? `Edit Role — ${role.name}` : 'Create New Role'}
      onClose={onClose}
      className="role-form-modal"
      footer={<>
        {role && <button type="button" className="btn danger" disabled={!canDelete} onClick={() => onDelete(role)}>Delete Role</button>}
        <button type="button" className="btn" onClick={onClose}>Cancel</button>
        <button type="button" className="btn primary" disabled={!draft.name.trim() || saving} onClick={() => onSave(draft, [...selectedIds])}>{saving ? 'Saving...' : role ? 'Save Changes' : 'Create Role'}</button>
      </>}
    >
      <div className="role-form-section">
        <div className="form-field">
          <label>Role Name</label>
          <input autoFocus={!role} disabled={!!role} value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="Enter role name" />
          {role && <small>Role names can't be changed after creation.</small>}
        </div>
        <div className="form-field">
          <label>Description</label>
          <input autoFocus={!!role} value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} placeholder="Describe this role" />
        </div>
        {role && <p className="step-help">{userCount > 0 ? `${userCount} user${userCount === 1 ? '' : 's'} currently assigned to this role.` : 'No users are currently assigned to this role.'}</p>}
      </div>

      <div className="role-form-section role-form-permissions">
        <div className="permission-selector-heading">
          <div className="access-section-label">Permissions</div>
          <span className="role-form-permission-count">{selectedIds.size} of {permissions.length} granted</span>
        </div>
        <input className="role-form-module-search" placeholder="Filter modules..." value={moduleQuery} onChange={(event) => setModuleQuery(event.target.value)} />
        <div className="role-form-permission-grid">
          {moduleKeys.map((moduleKey) => {
            const modulePermissions = grouped[moduleKey]
            const allSelected = modulePermissions.every((permission) => selectedIds.has(permission.id))
            return (
              <div className="role-form-module-card" key={moduleKey}>
                <div className="role-form-module-card-head">
                  <strong>{moduleKey.replaceAll('_', ' ')}</strong>
                  <button type="button" className="role-form-module-select" onClick={() => toggleModule(modulePermissions)}>{allSelected ? 'Clear all' : 'Select all'}</button>
                </div>
                {modulePermissions.map((permission) => (
                  <label key={permission.id} className="role-form-check-row">
                    <input type="checkbox" checked={selectedIds.has(permission.id)} onChange={() => toggle(permission.id)} />
                    <span>{permission.action} {moduleKey.replaceAll('_', ' ')}</span>
                  </label>
                ))}
              </div>
            )
          })}
          {moduleKeys.length === 0 && <div className="permission-empty">No modules match "{moduleQuery}".</div>}
        </div>
      </div>
    </Modal>
  )
}

export default function RolesPermissions() {
  usePageCrumb('Settings / <b>Roles &amp; Permissions</b>')
  const { can } = useAuth()
  const showToast = useToast()
  const confirm = useConfirm()

  const [roles, setRoles] = useState([])
  const [allPermissions, setAllPermissions] = useState([])
  const [grantedByRole, setGrantedByRole] = useState({})
  const [userCountByRole, setUserCountByRole] = useState({})
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [syncedAt, setSyncedAt] = useState(null)

  const [query, setQuery] = useState('')
  const [moduleFilter, setModuleFilter] = useState('')
  const [roleFilter, setRoleFilter] = useState('')
  const [showGranted, setShowGranted] = useState(false)
  const [showNotGranted, setShowNotGranted] = useState(false)
  const [collapsedModules, setCollapsedModules] = useState(new Set())

  const [roleFormModal, setRoleFormModal] = useState(null)
  const [savingRole, setSavingRole] = useState(false)
  const [catalogueOpen, setCatalogueOpen] = useState(false)
  const [permissionModal, setPermissionModal] = useState(null)
  const [catalogueFilters, setCatalogueFilters] = useState({ q: '', resource: '', action: '' })
  const [permPage, setPermPage] = useState(1)
  const [permMeta, setPermMeta] = useState({ total: 0, page: 1, page_size: 20, total_pages: 1 })

  const canEditRoles = can('roles', 'update')
  const canCreateRoles = can('roles', 'create')
  const canDeleteRoles = can('roles', 'delete')
  const canCreatePerms = can('permissions', 'create')
  const canUpdatePerms = can('permissions', 'update')
  const canDeletePerms = can('permissions', 'delete')

  async function refresh() {
    const [rolesResult, permissionsResult, usersResult] = await Promise.all([
      miscApi.listRoles(1, 200),
      miscApi.listPermissions(1, 500),
      miscApi.listUsers(1, 500),
    ])
    setRoles(rolesResult.items)
    setAllPermissions(permissionsResult.items)
    const grants = {}
    await Promise.all(rolesResult.items.map(async (role) => {
      const detail = await miscApi.getRolePermissions(role.id)
      grants[role.id] = new Set(detail.permissions.map((permission) => permission.id))
    }))
    setGrantedByRole(grants)
    const counts = {}
    usersResult.items.forEach((user) => { if (user.role_id) counts[user.role_id] = (counts[user.role_id] || 0) + 1 })
    setUserCountByRole(counts)
    setSyncedAt(new Date())
  }
  async function refreshCatalogue(page = permPage) { setPermMeta(await miscApi.listPermissions(page, 20)) }
  async function loadInitial() {
    setLoading(true)
    setLoadError('')
    try { await refresh() } catch (error) { setLoadError(error.message) } finally { setLoading(false) }
  }
  useEffect(() => { loadInitial() }, [])
  useEffect(() => { if (catalogueOpen) refreshCatalogue().catch((error) => showToast(error.message)) }, [permPage, catalogueOpen])

  const moduleKeys = useMemo(() => [...new Set(allPermissions.map((permission) => permission.resource))].sort(), [allPermissions])
  const roleColorById = useMemo(() => {
    const map = {}
    roles.forEach((role, index) => { map[role.id] = roleColor(index) })
    return map
  }, [roles])
  const visibleRoles = useMemo(() => (roleFilter ? roles.filter((role) => String(role.id) === roleFilter) : roles), [roles, roleFilter])

  const groupedModules = useMemo(() => {
    const groups = {}
    allPermissions
      .filter((permission) => {
        if (moduleFilter && permission.resource !== moduleFilter) return false
        if (query) {
          const text = `${permission.resource} ${permission.action} ${permission.description || ''}`.toLowerCase()
          if (!text.includes(query.toLowerCase())) return false
        }
        if (showGranted || showNotGranted) {
          const grantedCount = visibleRoles.filter((role) => grantedByRole[role.id]?.has(permission.id)).length
          const matchesGranted = showGranted && grantedCount > 0
          const matchesNotGranted = showNotGranted && grantedCount < visibleRoles.length
          if (!matchesGranted && !matchesNotGranted) return false
        }
        return true
      })
      .forEach((permission) => {
        if (!groups[permission.resource]) groups[permission.resource] = []
        groups[permission.resource].push(permission)
      })
    Object.values(groups).forEach((list) => list.sort((a, b) => ACTIONS.indexOf(a.action) - ACTIONS.indexOf(b.action)))
    return groups
  }, [allPermissions, query, moduleFilter, showGranted, showNotGranted, visibleRoles, grantedByRole])

  const visibleModuleKeys = Object.keys(groupedModules).sort()
  const allExpanded = visibleModuleKeys.length > 0 && visibleModuleKeys.every((key) => !collapsedModules.has(key))

  function toggleModule(key) {
    setCollapsedModules((current) => {
      const next = new Set(current)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })
  }
  function toggleExpandAll() {
    setCollapsedModules(allExpanded ? new Set(visibleModuleKeys) : new Set())
  }

  async function togglePermissionForRole(role, permission) {
    if (!canEditRoles) return
    const current = new Set(grantedByRole[role.id] || [])
    current.has(permission.id) ? current.delete(permission.id) : current.add(permission.id)
    setGrantedByRole((previous) => ({ ...previous, [role.id]: current }))
    try { await miscApi.setRolePermissions(role.id, [...current]) } catch (error) { showToast(error.message); await refresh() }
  }

  function openCreateRole() { setRoleFormModal({ role: null }) }
  function openEditRole(role) { setRoleFormModal({ role }) }
  async function saveRoleForm(details, permissionIds) {
    setSavingRole(true)
    try {
      let roleId = roleFormModal.role?.id
      if (roleId) {
        await miscApi.updateRole(roleId, details)
        showToast('Role updated successfully.')
      } else {
        const created = await miscApi.createRole(details)
        roleId = created.id
        showToast('Role created successfully.')
      }
      await miscApi.setRolePermissions(roleId, permissionIds)
      setRoleFormModal(null)
      await refresh()
    } catch (error) { showToast(error.message) } finally { setSavingRole(false) }
  }
  async function deleteRoleFromForm(role) {
    const userCount = userCountByRole[role.id] || 0
    const warning = userCount > 0
      ? `"${role.name}" is currently assigned to ${userCount} user${userCount === 1 ? '' : 's'}. Deleting it will immediately remove their access. This cannot be undone. Continue?`
      : `Delete role "${role.name}"? This action cannot be undone.`
    if (!(await confirm({ title: 'Delete Role', message: warning }))) return
    try { await miscApi.deleteRole(role.id); showToast('Role deleted.'); setRoleFormModal(null); await refresh() } catch (error) { showToast(error.message) }
  }

  async function createPermission(permission) {
    try {
      const created = await miscApi.createPermission(permission)
      setAllPermissions((current) => [...current, created])
      await refreshCatalogue(1)
      setPermPage(1)
      showToast('Permission created successfully.')
      return created
    } catch (error) { showToast(error.message); return null }
  }
  async function savePermission(permission) {
    try { await miscApi.updatePermission(permissionModal.id, permission); setPermissionModal(null); showToast('Permission updated successfully.'); await refresh(); await refreshCatalogue() } catch (error) { showToast(error.message) }
  }
  async function deletePermission(permission) {
    if (!(await confirm({ title: 'Delete Permission', message: `Delete permission "${permission.resource}:${permission.action}"?` }))) return
    try { await miscApi.deletePermission(permission.id); showToast('Permission deleted.'); await refresh(); await refreshCatalogue() } catch (error) { showToast(error.message) }
  }

  function exportCsv() {
    const header = ['Module', 'Permission', 'Key', ...visibleRoles.map((role) => role.name)]
    const rows = [header]
    visibleModuleKeys.forEach((moduleKey) => {
      groupedModules[moduleKey].forEach((permission) => {
        rows.push([
          moduleKey.replaceAll('_', ' '),
          `${permission.action} ${moduleKey.replaceAll('_', ' ')}`,
          `${moduleKey.toUpperCase()}.${permission.action.toUpperCase()}`,
          ...visibleRoles.map((role) => (grantedByRole[role.id]?.has(permission.id) ? 'Yes' : 'No')),
        ])
      })
    })
    const csv = rows.map((row) => row.map((cell) => `"${String(cell).replaceAll('"', '""')}"`).join(',')).join('\n')
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'role-permissions.csv'
    link.click()
    URL.revokeObjectURL(url)
  }

  const catalogueRows = (permMeta.items || []).filter((permission) => (
    (!catalogueFilters.resource || permission.resource === catalogueFilters.resource)
    && (!catalogueFilters.action || permission.action === catalogueFilters.action)
    && (!catalogueFilters.q || `${permission.resource} ${permission.action} ${permission.description || ''}`.toLowerCase().includes(catalogueFilters.q.toLowerCase()))
  ))

  return (
    <div className="settings-page-shell access-control-page perm-matrix-page">
      <div className="settings-page-header">
        <div className="settings-header-row">
          <div>
            <h1 className="page-title">Roles &amp; Permissions</h1>
            <p className="page-sub">Manage role-based permissions for each module. Changes affect all users assigned to the selected role.</p>
          </div>
          <button className="btn primary" disabled={!canCreateRoles} title={canCreateRoles ? '' : "You don't have permission to create roles."} onClick={openCreateRole}>+ Create Role</button>
        </div>
      </div>

      {!canEditRoles && (
        <div className="perm-access-notice">Your role has view-only access here — permission checkboxes and role editing are read-only. Ask an administrator to grant <code>roles:update</code> to make changes.</div>
      )}

      {loadError && <div className="error-text">Couldn't load roles &amp; permissions: {loadError}. <button type="button" className="btn" onClick={loadInitial}>Retry</button></div>}

      <div className="perm-toolbar-row">
        <input className="search-box perm-matrix-search" placeholder="Search permission name, key, or module..." value={query} onChange={(event) => setQuery(event.target.value)} />
        <select value={moduleFilter} onChange={(event) => setModuleFilter(event.target.value)}>
          <option value="">All Modules</option>
          {moduleKeys.map((key) => <option key={key} value={key}>{key.replaceAll('_', ' ')}</option>)}
        </select>
        <select value={roleFilter} onChange={(event) => setRoleFilter(event.target.value)}>
          <option value="">All Roles</option>
          {roles.map((role) => <option key={role.id} value={role.id}>{role.name}</option>)}
        </select>
        <div className="perm-toolbar-actions">
          <button type="button" className="btn" onClick={toggleExpandAll}>{allExpanded ? 'Collapse All' : 'Expand All'}</button>
          <button type="button" className="btn" onClick={exportCsv}>Export</button>
          <button type="button" className="btn" onClick={loadInitial}>Refresh</button>
          <button type="button" className="btn" onClick={() => setCatalogueOpen(true)}>Manage Catalogue</button>
        </div>
      </div>

      <div className="perm-legend">
        <button type="button" className="perm-legend-item" onClick={() => setShowGranted((v) => !v)} title="Show only permissions granted to at least one visible role">
          <span className={`perm-checkbox mini ${showGranted ? 'checked' : ''}`} aria-hidden="true">{showGranted ? '✓' : ''}</span>Granted
        </button>
        <button type="button" className="perm-legend-item" onClick={() => setShowNotGranted((v) => !v)} title="Show only permissions not granted to at least one visible role">
          <span className={`perm-checkbox mini ${showNotGranted ? 'checked' : ''}`} aria-hidden="true">{showNotGranted ? '✓' : ''}</span>Not granted
        </button>
        {(showGranted || showNotGranted) && <button type="button" className="btn" onClick={() => { setShowGranted(false); setShowNotGranted(false) }}>Clear filter</button>}
        {syncedAt && <span className="perm-sync-note">Synced {syncedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>}
      </div>

      {loading ? (
        <div className="loading">Loading roles &amp; permissions...</div>
      ) : visibleModuleKeys.length === 0 ? (
        <div className="empty-state-box">
          <div className="empty-icon">⊘</div>
          <strong>No permissions match your filters</strong>
          <span>Try a different search term, or clear the module filter.</span>
        </div>
      ) : visibleModuleKeys.map((moduleKey) => {
        const permissions = groupedModules[moduleKey]
        const isOpen = !collapsedModules.has(moduleKey)
        return (
          <section className="perm-module" key={moduleKey}>
            <div className="perm-module-head" onClick={() => toggleModule(moduleKey)}>
              <span className={`chev ${isOpen ? 'open' : ''}`}>▶</span>
              <span className="perm-module-icon">▦</span>
              <strong>{moduleKey.replaceAll('_', ' ')}</strong>
              <span className="perm-module-count">{permissions.length} permission{permissions.length === 1 ? '' : 's'}</span>
            </div>
            {isOpen && (
              <div className="perm-matrix-scroll">
                <table className="perm-matrix">
                  <thead>
                    <tr>
                      <th className="perm-name-col">Permission</th>
                      {visibleRoles.map((role) => {
                        const color = roleColorById[role.id] || ROLE_PALETTE[0]
                        return (
                          <th key={role.id} className="perm-role-col">
                            <span className="perm-role-pill-wrap">
                              <span className="perm-role-pill" style={{ background: color.bg, color: color.text }}>{role.name}</span>
                              <button type="button" className="perm-role-edit" disabled={!canEditRoles} title={canEditRoles ? `Edit ${role.name}` : "You don't have permission to edit roles."} onClick={() => openEditRole(role)}>✎</button>
                            </span>
                          </th>
                        )
                      })}
                    </tr>
                  </thead>
                  <tbody>
                    {permissions.map((permission) => (
                      <tr key={permission.id}>
                        <td className="perm-name-col">
                          <span className="perm-name">{permission.action} {moduleKey.replaceAll('_', ' ')}</span>
                          <span className="perm-key">{moduleKey.toUpperCase()}.{permission.action.toUpperCase()}</span>
                        </td>
                        {visibleRoles.map((role) => {
                          const granted = grantedByRole[role.id]?.has(permission.id)
                          return (
                            <td key={role.id} className="perm-role-col">
                              <button
                                type="button"
                                className={`perm-checkbox ${granted ? 'checked' : ''}`}
                                disabled={!canEditRoles}
                                title={canEditRoles ? '' : "You don't have permission to edit roles."}
                                aria-label={`${granted ? 'Revoke' : 'Grant'} ${permission.action} ${moduleKey} for ${role.name}`}
                                onClick={() => togglePermissionForRole(role, permission)}
                              >{granted ? '✓' : ''}</button>
                            </td>
                          )
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        )
      })}

      {roleFormModal && (
        <RoleFormModal
          key={roleFormModal.role?.id || 'new'}
          role={roleFormModal.role}
          userCount={roleFormModal.role ? (userCountByRole[roleFormModal.role.id] || 0) : 0}
          permissions={allPermissions}
          grantedIds={roleFormModal.role ? (grantedByRole[roleFormModal.role.id] || new Set()) : new Set()}
          canDelete={canDeleteRoles}
          onClose={() => setRoleFormModal(null)}
          onSave={saveRoleForm}
          onDelete={deleteRoleFromForm}
          saving={savingRole}
        />
      )}

      {catalogueOpen && (
        <Modal
          title="Permission Catalogue"
          subtitle="Manage the reusable resource and action permissions available to roles."
          onClose={() => setCatalogueOpen(false)}
          className="role-editor-modal"
          footer={<button type="button" className="btn" onClick={() => setCatalogueOpen(false)}>Close</button>}
        >
          <div className="compact-toolbar">
            <input className="toolbar-input" placeholder="Search permissions..." value={catalogueFilters.q} onChange={(event) => setCatalogueFilters({ ...catalogueFilters, q: event.target.value })} />
            <select className="toolbar-select" value={catalogueFilters.resource} onChange={(event) => setCatalogueFilters({ ...catalogueFilters, resource: event.target.value })}>
              <option value="">All resources</option>
              {moduleKeys.map((resource) => <option key={resource}>{resource}</option>)}
            </select>
            <select className="toolbar-select" value={catalogueFilters.action} onChange={(event) => setCatalogueFilters({ ...catalogueFilters, action: event.target.value })}>
              <option value="">All actions</option>
              {ACTIONS.map((action) => <option key={action}>{action}</option>)}
            </select>
            <button className="btn primary" disabled={!canCreatePerms} title={canCreatePerms ? '' : "You don't have permission to create permissions."} onClick={() => setPermissionModal({ id: null, permission: EMPTY_PERMISSION })}>+ Add Permission</button>
          </div>
          <div className="table-scroll">
            <table className="settings-table">
              <thead><tr><th>Resource</th><th>Action</th><th>Description</th><th>Actions</th></tr></thead>
              <tbody>
                {catalogueRows.map((permission) => (
                  <tr key={permission.id}>
                    <td className="resource-cell">{permission.resource.replaceAll('_', ' ')}</td>
                    <td><span className={`action-badge ${permission.action}`}>{permission.action}</span></td>
                    <td>{permission.description || '-'}</td>
                    <td><div className="table-actions">
                      <button className="kebab" disabled={!canUpdatePerms} title="Edit permission" onClick={() => setPermissionModal({ id: permission.id, permission })}>Edit</button>
                      <button className="kebab" disabled={!canDeletePerms} title="Delete permission" onClick={() => deletePermission(permission)}>Delete</button>
                    </div></td>
                  </tr>
                ))}
                {catalogueRows.length === 0 && <tr><td colSpan={4} className="empty-row">No permissions match the current filters.</td></tr>}
              </tbody>
            </table>
          </div>
          <Pagination page={permMeta.page} pageSize={permMeta.page_size} total={permMeta.total} totalPages={permMeta.total_pages} onPageChange={setPermPage} />
        </Modal>
      )}

      {permissionModal && (
        <PermissionDefinitionModal
          title={permissionModal.id ? 'Edit Permission' : 'Create Permission'}
          initial={permissionModal.permission}
          onClose={() => setPermissionModal(null)}
          onSave={permissionModal.id ? savePermission : async (permission) => { await createPermission(permission); setPermissionModal(null) }}
          saving={false}
        />
      )}
    </div>
  )
}
