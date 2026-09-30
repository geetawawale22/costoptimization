import { useEffect, useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { ssoLoginUrl, ssoStatus } from '../api/auth'

function MailIcon() {
  return (
    <svg className="login-field-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 6.5h16v11H4z" />
      <path d="m4.5 7 7.5 6 7.5-6" />
    </svg>
  )
}

function LockIcon() {
  return (
    <svg className="login-field-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M7 10V8a5 5 0 0 1 10 0v2" />
      <path d="M6 10h12v10H6z" />
      <path d="M12 14v2" />
    </svg>
  )
}

function EyeIcon({ hidden }) {
  return (
    <svg className="login-eye-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" />
      <path d="M12 9.5a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5Z" />
      {hidden && <path d="M4 4l16 16" />}
    </svg>
  )
}

function BrandHeader() {
  return (
    <div className="login-brand-header">
      <div className="login-logo" aria-hidden="true">co</div>
      <div>
        <strong>CostOptima</strong>
        <span>Cost intelligence platform</span>
      </div>
    </div>
  )
}

function MarketingPanel() {
  return (
    <aside className="login-marketing-panel" aria-label="CostOptima overview">
      <BrandHeader />

      <div className="login-marketing-copy">
        <span className="login-secure-badge">Secure workspace</span>
        <h1>Manufacturing cost control, simplified.</h1>
        <ul className="login-feature-list">
          <li>Real-time BOM cost calculations</li>
          <li>Role-based approvals and audit trail</li>
          <li>Master data governed in one place</li>
        </ul>
      </div>
    </aside>
  )
}

function FormField({ id, label, type = 'text', value, onChange, autoComplete, error, children }) {
  return (
    <div className={`login-field ${error ? 'has-error' : ''}`}>
      <label htmlFor={id}>{label}</label>
      <div className="login-input-wrap">
        {children}
        <input
          id={id}
          type={type}
          value={value}
          onChange={onChange}
          autoComplete={autoComplete}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? `${id}-error` : undefined}
          required
        />
      </div>
      {error && <span id={`${id}-error`} className="login-field-error">{error}</span>}
    </div>
  )
}

function PasswordField({ value, onChange, error }) {
  const [visible, setVisible] = useState(false)

  return (
    <div className={`login-field ${error ? 'has-error' : ''}`}>
      <label htmlFor="password">Password</label>
      <div className="login-input-wrap">
        <LockIcon />
        <input
          id="password"
          type={visible ? 'text' : 'password'}
          value={value}
          onChange={onChange}
          autoComplete="current-password"
          aria-invalid={Boolean(error)}
          aria-describedby={error ? 'password-error' : undefined}
          required
        />
        <button
          type="button"
          className="login-visibility-btn"
          onClick={() => setVisible((current) => !current)}
          aria-label={visible ? 'Hide password' : 'Show password'}
        >
          <EyeIcon hidden={!visible} />
        </button>
      </div>
      {error && <span id="password-error" className="login-field-error">{error}</span>}
    </div>
  )
}

function RememberMe() {
  return (
    <div className="login-options-row">
      <label className="login-check">
        <input type="checkbox" />
        <span>Keep me signed in</span>
      </label>
      <button type="button" className="login-link">Forgot password?</button>
    </div>
  )
}

function PrimaryButton({ busy }) {
  return (
    <button className="btn primary login-submit" disabled={busy}>
      {busy ? 'Signing in...' : 'Sign In'}
    </button>
  )
}

function SecurityNotice() {
  return (
    <p className="login-security-notice">Protected by enterprise security</p>
  )
}

function SsoButton() {
  return (
    <>
      <div className="login-divider"><span>or</span></div>
      <button type="button" className="btn login-sso-btn" onClick={() => { window.location.href = ssoLoginUrl() }}>
        Sign in with SSO
      </button>
    </>
  )
}

function LoginCard({ email, password, error, busy, ssoEnabled, onEmailChange, onPasswordChange, onSubmit }) {
  const emailError = !email ? 'Corporate email is required.' : ''
  const passwordError = !password && error ? 'Password is required.' : ''

  return (
    <section className="login-auth-panel" aria-label="Sign in form">
      <div className="login-card">
        <div className="login-header">
          <span className="login-kicker">Welcome back</span>
          <h2>Sign in to CostOptima</h2>
          <p>Use your corporate credentials to continue.</p>
        </div>

        <form onSubmit={onSubmit} noValidate>
          {error && <div className="error-text" role="alert">{error}</div>}

          <FormField
            id="email"
            label="Corporate Email"
            type="email"
            value={email}
            onChange={onEmailChange}
            autoComplete="email"
            error={emailError}
          >
            <MailIcon />
          </FormField>

          <PasswordField value={password} onChange={onPasswordChange} error={passwordError} />
          <RememberMe />
          <PrimaryButton busy={busy} />
        </form>

        {ssoEnabled && <SsoButton />}
        <SecurityNotice />
      </div>
    </section>
  )
}

export default function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = useState('admin@costoptima.com')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [ssoEnabled, setSsoEnabled] = useState(false)

  useEffect(() => {
    ssoStatus().then((res) => setSsoEnabled(Boolean(res?.enabled))).catch(() => setSsoEnabled(false))
  }, [])

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')

    if (!email || !password) {
      setError('Enter your corporate email and password to continue.')
      return
    }

    setBusy(true)
    try {
      await login(email, password)
      const dest = location.state?.from?.pathname || '/calculations'
      navigate(dest, { replace: true })
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="login-wrap">
      <div className="login-shell">
        <MarketingPanel />
        <LoginCard
          email={email}
          password={password}
          error={error}
          busy={busy}
          ssoEnabled={ssoEnabled}
          onEmailChange={(e) => setEmail(e.target.value)}
          onPasswordChange={(e) => setPassword(e.target.value)}
          onSubmit={handleSubmit}
        />
      </div>
    </main>
  )
}
