import { useRef, useState } from 'react'
import * as bomsApi from '../api/boms'
import * as calcApi from '../api/calculations'
import { downloadBlob } from '../api/client'
import { usePageCrumb } from '../components/Layout'
import { useToast } from '../context/ToastContext'
import StatusChip from '../components/StatusChip'

export default function NewCalculation() {
  usePageCrumb('New Calculation')
  const showToast = useToast()
  const fileInputRef = useRef(null)

  const [projectName, setProjectName] = useState('')
  const [description, setDescription] = useState('')
  const [email, setEmail] = useState('')

  const [uploading, setUploading] = useState(false)
  const [bom, setBom] = useState(null)
  const [tabs, setTabs] = useState(null)
  const [previewRows, setPreviewRows] = useState([])
  const [validation, setValidation] = useState(null)

  const [calculating, setCalculating] = useState(false)
  const [calculation, setCalculation] = useState(null)

  async function handleFileChange(e) {
    const file = e.target.files[0]
    if (!file) return
    if (!projectName.trim() || !description.trim()) {
      showToast('Complete calculation information before uploading a PCM file.')
      e.target.value = ''
      return
    }
    setUploading(true)
    setCalculation(null)
    setBom(null)
    setTabs(null)
    setPreviewRows([])
    setValidation(null)
    try {
      const uploaded = await bomsApi.uploadBom(file, projectName)
      setBom(uploaded)
      setTabs(uploaded.tabs || [])
      const [preview, checks] = await Promise.all([
        bomsApi.previewBom(uploaded.id).catch(() => []),
        bomsApi.validateBom(uploaded.id),
      ])
      setPreviewRows(preview || [])
      setValidation(checks)
    } catch (err) {
      showToast(err.message)
    } finally {
      setUploading(false)
    }
  }

  async function handleCalculate() {
    setCalculating(true)
    try {
      const finished = await calcApi.calculate(bom.id)
      setCalculation(finished)
    } catch (err) {
      showToast(err.message)
    } finally {
      setCalculating(false)
    }
  }

  async function handleDownloadOutput() {
    try {
      const blob = await calcApi.exportCalculation(calculation.id)
      downloadBlob(blob, `calculation_${calculation.id}_output.xlsx`)
    } catch (err) {
      showToast(err.message)
    }
  }

  const result = calculation?.result
  const previewColumns = Array.from(new Set(previewRows.flatMap((r) => Object.keys(r.row_data || {}).filter((k) => !k.startsWith('_'))))).slice(0, 10)
  const validationHasError = validation?.issues?.some((i) => i.status === 'Error')
  const calculationInfoComplete = Boolean(projectName.trim() && description.trim())
  const uploadComplete = calculationInfoComplete && Boolean(bom)
  const validationComplete = uploadComplete && Boolean(validation) && !validationHasError
  const calculationComplete = Boolean(result)
  const uploadLocked = !calculationInfoComplete
  const validationLocked = !uploadComplete
  const calculateLocked = !validationComplete
  const resultLocked = !calculationComplete

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h1 className="page-title">New Cost Calculation</h1>
          <p className="page-sub">New PCM estimate</p>
        </div>
      </div>

      <div className="stepper">
        <div className="step">
          <div className="step-num">1</div>
          <div className="step-body">
            <div className="step-title">Calculation Information</div>
            <div className="card">
              <div className="form-field"><label>Project Name <span className="required-mark">*</span></label><input value={projectName} onChange={(e) => setProjectName(e.target.value)} placeholder="e.g. S302" required /></div>
              <div className="form-field"><label>Description <span className="required-mark">*</span></label><input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Enter calculation notes..." required /></div>
              <div className="form-field"><label>Notify Email (optional, not configured yet)</label><input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Email will show Not configured until SMTP is configured" /></div>
              {!calculationInfoComplete && <p className="step-help">Complete the required calculation information to continue.</p>}
            </div>
          </div>
        </div>

        <div className={`step ${uploadLocked ? 'locked' : ''}`}>
          <div className="step-num">{uploadLocked ? '🔒' : '2'}</div>
          <div className="step-body">
            <div className="step-title">Upload PCM Input File</div>
            <div className="card">
              <label className={`dropzone ${uploadLocked ? 'disabled' : ''}`} style={{ display: 'block' }}>
                {uploadLocked ? 'Complete Step 1 to upload a PCM file.' : uploading ? '⏳ Uploading, reading sheets, and preparing preview...' : '☁ Click to upload or drag and drop'}
                <br />
                <span style={{ fontSize: 11.5 }}>Original input workbook (.xlsx)</span>
                <input ref={fileInputRef} type="file" accept=".xlsx" onChange={handleFileChange} style={{ display: 'none' }} disabled={uploadLocked || uploading} />
              </label>

              {tabs && (
                <div style={{ marginTop: 16 }}>
                  <p style={{ fontSize: 12.5, color: 'var(--text-muted)', marginBottom: 8 }}>Detected workbook sheets:</p>
                  {tabs.map((t) => <span className="commodity-pill" key={t.id}>✓ {t.detected_sheet_name}</span>)}
                </div>
              )}

              {previewRows.length > 0 && (
                <div className="table-scroll" style={{ marginTop: 16 }}>
                  <p style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>Input preview (first rows):</p>
                  <table>
                    <thead><tr>{previewColumns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
                    <tbody>
                      {previewRows.slice(0, 5).map((r) => (
                        <tr key={r.id}>{previewColumns.map((c) => <td key={c}>{String((r.row_data || {})[c] ?? '')}</td>)}</tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </div>

        <div className={`step ${validationLocked ? 'locked' : ''}`}>
          <div className="step-num">{validationLocked ? '🔒' : '3'}</div>
          <div className="step-body">
            <div className="step-title">Validation Checklist</div>
            <div className="card">
              {validationLocked && <p className="step-help">Upload a PCM input file to run validation.</p>}
              {!validationLocked && !validation && <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>Validation will appear after upload processing finishes.</p>}
              {validation && validation.issues.map((issue) => (
                <div className="validation-row" key={issue.check}>
                  <span>{issue.check}{issue.message ? ` — ${issue.message}` : ''}</span>
                  <StatusChip value={issue.status} />
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className={`step ${calculateLocked ? 'locked' : ''}`}>
          <div className="step-num">{calculateLocked ? '🔒' : '4'}</div>
          <div className="step-body">
            <div className="step-title">Calculate PCM Cost</div>
            <div className="card">
              <button className="btn primary" onClick={handleCalculate} disabled={calculateLocked || calculating || Boolean(calculation)}>
                {calculating ? '⏳ Generating PCM workbook...' : '▶ Generate Output Workbook'}
              </button>
              {!validation && <p className="step-help">Complete validation before generating output.</p>}
              {validationHasError && <p className="validation-err">Fix validation errors before generating output.</p>}
              {calculation?.logs?.length > 0 && (
                <div style={{ marginTop: 16 }}>
                  <p style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>Process logs:</p>
                  {calculation.logs.map((log) => (
                    <div className="validation-row" key={log.id}>
                      <span><b>{log.process || log.level}</b> — {log.message}</span>
                      <StatusChip value={log.level === 'Success' ? 'Passed' : 'Info'} />
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        <div className={`step ${resultLocked ? 'locked' : ''}`}>
          <div className="step-num">{resultLocked ? '🔒' : '5'}</div>
          <div className="step-body">
            <div className="step-title">Result</div>
            <div className="card">
              {!result && <p className="step-help">Result appears after Step 4 finishes. The stored workbook is available from File Download.</p>}
              {result && (
                <div className="result-summary">
                  <div className="result-summary-done">
                    <div className="result-summary-check">✓</div>
                    <div>
                      <p className="result-summary-title">PCM Calculation Completed</p>
                      <p className="result-summary-sub">Calculation completed and the PCM workbook is stored in outputs. Download fetches the same stored file.</p>
                    </div>
                  </div>
                  <div className="validation-row">
                    <span>Email notification</span>
                    <StatusChip value={result.email_sent === 'Yes' ? 'Completed' : 'Not configured'} />
                  </div>
                  <div className="validation-row">
                    <span>📄 PCM Calculation Output.xlsx</span>
                    <button className="btn" onClick={handleDownloadOutput}>⬇ Download</button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  )
}
