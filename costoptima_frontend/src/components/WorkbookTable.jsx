import { useEffect, useState, useCallback, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import * as wb from '../api/workbooks'
import { downloadBlob } from '../api/client'
import { useAuth } from '../context/AuthContext'
import { useToast } from '../context/ToastContext'
import { useConfirm } from '../context/ConfirmContext'
import ImportModal from './modals/ImportModal'
import RowModal from './modals/RowModal'
import Pagination from './Pagination'

/**
 * Renders a full Masters/Commodity-Types page for one WorkbookType:
 * a tab bar (if the workbook has more than one tab) + an optional section sub-bar
 * (for tabs whose worksheet has multiple tables/header blocks) + search +
 * Import/Export/+Add Row + a data table with per-row Edit/Delete — all driven
 * entirely by the backend schema, so this one component covers every one of the
 * ~28 workbook types with zero extra code.
 *
 * BUGFIX (stale-tab race / "Unknown tab X in Y" on navigation & refresh):
 * Previously `activeTab` was plain React state seeded once from the schema. When the
 * `typeCode` prop changed (e.g. clicking from "Master Output Matrix" to "Column
 * Position" in the sidebar, which re-uses this same component instance), the rows
 * effect fired immediately with the OLD activeTab ("Master output Final") against the
 * NEW typeCode ("column_position") — before the new schema had loaded and reset
 * activeTab — producing exactly "Unknown tab 'Master output Final' in
 * 'column_position'". The fix: reset schema/activeTab synchronously whenever typeCode
 * changes (so no request can fire with a stale tab/type pairing), track requests with
 * a ref so out-of-order responses are ignored, and persist the selected tab (and
 * section, if any) in the URL (?tab=...&section=...) so a browser refresh or direct
 * link restores the same view — validated against the freshly-loaded schema, with a
 * graceful fallback (not a crash) if that tab/section no longer exists.
 *
 * SECTION AWARENESS: when a tab has multiple tables/header blocks in one worksheet
 * (tab.has_multiple_sections), its columns are split per-section (WorkbookSection).
 * A secondary sub-bar lets the user pick which table they're viewing; the column
 * headers, the Add/Edit Row form, and the row list are all filtered to that section
 * so two unrelated tables sharing one worksheet never get mixed into one form.
 */
export default function WorkbookTable({ typeCode, title, subtitle }) {
  const { can } = useAuth()
  const showToast = useToast()
  const confirm = useConfirm()
  const [searchParams, setSearchParams] = useSearchParams()

  const [schema, setSchema] = useState(null)
  const [activeTab, setActiveTab] = useState(null)
  const [activeSectionId, setActiveSectionId] = useState(null) // null = tab has no sections
  const [rows, setRows] = useState([])
  const [rowsMeta, setRowsMeta] = useState({ total: 0, page: 1, page_size: 50, total_pages: 1 })
  const [rowPage, setRowPage] = useState(1)
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [modal, setModal] = useState(null) // 'import' | { row } for edit | 'add'

  const schemaRequestId = useRef(0)
  const rowsRequestId = useRef(0)

  // Reset immediately on typeCode change so no stale (typeCode, activeTab, section)
  // pairing can ever reach the API — this is the core of the race-condition fix.
  useEffect(() => {
    setSchema(null)
    setActiveTab(null)
    setActiveSectionId(null)
    setRows([])
    setError('')
    setRowPage(1)
    setSearch('')
  }, [typeCode])

  const loadSchema = useCallback(async () => {
    const myRequestId = ++schemaRequestId.current
    setLoading(true)
    setError('')
    try {
      const s = await wb.getSchema(typeCode)
      if (myRequestId !== schemaRequestId.current) return // a newer typeCode superseded this request

      setSchema(s)
      const tabNames = s.tabs.map((t) => t.tab_name)
      const requestedTab = searchParams.get('tab')
      const nextTab = requestedTab && tabNames.includes(requestedTab) ? requestedTab : (s.tabs[0]?.tab_name || null)
      setActiveTab(nextTab)

      const nextTabSchema = s.tabs.find((t) => t.tab_name === nextTab)
      const sections = (nextTabSchema?.sections || []).slice().sort((a, b) => a.position - b.position)
      let nextSectionId = null
      if (nextTabSchema?.has_multiple_sections && sections.length > 0) {
        const requestedSection = Number(searchParams.get('section'))
        nextSectionId = sections.some((sec) => sec.id === requestedSection) ? requestedSection : sections[0].id
      }
      setActiveSectionId(nextSectionId)

      const next = new URLSearchParams(searchParams)
      if (nextTab) next.set('tab', nextTab); else next.delete('tab')
      if (nextSectionId) next.set('section', String(nextSectionId)); else next.delete('section')
      setSearchParams(next, { replace: true })
    } catch (err) {
      if (myRequestId === schemaRequestId.current) setError(err.message)
    } finally {
      if (myRequestId === schemaRequestId.current) setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [typeCode])

  useEffect(() => {
    loadSchema()
  }, [loadSchema])

  const currentTabSchema = schema?.tabs.find((t) => t.tab_name === activeTab)
  const sections = (currentTabSchema?.sections || []).slice().sort((a, b) => a.position - b.position)
  const hasSections = !!currentTabSchema?.has_multiple_sections && sections.length > 0

  const loadRows = useCallback(async () => {
    // Guard: only fetch rows for a tab that actually belongs to the currently-loaded
    // schema for this typeCode — this is what prevents the "Unknown tab" request.
    if (!schema || !activeTab) return
    if (!schema.tabs.some((t) => t.tab_name === activeTab)) return
    if (hasSections && !activeSectionId) return // section not resolved yet

    const myRequestId = ++rowsRequestId.current
    try {
      const r = await wb.listRows(typeCode, activeTab, {
        q: search, page: rowPage, pageSize: 50,
        sectionId: hasSections ? activeSectionId : undefined,
      })
      if (myRequestId !== rowsRequestId.current) return
      setRows(r.items)
      setRowsMeta(r)
    } catch (err) {
      if (myRequestId !== rowsRequestId.current) return
      // Structured 404 (unknown_tab/unknown_workbook_type) -> recover gracefully by
      // falling back to the first available tab instead of dead-ending the page.
      if (err.message && err.message.includes('unknown_tab') && schema.tabs[0]) {
        setActiveTab(schema.tabs[0].tab_name)
        return
      }
      setError(err.message)
    }
  }, [typeCode, schema, activeTab, hasSections, activeSectionId, search, rowPage])

  useEffect(() => {
    setRowPage(1)
  }, [activeTab, activeSectionId, search])

  useEffect(() => {
    loadRows()
  }, [loadRows])

  function selectTab(tabName) {
    setActiveTab(tabName)
    const tabSchema = schema.tabs.find((t) => t.tab_name === tabName)
    const tabSections = (tabSchema?.sections || []).slice().sort((a, b) => a.position - b.position)
    const nextSectionId = tabSchema?.has_multiple_sections && tabSections.length > 0 ? tabSections[0].id : null
    setActiveSectionId(nextSectionId)

    const next = new URLSearchParams(searchParams)
    next.set('tab', tabName)
    if (nextSectionId) next.set('section', String(nextSectionId)); else next.delete('section')
    setSearchParams(next, { replace: true })
  }

  function selectSection(sectionId) {
    setActiveSectionId(sectionId)
    const next = new URLSearchParams(searchParams)
    next.set('section', String(sectionId))
    setSearchParams(next, { replace: true })
  }

  if (loading) return <div className="loading">Loading {title}...</div>
  if (error) return <p className="validation-err">{error}</p>
  if (!schema) return null

  if (schema.tabs.length === 0) {
    return (
      <>
        <h1 className="page-title">{title}</h1>
        <p className="page-sub">No sheets have been configured for this tab yet.</p>
        <div className="card">
          <p style={{ color: 'var(--text-muted)', fontSize: 13, margin: 0 }}>
            There are currently no files/items assigned here.
          </p>
        </div>
      </>
    )
  }

  const tab = currentTabSchema
  const activeSection = hasSections ? sections.find((s) => s.id === activeSectionId) : null
  // Columns are section-scoped: a tab with sections stores each section's columns
  // separately (column.section_id), so mixing them into one form would combine two
  // unrelated tables that just happen to share a worksheet.
  // Sorted explicitly by `position` rather than trusting array order: the backend
  // now sorts too (see get_schema in routers/workbooks.py), but the UI is the one
  // place a column's rendered order is actually observed, so it shouldn't silently
  // depend on the API never regressing that guarantee.
  const columns = (hasSections
    ? (tab?.columns || []).filter((c) => c.section_id === activeSection?.id)
    : (tab?.columns || []).filter((c) => c.section_id == null)
  ).slice().sort((a, b) => a.position - b.position)
  const readOnly = tab?.read_only
  const canView = can('masters', 'view')
  const canCreate = can('masters', 'create') && !readOnly
  const canUpdate = can('masters', 'update') && !readOnly
  const canDelete = can('masters', 'delete')

  async function handleAddOrUpdate(values, existingRowId) {
    const sectionId = hasSections ? activeSectionId : undefined
    if (existingRowId) {
      await wb.updateRow(typeCode, activeTab, existingRowId, values, sectionId)
      showToast('Row updated.')
    } else {
      await wb.addRow(typeCode, activeTab, values, sectionId)
      showToast('Row added.')
    }
    loadRows()
  }

  async function handleDelete(rowId) {
    if (!(await confirm({ title: 'Delete Row', message: 'Delete this row?' }))) return
    try {
      await wb.deleteRow(typeCode, activeTab, rowId)
      showToast('Row deleted.')
      loadRows()
    } catch (err) {
      showToast(err.message)
    }
  }

  async function handleDeleteAll() {
    const scope = hasSections && activeSection ? `${activeTab} / ${activeSection.section_name}` : activeTab
    if (!(await confirm({ title: 'Delete All Rows', message: `Delete all rows in ${scope}?` }))) return
    try {
      const result = await wb.deleteAllRows(typeCode, activeTab, hasSections ? activeSectionId : undefined)
      showToast(`${result.rows_deleted || 0} row(s) deleted.`)
      loadRows()
    } catch (err) {
      showToast(err.message)
    }
  }

  async function handleExportTab() {
    try {
      const blob = await wb.exportTab(typeCode, activeTab)
      downloadBlob(blob, `${activeTab}.xlsx`)
    } catch (err) {
      showToast(err.message)
    }
  }

  return (
    <>
      <h1 className="page-title">{title}</h1>
      {subtitle && <p className="page-sub">{subtitle}</p>}

      {schema.tabs.length > 1 && (
        <div className="tabbar">
          {schema.tabs.map((t) => (
            <button
              key={t.tab_name}
              className={`tab ${t.tab_name === activeTab ? 'active' : ''}`}
              onClick={() => selectTab(t.tab_name)}
            >
              {t.tab_name}
            </button>
          ))}
        </div>
      )}

      {hasSections && (
        <div className="tabbar secondary" style={{ marginTop: schema.tabs.length > 1 ? 6 : 0 }}>
          {sections.map((s) => (
            <button
              key={s.id}
              className={`tab ${s.id === activeSectionId ? 'active' : ''}`}
              onClick={() => selectSection(s.id)}
              title={s.description || ''}
            >
              {s.section_name}
            </button>
          ))}
        </div>
      )}

      <div className="card">
        <div className="toolbar">
          <div className="toolbar-left">
            <input
              className="search-box"
              style={{ width: 260 }}
              placeholder="Search this table..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div className="toolbar-right">
            <button
              className="btn"
              disabled={!canCreate}
              title={!canCreate ? 'Not permitted for your role' : ''}
              onClick={() => setModal('import')}
            >
              ⬆ Import
            </button>
            <button
              className="btn"
              disabled={!canView}
              title={!canView ? 'Not permitted for your role' : ''}
              onClick={handleExportTab}
            >
              ⬇ Export
            </button>
            <button
              className="btn"
              disabled={!canDelete || rowsMeta.total === 0}
              title={!canDelete ? 'Not permitted for your role' : ''}
              onClick={handleDeleteAll}
            >
              Delete All
            </button>
            <button
              className="btn primary"
              disabled={!canCreate}
              title={!canCreate ? 'Not permitted for your role' : ''}
              onClick={() => setModal('add')}
            >
              ＋ Add Row
            </button>
          </div>
        </div>
        {hasSections && activeSection && (
          <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '0 0 6px' }}>
            Header row: {activeSection.header_row + 1}
            {activeSection.description ? ` — ${activeSection.description}` : ''}
          </p>
        )}
        <div className="table-scroll">
          <table>
            <thead>
              {columns.some((c) => c.header_group) && (
                <tr>
                  {(() => {
                    const cells = []
                    let i = 0
                    while (i < columns.length) {
                      const group = columns[i].header_group || ''
                      let span = 1
                      while (group !== '' && i + span < columns.length && (columns[i + span].header_group || '') === group) span++
                      cells.push(<th key={i} colSpan={span} style={{ textAlign: 'center', background: '#F1F5F9' }}>{group}</th>)
                      i += span
                    }
                    return cells
                  })()}
                  <th></th>
                </tr>
              )}
                            <tr>
                {columns.map((c) => (
                  <th key={c.id}>
                    {c.column_name}
                    {c.excel_column && (
                      <span style={{ display: 'block', fontWeight: 400, fontSize: 10, color: 'var(--text-muted)' }}>
                        {c.excel_column}
                      </span>
                    )}
                  </th>
                ))}
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr>
                  <td colSpan={columns.length + 1} style={{ textAlign: 'center', padding: 24, color: 'var(--text-muted)' }}>
                    No rows match your search.
                  </td>
                </tr>
              )}
              {rows.map((row) => (
                <tr key={row.id}>
                  {columns.map((c) => (
                    <td key={c.id}>{String(row.row_data?.[c.column_name] ?? '')}</td>
                  ))}
                  <td>
                    <button className="kebab" disabled={!canUpdate} title="Edit" onClick={() => setModal({ row })}>✎</button>
                    <button className="kebab" disabled={!canDelete} title="Delete" onClick={() => handleDelete(row.id)}>🗑</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Pagination
          page={rowsMeta.page}
          pageSize={rowsMeta.page_size}
          total={rowsMeta.total}
          totalPages={rowsMeta.total_pages}
          onPageChange={setRowPage}
        />
      </div>

      {modal === 'import' && (
        <ImportModal
          typeCode={typeCode}
          activeTab={activeTab}
          canWholeWorkbook={schema.tabs.length > 1}
          onClose={() => setModal(null)}
          onImported={() => loadRows()}
        />
      )}
      {modal === 'add' && (
        <RowModal columns={columns} label={activeSection ? `${activeTab} — ${activeSection.section_name}` : activeTab}
                  onClose={() => setModal(null)} onSave={(values) => handleAddOrUpdate(values)} />
      )}
      {modal?.row && (
        <RowModal
          columns={columns}
          label={activeSection ? `${activeTab} — ${activeSection.section_name}` : activeTab}
          initialRow={modal.row}
          onClose={() => setModal(null)}
          onSave={(values) => handleAddOrUpdate(values, modal.row.id)}
        />
      )}
    </>
  )
}
