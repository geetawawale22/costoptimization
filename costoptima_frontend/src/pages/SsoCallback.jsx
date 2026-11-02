import React from 'react'
import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { exchangeSsoToken } from '../services/api'
import { useAuth } from '../auth/AuthContext'

const TOKEN_KEYS = ['jwt_token', 'token', 'jwt', 'id_token', 'access_token']

function collectSsoParams(searchParams) {
  const values = {}
  searchParams.forEach((value, key) => {
    values[key] = value
  })
  // Preserve the original provider's opaque tokens when '+' was not escaped.
  for (const pair of window.location.search.slice(1).split('&')) {
    const separator = pair.indexOf('=')
    if (separator < 0) continue
    try {
      const key = decodeURIComponent(pair.slice(0, separator))
      if (TOKEN_KEYS.includes(key)) values[key] = decodeURIComponent(pair.slice(separator + 1))
    } catch { /* Invalid encodings are rejected by the identity verifier. */ }
  }
  const hash = window.location.hash ? window.location.hash.slice(1) : ''
  if (hash) {
    const hashParams = new URLSearchParams(hash)
    hashParams.forEach((value, key) => {
      values[key] = value
    })
  }
  return values
}

export default function SsoCallback() {
  const { reload } = useAuth()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const [message, setMessage] = useState('Completing SSO sign in...')

  useEffect(() => {
    async function finish() {
      const status = params.get('status')
      if (status === 'error') {
        setMessage(params.get('message') || 'SSO sign in failed.')
        return
      }
      try {
        const ssoParams = collectSsoParams(params)
        // Remove provider tokens from the URL before making application requests.
        window.history.replaceState(null, '', '/sso/callback')
        const tokenKey = TOKEN_KEYS.find((key) => ssoParams[key])
        if (tokenKey) {
          setMessage('Verifying SSO token...')
          await exchangeSsoToken({ [tokenKey]: ssoParams[tokenKey], state: ssoParams.state })
          window.history.replaceState(null, '', '/sso/callback?status=success')
        }
        const current = await reload()
        if (!current) throw new Error('SSO did not establish an application session.')
        const requested = sessionStorage.getItem('costoptima.returnTo')
        sessionStorage.removeItem('costoptima.returnTo')
        navigate(requested?.startsWith('/') && !requested.startsWith('//') && !requested.startsWith('/login') ? requested : '/calculations', { replace: true })
      } catch (err) {
        setMessage(err.message || 'Could not complete SSO sign in.')
      }
    }
    finish()
  }, [navigate, params, reload])

  return (
    <div className="login-wrap">
      <div className="login-card">
        <h1>SSO Sign In</h1>
        <p>{message}</p>
        <button className="btn primary" onClick={() => navigate('/login', { replace: true })}>Back to Login</button>
      </div>
    </div>
  )
}
