import { request } from './client.js'

export const listCommodities = () => request('/commodity-types')
export const createCommodity = (payload) => request('/commodity-types', { method: 'POST', body: payload })
export const getCommodity = (id) => request(`/commodity-types/${id}`)
export const updateCommodity = (id, payload) => request(`/commodity-types/${id}`, { method: 'PUT', body: payload })
export const deleteCommodity = (id) => request(`/commodity-types/${id}`, { method: 'DELETE' })

export const listFields = (commodityId) => request(`/commodity-types/${commodityId}/fields`)
export const addField = (commodityId, payload) =>
  request(`/commodity-types/${commodityId}/fields`, { method: 'POST', body: payload })
export const updateField = (fieldId, payload) =>
  request(`/commodity-types/fields/${fieldId}`, { method: 'PUT', body: payload })
export const deleteField = (fieldId) => request(`/commodity-types/fields/${fieldId}`, { method: 'DELETE' })

export const listFormulas = (commodityId) => request(`/commodity-types/${commodityId}/formulas`)
export const addFormula = (commodityId, payload) =>
  request(`/commodity-types/${commodityId}/formulas`, { method: 'POST', body: payload })
export const updateFormula = (ruleId, payload) =>
  request(`/commodity-types/formulas/${ruleId}`, { method: 'PUT', body: payload })
export const deleteFormula = (ruleId) => request(`/commodity-types/formulas/${ruleId}`, { method: 'DELETE' })
export const testFormula = (ruleId, sampleRow) =>
  request(`/commodity-types/formulas/${ruleId}/test`, { method: 'POST', body: { sample_row: sampleRow } })
