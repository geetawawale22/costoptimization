import ModalCloseButton from '../components/ModalCloseButton'
import React from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'
import * as miscApi from '../services/api'
import Pagination from '../components/Pagination'
import { usePageCrumb } from '../layouts/ProtectedLayout'
import { useAuth } from '../auth/AuthContext'
import { useToast } from '../context/ToastContext'
import { useConfirm } from '../context/ConfirmContext'

const TYPES = ['string', 'number', 'boolean', 'json', 'path', 'url', 'secret']
const TYPE_LABELS = { string: 'Text', number: 'Number', boolean: 'Yes / No', json: 'Structured settings (JSON)', path: 'Folder or file path', url: 'Web address', secret: 'Secret' }
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
  if (form.type === 'number' && value !== null && !Number.isFinite(value)) throw new Error('Enter a valid number for the setting value.')
  if (form.type === 'json' && form.value) {
    try { value = JSON.parse(form.value) } catch { throw new Error('The structured value is not valid JSON. Check brackets, quotes and commas before saving.') }
  }
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

function SettingValue({ row }) {
  if (row.is_secret) return <span>Saved secret (hidden)</span>
  const value = row.value ?? row.content
  if (value === null || value === undefined || value === '') return <span>No value entered</span>
  if (row.type === 'json' || typeof value === 'object' || String(value).length > 160) return <details>
    <summary>View saved setting details</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{valueToText(value)}</pre>
  </details>
  if (row.type === 'boolean') return <span>{value === true || value === 'true' ? 'Yes' : 'No'}</span>
  return <span>{valueToText(value)}</span>
}

export default function AppConfig() {
  usePageCrumb('Settings / <b>Application Settings</b>')
  const { can, reloadAppearance } = useAuth()
  const showToast = useToast()
  const confirm = useConfirm()
  const [rows, setRows] = useState([])
  const [options, setOptions] = useState({ groups: ['GENERAL'], keys: [], detected_keys: [], predefined_groups: [] })
  const [pagination, setPagination] = useState({ page: 1, page_size: 20, total_pages: 1, total: 0 })
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

  const filterRequest = useRef(0)
  async function load(page = pagination.page, selectedFilters = filters) {
    const requestId = ++filterRequest.current
    setLoading(true)
    setError('')
    try {
      const params = {
        page,
        pageSize: 20,
        q: selectedFilters.q.trim(),
        group: selectedFilters.group,
        key: selectedFilters.key,
        active: selectedFilters.active,
        type: selectedFilters.type,
      }
      const data = await miscApi.listAppConfig(params)
      if (requestId !== filterRequest.current) return
      setRows(data.items || [])
      setPagination({ page: data.page, page_size: data.page_size, total_pages: data.total_pages, total: data.total })
      await loadOptions()
    } catch (err) { if (requestId === filterRequest.current) setError(err.message) }
    finally { if (requestId === filterRequest.current) setLoading(false) }
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
      if (payload.group === 'OUTPUT_TEMPLATE') throw new Error('Use Output Templates to validate and save Excel layouts as a new version.')
      if (editing) await miscApi.updateAppConfig(editing.id, payload)
      else await miscApi.createAppConfig(payload)
      showToast(editing ? 'Configuration updated.' : 'Configuration created.')
      closeForm()
      await load(editing ? pagination.page : 1)
      await reloadAppearance()
    } catch (err) { setError(err.message) }
    finally { setSaving(false) }
  }

  async function toggle(row) {
    if (!canEdit) return
    try { await miscApi.toggleAppConfig(row.id); await load(pagination.page); await reloadAppearance() }
    catch (err) { setError(err.message) }
  }

  async function remove(row) {
    if (!canDelete) return
    if (!(await confirm({ title: 'Delete Configuration', message: `Delete ${row.group}.${row.key}? This cannot be undone.` }))) return
    try { await miscApi.deleteAppConfig(row.id); showToast('Configuration deleted.'); await load(pagination.page); await reloadAppearance() }
    catch (err) { setError(err.message) }
  }

  const groupChoices = useMemo(() => Array.from(new Set([...(options.groups || []), 'FILE_STORAGE', 'BLOCK_STORAGE', 'DATABASE', 'AUTH', 'API', 'GENERAL'])).sort(), [options.groups])
  const keySuggestions = options.keys || []
  const visibleRows = rows

  const valueInput = useMemo(() => {
    const isSecretInput = form.is_secret || form.type === 'secret'
    if (form.type === 'json') return <textarea id="setting-value" rows={6} value={form.value} onChange={(e) => setForm({ ...form, value: e.target.value })} placeholder='{"key":"value"}' />
    if (form.type === 'boolean') return <select id="setting-value" value={String(form.value || 'false')} onChange={(e) => setForm({ ...form, value: e.target.value })}><option value="true">Yes</option><option value="false">No</option></select>
    return (
      <div className="config-value-field">
        <input id="setting-value" type={isSecretInput && !showSecretValue ? 'password' : 'text'} value={form.value} onChange={(e) => setForm({ ...form, value: e.target.value })} />
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
            <h1 className="page-title">Application Settings</h1>
            <p className="page-sub">Manage named application settings such as storage locations, service addresses and secrets. Group related settings so administrators can find them easily.</p>
          </div>
          {canEdit && (
            <button type="button" className="btn primary" onClick={startCreate}>+ Add Configuration</button>
          )}
        </div>


        <details className="configuration-section"><summary>How application settings are used</summary><p>Settings are saved in the database. A setting takes effect only where the application uses its group and key; adding a key alone does not create a calculation or connect a new service. Structured settings are available for advanced administration.</p></details>
        {error && !isFormOpen && <p role="alert">{error}</p>}
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
              <input aria-label="Search application settings" placeholder="Search group, key, description" value={filters.q} onChange={(e) => setFilters({ ...filters, q: e.target.value })} />
              <select aria-label="Filter settings by group" value={filters.group} onChange={(e) => setFilters({ ...filters, group: e.target.value })}><option value="">All groups</option>{groupChoices.map((group) => <option key={group} value={group}>{group}</option>)}</select>
              <input aria-label="Filter setting key" placeholder="Key filter" value={filters.key} onChange={(e) => setFilters({ ...filters, key: e.target.value })} />
              <select aria-label="Filter settings by type" value={filters.type} onChange={(e) => setFilters({ ...filters, type: e.target.value })}><option value="">All value types</option>{TYPES.map((type) => <option key={type} value={type}>{TYPE_LABELS[type]}</option>)}</select>
              <select aria-label="Filter settings by status" value={filters.active} onChange={(e) => setFilters({ ...filters, active: e.target.value })}><option value="">All statuses</option><option value="true">Active</option><option value="false">Inactive</option></select>
              <button className="btn" onClick={() => load(1)} disabled={loading} aria-busy={loading}>{loading ? 'Loading...' : 'Search'}</button>
              <button type="button" className="btn" onClick={() => { const cleared = { q: '', group: '', key: '', active: '', type: '' }; setFilters(cleared); load(1, cleared) }}>Reset</button>
            </div>
          </div>

          {loading ? (
            <div className="loading">Loading configuration...</div>
          ) : (
            <div className="table-scroll" role="region" aria-label="Application settings" tabIndex={0}>
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
                          <strong>No settings match these filters</strong>
                          <span>Reset the filters to review all settings, or add a setting if one is required.</span>
                          {canEdit && <button type="button" className="btn primary" onClick={startCreate}>+ Add Configuration</button>}
                        </div>
                      </td>
                    </tr>
                  )}
                  {visibleRows.map((row) => (
                    <tr key={row.id}>
                      <td>{row.group}</td>
                      <td><code>{row.key}</code></td>
                      <td className="config-value-cell"><SettingValue row={row} /></td>
                      <td>{TYPE_LABELS[row.type] || row.type}</td>
                      <td>{row.is_secret ? '🔒 Encrypted' : '—'}</td>
                      <td>{row.is_active ? <span className="chip green">Active</span> : <span className="chip gray">Inactive</span>}</td>
                      <td>{row.description || '—'}</td>
                      <td>
                        <div className="table-actions">
                          <>
                            <button className="row-action-btn" disabled={!canEdit} title={canEdit ? '' : "You don't have permission to edit configuration."} onClick={() => startEdit(row)}>Edit</button>
                            <button className={`row-action-btn ${row.is_active ? '' : 'success'}`} disabled={!canEdit} title={canEdit ? '' : "You don't have permission to edit configuration."} onClick={() => toggle(row)}>{row.is_active ? 'Disable' : 'Enable'}</button>
                            <button className="row-action-btn danger" disabled={!canDelete} title={canDelete ? '' : "You don't have permission to delete configuration."} onClick={() => remove(row)}>Delete</button>
                          </>
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
          <div className="config-modal" role="dialog" aria-modal="true" aria-label={editing ? 'Edit Configuration' : 'Add Configuration'} onClick={(e) => e.stopPropagation()}>
            <div className="config-modal-head">
              <div>
                <h3>{editing ? 'Edit Configuration' : 'Add Configuration'}</h3>
                <p>{editing ? 'Update the selected configuration value and metadata.' : 'Create a new configuration key and define how it should be stored and used.'}</p>
              </div>
              <ModalCloseButton onClick={closeForm} aria-label="Close" />
            </div>

            <div className="config-modal-body">
              <form onSubmit={save}>
                <div className="config-form-section">
                  <div className="config-section-label">Configuration</div>
                  <div className="config-grid modal-grid">
                    <div className="form-field">
                      <label htmlFor="setting-group">Group</label>
                      <select id="setting-group" value={form.group} onChange={(e) => setForm({ ...form, group: e.target.value })}>
                        {groupChoices.map((group) => <option key={group} value={group}>{group}</option>)}
                        <option value="__CUSTOM__">Custom group</option>
                      </select>
                    </div>

                    <div className="form-field">
                      <label htmlFor="setting-type">Value type</label>
                      <select id="setting-type" value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value, is_secret: e.target.value === 'secret' ? true : form.is_secret })}>{TYPES.map((type) => <option key={type} value={type}>{TYPE_LABELS[type]}</option>)}</select>
                    </div>

                    {form.group === '__CUSTOM__' && (
                      <div className="form-field wide">
                        <label htmlFor="setting-custom-group">Custom Group</label>
                        <input id="setting-custom-group" value={form.customGroup} onChange={(e) => setForm({ ...form, customGroup: e.target.value })} placeholder="MY_GROUP" />
                      </div>
                    )}

                    <div className="form-field wide">
                      <label htmlFor="setting-key">Key</label>
                      <input id="setting-key" list="app-config-keys" value={form.key} onChange={(e) => setForm({ ...form, key: e.target.value })} placeholder="OUTPUT_ROOT" required />
                      <datalist id="app-config-keys">{keySuggestions.map((item) => <option key={item} value={item} />)}</datalist>
                      <small>Use a key already recognized by the application, or enter a key needed by your configured integration.</small>
                    </div>

                    <div className="form-field wide">
                      <label htmlFor="setting-known-key">Use an existing or recognized key</label>
                      <select id="setting-known-key" defaultValue="" onChange={(e) => e.target.value && applyKeySuggestion(e.target.value)}>
                        <option value="">Select detected/configured key</option>
                        {keySuggestions.map((item) => <option key={item} value={item}>{item}</option>)}
                      </select>
                    </div>

                    <div className="form-field wide">
                      <label htmlFor="setting-value">Value</label>
                      {valueInput}
                      {editing?.is_secret && <small>The saved secret stays hidden. Leave the masked value unchanged to keep it, or enter a replacement.</small>}
                    </div>

                    <details className="form-field wide"><summary>Additional content (advanced)</summary><label htmlFor="setting-content">Content / Notes</label><textarea id="setting-content" rows={6} value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} placeholder="Optional setting content or notes" /><small>Some application policies use structured content here. Preserve the expected structure when editing an existing policy.</small></details>

                    <div className="form-field wide">
                      <label htmlFor="setting-description">Description</label>
                      <input id="setting-description" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="Explain what this setting controls" />
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

                {error && <div className="error-text" role="alert">{error}</div>}

                <div className="config-actions modal-actions">
                  <button type="button" className="btn" onClick={closeForm}>Cancel</button>
                  <button className="btn primary" disabled={!canEdit || saving} aria-busy={saving}>{saving ? 'Saving...' : editing ? 'Update Configuration' : 'Add Configuration'}</button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
