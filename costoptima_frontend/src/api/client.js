// Falls back to whatever host actually served this page, on port 8000 — so opening
// the app via a LAN IP (e.g. http://192.168.1.5:5173) automatically talks to
// http://192.168.1.5:8000 with zero per-machine config. Set VITE_API_BASE_URL to
// override (e.g. when frontend/backend run on different hosts).
const BASE_URL = (typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.VITE_API_BASE_URL)
  || `${window.location.protocol}//${window.location.hostname}:8000`
/**
 * Core request helper used by every api/*.js module.
 * - Session-cookie auth: no token is attached manually — `credentials: 'include'`
 *   makes the browser send the HttpOnly `session_id` cookie set by POST /auth/login.
 * - Throws an Error with a readable message on non-2xx responses.
 * - Returns parsed JSON, a Blob (for file downloads), or null (204/empty body).
 */
export async function request(path, { method = 'GET', body, isForm = false, isBlob = false, params } = {}) {
  const url = new URL(BASE_URL + path)
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v)
    })
  }

  const headers = {}
  let finalBody = body
  if (body && !isForm) {
    headers['Content-Type'] = 'application/json'
    finalBody = JSON.stringify(body)
  }

  const res = await fetch(url.toString(), { method, headers, body: finalBody, credentials: 'include' })

  if (res.status === 401) {
    if (window.location.pathname !== '/login') {
      window.location.href = '/login'
    }
    throw new Error('Session expired — please log in again')
  }

  if (!res.ok) {
    let detail = res.statusText
    try {
      const errBody = await res.json()
      detail = errBody.detail || JSON.stringify(errBody)
    } catch (_) {
      /* response wasn't JSON */
    }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
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
  URL.revokeObjectURL(url)
}

export { BASE_URL }
