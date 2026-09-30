import { request } from './client.js'

// Users
export const listUsers = (page = 1, pageSize = 50, q = '') =>
  request('/users', { params: { page, page_size: pageSize, q } })
export const createUser = (payload) => request('/users', { method: 'POST', body: payload })
export const getUser = (id) => request(`/users/${id}`)
export const updateUser = (id, payload) => request(`/users/${id}`, { method: 'PUT', body: payload })
export const deactivateUser = (id, reason) =>
  request(`/users/${id}`, { method: 'DELETE', params: { reason } })
export const deactivateAllUsers = (reason = 'Deactivated by admin') =>
  request('/users', { method: 'DELETE', params: { reason } })

// Roles
export const listRoles = (page = 1, pageSize = 50, q = '') =>
  request('/roles', { params: { page, page_size: pageSize, q } })
export const createRole = (payload) => request('/roles', { method: 'POST', body: payload })
export const updateRole = (id, payload) => request(`/roles/${id}`, { method: 'PUT', body: payload })
export const deleteRole = (id) => request(`/roles/${id}`, { method: 'DELETE' })
export const deleteAllRoles = () => request('/roles', { method: 'DELETE' })
export const getRolePermissions = (id) => request(`/roles/${id}/permissions`)
export const setRolePermissions = (id, permissionIds) =>
  request(`/roles/${id}/permissions`, { method: 'PUT', body: { permission_ids: permissionIds } })

// Permissions catalogue — fully dynamic: create/edit/delete resource x action grants.
export const listPermissions = (page = 1, pageSize = 200, resource = '') =>
  request('/permissions', { params: { page, page_size: pageSize, resource } })
export const createPermission = (payload) => request('/permissions', { method: 'POST', body: payload })
export const updatePermission = (id, payload) => request(`/permissions/${id}`, { method: 'PUT', body: payload })
export const deletePermission = (id) => request(`/permissions/${id}`, { method: 'DELETE' })
export const deleteAllPermissions = () => request('/permissions', { method: 'DELETE' })

// Notification routing
export const listNotificationRules = () => request('/notification-rules')
export const upsertNotificationRule = (payload) =>
  request('/notification-rules', { method: 'PUT', body: payload })

// Output templates
export const listOutputTemplates = () => request('/output-templates')
export const createOutputTemplate = (payload) => request('/output-templates', { method: 'POST', body: payload })
export const updateOutputTemplate = (id, payload) =>
  request(`/output-templates/${id}`, { method: 'PUT', body: payload })
export const resetPcmSheetMetalTemplate = () =>
  request('/output-templates/pcm-sheet-metal-forging/reset', { method: 'POST' })

// Dashboard / reports
export const dashboardSummary = () => request('/dashboard/summary')
export const commodityDistribution = () => request('/dashboard/commodity-distribution')
export const auditLog = (params = {}) => {
  const query = new URLSearchParams()
  if (params.entity) query.set('entity', params.entity)
  if (params.action) query.set('action', params.action)
  if (params.q) query.set('q', params.q)
  query.set('page', params.page || 1)
  query.set('page_size', params.pageSize || 50)
  return request(`/reports/audit-log?${query.toString()}`)
}

// Notifications (in-app)
export const listNotifications = () => request('/notifications')
export const markNotificationRead = (id) => request(`/notifications/${id}/read`, { method: 'POST' })


// Dynamic application configuration / secrets
export const appConfigOptions = () => request('/app-config/options')
export const listAppConfig = ({ page = 1, pageSize = 50, q = '', group = '', key = '', active = '' } = {}) =>
  request('/app-config', { params: { page, page_size: pageSize, q, group, key, active } })
export const createAppConfig = (payload) => request('/app-config', { method: 'POST', body: payload })
export const updateAppConfig = (id, payload) =>
  request(`/app-config/${encodeURIComponent(id)}`, { method: 'PUT', body: payload })
export const toggleAppConfig = (id) =>
  request(`/app-config/${encodeURIComponent(id)}/toggle`, { method: 'POST' })
export const deleteAppConfig = (id) =>
  request(`/app-config/${encodeURIComponent(id)}`, { method: 'DELETE' })
export const getAppConfigValue = (group, key) =>
  request('/app-config/value', { params: { group, key } })
