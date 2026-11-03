export function matchesSearch(query, values) {
  const needle = String(query ?? '').trim().toLowerCase()
  if (!needle) return true
  return values.some(value => {
    const text = value && typeof value === 'object' ? JSON.stringify(value) : String(value ?? '')
    return text.toLowerCase().includes(needle)
  })
}

export function matchesFormula(item, query, commodity) {
  return (!commodity || item.commodity === commodity) && matchesSearch(query, [item.name, item.key, item.expression, item.type, ...(Array.isArray(item.inputs) ? item.inputs : [item.inputs])])
}

export function matchesResult(row, query, status, issuesOnly) {
  const incomplete = row.displayStatus !== 'CALCULATED'
  return (!status || (status === 'INCOMPLETE' ? incomplete : row.displayStatus === status)) && (!issuesOnly || incomplete) && matchesSearch(query, [row.inputs?.['Customer No'], row.commodity, row.type, row.inputs?.Supplier, row.inputs?.['Purchasing Supplier'], row.inputs?.['Costing Supplier'], row.inputs?._costing_supplier, ...(row.diagnostics || []).map(issue => issue.message)])
}
