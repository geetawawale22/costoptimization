import { useEffect, useMemo, useState } from 'react'
import * as commodityApi from '../../api/commodities'
import { usePageCrumb } from '../../components/Layout'
import { useAuth } from '../../context/AuthContext'
import { useToast } from '../../context/ToastContext'
import { useConfirm } from '../../context/ConfirmContext'

function CommodityModal({ commodity, onClose, onSave, saving }) {
  const [draft, setDraft] = useState({ code: commodity?.code || '', name: commodity?.name || '' })
  const isEdit = !!commodity

  useEffect(() => {
    const closeOnEscape = (event) => event.key === 'Escape' && onClose()
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [onClose])

  return (
    <div className="config-modal-overlay" onClick={(event) => event.target === event.currentTarget && onClose()}>
      <div className="config-modal permission-definition-modal" role="dialog" aria-modal="true" aria-label={isEdit ? 'Edit Commodity Type' : 'Add Commodity Type'}>
        <header className="config-modal-head">
          <div><h2>{isEdit ? `Edit Commodity Type — ${commodity.name}` : 'Add Commodity Type'}</h2></div>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close">×</button>
        </header>
        <div className="config-modal-body">
          <div className="form-field">
            <label>Code</label>
            <input autoFocus={!isEdit} disabled={isEdit} value={draft.code} onChange={(e) => setDraft((v) => ({ ...v, code: e.target.value.trim() }))} placeholder="e.g. new_commodity" />
            {isEdit && <small>Codes can't be changed after creation.</small>}
          </div>
          <div className="form-field">
            <label>Name</label>
            <input autoFocus={isEdit} value={draft.name} onChange={(e) => setDraft((v) => ({ ...v, name: e.target.value }))} placeholder="e.g. New Commodity" />
          </div>
        </div>
        <footer className="config-modal-footer">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="button" className="btn primary" disabled={!draft.code || !draft.name || saving} onClick={() => onSave(draft)}>
            {saving ? 'Saving...' : isEdit ? 'Save Changes' : 'Add Commodity Type'}
          </button>
        </footer>
      </div>
    </div>
  )
}

export default function MasterConfiguration() {
  usePageCrumb('Settings / <b>Master Configuration</b>')
  const { can } = useAuth()
  const showToast = useToast()
  const confirm = useConfirm()

  const [commodities, setCommodities] = useState([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [modal, setModal] = useState(null)
  const [saving, setSaving] = useState(false)

  const canUpdate = can('commodity_config', 'update')
  const canCreate = can('commodity_config', 'create')
  const canDelete = can('commodity_config', 'delete')

  function refresh() {
    setLoading(true)
    commodityApi.listCommodities().then(setCommodities).finally(() => setLoading(false))
  }

  useEffect(refresh, [])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return commodities
    return commodities.filter((c) => `${c.code} ${c.name}`.toLowerCase().includes(q))
  }, [commodities, query])

  const activeCount = commodities.filter((c) => c.is_active).length
  const inactiveCount = commodities.length - activeCount

  async function saveCommodity(draft) {
    setSaving(true)
    try {
      if (modal.commodity) {
        await commodityApi.updateCommodity(modal.commodity.id, { ...modal.commodity, name: draft.name })
        showToast('Commodity type updated.')
      } else {
        await commodityApi.createCommodity(draft)
        showToast('Commodity type created.')
      }
      setModal(null)
      refresh()
    } catch (err) {
      showToast(err.message)
    } finally {
      setSaving(false)
    }
  }

  async function handleToggleActive(c) {
    try {
      await commodityApi.updateCommodity(c.id, { ...c, is_active: !c.is_active })
      showToast(c.is_active ? 'Commodity type deactivated.' : 'Commodity type activated.')
      refresh()
    } catch (err) { showToast(err.message) }
  }

  async function handleDelete(c) {
    if (!(await confirm({ title: 'Delete Commodity Type', message: `Delete "${c.name}" (${c.code})? This cannot be undone.` }))) return
    try {
      await commodityApi.deleteCommodity(c.id)
      showToast('Commodity type deleted.')
      refresh()
    } catch (err) { showToast(err.message) }
  }

  return (
    <div className="masterconfig-page">
      <div className="settings-page-header">
        <div className="settings-header-row">
          <div>
            <h1 className="page-title">Master Configuration</h1>
            <p className="page-sub">Manage commodity types — the "no re-coding" config engine.</p>
          </div>
          <button className="btn primary" disabled={!canCreate} title={canCreate ? '' : "You don't have permission to add commodity types."} onClick={() => setModal({ commodity: null })}>
            + Add Commodity Type
          </button>
        </div>
      </div>

      <div className="row">
        <div className="stat-card"><div className="lbl">Total</div><div className="val">{commodities.length}</div></div>
        <div className="stat-card"><div className="lbl">Active</div><div className="val">{activeCount}</div></div>
        <div className="stat-card"><div className="lbl">Inactive</div><div className="val">{inactiveCount}</div></div>
      </div>

      <div className="card">
        <div className="toolbar">
          <div className="toolbar-left">
            <input className="search-box" placeholder="Search commodity types..." value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
        </div>

        {loading ? (
          <div className="loading">Loading commodity types...</div>
        ) : (
          <div className="table-scroll">
            <table>
              <thead><tr><th>Code</th><th>Name</th><th>Status</th><th>Actions</th></tr></thead>
              <tbody>
                {filtered.map((c) => (
                  <tr key={c.id}>
                    <td><code>{c.code}</code></td>
                    <td>{c.name}</td>
                    <td><span className={`chip ${c.is_active ? 'green' : 'gray'}`}>{c.is_active ? 'Active' : 'Inactive'}</span></td>
                    <td>
                      <div className="table-actions">
                        <button className="row-action-btn" disabled={!canUpdate} title={canUpdate ? 'Edit' : "You don't have permission to edit commodity types."} onClick={() => setModal({ commodity: c })}>✎ Edit</button>
                        <button
                          className={`row-action-btn ${c.is_active ? '' : 'success'}`}
                          disabled={!canUpdate}
                          title={canUpdate ? (c.is_active ? 'Deactivate' : 'Activate') : "You don't have permission to edit commodity types."}
                          onClick={() => handleToggleActive(c)}
                        >
                          {c.is_active ? '⏸ Deactivate' : '▶ Activate'}
                        </button>
                        <button className="row-action-btn danger" disabled={!canDelete} title={canDelete ? 'Delete' : "You don't have permission to delete commodity types."} onClick={() => handleDelete(c)}>🗑 Delete</button>
                      </div>
                    </td>
                  </tr>
                ))}
                {filtered.length === 0 && <tr><td colSpan={4} className="empty-row">No commodity types match "{query}".</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {modal && (
        <CommodityModal
          commodity={modal.commodity}
          saving={saving}
          onClose={() => setModal(null)}
          onSave={saveCommodity}
        />
      )}
    </div>
  )
}
