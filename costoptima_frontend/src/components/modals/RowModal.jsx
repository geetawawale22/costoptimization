import { useState } from 'react'

export default function RowModal({ columns, initialRow, label, onClose, onSave }) {
  const [values, setValues] = useState(() => {
    const init = {}
    columns.forEach((c) => {
      init[c.column_name] = initialRow?.row_data?.[c.column_name] ?? ''
    })
    return init
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  async function handleSave() {
    setSaving(true)
    setError('')
    try {
      await onSave(values)
      onClose()
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-head">
          <h3>{initialRow ? 'Edit' : 'Add'} {label} Entry</h3>
          <button className="close-x" onClick={onClose}>✕</button>
        </div>
        <div className={`modal-body${columns.length > 4 ? ' modal-body-grid' : ''}`}>
          {columns.map((c) => (
            <div className="form-field" key={c.column_name}>
              <label>{c.column_name}{c.is_required ? ' *' : ''}</label>
              <input
                value={values[c.column_name]}
                onChange={(e) => setValues((v) => ({ ...v, [c.column_name]: e.target.value }))}
              />
            </div>
          ))}
          {error && <p className="validation-err">{error}</p>}
        </div>
        <div className="modal-foot">
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" onClick={handleSave} disabled={saving}>
            {saving ? 'Saving...' : 'Save Changes'}
          </button>
        </div>
      </div>
    </div>
  )
}
