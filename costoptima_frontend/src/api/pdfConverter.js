import { request } from './client'

export function convertPdfs(files) {
  const form = new FormData()
  files.forEach((file) => form.append('pdf_files', file))
  return request('/pdf-converter/process', {
    method: 'POST',
    body: form,
    isForm: true,
    isBlob: true,
  })
}

export const listConverterJobs = (params = {}) => request('/pdf-converter/jobs', { params })

export const downloadConverterJob = (id) => request(`/pdf-converter/jobs/${id}/download`, { isBlob: true })

