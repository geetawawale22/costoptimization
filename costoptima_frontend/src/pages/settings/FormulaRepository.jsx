import { useEffect, useMemo, useState } from 'react'
import * as commodityApi from '../../api/commodities'
import { usePageCrumb } from '../../components/Layout'
import { useAuth } from '../../context/AuthContext'
import { useToast } from '../../context/ToastContext'
import { useConfirm } from '../../context/ConfirmContext'

const OLD_PCM_PRESETS = [
  {
    key: 'buy_std_basic_cost',
    title: 'Buy Std: Basic Cost per Piece',
    oldFunction: 'bystd()',
    output_field: 'Basic Cost/Pc',
    condition_expr: "type == 'BUY STD' or Type == 'BUY STD'",
    formula_expr: 'basic_cost_pc',
    description: 'Use when the row is a BUY STD item. The value should come from configured Std BOP / live master data mapping.',
  },
  {
    key: 'buy_std_total',
    title: 'Buy Std: Total per Piece',
    oldFunction: 'bystd()',
    output_field: 'Total',
    condition_expr: "type == 'BUY STD' or Type == 'BUY STD'",
    formula_expr: 'basic_cost_pc + icc + rej + oh + profit',
    description: 'Old Buy Std total pattern: basic cost plus ICC, rejection, overhead, and profit.',
  },
  {
    key: 'pkg_total',
    title: 'Packaging: Variant Weighted Cost',
    oldFunction: 'PKG()',
    output_field: 'Total',
    condition_expr: "type == 'PKG' or Type == 'PKG'",
    formula_expr: 'qty_veh * basic_cost',
    description: 'Old PKG flow rolls package cost by Qty/Veh and variant. Use this preset after mapping Qty/Veh and Basic Cost fields.',
  },
  {
    key: 'frt_total',
    title: 'Freight: Variant Weighted Cost',
    oldFunction: 'FRT()',
    output_field: 'Total',
    condition_expr: "type == 'FRT' or Type == 'FRT'",
    formula_expr: 'qty_veh * basic_cost',
    description: 'Old FRT flow rolls freight cost by Qty/Veh and variant. Use this preset after mapping Qty/Veh and Basic Cost fields.',
  },
  {
    key: 'forging_basic',
    title: 'Forging: Basic Cost',
    oldFunction: 'forging_data() / forg_estimation()',
    output_field: 'Basic Cost/Pc',
    condition_expr: "commodity == 'FORGING' or Commodity == 'FORGING'",
    formula_expr: 'rm_cost_pc + process_cost',
    description: 'Forging basic cost pattern from old flow: raw material cost plus process cost.',
  },
  {
    key: 'make_total',
    title: 'Make: Total Cost',
    oldFunction: 'make()',
    output_field: 'Total',
    condition_expr: "type == 'MAKE' or Type == 'MAKE'",
    formula_expr: 'rm_cost_pc + process_cost + icc + rej + oh + profit',
    description: 'Generic MAKE row total: RM + process + commercial markups.',
  },
  {
    key: 'assy_total',
    title: 'Assembly: Total Cost',
    oldFunction: 'assy()',
    output_field: 'Total',
    condition_expr: "type == 'ASSY' or Type == 'ASSY'",
    formula_expr: 'basic_cost + markup',
    description: 'Assembly total pattern. Map Basic Cost and Markup from master/configured fields.',
  },
  {
    key: 'special_process_total',
    title: 'Special Process: Total Cost',
    oldFunction: 'special_process() / merge()',
    output_field: 'Total',
    condition_expr: "type == 'SPECIAL PROCESS' or Type == 'SPECIAL PROCESS'",
    formula_expr: 'basic_cost + markup',
    description: 'Special Process total pattern. The old merge used Paint/Soft/Norms lookups depending on process name.',
  },
]

const FIELD_ALIASES = [
  ['Qty/Veh', 'qty_veh'], ['Basic Cost', 'basic_cost'], ['Basic Cost/Pc', 'basic_cost_pc'],
  ['RM Cost/Pc', 'rm_cost_pc'], ['Process Cost', 'process_cost'], ['ICC', 'icc'],
  ['REJ', 'rej'], ['O&H', 'oh'], ['Profit', 'profit'], ['Markup', 'markup'],
  ['Type', 'type'], ['Commodity', 'commodity'], ['Material Code', 'material_code'],
]

function emptyFormula() { return { condition_expr: '', formula_expr: '', output_field: '', priority: 0 } }

function FormulaModal({ formula, canSave, onClose, onSave, saving }) {
  const [draft, setDraft] = useState(formula || emptyFormula())
  const isEdit = !!formula?.id

  useEffect(() => {
    const closeOnEscape = (event) => event.key === 'Escape' && onClose()
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [onClose])

  return (
    <div className="config-modal-overlay" onClick={(event) => event.target === event.currentTarget && onClose()}>
      <div className="config-modal formula-modal" role="dialog" aria-modal="true" aria-label={isEdit ? 'Edit Formula' : 'Add Formula'}>
        <header className="config-modal-head">
          <div><h2>{isEdit ? 'Edit Formula' : 'Add Formula'}</h2></div>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close">×</button>
        </header>
        <div className="config-modal-body">
          <div className="form-field">
            <label>Output Field</label>
            <input autoFocus value={draft.output_field} onChange={(e) => setDraft((v) => ({ ...v, output_field: e.target.value }))} placeholder="Example: Total" />
          </div>
          <div className="form-field">
            <label>When should this formula run?</label>
            <input value={draft.condition_expr || ''} onChange={(e) => setDraft((v) => ({ ...v, condition_expr: e.target.value }))} placeholder="Leave blank to always run" />
          </div>
          <div className="form-field">
            <label>Formula</label>
            <textarea value={draft.formula_expr} onChange={(e) => setDraft((v) => ({ ...v, formula_expr: e.target.value }))} rows={4} placeholder="Example: basic_cost_pc + icc + rej + oh + profit" />
          </div>
          <div className="form-field formula-priority-field">
            <label>Priority</label>
            <input type="number" value={draft.priority} onChange={(e) => setDraft((v) => ({ ...v, priority: Number(e.target.value) || 0 }))} />
          </div>

          <div className="formula-alias-section">
            <h4>Available Field Names</h4>
            <p className="page-sub formula-card-sub">Use the simple name in formulas — workbook columns are normalized by the calculation engine.</p>
            <div className="field-alias-list">
              {FIELD_ALIASES.map(([label, alias]) => (
                <span key={alias} className="field-alias-chip"><span>{label}</span><span className="field-alias-arrow">→</span><code>{alias}</code></span>
              ))}
            </div>
          </div>
        </div>
        <footer className="config-modal-footer">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button
            type="button"
            className="btn primary"
            disabled={!canSave || !draft.output_field || !draft.formula_expr || saving}
            title={canSave ? '' : "You don't have permission to save formulas."}
            onClick={() => onSave(draft)}
          >
            {saving ? 'Saving...' : isEdit ? 'Save Changes' : 'Add Formula'}
          </button>
        </footer>
      </div>
    </div>
  )
}

export default function FormulaRepository() {
  usePageCrumb('Settings / <b>Formula Repository</b>')
  const { can } = useAuth()
  const showToast = useToast()
  const confirm = useConfirm()

  const [commodities, setCommodities] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [formulas, setFormulas] = useState([])
  const [loading, setLoading] = useState(true)
  const [formulaModal, setFormulaModal] = useState(null)
  const [saving, setSaving] = useState(false)
  const [testingId, setTestingId] = useState(null)
  const [testSample, setTestSample] = useState('{"type":"BUY STD","basic_cost_pc":100,"icc":1,"rej":2,"oh":3,"profit":4}')
  const [testResult, setTestResult] = useState(null)
  const [presetFilter, setPresetFilter] = useState('')

  const selectedCommodity = commodities.find((c) => c.id === selectedId)
  const canDelete = can('commodity_config', 'delete')
  const canCreate = can('commodity_config', 'create')
  const canUpdate = can('commodity_config', 'update')

  async function loadFormulas(id = selectedId) {
    if (!id) return
    const rows = await commodityApi.listFormulas(id)
    setFormulas(rows.sort((a, b) => (a.priority || 0) - (b.priority || 0)))
  }

  useEffect(() => {
    setLoading(true)
    commodityApi.listCommodities()
      .then((cs) => { setCommodities(cs); if (cs.length) setSelectedId(cs[0].id) })
      .catch((err) => showToast(err.message))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => { if (selectedId) loadFormulas().catch((err) => showToast(err.message)) }, [selectedId]) // eslint-disable-line

  const presets = useMemo(() => {
    const q = presetFilter.trim().toLowerCase()
    if (!q) return OLD_PCM_PRESETS
    return OLD_PCM_PRESETS.filter((p) => `${p.title} ${p.oldFunction} ${p.description}`.toLowerCase().includes(q))
  }, [presetFilter])

  function openAddFormula() {
    setFormulaModal({ id: null, formula: emptyFormula() })
  }
  function usePreset(preset) {
    setFormulaModal({
      id: null,
      formula: {
        condition_expr: preset.condition_expr,
        formula_expr: preset.formula_expr,
        output_field: preset.output_field,
        priority: formulas.length + 1,
      },
    })
  }
  function editFormula(f) {
    setFormulaModal({
      id: f.id,
      formula: {
        condition_expr: f.condition_expr || '',
        formula_expr: f.formula_expr || '',
        output_field: f.output_field || '',
        priority: f.priority || 0,
      },
    })
  }

  async function saveFormula(draft) {
    setSaving(true)
    try {
      if (formulaModal.id) {
        await commodityApi.updateFormula(formulaModal.id, draft)
        showToast('Formula updated.')
      } else {
        await commodityApi.addFormula(selectedId, draft)
        showToast('Formula added.')
      }
      setFormulaModal(null)
      await loadFormulas()
    } catch (err) {
      showToast(err.message)
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(formula) {
    if (!(await confirm({ title: 'Delete Formula', message: `Delete the formula for "${formula.output_field}"? This cannot be undone.` }))) return
    try {
      await commodityApi.deleteFormula(formula.id)
      showToast('Formula deleted.')
      await loadFormulas()
    } catch (err) { showToast(err.message) }
  }

  async function handleTest(ruleId) {
    setTestingId(ruleId)
    setTestResult(null)
    try {
      const sampleRow = JSON.parse(testSample)
      const result = await commodityApi.testFormula(ruleId, sampleRow)
      setTestResult(result)
    } catch (err) {
      setTestResult({ error: err.message })
    }
  }

  const canSave = formulaModal?.id ? canUpdate : canCreate

  return (
    <div className="formula-page">
      <div className="settings-page-header">
        <div className="settings-header-row">
          <div>
            <h1 className="page-title">Formula Repository</h1>
            <p className="page-sub">Manage calculation rules using guided old-PCM presets — pick a preset, review its plain-language meaning, and save it for a commodity without writing code.</p>
          </div>
          <button className="btn primary" disabled={!canCreate} title={canCreate ? '' : "You don't have permission to add formulas."} onClick={openAddFormula}>+ Add Formula</button>
        </div>
      </div>

      <div className="card formula-toolbar-card">
        <div className="form-field formula-commodity-field">
          <label>Commodity / Process Area</label>
          <select value={selectedId || ''} onChange={(e) => setSelectedId(Number(e.target.value))}>
            {commodities.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
        {selectedCommodity && (
          <div className="formula-summary-strip">
            <span><strong>{selectedCommodity.code}</strong> code</span>
            <span><strong>{formulas.length}</strong> active formula{formulas.length === 1 ? '' : 's'}</span>
          </div>
        )}
      </div>

      {loading ? (
        <div className="loading">Loading commodities...</div>
      ) : (
        <>
          <div className="formula-columns">
            <section className="card">
              <div className="card-title-row">
                <div>
                  <h3>Old PCM Presets</h3>
                  <p className="page-sub formula-card-sub">Choose a migrated rule pattern from the old PCM_5 code — it opens pre-filled in the formula editor so you can still adjust it before saving.</p>
                </div>
              </div>
              <input className="formula-preset-search" value={presetFilter} onChange={(e) => setPresetFilter(e.target.value)} placeholder="Search presets..." />
              <div className="preset-list">
                {presets.map((p) => (
                  <div key={p.key} className="preset-row">
                    <div className="preset-row-head">
                      <div>
                        <strong>{p.title}</strong>
                        <span className="preset-badge">from {p.oldFunction}</span>
                      </div>
                      <button className="btn" disabled={!canCreate} title={canCreate ? '' : "You don't have permission to add formulas."} onClick={() => usePreset(p)}>Use Preset</button>
                    </div>
                    <p>{p.description}</p>
                  </div>
                ))}
                {presets.length === 0 && <div className="permission-empty">No presets match "{presetFilter}".</div>}
              </div>
            </section>

            <section className="card">
              <div className="card-title-row">
                <div>
                  <h3>Available Field Names</h3>
                  <p className="page-sub formula-card-sub">Use the simple name in formulas — workbook columns are normalized by the calculation engine.</p>
                </div>
              </div>
              <div className="field-alias-list">
                {FIELD_ALIASES.map(([label, alias]) => (
                  <span key={alias} className="field-alias-chip"><span>{label}</span><span className="field-alias-arrow">→</span><code>{alias}</code></span>
                ))}
              </div>
            </section>
          </div>

          <section className="card formula-active-card">
            <div className="card-title-row">
              <div><h3>Active Formulas for {selectedCommodity?.name || 'Commodity'}</h3></div>
            </div>
            <div className="formula-list">
              {formulas.map((f) => (
                <div className="formula-row" key={f.id}>
                  <div className="formula-row-head">
                    <div className="formula-row-meta">
                      <strong>{f.output_field}</strong>
                      <span className="formula-priority-chip">Priority {f.priority || 0}</span>
                      {f.condition_expr && <span className="formula-condition">when {f.condition_expr}</span>}
                    </div>
                    <div className="table-actions">
                      <button className="row-action-btn accent" onClick={() => handleTest(f.id)}>🧪 Test</button>
                      <button className="row-action-btn" disabled={!canUpdate} title={canUpdate ? '' : "You don't have permission to edit formulas."} onClick={() => editFormula(f)}>✎ Edit</button>
                      <button className="row-action-btn danger" disabled={!canDelete} title={canDelete ? '' : "You don't have permission to delete formulas."} onClick={() => handleDelete(f)}>🗑 Delete</button>
                    </div>
                  </div>
                  <code className="formula-code">{f.formula_expr}</code>
                  {testingId === f.id && testResult && (
                    <div className={`formula-test-result ${testResult.error ? 'error' : 'success'}`}>
                      {testResult.error ? `Error: ${testResult.error}` : `${testResult.output_field} = ${String(testResult.result)}`}
                    </div>
                  )}
                </div>
              ))}
              {formulas.length === 0 && <div className="permission-empty">No formulas yet for this commodity. Pick an old PCM preset above, or add one from scratch.</div>}
            </div>

            <div className="formula-test-panel">
              <div className="access-section-label">Sample row for Test</div>
              <textarea value={testSample} onChange={(e) => setTestSample(e.target.value)} rows={3} placeholder='{"type":"BUY STD", ...}' />
              <p className="step-help">Click "Test" on a formula above to run it against this sample row.</p>
            </div>
          </section>
        </>
      )}

      {formulaModal && (
        <FormulaModal
          formula={formulaModal.id ? { id: formulaModal.id, ...formulaModal.formula } : formulaModal.formula}
          canSave={canSave}
          saving={saving}
          onClose={() => setFormulaModal(null)}
          onSave={saveFormula}
        />
      )}
    </div>
  )
}
