import { request } from './api'

export const ftgCapabilities = () => request('/converters/ftg/capabilities')
export const listFtgJobs = (params = {}) => request('/converters/ftg/jobs', { params })
export function convertFtgPdfs(files) {
  const body = new FormData()
  files.forEach(file => body.append('pdf_files', file))
  return request('/converters/ftg/process', { method: 'POST', body, isForm: true })
}
export const downloadFtgJob = id => request(`/converters/ftg/jobs/${encodeURIComponent(id)}/download`, { isBlob: true })
export const previewFtgJob = (id, params = {}) => request(`/converters/ftg/jobs/${encodeURIComponent(id)}/preview`, { params })
