import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { exchangeSsoToken } from '../api/auth'
import { useAuth } from '../context/AuthContext'

const TOKEN_KEYS = ['jwt_token', 'token', 'jwt', 'id_token', 'access_token']

function collectSsoParams(searchParams) {
  const values = {}
  searchParams.forEach((value, key) => {
    values[key] = value
  })
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
        const tokenKey = TOKEN_KEYS.find((key) => ssoParams[key])
        if (tokenKey) {
          setMessage('Verifying SSO token...')
          await exchangeSsoToken({ [tokenKey]: ssoParams[tokenKey] })
          window.history.replaceState(null, '', '/sso/callback?status=success')
        }
        await reload()
        navigate('/calculations', { replace: true })
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
        {params.get('status') === 'error' && (
          <button className="btn primary" onClick={() => navigate('/login', { replace: true })}>Back to Login</button>
        )}
      </div>
    </div>
  )
}
