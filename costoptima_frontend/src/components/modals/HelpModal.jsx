import { useEffect } from 'react'

// Served from costoptima_frontend/public — replace the file there to publish a new version.
export const USER_GUIDE_URL = '/CostOptima_User_Guide.pdf'

const GETTING_AROUND = [
  ['Calculations', 'Browse, open and export your saved cost calculations.'],
  ['New Calculation', 'Upload a BOM and run a new cost estimate.'],
  ['Masters', 'View and import the rate and reference data the calculations depend on.'],
  ['Commodity Types', 'Open the PCM Master and each commodity’s tables.'],
  ['Converter', 'Convert PDFs and other input files into the formats the app uses.'],
  ['History', 'Review past activity and results.'],
]

/**
 * Help dialog opened from the top bar's Help button. Points users at the full PDF
 * user guide and gives a short orientation to the main areas of the application.
 */
export default function HelpModal({ onClose }) {
  useEffect(() => {
    function onKeyDown(e) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal help-modal" role="dialog" aria-modal="true" aria-labelledby="help-modal-title">
        <div className="modal-head">
          <h3 id="help-modal-title">Help &amp; Support</h3>
          <button className="close-x" onClick={onClose} aria-label="Close help" type="button">✕</button>
        </div>

        <div className="modal-body">
          <p className="help-intro">
            New to CostOptima? The user guide walks through the application step by step, from
            signing in to running and exporting a calculation.
          </p>

          <div className="help-guide-card">
            <div className="help-guide-icon" aria-hidden="true">PDF</div>
            <div className="help-guide-text">
              <strong>CostOptima User Guide</strong>
              <span>Complete instructions for using the application</span>
            </div>
            <div className="help-guide-actions">
              <a className="btn primary" href={USER_GUIDE_URL} target="_blank" rel="noreferrer">
                Open Guide
              </a>
              <a className="btn" href={USER_GUIDE_URL} download="CostOptima_User_Guide.pdf">
                Download
              </a>
            </div>
          </div>

          <h4 className="help-section-title">Getting around</h4>
          <ul className="help-list">
            {GETTING_AROUND.map(([name, desc]) => (
              <li key={name}>
                <b>{name}</b>
                <span>{desc}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="modal-foot">
          <button className="btn" onClick={onClose} type="button">Close</button>
        </div>
      </div>
    </div>
  )
}
