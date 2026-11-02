import React from 'react'

/** Numbered pagination shared by the application's data tables. */
export default function Pagination({ page, pageSize, total, totalPages, onPageChange }) {
  if (!total || totalPages <= 1) return null

  const start = (page - 1) * pageSize + 1
  const end = Math.min(page * pageSize, total)
  const pageNumbers = []
  const windowSize = 2
  for (let number = Math.max(1, page - windowSize); number <= Math.min(totalPages, page + windowSize); number += 1) {
    pageNumbers.push(number)
  }

  const Arrow = ({ direction }) => (
    <svg aria-hidden="true" viewBox="0 0 20 20" className={`pagination-arrow ${direction}`}>
      <path d="M11.75 4.5 6.25 10l5.5 5.5" />
    </svg>
  )

  return (
    <div className="foot-row pagination-bar">
      <span>Showing {start}{'\u2013'}{end} of {total}</span>
      <div className="pagination-controls">
        <button type="button" className="btn pagination-nav-btn" aria-label="Previous page" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
          <Arrow direction="left" /><span>Previous</span>
        </button>
        {pageNumbers[0] > 1 && <span className="pagination-ellipsis" aria-hidden="true">...</span>}
        {pageNumbers.map((number) => (
          <button
            type="button"
            key={number}
            className={`btn pagination-page-btn ${number === page ? 'active' : ''}`}
            aria-label={`Page ${number}`}
            aria-current={number === page ? 'page' : undefined}
            onClick={() => onPageChange(number)}
          >
            {number}
          </button>
        ))}
        {pageNumbers[pageNumbers.length - 1] < totalPages && <span className="pagination-ellipsis" aria-hidden="true">...</span>}
        <button type="button" className="btn pagination-nav-btn" aria-label="Next page" disabled={page >= totalPages} onClick={() => onPageChange(page + 1)}>
          <span>Next</span><Arrow direction="right" />
        </button>
      </div>
    </div>
  )
}
