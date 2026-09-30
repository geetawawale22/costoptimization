import { useEffect, useState } from 'react'
import * as wb from '../../api/workbooks'
import Pagination from '../../components/Pagination'
import { usePageCrumb } from '../../components/Layout'
import { useAuth } from '../../context/AuthContext'
import { useToast } from '../../context/ToastContext'
import { useConfirm } from '../../context/ConfirmContext'

const DATA_TYPES = ['string', 'number', 'date', 'boolean', 'email-list']
const STRUCTURE_OPTIONS = [
  { value: 'single', label: 'Single header row' },
  { value: 'none', label: 'No header row (raw data)' },
  { value: 'merge', label: 'Two merged header rows (rows 1 & 2)' },
]

const toDisplay = (v) => (v === null || v === undefined || v === '') ? '' : Number(v) + 1
const toStored = (v) => {
  if (v === '' || v === null || v === undefined) return null
  return Math.max(0, Number(v) - 1)
}

// Derives the Excel column letter from a 1-based column number purely for display
// next to the "Column #" input — e.g. 4 -> "D", 27 -> "AA". Never sent to the
// backend and never used as a substitute for column_name/header_name; the backend
// computes the authoritative version itself (WorkbookTabColumn.excel_column).
function excelLetter(oneBasedPosition) {
  let n = Number(oneBasedPosition)
  if (!n || n < 1) return ''
  let s = ''
  while (n > 0) {
    const rem = (n - 1) % 26
    s = String.fromCharCode(65 + rem) + s
    n = Math.floor((n - 1) / 26)
  }
  return s
}

function structureFromHeaderType(headerType) {
  if (headerType === 'typeSingle') return 'none'
  if (headerType === 'typeMerge') return 'merge'
  return 'single' // type0 / type1 — internal bookkeeping only, never shown to the user
}

function emptyType() { return { code: '', name: '', description: '' } }
function emptyTab() { return { tab_name: '', header_row: 1, structure: 'single', read_only: false, has_multiple_sections: false } }
function emptySection() { return { section_name: '', header_row: 1, data_start_row: '', data_end_row: '', column_start: '', column_end: '', header_type: '', position: 0, description: '' } }
function emptyColumn() { return { section_id: '', column_name: '', header_name: '', header_group: '', header_row: '', column_position: '', data_type: 'string', is_unique_key: false, is_required: false, position: 0 } }
function emptyBulkRow() { return { column_name: '', header_name: '', header_group: '', data_type: 'string', is_required: false, is_unique_key: false, header_row: '', column_position: '' } }

const CONFIG_QUERY_EXAMPLE = JSON.stringify({
  workbook_type: { code: "pcm_master", name: "PCM Master", description: "Shared master sheets" },
  tabs: [
    {
      tab_name: "Net Consumption base",
      structure: "merge",            // "single" | "none" | "merge"
      header_row: 1,                 // group-label row (1-based) — used when structure = "merge"
      has_multiple_sections: false,
      sections: [],
      columns: [
        { column_name: "Con1", header_group: "Conditions", data_type: "string", position: 0 },
        { column_name: "Con2", header_group: "Conditions", data_type: "string", position: 1 },
        { column_name: "Cal RM cons", header_group: "{Net RM Consumption} Calculation", data_type: "string", position: 16 }
      ]
    },
    {
      tab_name: "Sheet With Two Tables",
      structure: "single",
      header_row: 1,
      has_multiple_sections: true,
      sections: [
        { section_name: "Consumption Table", header_row: 9, data_start_row: 10, position: 0 },
        { section_name: "Cost Table", header_row: 21, data_start_row: 22, position: 1 }
      ],
      columns: [
        { column_name: "Month", section_name: "Consumption Table", position: 0 },
        { column_name: "Customer", section_name: "Consumption Table", position: 1 },
        { column_name: "Month", section_name: "Cost Table", position: 0 },
        { column_name: "Cost", section_name: "Cost Table", position: 1 }
      ]
    }
  ]
}, null, 2)

function Modal({ title, subtitle, children, footer, onClose, className = '' }) {
  useEffect(() => {
    const closeOnEscape = (event) => event.key === 'Escape' && onClose()
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [onClose])

  return (
    <div className="config-modal-overlay" onClick={(event) => event.target === event.currentTarget && onClose()}>
      <div className={`config-modal ${className}`} role="dialog" aria-modal="true" aria-label={title} onClick={(event) => event.stopPropagation()}>
        <header className="config-modal-head">
          <div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close">×</button>
        </header>
        <div className="config-modal-body">{children}</div>
        <footer className="config-modal-footer">{footer}</footer>
      </div>
    </div>
  )
}

function TypeModal({ initial, isNew, onClose, onSave }) {
  const [draft, setDraft] = useState(initial)
  return (
    <Modal title={isNew ? 'New Workbook' : `Edit Workbook — ${initial.code}`} onClose={onClose} className="wb-modal-md" footer={<>
      <button type="button" className="btn" onClick={onClose}>Cancel</button>
      <button type="button" className="btn primary" disabled={!draft.code || !draft.name} onClick={() => onSave(draft)}>Save</button>
    </>}>
      <div className="config-grid">
        <div className="form-field"><label>Code</label><input disabled={!isNew} value={draft.code} onChange={(e) => setDraft((v) => ({ ...v, code: e.target.value }))} />{!isNew && <small>Codes can't be changed after creation.</small>}</div>
        <div className="form-field"><label>Name</label><input value={draft.name} onChange={(e) => setDraft((v) => ({ ...v, name: e.target.value }))} /></div>
        <div className="form-field wide"><label>Description</label><input value={draft.description || ''} onChange={(e) => setDraft((v) => ({ ...v, description: e.target.value }))} /></div>
      </div>
    </Modal>
  )
}

function TabModal({ initial, isNew, onClose, onSave }) {
  const [draft, setDraft] = useState(initial)
  return (
    <Modal title={isNew ? 'New Tab' : `Edit Tab — ${initial.tab_name}`} onClose={onClose} className="wb-modal-md" footer={<>
      <button type="button" className="btn" onClick={onClose}>Cancel</button>
      <button type="button" className="btn primary" disabled={!draft.tab_name} onClick={() => onSave(draft)}>Save</button>
    </>}>
      <div className="config-grid">
        <div className="form-field wide"><label>Tab Name</label><input value={draft.tab_name} onChange={(e) => setDraft((v) => ({ ...v, tab_name: e.target.value }))} /></div>
        <div className="form-field">
          <label>Structure</label>
          <select value={draft.structure} onChange={(e) => setDraft((v) => ({ ...v, structure: e.target.value }))}>
            {STRUCTURE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
        {draft.structure === 'single' && (
          <div className="form-field"><label>Header Row</label><input type="number" min="1" value={draft.header_row} onChange={(e) => setDraft((v) => ({ ...v, header_row: e.target.value }))} /></div>
        )}
        <div className="form-field">
          <label>Read Only</label>
          <select value={draft.read_only ? '1' : '0'} onChange={(e) => setDraft((v) => ({ ...v, read_only: e.target.value === '1' }))}>
            <option value="0">No</option><option value="1">Yes</option>
          </select>
        </div>
        <div className="form-field">
          <label>Multiple Sections</label>
          <select value={draft.has_multiple_sections ? '1' : '0'} onChange={(e) => setDraft((v) => ({ ...v, has_multiple_sections: e.target.value === '1' }))}>
            <option value="0">No</option><option value="1">Yes</option>
          </select>
        </div>
      </div>
    </Modal>
  )
}

function SectionModal({ initial, isNew, onClose, onSave }) {
  const [draft, setDraft] = useState(initial)
  return (
    <Modal title={isNew ? 'New Section' : `Edit Section — ${initial.section_name}`} onClose={onClose} className="wb-modal-lg" footer={<>
      <button type="button" className="btn" onClick={onClose}>Cancel</button>
      <button type="button" className="btn primary" disabled={!draft.section_name} onClick={() => onSave(draft)}>Save</button>
    </>}>
      <div className="config-grid">
        <div className="form-field wide"><label>Section Name</label><input value={draft.section_name} onChange={(e) => setDraft((v) => ({ ...v, section_name: e.target.value }))} /></div>
        <div className="form-field"><label>Header Row</label><input type="number" min="1" value={draft.header_row} onChange={(e) => setDraft((v) => ({ ...v, header_row: e.target.value }))} /></div>
        <div className="form-field"><label>Data Start Row</label><input type="number" min="1" value={draft.data_start_row} onChange={(e) => setDraft((v) => ({ ...v, data_start_row: e.target.value }))} /></div>
        <div className="form-field"><label>Data End Row</label><input type="number" min="1" value={draft.data_end_row} onChange={(e) => setDraft((v) => ({ ...v, data_end_row: e.target.value }))} /></div>
        <div className="form-field"><label>Column Start</label><input type="number" min="1" value={draft.column_start} placeholder="1" onChange={(e) => setDraft((v) => ({ ...v, column_start: e.target.value }))} /></div>
        <div className="form-field"><label>Column End</label><input type="number" min="1" value={draft.column_end} placeholder="last" onChange={(e) => setDraft((v) => ({ ...v, column_end: e.target.value }))} /></div>
        <div className="form-field">
          <label>Header Type</label>
          <select value={draft.header_type} onChange={(e) => setDraft((v) => ({ ...v, header_type: e.target.value }))}>
            <option value="">Single row</option>
            <option value="typeMerge">2-row merged header</option>
          </select>
        </div>
        <div className="form-field"><label>Position</label><input type="number" value={draft.position} onChange={(e) => setDraft((v) => ({ ...v, position: e.target.value }))} /></div>
        <div className="form-field wide"><label>Description</label><input value={draft.description || ''} onChange={(e) => setDraft((v) => ({ ...v, description: e.target.value }))} /></div>
      </div>
    </Modal>
  )
}

function ColumnModal({ initial, isNew, hasSections, sections, onClose, onSave }) {
  const [draft, setDraft] = useState(initial)
  const invalid = !draft.column_name || (hasSections && !draft.section_id)
  return (
    <Modal title={isNew ? 'New Column' : `Edit Column — ${initial.column_name}`} onClose={onClose} className="wb-modal-lg" footer={<>
      <button type="button" className="btn" onClick={onClose}>Cancel</button>
      <button type="button" className="btn primary" disabled={invalid} onClick={() => onSave(draft)}>Save</button>
    </>}>
      <div className="config-grid">
        {hasSections && (
          <div className="form-field wide">
            <label>Section</label>
            <select value={draft.section_id} onChange={(e) => setDraft((v) => ({ ...v, section_id: e.target.value }))}>
              <option value="">Select a section...</option>
              {sections.map((s) => <option key={s.id} value={s.id}>{s.section_name}</option>)}
            </select>
          </div>
        )}
        <div className="form-field"><label>Column Name (canonical)</label><input value={draft.column_name} onChange={(e) => setDraft((v) => ({ ...v, column_name: e.target.value }))} /></div>
        <div className="form-field"><label>Header Name (in source file)</label><input value={draft.header_name || ''} onChange={(e) => setDraft((v) => ({ ...v, header_name: e.target.value }))} placeholder="defaults to Column Name" /></div>
        <div className="form-field"><label>Header Group (optional — 2-row Excel header)</label><input value={draft.header_group || ''} onChange={(e) => setDraft((v) => ({ ...v, header_group: e.target.value }))} placeholder="e.g. Conditions" /></div>
        <div className="form-field"><label>Header Row Override</label><input type="number" min="1" value={draft.header_row} onChange={(e) => setDraft((v) => ({ ...v, header_row: e.target.value }))} /></div>
        <div className="form-field">
          <label>Column Position</label>
          <input type="number" min="1" value={draft.column_position} onChange={(e) => setDraft((v) => ({ ...v, column_position: e.target.value }))} />
          {draft.column_position !== '' && <small>= {excelLetter(draft.column_position)}</small>}
        </div>
        <div className="form-field">
          <label>Data Type</label>
          <select value={draft.data_type} onChange={(e) => setDraft((v) => ({ ...v, data_type: e.target.value }))}>
            {DATA_TYPES.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </div>
        <div className="form-field">
          <label>Required</label>
          <select value={draft.is_required ? '1' : '0'} onChange={(e) => setDraft((v) => ({ ...v, is_required: e.target.value === '1' }))}>
            <option value="0">No</option><option value="1">Yes</option>
          </select>
        </div>
        <div className="form-field">
          <label>Unique Key</label>
          <select value={draft.is_unique_key ? '1' : '0'} onChange={(e) => setDraft((v) => ({ ...v, is_unique_key: e.target.value === '1' }))}>
            <option value="0">No</option><option value="1">Yes</option>
          </select>
        </div>
        <div className="form-field"><label>Position</label><input type="number" value={draft.position} onChange={(e) => setDraft((v) => ({ ...v, position: e.target.value }))} /></div>
      </div>
    </Modal>
  )
}

function BulkAddModal({ hasSections, sections, onClose, onSave, onDetectFile, saving }) {
  const [sectionId, setSectionId] = useState('')
  const [rows, setRows] = useState([emptyBulkRow(), emptyBulkRow(), emptyBulkRow()])
  const [isMerge, setIsMerge] = useState(false)
  const [groupRow, setGroupRow] = useState(1)
  const [nameRow, setNameRow] = useState(2)

  function updateRow(idx, field, value) {
    setRows((prev) => prev.map((r, i) => (i === idx ? { ...r, [field]: value } : r)))
  }
  function addRow() { setRows((prev) => [...prev, emptyBulkRow()]) }
  function removeRow(idx) { setRows((prev) => prev.filter((_, i) => i !== idx)) }

  async function handleFile(e) {
    const file = e.target.files[0]
    if (!file) return
    const result = await onDetectFile(file, { isMerge, groupRow, nameRow, sectionId })
    e.target.value = ''
    if (!result) return
    if (result.auto_detected_two_row_header && !isMerge) {
      setIsMerge(true)
      setNameRow((Number(groupRow) || 1) + 1)
    }
    setRows(result.columns.map((c) => ({
      column_name: c.column_name, header_name: c.header_name, header_group: c.header_group || '',
      data_type: c.data_type, is_required: c.is_required, is_unique_key: c.is_unique_key,
      header_row: toDisplay(c.header_row), column_position: toDisplay(c.column_position),
    })))
  }

  const namedCount = rows.filter((r) => r.column_name.trim()).length

  return (
    <Modal title="Bulk Add Columns" subtitle="Fill in as many rows as you need, then Save All — one request, no repeated dialogs." onClose={onClose} className="wb-modal-xl" footer={<>
      <button type="button" className="btn" onClick={onClose} disabled={saving}>Cancel</button>
      <button type="button" className="btn primary" disabled={saving || namedCount === 0} onClick={() => onSave(rows, sectionId)}>
        {saving ? 'Saving...' : `Save All (${namedCount})`}
      </button>
    </>}>
      {hasSections && (
        <div className="form-field" style={{ maxWidth: 320, marginBottom: 14 }}>
          <label>Section (applies to every row below)</label>
          <select value={sectionId} onChange={(e) => setSectionId(e.target.value)}>
            <option value="">Select a section...</option>
            {sections.map((s) => <option key={s.id} value={s.id}>{s.section_name}</option>)}
          </select>
        </div>
      )}

      <div className="wb-bulk-toolbar">
        <label className="wb-bulk-checkbox">
          <input type="checkbox" checked={isMerge} onChange={(e) => setIsMerge(e.target.checked)} />
          This sheet has a 2-row header (group label + column name)
        </label>
        {isMerge && (
          <>
            <span>Group row</span>
            <input type="number" min="1" className="wb-bulk-narrow" value={groupRow} onChange={(e) => setGroupRow(e.target.value)} />
            <span>Name row</span>
            <input type="number" min="1" className="wb-bulk-narrow" value={nameRow} onChange={(e) => setNameRow(e.target.value)} />
          </>
        )}
        <input type="file" id="detect-file-input" accept=".xlsx" style={{ display: 'none' }} onChange={handleFile} />
        <button type="button" className="btn" onClick={() => document.getElementById('detect-file-input').click()}>📄 Detect from File</button>
      </div>

      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Column Name</th><th>Header (in file)</th><th>Header Group</th><th>Type</th>
              <th>Required</th><th>Unique</th><th>Header Row</th><th>Column #</th><th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, idx) => (
              <tr key={idx}>
                <td><input style={{ minWidth: 140 }} value={r.column_name} onChange={(e) => updateRow(idx, 'column_name', e.target.value)} /></td>
                <td><input style={{ minWidth: 140 }} placeholder="same as name" value={r.header_name} onChange={(e) => updateRow(idx, 'header_name', e.target.value)} /></td>
                <td><input style={{ minWidth: 140 }} placeholder="optional — e.g. Conditions" value={r.header_group} onChange={(e) => updateRow(idx, 'header_group', e.target.value)} /></td>
                <td>
                  <select value={r.data_type} onChange={(e) => updateRow(idx, 'data_type', e.target.value)}>
                    {DATA_TYPES.map((d) => <option key={d} value={d}>{d}</option>)}
                  </select>
                </td>
                <td style={{ textAlign: 'center' }}><input type="checkbox" checked={r.is_required} onChange={(e) => updateRow(idx, 'is_required', e.target.checked)} /></td>
                <td style={{ textAlign: 'center' }}><input type="checkbox" checked={r.is_unique_key} onChange={(e) => updateRow(idx, 'is_unique_key', e.target.checked)} /></td>
                <td><input type="number" min="1" style={{ width: 70 }} value={r.header_row} onChange={(e) => updateRow(idx, 'header_row', e.target.value)} /></td>
                <td>
                  <input type="number" min="1" style={{ width: 70 }} value={r.column_position} onChange={(e) => updateRow(idx, 'column_position', e.target.value)} />
                  {r.column_position !== '' && <span className="wb-excel-letter">= {excelLetter(r.column_position)}</span>}
                </td>
                <td><button className="kebab" title="Remove row" onClick={() => removeRow(idx)}>✕</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button type="button" className="btn" style={{ marginTop: 10 }} onClick={addRow}>+ Add Row</button>
    </Modal>
  )
}

export default function WorkbookConfig() {
  usePageCrumb('Settings / <b>Workbook Configuration</b>')
  const { can } = useAuth()
  const showToast = useToast()
  const confirm = useConfirm()

  const canCreate = can('workbook_config', 'create')
  const canUpdate = can('workbook_config', 'update')
  const canDelete = can('workbook_config', 'delete')

  // ---- Workbook Types ----
  const [typesMeta, setTypesMeta] = useState({ items: [], total: 0, page: 1, page_size: 20, total_pages: 1 })
  const [typesPage, setTypesPage] = useState(1)
  const [typeModal, setTypeModal] = useState(null) // null | { isNew, initial }
  const [selectedType, setSelectedType] = useState(null) // code string

  async function refreshTypes(page = typesPage) {
    const res = await wb.listWorkbookTypes(page, 20)
    setTypesMeta(res)
  }
  useEffect(() => { refreshTypes(typesPage) }, [typesPage]) // eslint-disable-line

  async function saveType(draft) {
    try {
      if (typeModal.isNew) {
        await wb.createWorkbookType(draft)
        showToast('Workbook created.')
      } else {
        await wb.updateWorkbookType(draft.code, { name: draft.name, description: draft.description })
        showToast('Workbook updated.')
      }
      setTypeModal(null)
      refreshTypes(1)
      setTypesPage(1)
    } catch (err) {
      showToast(err.message)
    }
  }

  async function deleteType(code) {
    if (!(await confirm({ title: 'Delete Workbook', message: `Delete workbook "${code}"? Its tabs/sections/columns/data will no longer be reachable.` }))) return
    try {
      await wb.deleteWorkbookType(code)
      showToast('Workbook deleted.')
      if (selectedType === code) setSelectedType(null)
      refreshTypes(typesPage)
    } catch (err) {
      showToast(err.message)
    }
  }

  async function deleteAllTypes() {
    if (!(await confirm({ title: 'Delete All Workbook Configurations', message: 'Delete ALL workbook configurations? This hides every workbook, tab, section, column, and data row linked to them.' }))) return
    try {
      const res = await wb.deleteAllWorkbookTypes()
      showToast(`${res.workbook_types_deleted || 0} workbook(s) deleted.`)
      setSelectedType(null)
      setSelectedTab(null)
      refreshTypes(1)
      setTypesPage(1)
    } catch (err) {
      showToast(err.message)
    }
  }

  // ---- Configuration Query ----
  const [queryOpen, setQueryOpen] = useState(false)
  const [queryText, setQueryText] = useState(CONFIG_QUERY_EXAMPLE)
  const [queryRunning, setQueryRunning] = useState(false)

  async function runConfigQuery() {
    let parsed
    try {
      parsed = JSON.parse(queryText)
    } catch (err) {
      showToast(`Invalid JSON: ${err.message}`)
      return
    }
    setQueryRunning(true)
    try {
      const res = await wb.applyConfigQuery(parsed)
      showToast(`Applied — ${res.tabs_created} tab(s) created, ${res.tabs_updated} updated, `
        + `${res.sections_created} section(s) created, ${res.columns_created} column(s) created, ${res.columns_updated} updated.`)
      setQueryOpen(false)
      refreshTypes(1)
      setTypesPage(1)
    } catch (err) {
      showToast(err.message)
    } finally {
      setQueryRunning(false)
    }
  }

  // ---- Tabs (of selectedType) ----
  const [tabsMeta, setTabsMeta] = useState({ items: [], total: 0, page: 1, page_size: 20, total_pages: 1 })
  const [tabsPage, setTabsPage] = useState(1)
  const [tabModal, setTabModal] = useState(null)
  const [selectedTab, setSelectedTab] = useState(null)

  async function refreshTabs(page = tabsPage) {
    if (!selectedType) return
    const res = await wb.listTabs(selectedType, page, 20)
    setTabsMeta(res)
  }
  useEffect(() => { setSelectedTab(null); setTabsPage(1); refreshTabs(1) }, [selectedType]) // eslint-disable-line
  useEffect(() => { refreshTabs(tabsPage) }, [tabsPage]) // eslint-disable-line

  async function saveTab(draft) {
    try {
      const payload = {
        tab_name: draft.tab_name,
        header_row_index: draft.structure === 'single' ? toStored(draft.header_row) ?? 0 : 0,
        structure: draft.structure,
        read_only: draft.read_only,
        has_multiple_sections: draft.has_multiple_sections,
      }
      if (tabModal.isNew) {
        await wb.createTab(selectedType, payload)
        showToast('Tab created.')
      } else {
        await wb.updateTab(selectedType, draft.tab_name, payload)
        showToast('Tab updated.')
      }
      setTabModal(null)
      refreshTabs(1)
      setTabsPage(1)
    } catch (err) {
      showToast(err.message)
    }
  }

  function openEditTab(t) {
    setTabModal({
      isNew: false,
      initial: {
        tab_name: t.tab_name,
        header_row: toDisplay(t.header_row_index) || 1,
        structure: structureFromHeaderType(t.header_type),
        read_only: t.read_only,
        has_multiple_sections: t.has_multiple_sections,
      },
    })
  }

  async function deleteTab(tabName) {
    if (!(await confirm({ title: 'Delete Tab', message: `Delete tab "${tabName}"? Its rows/sections/columns will no longer be reachable.` }))) return
    try {
      await wb.deleteTab(selectedType, tabName)
      showToast('Tab deleted.')
      if (selectedTab === tabName) setSelectedTab(null)
      refreshTabs(tabsPage)
    } catch (err) {
      showToast(err.message)
    }
  }

  async function deleteAllTabs() {
    if (!selectedType) return
    if (!(await confirm({ title: 'Delete All Tabs', message: `Delete ALL tabs in "${selectedType}"? Their rows/sections/columns will no longer be reachable.` }))) return
    try {
      const res = await wb.deleteAllTabs(selectedType)
      showToast(`${res.tabs_deleted || 0} tab(s) deleted.`)
      setSelectedTab(null)
      refreshTabs(1)
      setTabsPage(1)
    } catch (err) {
      showToast(err.message)
    }
  }

  // ---- Sections (of selectedTab) ----
  const [sectionsMeta, setSectionsMeta] = useState({ items: [], total: 0, page: 1, page_size: 20, total_pages: 1 })
  const [sectionsPage, setSectionsPage] = useState(1)
  const [sectionModal, setSectionModal] = useState(null)
  const [sectionProposal, setSectionProposal] = useState(null) // SectionDetectionResult
  const [applyingProposal, setApplyingProposal] = useState(false)

  async function refreshSections(page = sectionsPage) {
    if (!selectedType || !selectedTab) return
    const res = await wb.listSections(selectedType, selectedTab, page, 20)
    setSectionsMeta(res)
  }
  useEffect(() => { setSectionsPage(1); setSectionProposal(null); refreshSections(1); refreshColumns(1) }, [selectedTab]) // eslint-disable-line
  useEffect(() => { refreshSections(sectionsPage) }, [sectionsPage]) // eslint-disable-line

  async function saveSection(draft) {
    try {
      const payload = {
        section_name: draft.section_name,
        header_row: toStored(draft.header_row) ?? 0,
        data_start_row: toStored(draft.data_start_row),
        data_end_row: toStored(draft.data_end_row),
        column_start: toStored(draft.column_start),
        column_end: draft.column_end === '' ? null : Number(draft.column_end),
        header_type: draft.header_type || null,
        position: Number(draft.position) || 0,
        description: draft.description,
      }
      if (sectionModal.isNew) {
        await wb.createSection(selectedType, selectedTab, payload)
        showToast('Section created.')
      } else {
        await wb.updateSection(selectedType, selectedTab, sectionModal.id, payload)
        showToast('Section updated.')
      }
      setSectionModal(null)
      refreshSections(1)
      refreshTabs(tabsPage) // has_multiple_sections may have flipped
      setSectionsPage(1)
    } catch (err) {
      showToast(err.message)
    }
  }

  function openEditSection(s) {
    setSectionModal({
      isNew: false,
      id: s.id,
      initial: {
        section_name: s.section_name,
        header_row: toDisplay(s.header_row) || 1,
        data_start_row: toDisplay(s.data_start_row),
        data_end_row: toDisplay(s.data_end_row),
        column_start: toDisplay(s.column_start),
        column_end: s.column_end ?? '',
        header_type: s.header_type || '',
        position: s.position,
        description: s.description || '',
      },
    })
  }

  async function deleteSection(id) {
    if (!(await confirm({ title: 'Delete Section', message: 'Delete this section? Its columns will no longer be reachable.' }))) return
    try {
      await wb.deleteSection(selectedType, selectedTab, id)
      showToast('Section deleted.')
      refreshSections(sectionsPage)
    } catch (err) {
      showToast(err.message)
    }
  }

  async function deleteAllSections() {
    if (!selectedType || !selectedTab) return
    if (!(await confirm({ title: 'Delete All Sections', message: `Delete ALL sections in "${selectedTab}"? Section-specific columns will no longer be reachable.` }))) return
    try {
      const res = await wb.deleteAllSections(selectedType, selectedTab)
      showToast(`${res.sections_deleted || 0} section(s) deleted.`)
      setSectionProposal(null)
      refreshSections(1)
      refreshTabs(tabsPage)
      setSectionsPage(1)
    } catch (err) {
      showToast(err.message)
    }
  }

  async function handleDetectSectionsFile(e) {
    const file = e.target.files[0]
    if (!file) return
    try {
      const res = await wb.detectSections(selectedType, selectedTab, file)
      if (!res.has_multiple_sections) {
        showToast('Only one table detected in this file — use Bulk Add Columns → Detect from File instead.')
        return
      }
      setSectionProposal(res)
    } catch (err) {
      showToast(err.message)
    }
    e.target.value = ''
  }

  async function applySectionProposal() {
    setApplyingProposal(true)
    try {
      if (!tabsMeta.items.find((t) => t.tab_name === selectedTab)?.has_multiple_sections) {
        await wb.updateTab(selectedType, selectedTab, { has_multiple_sections: true })
      }
      for (const sec of sectionProposal.sections) {
        const created = await wb.createSection(selectedType, selectedTab, {
          section_name: sec.section_name, header_row: sec.header_row - 1, data_start_row: sec.data_start_row - 1,
          column_start: sec.column_start ? sec.column_start - 1 : null, column_end: sec.column_end ?? null,
          header_type: sec.header_type || null,
        })
        if (sec.columns.length > 0) {
          await wb.bulkCreateColumns(selectedType, selectedTab, sec.columns.map((c, i) => ({
            section_id: created.id, column_name: c.column_name, header_name: c.header_name,
            header_group: c.header_group || null, data_type: c.data_type, is_required: false,
            is_unique_key: false, position: i,
          })))
        }
      }
      showToast(`Applied ${sectionProposal.sections.length} section(s) with their columns.`)
      setSectionProposal(null)
      refreshTabs(tabsPage)
      refreshSections(1)
      setSectionsPage(1)
    } catch (err) {
      showToast(err.message)
    } finally {
      setApplyingProposal(false)
    }
  }

  // ---- Columns (of selectedTab) ----
  const [columnsMeta, setColumnsMeta] = useState({ items: [], total: 0, page: 1, page_size: 20, total_pages: 1 })
  const [columnsPage, setColumnsPage] = useState(1)
  const [columnModal, setColumnModal] = useState(null)

  async function refreshColumns(page = columnsPage) {
    if (!selectedType || !selectedTab) return
    const res = await wb.listColumns(selectedType, selectedTab, page, 20)
    setColumnsMeta(res)
  }
  useEffect(() => { refreshColumns(columnsPage) }, [columnsPage]) // eslint-disable-line

  async function saveColumn(draft) {
    try {
      const payload = {
        section_id: draft.section_id === '' ? null : Number(draft.section_id),
        column_name: draft.column_name,
        header_name: draft.header_name || null,
        header_group: draft.header_group || null,
        header_row: toStored(draft.header_row),
        column_position: toStored(draft.column_position),
        data_type: draft.data_type,
        is_unique_key: draft.is_unique_key,
        is_required: draft.is_required,
        position: Number(draft.position) || 0,
      }
      if (columnModal.isNew) {
        await wb.createColumn(selectedType, selectedTab, payload)
        showToast('Column created.')
      } else {
        await wb.updateColumn(selectedType, selectedTab, columnModal.id, payload)
        showToast('Column updated.')
      }
      setColumnModal(null)
      refreshColumns(1)
      setColumnsPage(1)
    } catch (err) {
      showToast(err.message)
    }
  }

  function openEditColumn(c) {
    setColumnModal({
      isNew: false,
      id: c.id,
      initial: {
        section_id: c.section_id ?? '',
        column_name: c.column_name,
        header_name: c.header_name || '',
        header_group: c.header_group || '',
        header_row: toDisplay(c.header_row),
        column_position: toDisplay(c.column_position),
        data_type: c.data_type,
        is_unique_key: c.is_unique_key,
        is_required: c.is_required,
        position: c.position,
      },
    })
  }

  async function deleteColumn(id) {
    if (!(await confirm({ title: 'Delete Column', message: 'Delete this column?' }))) return
    try {
      await wb.deleteColumn(selectedType, selectedTab, id)
      showToast('Column deleted.')
      refreshColumns(columnsPage)
    } catch (err) {
      showToast(err.message)
    }
  }

  async function deleteAllColumns() {
    if (!selectedType || !selectedTab) return
    if (!(await confirm({ title: 'Delete All Columns', message: `Delete ALL columns in "${selectedTab}"? Imported data for this tab may no longer display correctly.` }))) return
    try {
      const res = await wb.deleteAllColumns(selectedType, selectedTab)
      showToast(`${res.columns_deleted || 0} column(s) deleted.`)
      setBulkOpen(false)
      refreshColumns(1)
      setColumnsPage(1)
    } catch (err) {
      showToast(err.message)
    }
  }

  // ---- Bulk Add Columns — fill in every column of a sheet at once ----
  const [bulkOpen, setBulkOpen] = useState(false)
  const [bulkSaving, setBulkSaving] = useState(false)

  async function handleDetectFile(file, opts) {
    try {
      const res = await wb.detectColumns(selectedType, selectedTab, file, {
        headerRow: opts.isMerge ? Number(opts.groupRow) : undefined,
        secondHeaderRow: opts.isMerge ? Number(opts.nameRow) : undefined,
        sectionId: activeTabSummary?.has_multiple_sections ? Number(opts.sectionId) || undefined : undefined,
      })
      if (res.columns.length === 0) {
        showToast('No new columns detected — they may already be configured.')
        return null
      }
      showToast(res.auto_detected_two_row_header
        ? `Detected a 2-row header and combined both — review the group/name row and Save All.`
        : `Detected ${res.columns.length} column(s) — review and Save All.`)
      return res
    } catch (err) {
      showToast(err.message)
      return null
    }
  }

  async function saveBulkRows(rows, sectionId) {
    const named = rows.filter((r) => r.column_name.trim())
    if (named.length === 0) {
      showToast('Add at least one column name.')
      return
    }
    if (activeTabSummary?.has_multiple_sections && !sectionId) {
      showToast('Pick which section/table these columns belong to.')
      return
    }
    setBulkSaving(true)
    try {
      const payload = named.map((r, i) => ({
        section_id: activeTabSummary?.has_multiple_sections ? Number(sectionId) : null,
        column_name: r.column_name.trim(),
        header_name: r.header_name.trim() || null,
        header_group: r.header_group.trim() || null,
        header_row: toStored(r.header_row),
        column_position: toStored(r.column_position),
        data_type: r.data_type,
        is_unique_key: r.is_unique_key,
        is_required: r.is_required,
        position: i,
      }))
      await wb.bulkCreateColumns(selectedType, selectedTab, payload)
      showToast(`${payload.length} column(s) added.`)
      setBulkOpen(false)
      refreshColumns(1)
      setColumnsPage(1)
    } catch (err) {
      showToast(err.message)
    } finally {
      setBulkSaving(false)
    }
  }

  const activeTabSummary = tabsMeta.items.find((t) => t.tab_name === selectedTab)

  return (
    <div className="workbook-config-page">
      <div className="settings-page-header">
        <h1 className="page-title">Workbook Configuration</h1>
        <p className="page-sub">
          Manage every Masters / Commodity Types workbook: workbook → tab → table/section → column.
          Row and position numbers are entered the way you'd read them in Excel (row 1, row 2, ...).
        </p>
      </div>

      {/* ---------- Breadcrumb ---------- */}
      <div className="wb-breadcrumb">
        <button type="button" className={`wb-breadcrumb-item ${!selectedType ? 'active' : ''}`} onClick={() => { setSelectedType(null); setSelectedTab(null) }}>Workbooks</button>
        {selectedType && (
          <>
            <span className="wb-breadcrumb-sep">/</span>
            <button type="button" className={`wb-breadcrumb-item ${selectedType && !selectedTab ? 'active' : ''}`} onClick={() => setSelectedTab(null)}>{selectedType}</button>
          </>
        )}
        {selectedTab && (
          <>
            <span className="wb-breadcrumb-sep">/</span>
            <span className="wb-breadcrumb-item active">{selectedTab}</span>
          </>
        )}
      </div>

      {/* ---------- Workbook Types ---------- */}
      {!selectedType && (
        <div className="card wb-card">
          <div className="toolbar">
            <div className="toolbar-left"><h3 style={{ margin: 0 }}>Workbooks</h3></div>
            <div className="toolbar-right">
              <button className="btn danger" disabled={!canDelete || typesMeta.total === 0} onClick={deleteAllTypes}>🗑 Delete All</button>
              <button className="btn" onClick={() => setQueryOpen(true)}>🔧 Configuration Query</button>
              {canCreate && <button className="btn primary" onClick={() => setTypeModal({ isNew: true, initial: emptyType() })}>+ New Workbook</button>}
            </div>
          </div>

          <div className="table-scroll">
            <table>
              <thead><tr><th>Code</th><th>Name</th><th>Description</th><th>Actions</th></tr></thead>
              <tbody>
                {typesMeta.items.map((t) => (
                  <tr key={t.code} onClick={() => setSelectedType(t.code)} style={{ cursor: 'pointer' }}>
                    <td><code>{t.code}</code></td>
                    <td>{t.name}</td>
                    <td>{t.description || '—'}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <div className="table-actions">
                        <button className="row-action-btn" disabled={!canUpdate} onClick={() => setTypeModal({ isNew: false, initial: { code: t.code, name: t.name, description: t.description || '' } })}>✎ Edit</button>
                        <button className="row-action-btn danger" disabled={!canDelete} onClick={() => deleteType(t.code)}>🗑 Delete</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={typesMeta.page} pageSize={typesMeta.page_size} total={typesMeta.total}
            totalPages={typesMeta.total_pages} onPageChange={setTypesPage} />
        </div>
      )}

      {/* ---------- Tabs ---------- */}
      {selectedType && !selectedTab && (
        <div className="card wb-card">
          <div className="toolbar">
            <div className="toolbar-left"><h3 style={{ margin: 0 }}>Tabs — {selectedType}</h3></div>
            <div className="toolbar-right">
              <button className="btn danger" disabled={!canDelete || tabsMeta.total === 0} onClick={deleteAllTabs}>🗑 Delete All Tabs</button>
              {canCreate && <button className="btn primary" onClick={() => setTabModal({ isNew: true, initial: emptyTab() })}>+ New Tab</button>}
            </div>
          </div>

          <div className="table-scroll">
            <table>
              <thead><tr><th>Tab</th><th>Rows</th><th>Structure</th><th>Header Row</th><th>Read Only</th><th>Sections?</th><th>Actions</th></tr></thead>
              <tbody>
                {tabsMeta.items.map((t) => (
                  <tr key={t.id} onClick={() => setSelectedTab(t.tab_name)} style={{ cursor: 'pointer' }}>
                    <td><strong>{t.tab_name}</strong></td>
                    <td>{t.row_count}</td>
                    <td>{STRUCTURE_OPTIONS.find((o) => o.value === structureFromHeaderType(t.header_type))?.label}</td>
                    <td>{structureFromHeaderType(t.header_type) === 'single' ? toDisplay(t.header_row_index) : '—'}</td>
                    <td>{t.read_only ? 'Yes' : 'No'}</td>
                    <td>{t.has_multiple_sections ? 'Yes' : 'No'}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <div className="table-actions">
                        <button className="row-action-btn" disabled={!canUpdate} onClick={() => openEditTab(t)}>✎ Edit</button>
                        <button className="row-action-btn danger" disabled={!canDelete} onClick={() => deleteTab(t.tab_name)}>🗑 Delete</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={tabsMeta.page} pageSize={tabsMeta.page_size} total={tabsMeta.total}
            totalPages={tabsMeta.total_pages} onPageChange={setTabsPage} />
        </div>
      )}

      {/* ---------- Sections ---------- */}
      {selectedType && selectedTab && (
        <div className="card wb-card">
          <div className="toolbar">
            <div className="toolbar-left"><h3 style={{ margin: 0 }}>Tables / Sections — {selectedTab}</h3></div>
            <div className="toolbar-right">
              <button className="btn danger" disabled={!canDelete || sectionsMeta.total === 0} onClick={deleteAllSections}>🗑 Delete All Sections</button>
              {canCreate && (
                <>
                  <input type="file" id="detect-sections-input" accept=".xlsx" style={{ display: 'none' }} onChange={handleDetectSectionsFile} />
                  <button className="btn" onClick={() => document.getElementById('detect-sections-input').click()}>🔍 Configure From File</button>
                  <button className="btn primary" onClick={() => setSectionModal({ isNew: true, initial: emptySection() })}>+ New Section</button>
                </>
              )}
            </div>
          </div>
          <p className="page-sub wb-section-hint">
            Use this when one worksheet contains more than one table (e.g. "Consumption" starting row 9 and
            "Cost" starting row 21) — one section per table, each with its own header row and row range.
            "Configure From File" reads a sample file and proposes sections/columns automatically; review before applying.
          </p>

          {sectionProposal && (
            <div className="wb-proposal-card">
              <strong>Detected {sectionProposal.sections.length} table(s) in this file — review before applying</strong>
              {sectionProposal.sections.map((sec, si) => (
                <div className="wb-proposal-row" key={si}>
                  <strong>{sec.section_name}</strong> — header row {sec.header_row}, data starts row {sec.data_start_row},
                  {' '}columns {sec.column_start}–{sec.column_end}
                  {sec.header_type === 'typeMerge' ? ' (2-row merged header)' : ''},
                  {' '}{sec.columns.length} column(s): {sec.columns.map((c) => c.column_name).join(', ') || '(none detected)'}
                </div>
              ))}
              <div className="formula-form-actions" style={{ marginTop: 10 }}>
                <button className="btn primary" onClick={applySectionProposal} disabled={applyingProposal}>
                  {applyingProposal ? 'Applying...' : 'Apply All Sections'}
                </button>
                <button className="btn" onClick={() => setSectionProposal(null)}>Discard</button>
              </div>
            </div>
          )}

          <div className="table-scroll">
            <table>
              <thead><tr><th>Section</th><th>Header Row</th><th>Data Start</th><th>Data End</th><th>Columns</th><th>Header Type</th><th>Position</th><th>Actions</th></tr></thead>
              <tbody>
                {sectionsMeta.items.length === 0 && (
                  <tr><td colSpan={8} className="empty-row">No sections — this tab reads as a single table.</td></tr>
                )}
                {sectionsMeta.items.map((s) => (
                  <tr key={s.id}>
                    <td>{s.section_name}</td>
                    <td>{toDisplay(s.header_row)}</td>
                    <td>{toDisplay(s.data_start_row) || '—'}</td>
                    <td>{toDisplay(s.data_end_row) || '—'}</td>
                    <td>{s.column_start || s.column_end ? `${toDisplay(s.column_start) || 1}–${s.column_end || 'end'}` : 'all'}</td>
                    <td>{s.header_type === 'typeMerge' ? '2-row merged' : 'single row'}</td>
                    <td>{s.position}</td>
                    <td>
                      <div className="table-actions">
                        <button className="row-action-btn" disabled={!canUpdate} onClick={() => openEditSection(s)}>✎ Edit</button>
                        <button className="row-action-btn danger" disabled={!canDelete} onClick={() => deleteSection(s.id)}>🗑 Delete</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={sectionsMeta.page} pageSize={sectionsMeta.page_size} total={sectionsMeta.total}
            totalPages={sectionsMeta.total_pages} onPageChange={setSectionsPage} />
        </div>
      )}

      {/* ---------- Columns ---------- */}
      {selectedType && selectedTab && (
        <div className="card wb-card">
          <div className="toolbar">
            <div className="toolbar-left"><h3 style={{ margin: 0 }}>Columns — {selectedTab}</h3></div>
            <div className="toolbar-right">
              <button className="btn danger" disabled={!canDelete || columnsMeta.total === 0} onClick={deleteAllColumns}>🗑 Delete All Columns</button>
              {canCreate && (
                <>
                  <button className="btn" onClick={() => setBulkOpen(true)}>▦ Bulk Add Columns</button>
                  <button className="btn primary" onClick={() => setColumnModal({ isNew: true, initial: emptyColumn() })}>+ New Column</button>
                </>
              )}
            </div>
          </div>

          <div className="table-scroll">
            <table>
              <thead><tr><th>Column</th><th>Header (file)</th><th>Group</th><th>Header Row</th><th>Column #</th><th>Type</th><th>Required</th><th>Unique</th><th>Position</th><th>Actions</th></tr></thead>
              <tbody>
                {columnsMeta.items.map((c) => (
                  <tr key={c.id}>
                    <td>{c.column_name}</td>
                    <td>{c.header_name && c.header_name !== c.column_name ? c.header_name : '—'}</td>
                    <td>{c.header_group || '—'}</td>
                    <td>{toDisplay(c.header_row) || '—'}</td>
                    <td>{toDisplay(c.column_position) || '—'}</td>
                    <td>{c.data_type}</td>
                    <td>{c.is_required ? 'Yes' : 'No'}</td>
                    <td>{c.is_unique_key ? 'Yes' : 'No'}</td>
                    <td>{c.position}</td>
                    <td>
                      <div className="table-actions">
                        <button className="row-action-btn" disabled={!canUpdate} onClick={() => openEditColumn(c)}>✎ Edit</button>
                        <button className="row-action-btn danger" disabled={!canDelete} onClick={() => deleteColumn(c.id)}>🗑 Delete</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={columnsMeta.page} pageSize={columnsMeta.page_size} total={columnsMeta.total}
            totalPages={columnsMeta.total_pages} onPageChange={setColumnsPage} />
        </div>
      )}

      {typeModal && <TypeModal initial={typeModal.initial} isNew={typeModal.isNew} onClose={() => setTypeModal(null)} onSave={saveType} />}
      {tabModal && <TabModal initial={tabModal.initial} isNew={tabModal.isNew} onClose={() => setTabModal(null)} onSave={saveTab} />}
      {sectionModal && <SectionModal initial={sectionModal.initial} isNew={sectionModal.isNew} onClose={() => setSectionModal(null)} onSave={saveSection} />}
      {columnModal && (
        <ColumnModal
          initial={columnModal.initial}
          isNew={columnModal.isNew}
          hasSections={!!activeTabSummary?.has_multiple_sections}
          sections={sectionsMeta.items}
          onClose={() => setColumnModal(null)}
          onSave={saveColumn}
        />
      )}
      {bulkOpen && (
        <BulkAddModal
          hasSections={!!activeTabSummary?.has_multiple_sections}
          sections={sectionsMeta.items}
          saving={bulkSaving}
          onClose={() => setBulkOpen(false)}
          onSave={saveBulkRows}
          onDetectFile={handleDetectFile}
        />
      )}

      {queryOpen && (
        <Modal
          title="Configuration Query"
          subtitle="Paste JSON matching the format below and Run — it creates whatever doesn't exist yet (workbook, tabs, sections, columns, matched by name) and updates whatever does. No row data is touched."
          onClose={() => setQueryOpen(false)}
          className="wb-modal-xl"
          footer={<>
            <button className="btn" onClick={() => setQueryOpen(false)}>Cancel</button>
            <button className="btn primary" onClick={runConfigQuery} disabled={queryRunning}>{queryRunning ? 'Running...' : 'Run Query'}</button>
          </>}
        >
          <textarea className="wb-query-textarea" value={queryText} onChange={(e) => setQueryText(e.target.value)} />
        </Modal>
      )}
    </div>
  )
}
