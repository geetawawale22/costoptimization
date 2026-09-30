import { useEffect, useRef, useState } from 'react'
import { downloadBlob } from '../api/client'
import { convertPdfs, downloadConverterJob, listConverterJobs } from '../api/pdfConverter'
import { usePageCrumb } from '../components/Layout'
import StatusChip from '../components/StatusChip'
import './PdfConverter.css'

export default function PdfConverter() {
  usePageCrumb('PDF TO EXCEL CONVERTER')
  const [files, setFiles] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [download, setDownload] = useState('')
  const [jobs, setJobs] = useState([])
  const [historyLoading, setHistoryLoading] = useState(true)
  const downloadRef = useRef('')

  function clearDownload() {
    if (downloadRef.current) URL.revokeObjectURL(downloadRef.current)
    downloadRef.current = ''
    setDownload('')
  }

  useEffect(() => () => {
    if (downloadRef.current) URL.revokeObjectURL(downloadRef.current)
  }, [])

  async function refreshJobs() {
    setHistoryLoading(true)
    try {
      const rows = await listConverterJobs({ flow_type: 'ftg', converter_type: 'pdf_converter' })
      setJobs(rows)
    } catch (failure) {
      setError(failure.message || 'Unable to load converter history.')
    } finally {
      setHistoryLoading(false)
    }
  }

  useEffect(() => {
    refreshJobs()
  }, [])

  function selectFiles(event) {
    clearDownload()
    setError('')
    const selected = Array.from(event.target.files || [])
    if (selected.some((file) => !file.name.toLowerCase().endsWith('.pdf'))) {
      setFiles([])
      setError('Please select PDF files only.')
      event.target.value = ''
      return
    }
    setFiles(selected)
  }

  async function convert(event) {
    event.preventDefault()
    if (busy || !files.length) return
    setBusy(true)
    setError('')
    clearDownload()
    try {
      const blob = await convertPdfs(files)
      const url = URL.createObjectURL(blob)
      downloadRef.current = url
      setDownload(url)
      await refreshJobs()
    } catch (failure) {
      setError(failure.message || 'Unable to connect to the converter.')
    } finally {
      setBusy(false)
    }
  }

  async function downloadSaved(job) {
    try {
      const blob = await downloadConverterJob(job.id)
      downloadBlob(blob, job.output_filename || `pdf_converter_v${job.version}.xlsx`)
    } catch (failure) {
      setError(failure.message || 'Unable to download converter output.')
    }
  }

  return (
    <div className="pdf-converter-page">
      <h1 className="page-title">PDF TO EXCEL CONVERTER</h1>
      <p className="page-sub">Convert mould specification PDFs into the existing MSS Excel workbook format.</p>

      <div className="pdf-converter-card">
        <section className="pdf-converter-intro">
          <p className="pdf-converter-eyebrow">PDF TO EXCEL</p>
          <h2>Your PDFs. One Excel workbook.</h2>
          <p>Select your mould specification PDFs and convert them into the existing MSS Excel format.</p>
        </section>

        <form onSubmit={convert} aria-busy={busy} className="pdf-converter-form">
          <div className="pdf-converter-step">
            <span>01</span>
            <h3>Select your files</h3>
          </div>

          <label className="pdf-converter-file-label" htmlFor="pdf-files">PDF documents</label>
          <input
            id="pdf-files"
            type="file"
            accept=".pdf,application/pdf"
            multiple
            onChange={selectFiles}
            disabled={busy}
            aria-describedby="pdf-file-hint"
          />
          <p id="pdf-file-hint" className="pdf-converter-hint">
            Choose one or more PDFs. Multiple files are combined into one workbook.
          </p>

          {files.length > 0 && (
            <ul className="pdf-converter-files">
              {files.map((file, index) => (
                <li key={`${file.name}-${index}`}>
                  <span>{file.name}</span>
                  <small>{Math.max(1, Math.round(file.size / 1024))} KB</small>
                </li>
              ))}
            </ul>
          )}

          <button className="btn primary pdf-converter-submit" type="submit" disabled={busy || !files.length}>
            {busy ? 'Converting...' : 'Convert to Excel'}
          </button>

          <div aria-live="polite" role="status">
            {busy && <p className="pdf-converter-hint">Processing your PDFs. Larger batches may take several minutes.</p>}
            {download && (
              <div className="pdf-converter-success">
                <p>Your workbook is ready.</p>
                <a className="btn" href={download} download="Compiled_MSS_Report.xlsx">Download Excel</a>
              </div>
            )}
          </div>

          {error && <p className="pdf-converter-error" role="alert">{error}</p>}
        </form>
      </div>

      <div className="card" style={{ marginTop: 18 }}>
        <h3>Converted Files</h3>
        {historyLoading && <div className="loading">Loading...</div>}
        {!historyLoading && (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Input File</th>
                  <th>Version</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th>Output</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {jobs.length === 0 && (
                  <tr>
                    <td colSpan={6} style={{ textAlign: 'center', padding: 24, color: 'var(--text-muted)' }}>
                      No converted files yet.
                    </td>
                  </tr>
                )}
                {jobs.map((job) => (
                  <tr key={job.id}>
                    <td>{job.source_name}</td>
                    <td>v{job.version}</td>
                    <td><StatusChip value={job.status} /></td>
                    <td>{job.created_at ? new Date(job.created_at).toLocaleString() : '-'}</td>
                    <td>{job.output_filename}</td>
                    <td><button className="btn" onClick={() => downloadSaved(job)}>Download</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
