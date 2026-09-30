import { useEffect, useMemo, useState } from 'react'
import * as miscApi from '../../api/misc'
import Pagination from '../../components/Pagination'
import { usePageCrumb } from '../../components/Layout'
import { useAuth } from '../../context/AuthContext'
import { useToast } from '../../context/ToastContext'
import { useConfirm } from '../../context/ConfirmContext'

const TYPES = ['string', 'number', 'boolean', 'json', 'path', 'url', 'secret']
const EMPTY_FORM = { group: 'GENERAL', customGroup: '', key: '', value: '', content: '', type: 'string', description: '', is_secret: false, is_active: true }

function valueToText(value) {
  if (value === null || value === undefined) return ''
  return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)
}

function splitSuggestion(value) {
  if (!value || !value.includes('.')) return { group: '', key: value || '' }
  const [group, ...rest] = value.split('.')
  return { group, key: rest.join('.') }
}

function effectiveGroup(form) {
  return form.group === '__CUSTOM__' ? form.customGroup.trim().toUpperCase() : form.group
}

function formPayload(form) {
  let value = form.value
  if (form.type === 'boolean') value = ['true', '1', 'yes', 'y'].includes(String(form.value).toLowerCase())
  if (form.type === 'number') value = form.value === '' ? null : Number(form.value)
  if (form.type === 'json' && form.value) value = JSON.parse(form.value)
  return {
    group: effectiveGroup(form),
    key: form.key.trim(),
    value,
    content: form.content,
    type: form.type,
    description: form.description,
    is_secret: form.is_secret || form.type === 'secret',
    is_active: form.is_active,
  }
}

export default function AppConfig() {
  usePageCrumb('Settings / <b>Key-Value Configuration</b>')
  const { can } = useAuth()
  const showToast = useToast()
  const confirm = useConfirm()
  const [rows, setRows] = useState([])
  const [options, setOptions] = useState({ groups: ['GENERAL'], keys: [], detected_keys: [], predefined_groups: [] })
  const [pagination, setPagination] = useState({ page: 1, page_size: 50, total_pages: 1, total: 0 })
  const [filters, setFilters] = useState({ q: '', group: '', key: '', active: '', type: '' })
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [editing, setEditing] = useState(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const [error, setError] = useState('')
  const [isFormOpen, setIsFormOpen] = useState(false)
  const [showSecretValue, setShowSecretValue] = useState(false)

  const canEdit = can('settings', 'update')
  const canDelete = can('settings', 'delete')

  useEffect(() => {
    if (!isFormOpen) return
    const onKeyDown = (event) => {
      if (event.key === 'Escape') closeForm()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [isFormOpen])

  async function loadOptions() {
    try { setOptions(await miscApi.appConfigOptions()) } catch (_) { /* keep defaults */ }
  }

  async function load(page = pagination.page) {
    setLoading(true)
    setError('')
    try {
      const params = {
        page,
        pageSize: pagination.page_size,
        q: filters.q,
        group: filters.group,
        key: filters.key,
        active: filters.active,
      }
      const data = await miscApi.listAppConfig(params)
      setRows(data.items || [])
      setPagination({ page: data.page, page_size: data.page_size, total_pages: data.total_pages, total: data.total })
      await loadOptions()
    } catch (err) { setError(err.message) }
    finally { setLoading(false) }
  }

  useEffect(() => { load(1); loadOptions() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  function closeForm() {
    setIsFormOpen(false)
    setError('')
    setShowSecretValue(false)
  }

  function startCreate() {
    setEditing(null)
    setForm(EMPTY_FORM)
    setError('')
    setShowSecretValue(false)
    setIsFormOpen(true)
  }

  function applyKeySuggestion(value) {
    const parsed = splitSuggestion(value)
    setForm((current) => ({ ...current, group: parsed.group || current.group, key: parsed.key || current.key }))
  }

  function startEdit(row) {
    setEditing(row)
    setForm({
      group: row.group || 'GENERAL', customGroup: '', key: row.key,
      value: valueToText(row.value), content: row.content || '', type: row.type,
      description: row.description || '', is_secret: row.is_secret, is_active: row.is_active,
    })
    setError('')
    setShowSecretValue(false)
    setIsFormOpen(true)
  }

  async function save(event) {
    event.preventDefault()
    if (!canEdit) return
    setSaving(true)
    setError('')
    try {
      const payload = formPayload(form)
      if (!payload.group || !payload.key) throw new Error('Group and key are required')
      if (editing) await miscApi.updateAppConfig(editing.id, payload)
      else await miscApi.createAppConfig(payload)
      showToast(editing ? 'Configuration updated.' : 'Configuration created.')
      closeForm()
      await load(editing ? pagination.page : 1)
    } catch (err) { setError(err.message) }
    finally { setSaving(false) }
  }

  async function toggle(row) {
    if (!canEdit) return
    try { await miscApi.toggleAppConfig(row.id); await load(pagination.page) }
    catch (err) { setError(err.message) }
  }

  async function remove(row) {
    if (!canDelete) return
    if (!(await confirm({ title: 'Delete Configuration', message: `Delete ${row.group}.${row.key}? This cannot be undone.` }))) return
    try { await miscApi.deleteAppConfig(row.id); showToast('Configuration deleted.'); await load(pagination.page) }
    catch (err) { setError(err.message) }
  }

  const groupChoices = useMemo(() => Array.from(new Set([...(options.groups || []), 'FILE_STORAGE', 'BLOCK_STORAGE', 'DATABASE', 'AUTH', 'API', 'GENERAL'])).sort(), [options.groups])
  const keySuggestions = options.keys || []
  const visibleRows = useMemo(() => {
    if (!filters.type) return rows
    return rows.filter((row) => String(row.type).toLowerCase() === String(filters.type).toLowerCase())
  }, [rows, filters.type])

  const valueInput = useMemo(() => {
    const isSecretInput = form.is_secret || form.type === 'secret'
    if (form.type === 'json') return <textarea rows={6} value={form.value} onChange={(e) => setForm({ ...form, value: e.target.value })} placeholder='{"key":"value"}' />
    if (form.type === 'boolean') return <select value={String(form.value || 'false')} onChange={(e) => setForm({ ...form, value: e.target.value })}><option value="true">true</option><option value="false">false</option></select>
    return (
      <div className="config-value-field">
        <input type={isSecretInput && !showSecretValue ? 'password' : 'text'} value={form.value} onChange={(e) => setForm({ ...form, value: e.target.value })} />
        {isSecretInput && (
          <button type="button" className="config-visibility-btn" onClick={() => setShowSecretValue((current) => !current)}>
            {showSecretValue ? 'Hide' : 'Show'}
          </button>
        )}
      </div>
    )
  }, [form, showSecretValue])

  return (
    <>
      <div className="config-page">
        <div className="config-header-row">
          <div>
            <h1 className="page-title">Key-Value Configuration</h1>
            <p className="page-sub">Map storage paths, URLs, passwords, API keys, and application variables into database-backed configuration.</p>
          </div>
          {canEdit && (
            <button type="button" className="btn primary" onClick={startCreate}>+ Add Configuration</button>
          )}
        </div>

        <div className="card config-list-card">
          <div className="list-header-row">
            <div>
              <h3>Configuration Keys</h3>
              <p>Manage and review existing application configuration.</p>
            </div>
            <span className="list-meta">{pagination.total} key(s)</span>
          </div>

          <div className="toolbar config-toolbar">
            <div className="toolbar-left config-filterbar">
              <input placeholder="Search group, key, description" value={filters.q} onChange={(e) => setFilters({ ...filters, q: e.target.value })} />
              <select value={filters.group} onChange={(e) => setFilters({ ...filters, group: e.target.value })}><option value="">All groups</option>{groupChoices.map((group) => <option key={group} value={group}>{group}</option>)}</select>
              <input placeholder="Key filter" value={filters.key} onChange={(e) => setFilters({ ...filters, key: e.target.value })} />
              <select value={filters.type} onChange={(e) => setFilters({ ...filters, type: e.target.value })}><option value="">All types</option>{TYPES.map((type) => <option key={type} value={type}>{type}</option>)}</select>
              <button className="btn" onClick={() => load(1)} disabled={loading}>{loading ? 'Loading...' : 'Search'}</button>
              <button type="button" className="btn" onClick={() => { setFilters({ q: '', group: '', key: '', active: '', type: '' }); load(1) }}>Reset</button>
            </div>
          </div>

          {loading ? (
            <div className="loading">Loading configuration...</div>
          ) : (
            <div className="table-scroll">
              <table className="config-table">
                <thead>
                  <tr>
                    <th>Group</th>
                    <th>Key</th>
                    <th>Value</th>
                    <th>Type</th>
                    <th>Secret</th>
                    <th>Status</th>
                    <th>Description</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleRows.length === 0 && (
                    <tr>
                      <td colSpan={8} className="empty-row">
                        <div className="empty-state-box">
                          <div className="empty-icon">⚙</div>
                          <strong>No configuration keys yet</strong>
                          <span>Create your first configuration key to manage application settings and environment values.</span>
                          <button type="button" className="btn primary" onClick={startCreate}>+ Add Configuration</button>
                        </div>
                      </td>
                    </tr>
                  )}
                  {visibleRows.map((row) => (
                    <tr key={row.id}>
                      <td>{row.group}</td>
                      <td><code>{row.key}</code></td>
                      <td className="config-value-cell">{valueToText(row.value)}</td>
                      <td>{row.type}</td>
                      <td>{row.is_secret ? '🔒 Encrypted' : '—'}</td>
                      <td>{row.is_active ? <span className="chip green">Active</span> : <span className="chip gray">Inactive</span>}</td>
                      <td>{row.description || '—'}</td>
                      <td>
                        <div className="table-actions">
                          <button className="row-action-btn" disabled={!canEdit} title={canEdit ? '' : "You don't have permission to edit configuration."} onClick={() => startEdit(row)}>✎ Edit</button>
                          <button className={`row-action-btn ${row.is_active ? '' : 'success'}`} disabled={!canEdit} title={canEdit ? '' : "You don't have permission to edit configuration."} onClick={() => toggle(row)}>{row.is_active ? '⏸ Disable' : '▶ Enable'}</button>
                          <button className="row-action-btn danger" disabled={!canDelete} title={canDelete ? '' : "You don't have permission to delete configuration."} onClick={() => remove(row)}>🗑 Delete</button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <Pagination page={pagination.page} pageSize={pagination.page_size} total={pagination.total}
            totalPages={pagination.total_pages} onPageChange={load} />
        </div>
      </div>

      {isFormOpen && (
        <div className="config-modal-overlay" onClick={(e) => e.target === e.currentTarget && closeForm()}>
          <div className="config-modal" onClick={(e) => e.stopPropagation()}>
            <div className="config-modal-head">
              <div>
                <h3>{editing ? 'Edit Configuration' : 'Add Configuration'}</h3>
                <p>{editing ? 'Update the selected configuration value and metadata.' : 'Create a new configuration key and define how it should be stored and used.'}</p>
              </div>
              <button type="button" className="close-x" onClick={closeForm} aria-label="Close">×</button>
            </div>

            <div className="config-modal-body">
              <form onSubmit={save}>
                <div className="config-form-section">
                  <div className="config-section-label">Configuration</div>
                  <div className="config-grid modal-grid">
                    <div className="form-field">
                      <label>Group</label>
                      <select value={form.group} onChange={(e) => setForm({ ...form, group: e.target.value })}>
                        {groupChoices.map((group) => <option key={group} value={group}>{group}</option>)}
                        <option value="__CUSTOM__">Custom group</option>
                      </select>
                    </div>

                    <div className="form-field">
                      <label>Type</label>
                      <select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value, is_secret: e.target.value === 'secret' ? true : form.is_secret })}>{TYPES.map((type) => <option key={type} value={type}>{type}</option>)}</select>
                    </div>

                    {form.group === '__CUSTOM__' && (
                      <div className="form-field wide">
                        <label>Custom Group</label>
                        <input value={form.customGroup} onChange={(e) => setForm({ ...form, customGroup: e.target.value })} placeholder="MY_GROUP" />
                      </div>
                    )}

                    <div className="form-field wide">
                      <label>Key</label>
                      <input list="app-config-keys" value={form.key} onChange={(e) => setForm({ ...form, key: e.target.value })} placeholder="OUTPUT_ROOT" required />
                      <datalist id="app-config-keys">{keySuggestions.map((item) => <option key={item} value={item} />)}</datalist>
                      <small>Pick a detected key or type a new one manually.</small>
                    </div>

                    <div className="form-field wide">
                      <label>Apply detected key</label>
                      <select defaultValue="" onChange={(e) => e.target.value && applyKeySuggestion(e.target.value)}>
                        <option value="">Select detected/configured key</option>
                        {keySuggestions.map((item) => <option key={item} value={item}>{item}</option>)}
                      </select>
                    </div>

                    <div className="form-field wide">
                      <label>Value</label>
                      {valueInput}
                    </div>

                    <div className="form-field wide">
                      <label>Content / Notes</label>
                      <textarea rows={4} value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} placeholder="Optional structured notes, storage config, or usage context" />
                    </div>

                    <div className="form-field wide">
                      <label>Description</label>
                      <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="Short summary of this setting" />
                    </div>
                  </div>
                </div>

                <div className="config-form-section">
                  <div className="config-section-label">Security</div>
                  <div className="config-check-row">
                    <label className="config-toggle-option">
                      <input type="checkbox" checked={form.is_secret} onChange={(e) => setForm({ ...form, is_secret: e.target.checked })} />
                      <span>
                        <strong>Secret / encrypted</strong>
                        <small>Protect sensitive data</small>
                      </span>
                    </label>

                    <label className="config-toggle-option active-option">
                      <input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} />
                      <span>
                        <strong>Active</strong>
                        <small>Configuration enabled</small>
                      </span>
                    </label>
                  </div>
                </div>

                {error && <div className="error-text">{error}</div>}

                <div className="config-actions modal-actions">
                  <button type="button" className="btn" onClick={closeForm}>Cancel</button>
                  <button className="btn primary" disabled={!canEdit || saving}>{saving ? 'Saving...' : editing ? 'Update Configuration' : 'Add Configuration'}</button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
