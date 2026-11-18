import { useState, type FormEvent } from 'react'
import { ArrowRight, LockKeyhole, Mail } from 'lucide-react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'

export function LoginPage() {
  const { signIn } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [identifier, setIdentifier] = useState('')
  const [password, setPassword] = useState('')
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
        <div className="auth-brand"><span className="brand-mark">c</span><span>Cortex</span></div>
        <div className="auth-brand-copy">
          <p className="eyebrow">ENTERPRISE KNOWLEDGE</p>
          <h1>Find the answer.<br /><span>Know where it came from.</span></h1>
          <p>One workspace for company knowledge, operational context and decisions backed by sources.</p>
        </div>
        <div className="auth-brand-footer"><span>PRIVATE BY DESIGN</span><span>© 2026 Cortex</span></div>
      </section>
      <section className="auth-form-panel">
        <div className="auth-form-wrap">
          <div className="mobile-auth-brand"><span className="brand-mark">c</span><span>Cortex</span></div>
          <p className="eyebrow">WELCOME BACK</p>
          <h2>Sign in to Cortex</h2>
          <p className="auth-subtitle">Use your company username or email address.</p>
          <form className="auth-form" onSubmit={submit}>
            <label htmlFor="identifier">Username or email</label>
            <div className="field-with-icon"><Mail size={17} /><input id="identifier" value={identifier} onChange={(e) => setIdentifier(e.target.value)} placeholder="name@company.com" autoComplete="username" required /></div>
            <div className="label-row"><label htmlFor="password">Password</label></div>
            <div className="field-with-icon"><LockKeyhole size={17} /><input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Enter your password" autoComplete="current-password" required /></div>
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
