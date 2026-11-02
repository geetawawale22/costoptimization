// Falls back to whatever host actually served this page, on port 8000 — so opening
// the app via a LAN IP (e.g. http://192.168.1.5:5173) automatically talks to
// http://192.168.1.5:8000 with zero per-machine config. Set VITE_API_BASE_URL to
// override (e.g. when frontend/backend run on different hosts).
const BASE_URL = '/api'
let csrf = null
export function setCsrf(value) { csrf = value }
/**
 * Core request helper used by every api/*.js module.
 * - Session-cookie auth: no token is attached manually — `credentials: 'include'`
 *   makes the browser send the HttpOnly `session_id` cookie set by POST /auth/login.
 * - Throws an Error with a readable message on non-2xx responses.
 * - Returns parsed JSON, a Blob (for file downloads), or null (204/empty body).
 */
export async function request(path, { method = 'GET', body, isForm = false, isBlob = false, params } = {}) {
  const url = new URL(BASE_URL + path, window.location.origin)
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v)
    })
  }

  const headers = {}
  if (!["GET", "HEAD"].includes(method)) { headers["X-CostOptima-Request"] = "1"; if (csrf) headers["X-CSRF-Token"] = csrf }
  let finalBody = body
  if (body && !isForm) {
    headers['Content-Type'] = 'application/json'
    finalBody = JSON.stringify(body)
  }

  const res = await fetch(url.toString(), { method, headers, body: finalBody, credentials: 'include' })

  if (res.status === 401) {
    window.dispatchEvent(new Event("costoptima:expired"))
    if (window.location.pathname !== '/login' && window.location.pathname !== '/sso/callback' && window.location.pathname !== '/sso') {
      const returnTo = window.location.pathname + window.location.search + window.location.hash
      sessionStorage.setItem('costoptima.returnTo', returnTo)
      window.location.replace('/login')
    }
    const error = new Error('Session expired — please log in again')
    error.status = 401
    throw error
  }

  if (!res.ok) {
    let detail = res.statusText
    try {
      const errBody = await res.json()
      detail = errBody.detail || JSON.stringify(errBody)
    } catch (_) {
      /* response wasn't JSON */
    }
    const error = new Error(typeof detail === 'string' ? detail : (detail.message || JSON.stringify(detail)))
    error.status = res.status
    error.details = detail
    throw error
  }

  if (isBlob) return res.blob()
  const text = await res.text()
  return text ? JSON.parse(text) : null
}

export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  // Let the browser finish consuming the Blob before releasing its URL.
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

export { BASE_URL }

export async function login(email, password) {
  const body = new URLSearchParams()
  body.set('username', email)
  body.set('password', password)
  const res = await fetch(`${BASE_URL}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-CostOptima-Request': '1' },
    body,
    credentials: 'include', // required so the browser stores the session cookie the response sets
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Login failed')
  }
  const me = await res.json(); setCsrf(me.csrf_token); return me
}

export function me() {
  return request('/auth/me')
}

export function logout() {
  return request('/auth/logout', { method: 'POST' })
}



// Users
export const listUsers = (page = 1, pageSize = 20, q = '', filters = {}) =>
  request('/users', { params: { page, page_size: pageSize, q, ...filters } })
export const createUser = (payload) => request('/users', { method: 'POST', body: payload })
export const getUser = (id) => request(`/users/${id}`)
export const updateUser = (id, payload) => request(`/users/${id}`, { method: 'PUT', body: payload })
export const deactivateUser = (id, reason) =>
  request(`/users/${id}`, { method: 'DELETE', params: { reason } })

// Roles
export const listRoles = (page = 1, pageSize = 20, q = '') =>
  request('/roles', { params: { page, page_size: pageSize, q } })
export const createRole = (payload) => request('/roles', { method: 'POST', body: payload })
export const updateRole = (id, payload) => request(`/roles/${id}`, { method: 'PUT', body: payload })
export const deleteRole = (id) => request(`/roles/${id}`, { method: 'DELETE' })
export const getRolePermissions = (id) => request(`/roles/${id}/permissions`)
export const setRolePermissions = (id, permissionIds) =>
  request(`/roles/${id}/permissions`, { method: 'PUT', body: { permission_ids: permissionIds } })

// Permissions catalogue — fully dynamic: create/edit/delete resource x action grants.
export const listPermissions = (page = 1, pageSize = 200, resource = '', q = '', action = '') =>
  request('/permissions', { params: { page, page_size: pageSize, resource, q, action } })
export const createPermission = (payload) => request('/permissions', { method: 'POST', body: payload })
export const updatePermission = (id, payload) => request(`/permissions/${id}`, { method: 'PUT', body: payload })
export const deletePermission = (id) => request(`/permissions/${id}`, { method: 'DELETE' })

// Dynamic application configuration / secrets
export const appConfigOptions = () => request('/app-config/options')
export const listAppConfig = ({ page = 1, pageSize = 20, q = '', group = '', key = '', type = '', active = '' } = {}) =>
  request('/app-config', { params: { page, page_size: pageSize, q, group, key, type, active } })
export const createAppConfig = (payload) => request('/app-config', { method: 'POST', body: payload })
export const updateAppConfig = (id, payload) =>
  request(`/app-config/${encodeURIComponent(id)}`, { method: 'PUT', body: payload })
export const toggleAppConfig = (id) =>
  request(`/app-config/${encodeURIComponent(id)}/toggle`, { method: 'POST' })
export const deleteAppConfig = (id) =>
  request(`/app-config/${encodeURIComponent(id)}`, { method: 'DELETE' })
export const getAppConfigValue = (group, key) =>
  request('/app-config/value', { params: { group, key } })

export const appearance = () => request('/settings/appearance')
export const userStatistics = () => request('/user-statistics')
async function allPages(loader) {
  const first = await loader(1, 500)
  const items = [...first.items]
  for (let page = 2; page <= first.total_pages; page++) items.push(...(await loader(page, 500)).items)
  return { ...first, items }
}
export const allUsers = () => allPages(listUsers)
export const allRoles = () => allPages(listRoles)
export const allPermissions = () => allPages(listPermissions)

// Copied original SSO API entry points, using the same application client and session state.
export function exchangeSsoToken(payload) { return request('/auth/sso/exchange', { method: 'POST', body: payload }) }
export function ssoLoginUrl() { return `${BASE_URL}/auth/sso/login` }
export function ssoStatus() { return request('/auth/sso/status') }

// Centralized audit history; intentionally no mutation/export endpoints.
export const auditLog = params => request('/reports/audit-log', {params})
export const auditOptions = () => request('/reports/audit-log/options')
export const auditDetail = id => request(`/reports/audit-log/${encodeURIComponent(id)}`)
