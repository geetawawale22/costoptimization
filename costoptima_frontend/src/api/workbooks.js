import { request } from './client.js'

export const listWorkbookTypes = (page = 1, pageSize = 50, q = '') =>
  request('/workbook-types', { params: { page, page_size: pageSize, q } })

export const createWorkbookType = (payload) => request('/workbook-types/', { method: 'POST', body: payload })
export const updateWorkbookType = (typeCode, payload) =>
  request(`/workbook-types/${typeCode}`, { method: 'PUT', body: payload })
export const deleteWorkbookType = (typeCode) => request(`/workbook-types/${typeCode}`, { method: 'DELETE' })
export const deleteAllWorkbookTypes = () => request('/workbook-types', { method: 'DELETE' })

export const getSchema = (typeCode) => request(`/workbook-types/${typeCode}/schema`)

export const listTabs = (typeCode, page = 1, pageSize = 50) =>
  request(`/workbook-types/${typeCode}/tabs`, { params: { page, page_size: pageSize } })

export const createTab = (typeCode, payload) =>
  request(`/workbook-types/${typeCode}/tabs`, { method: 'POST', body: payload })
export const updateTab = (typeCode, tabName, payload) =>
  request(`/workbook-types/${typeCode}/tabs/${encodeURIComponent(tabName)}`, { method: 'PUT', body: payload })
export const deleteTab = (typeCode, tabName) =>
  request(`/workbook-types/${typeCode}/tabs/${encodeURIComponent(tabName)}`, { method: 'DELETE' })
export const deleteAllTabs = (typeCode) =>
  request(`/workbook-types/${typeCode}/tabs`, { method: 'DELETE' })

export const listSections = (typeCode, tabName, page = 1, pageSize = 50) =>
  request(`/workbook-types/${typeCode}/tabs/${encodeURIComponent(tabName)}/sections`, {
    params: { page, page_size: pageSize },
  })
export const createSection = (typeCode, tabName, payload) =>
  request(`/workbook-types/${typeCode}/tabs/${encodeURIComponent(tabName)}/sections`, {
    method: 'POST', body: payload,
  })
export const updateSection = (typeCode, tabName, sectionId, payload) =>
  request(`/workbook-types/${typeCode}/tabs/${encodeURIComponent(tabName)}/sections/${sectionId}`, {
    method: 'PUT', body: payload,
  })
export const deleteSection = (typeCode, tabName, sectionId) =>
  request(`/workbook-types/${typeCode}/tabs/${encodeURIComponent(tabName)}/sections/${sectionId}`, {
    method: 'DELETE',
  })
export const deleteAllSections = (typeCode, tabName) =>
  request(`/workbook-types/${typeCode}/tabs/${encodeURIComponent(tabName)}/sections`, { method: 'DELETE' })

export const listColumns = (typeCode, tabName, page = 1, pageSize = 50, sectionId) =>
  request(`/workbook-types/${typeCode}/tabs/${encodeURIComponent(tabName)}/columns`, {
    params: { page, page_size: pageSize, section_id: sectionId },
  })
export const createColumn = (typeCode, tabName, payload) =>
  request(`/workbook-types/${typeCode}/tabs/${encodeURIComponent(tabName)}/columns`, {
    method: 'POST', body: payload,
  })
export const bulkCreateColumns = (typeCode, tabName, columns) =>
  request(`/workbook-types/${typeCode}/tabs/${encodeURIComponent(tabName)}/columns/bulk`, {
    method: 'POST', body: { columns },
  })
export const updateColumn = (typeCode, tabName, columnId, payload) =>
  request(`/workbook-types/${typeCode}/tabs/${encodeURIComponent(tabName)}/columns/${columnId}`, {
    method: 'PUT', body: payload,
  })
export const deleteColumn = (typeCode, tabName, columnId) =>
  request(`/workbook-types/${typeCode}/tabs/${encodeURIComponent(tabName)}/columns/${columnId}`, {
    method: 'DELETE',
  })
export const deleteAllColumns = (typeCode, tabName, sectionId) =>
  request(`/workbook-types/${typeCode}/tabs/${encodeURIComponent(tabName)}/columns`, {
    method: 'DELETE', params: { section_id: sectionId },
  })

/** Detects columns (and, if secondHeaderRow is given, a 2-row merged header with a
    group label per column) directly from an uploaded file — nothing is saved until
    the results are reviewed and applied via bulkCreateColumns. */
export const detectColumns = (typeCode, tabName, file, { headerRow, secondHeaderRow, sectionId } = {}) => {
  const form = new FormData()
  form.append('file', file)
  return request(`/workbook-types/${typeCode}/tabs/${encodeURIComponent(tabName)}/columns/detect`, {
    method: 'POST', body: form, isForm: true,
    params: { header_row: headerRow, second_header_row: secondHeaderRow, section_id: sectionId },
  })
}

/** Structure-only detection: proposes Sections (for a sheet with several blank-row
    separated tables) plus each section's columns. Never reads row data into the app —
    review the proposal, then it's applied via createSection + bulkCreateColumns. */
export const detectSections = (typeCode, tabName, file) => {
  const form = new FormData()
  form.append('file', file)
  return request(`/workbook-types/${typeCode}/tabs/${encodeURIComponent(tabName)}/detect-sections`, {
    method: 'POST', body: form, isForm: true,
  })
}

/** Declarative bulk create-or-update of workbook/tabs/sections/columns from a JSON
    "configuration query" — see the format shown in the Workbook Configuration page's
    Configuration Query dialog. Never touches row data. */
export const applyConfigQuery = (payload) =>
  request('/workbook-types/config/apply', { method: 'POST', body: payload })

export const listRows = (typeCode, tabName, { q = '', page = 1, pageSize = 50, sectionId } = {}) =>
  request(`/workbook-types/${typeCode}/tabs/${encodeURIComponent(tabName)}/rows`, {
    params: { q, page, page_size: pageSize, section_id: sectionId },
  })

export const addRow = (typeCode, tabName, rowData, sectionId) =>
  request(`/workbook-types/${typeCode}/tabs/${encodeURIComponent(tabName)}/rows`, {
    method: 'POST',
    body: { row_data: rowData, section_id: sectionId ?? null },
  })

export const updateRow = (typeCode, tabName, rowId, rowData, sectionId) =>
  request(`/workbook-types/${typeCode}/tabs/${encodeURIComponent(tabName)}/rows/${rowId}`, {
    method: 'PUT',
    body: { row_data: rowData, section_id: sectionId ?? null },
  })

export const deleteRow = (typeCode, tabName, rowId) =>
  request(`/workbook-types/${typeCode}/tabs/${encodeURIComponent(tabName)}/rows/${rowId}`, {
    method: 'DELETE',
  })

export const deleteAllRows = (typeCode, tabName, sectionId) =>
  request(`/workbook-types/${typeCode}/tabs/${encodeURIComponent(tabName)}/rows`, {
    method: 'DELETE',
    params: { section_id: sectionId },
  })

export const importWorkbook = (typeCode, file) => {
  const form = new FormData()
  form.append('file', file)
  return request(`/workbook-types/${typeCode}/import`, { method: 'POST', body: form, isForm: true })
}

export const importTab = (typeCode, tabName, file) => {
  const form = new FormData()
  form.append('file', file)
  return request(`/workbook-types/${typeCode}/tabs/${encodeURIComponent(tabName)}/import`, {
    method: 'POST', body: form, isForm: true,
  })
}

export const previewImport = (typeCode, file, tabName, headerRowOverride) => {
  const form = new FormData()
  form.append('file', file)
  return request(`/workbook-types/${typeCode}/import/preview`, {
    method: 'POST', body: form, isForm: true,
    params: { tab_name: tabName || undefined, header_row_override: headerRowOverride || undefined },
  })
}

export const commitImport = (typeCode, previewId, { tabNames, overrides, headerRowOverride } = {}) =>
  request(`/workbook-types/${typeCode}/import/commit`, {
    method: 'POST',
    body: {
      preview_id: previewId, tab_names: tabNames ?? null, overrides: overrides ?? {},
      header_row_override: headerRowOverride ?? null,
    },
  })

export const exportWorkbook = (typeCode) =>
  request(`/workbook-types/${typeCode}/export`, { isBlob: true })

export const exportTab = (typeCode, tabName) =>
  request(`/workbook-types/${typeCode}/tabs/${encodeURIComponent(tabName)}/export`, { isBlob: true })

export const listVersions = (typeCode) => request(`/workbook-types/${typeCode}/versions`)

export const rollback = (typeCode, version) =>
  request(`/workbook-types/${typeCode}/rollback/${version}`, { method: 'POST' })

export const runMerge = () => request('/workbook-types/merge/run', { method: 'POST' })
