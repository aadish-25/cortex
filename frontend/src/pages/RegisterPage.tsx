import { useState, type FormEvent } from 'react'
import { ArrowRight, Mail, UserRound } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import CortexLogo from '../components/CortexLogo'

export function RegisterPage() {
  const navigate = useNavigate()
  const [form, setForm] = useState({ first_name: '', last_name: '', email: '' })
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitted, setSubmitted] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      await api('/api/v1/auth/register', {
        method: 'POST',
        body: JSON.stringify({
          first_name: form.first_name.trim(),
          last_name: form.last_name.trim(),
          name: `${form.first_name.trim()} ${form.last_name.trim()}`.trim(),
          email: form.email.trim(),
        }),
      })
      setSubmitted(true)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Unable to submit your account request.')
    } finally { setSubmitting(false) }
  }

  return (
    <main className="auth-page register-page">
      <section className="auth-brand-panel">
        <div className="auth-brand"><CortexLogo size={32} /><span>cortex</span></div>
        <div className="auth-brand-copy"><p className="eyebrow">CONTROLLED ACCESS</p><h1>A considered way<br /><span>into company knowledge.</span></h1><p>New accounts are provisioned by an administrator with your corporate email, department, and clearance level.</p></div>
        <div className="auth-brand-footer"><span>ACCESS REQUESTS</span><span>© 2026 Cortex</span></div>
      </section>
      <section className="auth-form-panel">
        <div className="auth-form-wrap">
          <div className="mobile-auth-brand"><CortexLogo size={28} /><span>cortex</span></div>
          {submitted ? (
            <div className="registration-success">
              <div className="success-mark">✓</div>
              <p className="eyebrow">REQUEST RECEIVED</p>
              <h2>Your account request is pending approval.</h2>
              <p>An administrator will review your request, assign your work email, department, and clearance, and activate your account.</p>
              <button className="primary-button" onClick={() => navigate('/login')}>Go to log in<ArrowRight size={17} /></button>
            </div>
          ) : <>
            <p className="eyebrow">ACCOUNT REQUEST</p>
            <h2>Request access</h2>
            <p className="auth-subtitle">Share your details so an administrator can provision your corporate account.</p>
            <form className="auth-form" onSubmit={submit}>
              <div className="auth-name-grid" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                <div>
                  <label htmlFor="first_name">First name</label>
                  <div className="field-with-icon"><UserRound size={17} /><input id="first_name" value={form.first_name} onChange={(e) => setForm((c) => ({ ...c, first_name: e.target.value }))} placeholder="First name" required minLength={1} /></div>
                </div>
                <div>
                  <label htmlFor="last_name">Last name</label>
                  <div className="field-with-icon"><UserRound size={17} /><input id="last_name" value={form.last_name} onChange={(e) => setForm((c) => ({ ...c, last_name: e.target.value }))} placeholder="Last name" required minLength={1} /></div>
                </div>
              </div>
              <div className="label-row"><label htmlFor="email">Personal email</label></div>
              <div className="field-with-icon"><Mail size={17} /><input id="email" type="email" value={form.email} onChange={(e) => setForm((c) => ({ ...c, email: e.target.value }))} placeholder="you@gmail.com" required /></div>
              {error && <div className="form-error" role="alert">{error}</div>}
              <div className="access-note"><strong>Provisioned by your administrator.</strong><span>Your work email, username, department, role, password, and clearance level will be assigned upon approval.</span></div>
              <button className="primary-button auth-submit" type="submit" disabled={submitting}>{submitting ? 'Submitting…' : 'Submit access request'}<ArrowRight size={17} /></button>
            </form>
            <div className="auth-switch">Already have an account? <Link to="/login">Log in</Link></div>
            <p className="auth-footnote">All access requests are logged for security and compliance review.</p>
          </>}
        </div>
      </section>
    </main>
  )
}
