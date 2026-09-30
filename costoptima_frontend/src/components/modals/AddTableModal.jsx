import { useState } from 'react'

/** Adds ANOTHER logical table to a tab that already has one — e.g. a sheet like
    SBC Format that packs a "Reference Commercial" block and a "Reference Technical"
    block into the same sheet, each with its own header row and columns. */
export default function AddTableModal({ onClose, onCreate }) {
  const [tableName, setTableName] = useState('')
  const [headerRowIndex, setHeaderRowIndex] = useState(0)
  const [columns, setColumns] = useState([{ column_name: '', data_type: 'string', is_required: false, is_unique_key: false }])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  function updateColumn(i, field, value) {
    setColumns((cols) => cols.map((c, idx) => (idx === i ? { ...c, [field]: value } : c)))
  }
  function addColumnRow() {
    setColumns((cols) => [...cols, { column_name: '', data_type: 'string', is_required: false, is_unique_key: false }])
  }
  function removeColumnRow(i) {
    setColumns((cols) => cols.filter((_, idx) => idx !== i))
  }

  async function handleSubmit() {
    if (!tableName.trim()) {
      setError('Table name is required')
      return
    }
    setBusy(true)
    setError('')
    try {
      const cleanCols = columns.filter((c) => c.column_name.trim())
      await onCreate({ table_name: tableName.trim(), header_row_index: Number(headerRowIndex) || 0, columns: cleanCols })
      onClose()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-head">
          <h3>Add Another Table to This Tab</h3>
          <button className="close-x" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">
          <p style={{ fontSize: 12.5, color: 'var(--text-muted)', marginTop: 0 }}>
            Use this when one sheet contains more than one logical table (e.g. a second block
            of data starting further down the sheet, with its own header row).
          </p>
          <div className="row">
            <div className="form-field" style={{ flex: 2 }}>
              <label>Table Name</label>
              <input value={tableName} onChange={(e) => setTableName(e.target.value)} placeholder="e.g. Reference Technical" />
            </div>
            <div className="form-field" style={{ flex: 1 }}>
              <label>Header Row No.</label>
              <input type="number" min="0" value={headerRowIndex} onChange={(e) => setHeaderRowIndex(e.target.value)} />
            </div>
          </div>

          {columns.map((c, i) => (
            <div key={i} className="row" style={{ alignItems: 'flex-end', marginBottom: 8 }}>
              <div className="form-field" style={{ flex: 2, marginBottom: 0 }}>
                <label>Column Name</label>
                <input value={c.column_name} onChange={(e) => updateColumn(i, 'column_name', e.target.value)} />
              </div>
              <div className="form-field" style={{ flex: 1, marginBottom: 0 }}>
                <label>Type</label>
                <select value={c.data_type} onChange={(e) => updateColumn(i, 'data_type', e.target.value)}>
                  <option value="string">string</option>
                  <option value="number">number</option>
                  <option value="boolean">boolean</option>
                  <option value="email-list">email-list</option>
                </select>
              </div>
              <button className="btn" onClick={() => removeColumnRow(i)} title="Remove">🗑</button>
            </div>
          ))}
          <button className="btn" onClick={addColumnRow}>＋ Add Column</button>
          {error && <p className="validation-err">{error}</p>}
        </div>
        <div className="modal-foot">
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" onClick={handleSubmit} disabled={busy}>
            {busy ? 'Creating...' : 'Create Table'}
          </button>
        </div>
      </div>
    </div>
  )
}