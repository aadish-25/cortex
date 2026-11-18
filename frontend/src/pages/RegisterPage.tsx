import { useState, type FormEvent } from 'react'
import { ArrowLeft, ArrowRight } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import { api, ApiError } from '../api/client'

export function RegisterPage() {
  const navigate = useNavigate()
  const [form, setForm] = useState({ name: '', username: '', email: '', job_title: '', department: '', password: '' })
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const update = (key: keyof typeof form, value: string) => setForm((current) => ({ ...current, [key]: value }))

  async function submit(event: FormEvent) {
    event.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      await api('/api/v1/auth/register', { method: 'POST', body: JSON.stringify(form) })
      setSubmitted(true)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Unable to submit your account request.')
    } finally { setSubmitting(false) }
  }

  return (
    <main className="auth-page register-page">
      <section className="auth-brand-panel">
        <div className="auth-brand"><span className="brand-mark">c</span><span>Cortex</span></div>
        <div className="auth-brand-copy"><p className="eyebrow">CONTROLLED ACCESS</p><h1>A considered way<br /><span>into company knowledge.</span></h1><p>New accounts begin with limited access and are activated by an administrator.</p></div>
        <div className="auth-brand-footer"><span>ACCESS REQUESTS</span><span>© 2026 Cortex</span></div>
      </section>
      <section className="auth-form-panel">
        <div className="auth-form-wrap register-wrap">
          <div className="mobile-auth-brand"><span className="brand-mark">c</span><span>Cortex</span></div>
          {submitted ? (
            <div className="registration-success">
              <div className="success-mark">✓</div><p className="eyebrow">REQUEST RECEIVED</p><h2>Your account is awaiting approval.</h2><p>An administrator needs to activate your employee account before you can sign in. Your initial access level is Public.</p><button className="primary-button" onClick={() => navigate('/login')}>Back to sign in<ArrowRight size={17} /></button>
            </div>
          ) : <>
            <Link className="back-link" to="/login"><ArrowLeft size={15} /> Back to sign in</Link>
            <p className="eyebrow">ACCOUNT REQUEST</p><h2>Create an account</h2><p className="auth-subtitle">Submit your details for administrator approval.</p>
            <form className="auth-form register-form" onSubmit={submit}>
              <div className="form-grid">
                <div><label htmlFor="name">Full name</label><input id="name" value={form.name} onChange={(e) => update('name', e.target.value)} required minLength={2} /></div>
                <div><label htmlFor="username">Username</label><input id="username" value={form.username} onChange={(e) => update('username', e.target.value)} required minLength={3} /></div>
                <div className="span-2"><label htmlFor="email">Work email</label><input id="email" type="email" value={form.email} onChange={(e) => update('email', e.target.value)} required /></div>
                <div><label htmlFor="job_title">Job title</label><input id="job_title" value={form.job_title} onChange={(e) => update('job_title', e.target.value)} required /></div>
                <div><label htmlFor="department">Department</label><input id="department" value={form.department} onChange={(e) => update('department', e.target.value)} placeholder="Engineering" required /></div>
                <div className="span-2"><label htmlFor="new-password">Password</label><input id="new-password" type="password" value={form.password} onChange={(e) => update('password', e.target.value)} minLength={10} required /></div>
              </div>
              {error && <div className="form-error" role="alert">{error}</div>}
              <div className="access-note"><strong>Access is not granted immediately.</strong><span>New accounts are created as inactive employees with Public clearance. An administrator assigns access after review.</span></div>
              <button className="primary-button auth-submit" type="submit" disabled={submitting}>{submitting ? 'Submitting…' : 'Submit account request'}<ArrowRight size={17} /></button>
            </form>
          </>}
        </div>
      </section>
    </main>
  )
}
