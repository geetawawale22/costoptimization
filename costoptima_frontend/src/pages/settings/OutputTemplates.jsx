import { useEffect, useMemo, useState } from 'react'
import * as miscApi from '../../api/misc'
import { usePageCrumb } from '../../components/Layout'
import { useAuth } from '../../context/AuthContext'
import { useToast } from '../../context/ToastContext'
import { useConfirm } from '../../context/ConfirmContext'

const PCM_TEMPLATE_NAME = 'pcm_sheet_metal_forging'
const TABS = ['Guided Setup', 'Sheets', 'Formulas', 'Old PCM Status', 'Advanced JSON']

function pretty(value) { return JSON.stringify(value || {}, null, 2) }
function clone(value) { return JSON.parse(JSON.stringify(value || {})) }

export default function OutputTemplates() {
  usePageCrumb('Settings / <b>Output Templates</b>')
  const { can } = useAuth()
  const showToast = useToast()
  const confirm = useConfirm()
  const canUpdate = can('settings', 'update')
  const [templates, setTemplates] = useState([])
  const [selectedId, setSelectedId] = useState('')
  const [layout, setLayout] = useState({})
  const [jsonText, setJsonText] = useState('{}')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [activeTab, setActiveTab] = useState('Guided Setup')
  const [newSheet, setNewSheet] = useState('')
  const [newPresetName, setNewPresetName] = useState('')
  const [newPresetFormula, setNewPresetFormula] = useState('')

  async function load() {
    setLoading(true)
    try {
      const rows = await miscApi.listOutputTemplates()
      setTemplates(rows)
      const preferred = rows.find((t) => t.name === PCM_TEMPLATE_NAME) || rows[0]
      if (preferred) chooseTemplate(preferred, rows)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load().catch((err) => showToast(err.message)) }, []) // eslint-disable-line

  const selected = templates.find((t) => String(t.id) === String(selectedId))
  const legacyMap = layout?.legacy_pcm5_porting || {}
  const formulaPresets = layout?.old_formula_presets || {}
  const sheetOrder = layout?.sheet_order || []
  const minimumRows = layout?.minimum_rows || {}
  const footerRules = layout?.footer_rules || {}
  const formulaColumns = layout?.formula_columns || {}

  const portCounts = useMemo(() => {
    const all = [
      ...Object.values(legacyMap.commodity_functions || {}),
      ...Object.values(legacyMap.summary_functions || {}),
    ]
    return {
      total: all.length,
      partial: all.filter((x) => String(x.status || '').includes('partial')).length,
      pending: all.filter((x) => String(x.status || '').includes('pending')).length,
    }
  }, [legacyMap])

  function chooseTemplate(t, rows = templates) {
    setSelectedId(String(t?.id || ''))
    const next = clone(t?.layout || {})
    setLayout(next)
    setJsonText(pretty(next))
    if (rows.length) setTemplates(rows)
  }

  function choose(id) {
    const t = templates.find((x) => String(x.id) === String(id))
    if (t) chooseTemplate(t)
  }

  function updateLayout(mutator) {
    setLayout((prev) => {
      const next = clone(prev)
      mutator(next)
      setJsonText(pretty(next))
      return next
    })
  }

  async function save(currentLayout = layout) {
    if (!selected) return
    setSaving(true)
    try {
      const updated = await miscApi.updateOutputTemplate(selected.id, {
        name: selected.name,
        format_type: selected.format_type || 'xlsx',
        layout: currentLayout,
      })
      showToast('Output template saved.')
      setTemplates((prev) => prev.map((t) => t.id === updated.id ? updated : t))
      chooseTemplate(updated)
    } catch (err) {
      showToast(err.message)
    } finally {
      setSaving(false)
    }
  }

  async function saveJson() {
    try {
      const parsed = JSON.parse(jsonText)
      setLayout(parsed)
      await save(parsed)
    } catch (err) {
      showToast(`Invalid JSON: ${err.message}`)
    }
  }

  async function resetPcm() {
    if (!(await confirm({ title: 'Reset Output Template', message: 'Reset the PCM Sheet Metal/Forging output template to the latest migrated defaults?' }))) return
    setSaving(true)
    try {
      const updated = await miscApi.resetPcmSheetMetalTemplate()
      showToast('PCM template reset with latest migrated defaults.')
      await load()
      chooseTemplate(updated)
    } catch (err) {
      showToast(err.message)
    } finally {
      setSaving(false)
    }
  }

  function moveSheet(index, delta) {
    updateLayout((next) => {
      const arr = [...(next.sheet_order || [])]
      const target = index + delta
      if (target < 0 || target >= arr.length) return
      const [item] = arr.splice(index, 1)
      arr.splice(target, 0, item)
      next.sheet_order = arr
    })
  }

  function addSheet() {
    if (!newSheet.trim()) return
    updateLayout((next) => {
      next.sheet_order = [...(next.sheet_order || []), newSheet.trim()]
      next.minimum_rows = { ...(next.minimum_rows || {}), [newSheet.trim()]: 1 }
    })
    setNewSheet('')
  }

  async function removeSheet(name) {
    if (!(await confirm({ title: 'Remove Sheet', message: `Remove sheet "${name}" from output template?` }))) return
    updateLayout((next) => {
      next.sheet_order = (next.sheet_order || []).filter((x) => x !== name)
      delete (next.minimum_rows || {})[name]
      delete (next.footer_rules || {})[name]
      delete (next.formula_columns || {})[name]
    })
  }

  function addPreset() {
    if (!newPresetName.trim() || !newPresetFormula.trim()) return
    updateLayout((next) => {
      next.old_formula_presets = { ...(next.old_formula_presets || {}), [newPresetName.trim()]: newPresetFormula.trim() }
    })
    setNewPresetName('')
    setNewPresetFormula('')
  }

  return (
    <div className="ot-page">
      <div className="settings-page-header">
        <div className="settings-header-row">
          <div>
            <h1 className="page-title">Output Templates</h1>
            <p className="page-sub">Configure the PCM output workbook using guided screens — sheets, formulas, footer rules, and old PCM migration status, without touching code. Advanced JSON is still available for support use.</p>
          </div>
        </div>
      </div>

      {loading ? (
        <div className="loading">Loading output templates...</div>
      ) : (
        <>
          <div className="card ot-toolbar-card">
            <div className="form-field ot-template-field">
              <label>Template</label>
              <select value={selectedId} onChange={(e) => choose(e.target.value)}>
                {templates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
            </div>
            <div className="ot-toolbar-actions">
              <button className="btn" disabled={!canUpdate || saving} title={canUpdate ? '' : "You don't have permission to reset templates."} onClick={resetPcm}>Reset PCM Defaults</button>
              <button className="btn primary" disabled={!selected || !canUpdate || saving} title={canUpdate ? '' : "You don't have permission to save templates."} onClick={() => save()}>{saving ? 'Saving...' : 'Save Template'}</button>
            </div>
          </div>

          {selected && (
            <>
              <div className="tabbar ot-tabbar">
                {TABS.map((t) => <button key={t} className={`tab ${activeTab === t ? 'active' : ''}`} onClick={() => setActiveTab(t)}>{t}</button>)}
              </div>

              {activeTab === 'Guided Setup' && (
                <div className="row" style={{ alignItems: 'stretch' }}>
                  <div className="card" style={{ flex: 1 }}>
                    <h3>Workbook Basics</h3>
                    <div className="validation-row"><span>Output sheets</span><strong>{sheetOrder.length}</strong></div>
                    <div className="validation-row"><span>Footer rules</span><strong>{Object.keys(footerRules).length}</strong></div>
                    <div className="validation-row"><span>Formula preset library</span><strong>{Object.keys(formulaPresets).length}</strong></div>
                    <div className="validation-row"><span>Old PCM functions tracked</span><strong>{portCounts.total}</strong></div>
                  </div>
                  <div className="card" style={{ flex: 1 }}>
                    <h3>Old PCM Migration</h3>
                    <div className="validation-row"><span>Partially ported</span><strong>{portCounts.partial}</strong></div>
                    <div className="validation-row"><span>Pending full formula port</span><strong>{portCounts.pending}</strong></div>
                    <p className="page-sub ot-hint">Use the Old PCM Status tab to see each old function and whether it is pending, partial, or configurable.</p>
                  </div>
                  <div className="card" style={{ flex: 1 }}>
                    <h3>Performance Settings</h3>
                    <div className="validation-row"><span>Calculation</span><strong>{layout.performance_plan?.calculation || 'in memory'}</strong></div>
                    <div className="validation-row"><span>Output writer</span><strong>{layout.performance_plan?.final_output_writer || 'xlsxwriter'}</strong></div>
                    <p className="page-sub ot-hint">Intermediate MA/BOP/Part1 files stay disabled. Only the final output workbook is stored.</p>
                  </div>
                </div>
              )}

              {activeTab === 'Sheets' && (
                <div className="card">
                  <div className="toolbar">
                    <div className="toolbar-left"><h3 style={{ margin: 0 }}>Output Sheets</h3></div>
                    <div className="toolbar-right">
                      <input value={newSheet} onChange={(e) => setNewSheet(e.target.value)} placeholder="New sheet name" disabled={!canUpdate} />
                      <button className="btn" disabled={!canUpdate} onClick={addSheet}>+ Add Sheet</button>
                    </div>
                  </div>
                  <div className="table-scroll">
                    <table>
                      <thead><tr><th>Order</th><th>Sheet</th><th>Minimum Rows</th><th>Footer Rule</th><th>Actions</th></tr></thead>
                      <tbody>
                        {sheetOrder.map((name, index) => (
                          <tr key={name}>
                            <td>{index + 1}</td>
                            <td><strong>{name}</strong></td>
                            <td><input type="number" min="1" disabled={!canUpdate} value={minimumRows[name] || ''} onChange={(e) => updateLayout((next) => { next.minimum_rows = { ...(next.minimum_rows || {}), [name]: Number(e.target.value) || 1 } })} /></td>
                            <td>
                              <select disabled={!canUpdate} value={footerRules[name]?.type || ''} onChange={(e) => updateLayout((next) => {
                                next.footer_rules = { ...(next.footer_rules || {}) }
                                if (!e.target.value) delete next.footer_rules[name]
                                else if (e.target.value === 'variant_sum') next.footer_rules[name] = { type: 'variant_sum', label_column: 1 }
                                else next.footer_rules[name] = { type: 'pnf_sumproduct', label: 'Pnf', label_column: 7, qty_column: 5, basic_cost_column: 6 }
                              })}>
                                <option value="">None</option>
                                <option value="variant_sum">Variant SUM footer</option>
                                <option value="pnf_sumproduct">PnF SUMPRODUCT footer</option>
                              </select>
                            </td>
                            <td>
                              <div className="table-actions">
                                <button className="row-action-btn" disabled={!canUpdate || index === 0} onClick={() => moveSheet(index, -1)}>↑ Up</button>
                                <button className="row-action-btn" disabled={!canUpdate || index === sheetOrder.length - 1} onClick={() => moveSheet(index, 1)}>↓ Down</button>
                                <button className="row-action-btn danger" disabled={!canUpdate} onClick={() => removeSheet(name)}>🗑 Remove</button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {activeTab === 'Formulas' && (
                <div className="row" style={{ alignItems: 'stretch' }}>
                  <div className="card" style={{ flex: 1 }}>
                    <h3>Formula Presets</h3>
                    <p className="page-sub ot-hint">These are old PCM formula patterns available to reuse in Formula Repository and output rules.</p>
                    {Object.entries(formulaPresets).map(([name, formula]) => (
                      <div key={name} className="ot-preset-row">
                        <div className="ot-preset-row-head">
                          <strong>{name}</strong>
                          <button className="row-action-btn danger" disabled={!canUpdate} onClick={() => updateLayout((next) => { delete next.old_formula_presets[name] })}>🗑 Remove</button>
                        </div>
                        <textarea className="ot-mono-textarea" disabled={!canUpdate} value={formula} onChange={(e) => updateLayout((next) => { next.old_formula_presets = { ...(next.old_formula_presets || {}), [name]: e.target.value } })} rows={2} />
                      </div>
                    ))}
                    <div className="ot-add-preset-row">
                      <input value={newPresetName} onChange={(e) => setNewPresetName(e.target.value)} placeholder="Preset name" disabled={!canUpdate} />
                      <input value={newPresetFormula} onChange={(e) => setNewPresetFormula(e.target.value)} placeholder="Formula" disabled={!canUpdate} />
                      <button className="btn" disabled={!canUpdate} onClick={addPreset}>+ Add Preset</button>
                    </div>
                  </div>
                  <div className="card" style={{ flex: 1 }}>
                    <h3>Sheet Formula Columns</h3>
                    <p className="page-sub ot-hint">Simple editor for formulas written into output sheet columns.</p>
                    {Object.entries(formulaColumns).map(([sheet, rules]) => (
                      <div key={sheet} className="ot-formula-sheet-group">
                        <strong>{sheet}</strong>
                        {(rules || []).map((rule, idx) => (
                          <div className="ot-preset-row" key={`${sheet}-${idx}`}>
                            <input value={rule.column || ''} disabled={!canUpdate} onChange={(e) => updateLayout((next) => { next.formula_columns[sheet][idx].column = e.target.value })} placeholder="Column name" />
                            <textarea className="ot-mono-textarea" disabled={!canUpdate} value={rule.formula || ''} onChange={(e) => updateLayout((next) => { next.formula_columns[sheet][idx].formula = e.target.value })} rows={2} style={{ marginTop: 6 }} />
                          </div>
                        ))}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {activeTab === 'Old PCM Status' && (
                <div className="row" style={{ alignItems: 'flex-start' }}>
                  <div className="card" style={{ flex: 1 }}>
                    <h3>Commodity Functions</h3>
                    {Object.entries(legacyMap.commodity_functions || {}).map(([name, info]) => (
                      <div className="validation-row" key={name}>
                        <span><strong>{name}</strong><br /><small>old lines {info.lines}</small></span>
                        <select disabled={!canUpdate} value={info.status || ''} onChange={(e) => updateLayout((next) => { next.legacy_pcm5_porting.commodity_functions[name].status = e.target.value })}>
                          <option value="pending_full_formula_port">Pending full formula port</option>
                          <option value="partial_output_port">Partial output port</option>
                          <option value="partial_calc_port">Partial calculation port</option>
                          <option value="partial_footer_port">Partial footer port</option>
                          <option value="complete">Complete</option>
                        </select>
                      </div>
                    ))}
                  </div>
                  <div className="card" style={{ flex: 1 }}>
                    <h3>Summary / Merge Rules</h3>
                    {Object.entries(legacyMap.summary_functions || {}).map(([name, info]) => (
                      <div className="validation-row" key={name}>
                        <span><strong>{name}</strong><br /><small>old lines {info.lines}</small></span>
                        <select disabled={!canUpdate} value={info.status || ''} onChange={(e) => updateLayout((next) => { next.legacy_pcm5_porting.summary_functions[name].status = e.target.value })}>
                          <option value="pending_full_formula_port">Pending full formula port</option>
                          <option value="partial_layout_port">Partial layout port</option>
                          <option value="partial_bom_source_port">Partial BOM source port</option>
                          <option value="partial_footer_port">Partial footer port</option>
                          <option value="complete">Complete</option>
                        </select>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {activeTab === 'Advanced JSON' && (
                <div className="card">
                  <h3>Advanced Layout JSON</h3>
                  <p className="page-sub ot-hint">Use this only for advanced support. Normal users should use the guided tabs.</p>
                  <textarea className="ot-json-textarea" value={jsonText} disabled={!canUpdate} onChange={(e) => setJsonText(e.target.value)} />
                  <button className="btn primary" style={{ marginTop: 10 }} disabled={!canUpdate || saving} onClick={saveJson}>Save JSON</button>
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  )
}
