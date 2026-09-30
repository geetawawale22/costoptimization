import { useState } from 'react'
import * as wb from '../../api/workbooks'
import { useAuth } from '../../context/AuthContext'
import { useToast } from '../../context/ToastContext'

const STATUS_CLASS = { Valid: 'green', 'Needs Review': 'amber', Invalid: 'red' }

/**
 * Preview -> resolve -> commit import flow.
 *   1. Pick a file. It is parsed + validated on the backend WITHOUT writing anything
 *      (POST .../import/preview), returning detected sheet/section/header/columns,
 *      a small data sample, and a per-column header-mapping status.
 *   2. If almost every configured column comes back "unmatched" — the classic symptom
 *      of a blank spacer row sitting above the real header — the backend suggests the
 *      row that actually matches, shown as a "Try row X" callout. Clicking it
 *      re-previews with that header row without re-uploading; "Save permanently"
 *      writes it back onto the tab's configuration.
 *   3. STRICT VALIDATION: every configured column must either be matched to a header
 *      in the file, or explicitly marked "Ignore" — there is no silent default. Import
 *      is disabled until every unmatched column is resolved one way or the other, and
 *      a red banner in the footer counts down how many remain.
 *   4. Only once the user clicks "Import" does POST .../import/commit actually write
 *      rows, re-reading the same cached upload (by preview_id) and applying whatever
 *      mapping/ignore choices and header-row correction were made.
 *
 * Two import modes, matching the backend's import scopes:
 *  - "This file only" (default) — previews/commits just the currently-open tab.
 *  - "Whole workbook" — a multi-tab file that updates every tab in this workbook type.
 */
export default function ImportModal({ typeCode, activeTab, canWholeWorkbook, onClose, onImported }) {
  const { can } = useAuth()
  const showToast = useToast()
  const [mode, setMode] = useState('single') // 'single' | 'whole'
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [preview, setPreview] = useState(null) // ImportPreviewResult
  // key ("Tab" or "Tab::Section") -> { header_mapping: {configuredCol: sourceHeader}, ignored_columns: [configuredCol, ...] }
  const [overrides, setOverrides] = useState({})
  const [headerRowOverride, setHeaderRowOverride] = useState(null)
  const [result, setResult] = useState(null) // ImportResult, once committed

  async function runPreview(chosenFile, override) {
    setBusy(true)
    setError('')
    try {
      const res = await wb.previewImport(typeCode, chosenFile, mode === 'single' ? activeTab : undefined, override)
      setPreview(res)
      setHeaderRowOverride(override || null)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function handleFileChosen(e) {
    const chosen = e.target.files[0]
    if (!chosen) return
    setFile(chosen)
    setOverrides({})
    setResult(null)
    setPreview(null)
    await runPreview(chosen, null)
  }

  async function retryWithHeaderRow(row) {
    await runPreview(file, row)
  }

  async function savePermanently(row) {
    try {
      await wb.updateTab(typeCode, activeTab, { header_row_index: row - 1 })
      showToast(`Saved — row ${row} is now this tab's default header row.`)
    } catch (err) {
      showToast(err.message)
    }
  }

  function overrideKey(tabName, sectionName) {
    return sectionName ? `${tabName}::${sectionName}` : tabName
  }

  function setMapping(tabName, sectionName, configuredHeader, sourceHeader) {
    const key = overrideKey(tabName, sectionName)
    setOverrides((prev) => {
      const current = prev[key] || { header_mapping: {}, ignored_columns: [] }
      const nextMapping = { ...current.header_mapping }
      const nextIgnored = (current.ignored_columns || []).filter((c) => c !== configuredHeader)
      if (sourceHeader) nextMapping[configuredHeader] = sourceHeader
      else delete nextMapping[configuredHeader]
      return { ...prev, [key]: { header_mapping: nextMapping, ignored_columns: nextIgnored } }
    })
  }

  function markIgnored(tabName, sectionName, configuredHeader) {
    const key = overrideKey(tabName, sectionName)
    setOverrides((prev) => {
      const current = prev[key] || { header_mapping: {}, ignored_columns: [] }
      const nextMapping = { ...current.header_mapping }
      delete nextMapping[configuredHeader]
      const nextIgnored = [...new Set([...(current.ignored_columns || []), configuredHeader])]
      return { ...prev, [key]: { header_mapping: nextMapping, ignored_columns: nextIgnored } }
    })
  }

  async function handleCommit() {
    if (!preview) return
    setBusy(true)
    setError('')
    try {
      // `overrides` already carries { header_mapping, ignored_columns } per key —
      // nothing extra to pass alongside it.
      const res = await wb.commitImport(typeCode, preview.preview_id, {
        tabNames: preview.tabs.filter((t) => t.found_in_file).map((t) => t.tab_name),
        overrides,
        headerRowOverride,
      })
      setResult(res)
      showToast('Import complete.')
      onImported && onImported()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  function reset() {
    setFile(null)
    setPreview(null)
    setOverrides({})
    setHeaderRowOverride(null)
    setResult(null)
    setError('')
  }

  const unresolvedTotal = preview?.tabs?.reduce((sum, t) => {
    if (!t.found_in_file) return sum
    return sum + t.sections.reduce((s2, sec) => {
      const key = overrideKey(t.tab_name, sec.section_name)
      const resolved = overrides[key] || { header_mapping: {}, ignored_columns: [] }
      const unmatched = sec.header_mappings.filter((m) => m.status === 'unmatched')
      const stillUnresolved = unmatched.filter((m) =>
        !resolved.header_mapping?.[m.source_header] && !(resolved.ignored_columns || []).includes(m.source_header)
      )
      return s2 + stillUnresolved.length
    }, 0)
  }, 0) || 0

  const hasParseErrors = preview?.tabs?.some((t) => t.status === 'Invalid' && t.found_in_file &&
    t.sections.some((s) => s.errors.some((e) => !e.startsWith('Required column'))))
  const importDisabled = busy || hasParseErrors || unresolvedTotal > 0
  const canEditWorkbookConfig = can('workbook_config', 'update')

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ maxWidth: 780 }}>
        <div className="modal-head">
          <h3>Import Data — {typeCode}</h3>
          <button className="close-x" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body" style={{ maxHeight: '65vh', overflowY: 'auto' }}>
          {!file && (
            <>
              <div className="tabbar secondary" style={{ marginBottom: 14 }}>
                <button className={`tab ${mode === 'single' ? 'active' : ''}`} onClick={() => setMode('single')}>
                  This file only{activeTab ? ` (${activeTab})` : ''}
                </button>
                {canWholeWorkbook && (
                  <button className={`tab ${mode === 'whole' ? 'active' : ''}`} onClick={() => setMode('whole')}>
                    Whole workbook (all tabs)
                  </button>
                )}
              </div>
              <label className="dropzone" style={{ display: 'block' }}>
                ☁ Click to upload or drag and drop
                <br />
                <span style={{ fontSize: 11.5 }}>Supports .xlsx files up to 25MB</span>
                <input type="file" accept=".xlsx" onChange={handleFileChosen} style={{ display: 'none' }} />
              </label>
            </>
          )}

          {busy && !result && <p style={{ textAlign: 'center', color: 'var(--text-muted)' }}>⏳ Working...</p>}
          {error && <p className="validation-err">{error}</p>}

          {preview && !result && (
            <div style={{ marginTop: 12 }}>
              <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>
                File: <b>{preview.file_name}</b> — review below, resolve any header mismatches, then click Import.
              </p>
              {preview.tabs.map((t) => (
                <div key={t.tab_name} className="card" style={{ marginBottom: 12 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <b>{t.tab_name}</b>
                    <span className={`chip ${STATUS_CLASS[t.status] || ''}`}>{t.status}</span>
                  </div>
                  {!t.found_in_file && (
                    <p className="validation-err">Sheet '{t.tab_name}' was not found in the uploaded file.</p>
                  )}

                  {t.suggested_header_row && (
                    <div className="validation-row" style={{ background: '#FFFBEB', borderRadius: 6, padding: 10, marginTop: 8, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10 }}>
                      <span style={{ fontSize: 13 }}>
                        Almost every configured column is unmatched — the real header might actually be on
                        <b> row {t.suggested_header_row}</b>, not the row currently configured.
                      </span>
                      <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
                        <button className="btn primary" onClick={() => retryWithHeaderRow(t.suggested_header_row)}>
                          Try row {t.suggested_header_row}
                        </button>
                        {canEditWorkbookConfig && (
                          <button className="btn" onClick={() => savePermanently(t.suggested_header_row)}>
                            Save permanently
                          </button>
                        )}
                      </div>
                    </div>
                  )}

                  {t.sections.map((s, si) => {
                    const key = overrideKey(t.tab_name, s.section_name)
                    const extraHeaders = s.header_mappings.filter((m) => m.status === 'extra').map((m) => m.source_header)
                    const unmatched = s.header_mappings.filter((m) => m.status === 'unmatched')
                    return (
                      <div key={si} style={{ marginTop: 10, borderTop: '1px solid #EEF1F5', paddingTop: 10 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5, color: 'var(--text-muted)' }}>
                          <span>
                            {s.section_name ? `Section: ${s.section_name} — ` : ''}
                            Header row {s.header_row + 1} · {s.total_rows} data row(s)
                          </span>
                          <span className={`chip ${STATUS_CLASS[s.status] || ''}`}>{s.status}</span>
                        </div>

                        {s.errors.map((e, i) => <div className="validation-err" key={i}>{e}</div>)}

                        {unmatched.length > 0 && (
                          <div style={{ marginTop: 8 }}>
                            {unmatched.map((m) => {
                              const resolved = overrides[key] || { header_mapping: {}, ignored_columns: [] }
                              const chosen = resolved.header_mapping?.[m.source_header] || ''
                              const isIgnored = (resolved.ignored_columns || []).includes(m.source_header)
                              return (
                                <div className="validation-row" key={m.source_header} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                                  <span style={{ flex: 1 }}>
                                    Configured column <b>{m.source_header}</b> not found in file
                                    {m.suggested_column ? <> — did you mean <i>{m.suggested_column}</i>?</> : null}
                                    {!chosen && !isIgnored && <span style={{ color: '#B45309', fontWeight: 600 }}> — needs resolving</span>}
                                  </span>
                                  <select
                                    value={isIgnored ? '__ignore__' : chosen}
                                    onChange={(e) => {
                                      const val = e.target.value
                                      if (val === '__ignore__') markIgnored(t.tab_name, s.section_name, m.source_header)
                                      else setMapping(t.tab_name, s.section_name, m.source_header, val || null)
                                    }}
                                  >
                                    <option value="">— Choose —</option>
                                    <option value="__ignore__">Ignore this column (not in this data)</option>
                                    {(m.suggested_column && !extraHeaders.includes(m.suggested_column)
                                      ? [m.suggested_column, ...extraHeaders]
                                      : extraHeaders
                                    ).map((h) => (
                                      <option key={h} value={h}>Map to "{h}"</option>
                                    ))}
                                  </select>
                                </div>
                              )
                            })}
                          </div>
                        )}

                        {s.sample_rows.length > 0 && (
                          <div className="table-scroll" style={{ marginTop: 8, maxHeight: 180 }}>
                            <table>
                              {s.header_rows && s.header_rows.length === 2 ? (
                                <thead>
                                  <tr>
                                    {(() => {
                                      const row0 = s.header_rows[0]
                                      const cells = []
                                      let i = 0
                                      while (i < row0.length) {
                                        const label = row0[i] || ''
                                        let span = 1
                                        while (label !== '' && i + span < row0.length && (row0[i + span] || '') === label) span++
                                        cells.push(<th key={i} colSpan={span} style={{ textAlign: 'center', background: '#F1F5F9' }}>{label}</th>)
                                        i += span
                                      }
                                      return cells
                                    })()}
                                  </tr>
                                  <tr>
                                    {s.header_rows[1].map((h, hi) => <th key={hi}>{h}</th>)}
                                  </tr>
                                </thead>
                              ) : (
                                <thead>
                                  <tr>{s.columns_detected.map((c) => <th key={c}>{c}</th>)}</tr>
                                </thead>
                              )}
                              <tbody>
                                {s.sample_rows.slice(0, 5).map((r, ri) => (
                                  <tr key={ri}>
                                    {s.columns_detected.map((c) => <td key={c}>{String(r[c] ?? '')}</td>)}
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              ))}
            </div>
          )}

          {result && (
            <div style={{ marginTop: 12 }}>
              {result.tabs.map((t) => (
                <div key={t.tab_name} className="validation-row" style={{ display: 'block' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span>Sheet: {t.tab_name} — {t.rows_imported} row(s) imported</span>
                    <span className={`chip ${STATUS_CLASS[t.status] || ''}`}>{t.status}</span>
                  </div>
                  {t.errors?.map((e, i) => (
                    <div className="validation-err" key={i}>{e}</div>
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="modal-foot" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            {preview && !result && unresolvedTotal > 0 && (
              <p className="validation-err" style={{ margin: 0 }}>
                {unresolvedTotal} column{unresolvedTotal > 1 ? 's' : ''} still need{unresolvedTotal === 1 ? 's' : ''} to be resolved
                above (map it or mark Ignore) before you can import.
              </p>
            )}
            {preview && !result && unresolvedTotal === 0 && (
              <button className="btn" onClick={reset} disabled={busy}>← Choose a different file</button>
            )}
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn" onClick={onClose}>{result ? 'Close' : 'Cancel'}</button>
            {preview && !result && (
              <button className="btn primary" onClick={handleCommit} disabled={importDisabled}
                      title={importDisabled ? 'Resolve every mismatched column above first' : ''}>
                ⬆ Import
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}