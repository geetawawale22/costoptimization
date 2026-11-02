import React from 'react'
/**
 * Generic server-side pagination bar. Expects a PaginatedResponse-shaped object
 * ({ page, page_size, total, total_pages }) and calls onPageChange(nextPage) with a
 * real page number — the caller is responsible for re-fetching that page, so clicking
 * "2" always loads the actual next 50 records rather than just relabelling the button.
 */
export default function Pagination({ page, pageSize, total, totalPages, onPageChange }) {
  if (!total || totalPages <= 1) return null

  const start = total === 0 ? 0 : (page - 1) * pageSize + 1
  const end = Math.min(page * pageSize, total)

  const pageNumbers = []
  const windowSize = 2
  for (let p = Math.max(1, page - windowSize); p <= Math.min(totalPages, page + windowSize); p++) {
    pageNumbers.push(p)
  }

  return (
    <div className="foot-row" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
      <span>
        Showing {start}–{end} of {total}
      </span>
      <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
        <button className="btn" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>‹ Prev</button>
        {pageNumbers[0] > 1 && <span style={{ padding: '0 4px' }}>…</span>}
        {pageNumbers.map((p) => (
          <button
            key={p}
            className={`btn ${p === page ? 'primary' : ''}`}
            style={{ minWidth: 32 }}
            onClick={() => onPageChange(p)}
          >
            {p}
          </button>
        ))}
        {pageNumbers[pageNumbers.length - 1] < totalPages && <span style={{ padding: '0 4px' }}>…</span>}
        <button className="btn" disabled={page >= totalPages} onClick={() => onPageChange(page + 1)}>Next ›</button>
      </div>
    </div>
  )
}