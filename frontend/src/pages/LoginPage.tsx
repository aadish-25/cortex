import { useState, type FormEvent } from 'react'
import { ArrowRight, Eye, EyeOff, LockKeyhole, Mail } from 'lucide-react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import CortexLogo from '../components/CortexLogo'

export function LoginPage() {
  const { signIn } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [identifier, setIdentifier] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      const user = await signIn(identifier.trim(), password)
      const from = (location.state as { from?: string } | null)?.from
      navigate(from || (user.role === 'Admin' ? '/admin/overview' : '/chat'), { replace: true })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Unable to sign in. Check your connection and try again.')
    } finally { setSubmitting(false) }
  }

  return (
    <main className="auth-page">
      <section className="auth-brand-panel">
        <div className="auth-brand"><CortexLogo size={32} /><span>cortex</span></div>
        <div className="auth-brand-copy">
          <p className="eyebrow">ENTERPRISE KNOWLEDGE</p>
          <h1>Find the answer.<br /><span>Know where it came from.</span></h1>
          <p>One workspace for company knowledge, operational context and decisions backed by sources.</p>
        </div>
        <div className="auth-brand-footer"><span>PRIVATE BY DESIGN</span><span>© 2026 Cortex</span></div>
      </section>
      <section className="auth-form-panel">
        <div className="auth-form-wrap">
          <div className="mobile-auth-brand"><CortexLogo size={28} /><span>cortex</span></div>
          <p className="eyebrow">WELCOME BACK</p>
          <h2>Sign in to Cortex</h2>
          <p className="auth-subtitle">Use your company username or email address.</p>
          <form className="auth-form" onSubmit={submit}>
            <label htmlFor="identifier">Username or email</label>
            <div className="field-with-icon"><Mail size={17} /><input id="identifier" value={identifier} onChange={(e) => setIdentifier(e.target.value)} placeholder="name@company.com" autoComplete="username" required /></div>
            <div className="label-row"><label htmlFor="password">Password</label></div>
            <div className="field-with-icon" style={{ position: 'relative' }}>
              <LockKeyhole size={17} />
              <input
                id="password"
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter your password"
                autoComplete="current-password"
                required
                style={{ paddingRight: 38 }}
              />
              <button
                type="button"
                onClick={() => setShowPassword((prev) => !prev)}
                aria-label={showPassword ? 'Hide password' : 'Show password'}
                title={showPassword ? 'Hide password' : 'Show password'}
                tabIndex={-1}
                style={{
                  position: 'absolute',
                  right: 8,
                  top: '50%',
                  transform: 'translateY(-50%)',
                  background: 'transparent',
                  border: 'none',
                  color: 'var(--muted)',
                  cursor: 'pointer',
                  padding: 6,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
            {error && <div className="form-error" role="alert">{error}</div>}
            <button className="primary-button auth-submit" type="submit" disabled={submitting}>{submitting ? 'Signing in…' : 'Sign in'}<ArrowRight size={17} /></button>
          </form>
          <div className="auth-switch">New to Cortex? <Link to="/register">Create an account</Link></div>
          <p className="auth-footnote">Your session is protected with an HttpOnly authentication cookie.</p>
        </div>
      </section>
    </main>
  )
}
