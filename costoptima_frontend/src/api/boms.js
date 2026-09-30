import { request } from './client.js'

export function uploadBom(file, projectName, customer) {
  const form = new FormData()
  form.append('file', file)
  if (projectName) form.append('project_name', projectName)
  if (customer) form.append('customer', customer)
  return request('/boms/upload', { method: 'POST', body: form, isForm: true })
}

export const listBoms = () => request('/boms')
export const getBom = (id) => request(`/boms/${id}`)
export const getBomTabs = (id) => request(`/boms/${id}/tabs`)
export const previewBom = (id) => request(`/boms/${id}/preview`)
export const validateBom = (id) => request(`/boms/${id}/validate`, { method: 'POST' })
export const deleteBom = (id) => request(`/boms/${id}`, { method: 'DELETE' })
export const deleteAllBoms = () => request('/boms', { method: 'DELETE' })
