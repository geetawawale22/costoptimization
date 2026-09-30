import { request } from './client.js'

/**
 * Runs synchronously on the backend — this single call uploads no job id to poll;
 * the response IS the finished calculation (status, per-commodity results, logs,
 * and the final cost result all together).
 */
export const calculate = (bomId) => request(`/boms/${bomId}/calculate`, { method: 'POST' })

export const listCalculations = () => request('/calculations')
export const getCalculation = (id) => request(`/calculations/${id}`)
export const exportCalculation = (id) => request(`/calculations/${id}/export`, { isBlob: true })
export const exportAllCalculations = () => request('/calculations/export-all', { isBlob: true })
export const deleteCalculation = (id) => request(`/calculations/${id}`, { method: 'DELETE' })
export const deleteAllCalculations = () => request('/calculations', { method: 'DELETE' })
