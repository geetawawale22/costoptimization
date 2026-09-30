import { request, BASE_URL } from './client.js'

export async function login(email, password) {
  const body = new URLSearchParams()
  body.set('username', email)
  body.set('password', password)
  const res = await fetch(`${BASE_URL}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
    credentials: 'include', // required so the browser stores the session cookie the response sets
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Login failed')
  }
  return res.json() // MeOut: user + role + permissions, in one call
}

export function me() {
  return request('/auth/me')
}

export function logout() {
  return request('/auth/logout', { method: 'POST' })
}


export function exchangeSsoToken(payload) {
  return request('/auth/sso/exchange', { method: 'POST', body: payload })
}


export function ssoLoginUrl() {
  return `${BASE_URL}/auth/sso/login`
}


export function ssoStatus() {
  return request('/auth/sso/status')
}
