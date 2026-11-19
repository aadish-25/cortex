import { useEffect, useState, type FormEvent } from 'react'
import { ArrowLeft, ArrowUpRight, Brain, CheckCircle2, Lock, Mail, MessageSquare, Moon, Pencil, ShieldCheck, Sun, X } from 'lucide-react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import type { Conversation, Page } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { useTheme } from '../components/ThemeToggle'
import { Modal } from '../components/Modal'

const CLEARANCE_ORDER = ['Public', 'Internal', 'Confidential'] as const

interface EmailReq {
  id: string
  requested_email: string
  current_email: string
  reason?: string
  status: string
  created_at: string
}

export function ProfilePage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const { theme, toggleTheme } = useTheme()
  const [memoryCount, setMemoryCount] = useState<number | null>(null)
  const [conversationCount, setConversationCount] = useState<number | null>(null)
  const [emailModalOpen, setEmailModalOpen] = useState(false)
  const [requestedEmail, setRequestedEmail] = useState('')
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [reqError, setReqError] = useState('')
  const [toast, setToast] = useState('')
  const [pendingReq, setPendingReq] = useState<EmailReq | null>(null)
  const [hasSubmittedEver, setHasSubmittedEver] = useState(false)

  useEffect(() => {
    if (toast) {
      const t = setTimeout(() => setToast(''), 3500)
      return () => clearTimeout(t)
    }
  }, [toast])

  const loadRequests = () => {
    api<EmailReq[]>('/api/v1/users/my-email-requests')
      .then((items) => {
        const p = items.find((i) => i.status === 'pending')
        setPendingReq(p || null)
        setHasSubmittedEver(items.length > 0)
      })
      .catch(() => undefined)
  }

  useEffect(() => {
    if (location.hash === '#preferences') {
      document.getElementById('preferences')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [location.hash])

  useEffect(() => {
    api<{ total: number }>('/api/v1/chat/memories?limit=1').then((r) => setMemoryCount(r.total)).catch(() => undefined)
    api<Page<Conversation>>('/api/v1/chat/conversations?page=1&page_size=1').then((r) => setConversationCount(r.total)).catch(() => undefined)
    loadRequests()
  }, [])

  async function handleEmailRequest(e: FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setReqError('')
    try {
      await api('/api/v1/users/request-email-change', {
        method: 'POST',
        body: JSON.stringify({ requested_email: requestedEmail.trim(), reason: reason.trim() || undefined }),
      })
      setToast('Revision request submitted to administrators.')
      setEmailModalOpen(false)
      setRequestedEmail('')
      setReason('')
      loadRequests()
    } catch (err) {
      setReqError(err instanceof ApiError ? err.message : 'Could not submit request.')
    } finally {
      setSubmitting(false)
    }
  }

  if (!user) return null
  const initials = user.name.split(/\s+/).slice(0, 2).map((part) => part[0]).join('').toUpperCase()
  const userClearanceIdx = CLEARANCE_ORDER.indexOf(user.clearance_level as typeof CLEARANCE_ORDER[number])

  return <div className="admin-page profile-page">
    <header className="page-header"><div><button className="back-link" onClick={() => navigate(-1)}><ArrowLeft size={15} /> Back</button><p className="eyebrow">ACCOUNT</p><h1>Your profile</h1><p className="page-subtitle">Your account identity, workspace permissions, and preferences.</p></div></header>

    <div className="profile-grid">
      <section className="panel profile-card">
        <div className="profile-hero"><span className="user-avatar profile-avatar">{initials}</span><div><h2>{user.name}</h2><p>{user.job_title}</p><span className="role-pill">{user.role}</span></div></div>
        <div className="profile-details">
          <div>
            <small>COMPANY EMAIL</small>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 2 }}>
              <strong>{user.email}</strong>
              {!hasSubmittedEver && (
                <button
                  type="button"
                  className="icon-button small"
                  style={{ width: 22, height: 22, padding: 0 }}
                  onClick={() => setEmailModalOpen(true)}
                  title="Request company email revision"
                  aria-label="Edit company email"
                >
                  <Pencil size={12} />
                </button>
              )}
            </div>
            {pendingReq && (
              <span className="status-pill pending" style={{ fontSize: 10, marginTop: 4, display: 'inline-block' }}>
                Revision Pending ({pendingReq.requested_email})
              </span>
            )}
            {hasSubmittedEver && !pendingReq && (
              <small style={{ color: 'var(--muted)', fontSize: 10, display: 'block', marginTop: 3 }}>
                One-time email revision limit reached
              </small>
            )}
          </div>
          <div><small>USERNAME</small><strong>{user.username}</strong></div>
          <div><small>DEPARTMENT</small><strong>{user.department}</strong></div>
          <div><small>CLEARANCE</small><strong>{user.clearance_level}</strong></div>
        </div>
      </section>

      <section id="preferences" className="panel preferences-card">
        <div className="panel-heading"><div><p className="eyebrow">PREFERENCES</p><h2>Workspace settings</h2></div></div>
        <div className="preference-row">
          <div><strong>Appearance</strong><small>Currently using {theme} mode.</small></div>
          <button className="secondary-button compact-button" onClick={toggleTheme}>
            {theme === 'light' ? <><Moon size={13} /> Dark mode</> : <><Sun size={13} /> Light mode</>}
          </button>
        </div>
        <div className="preference-row">
          <div><strong>Session access</strong><small>Controlled by your role and clearance.</small></div>
          <span className={`status-pill ${user.is_active ? 'active' : 'pending'}`}>{user.is_active ? 'Active' : 'Inactive'}</span>
        </div>
        <div className="preference-row">
          <div><strong>Personal memories</strong><small>Manage facts and preferences Cortex remembers.</small></div>
          <Link className="text-link" to="/memories">Manage <ArrowUpRight size={13} /></Link>
        </div>
      </section>
    </div>

    <div className="profile-grid">
      <section className="panel profile-card">
        <div className="panel-heading"><div><p className="eyebrow">DATA ACCESS SCOPE</p><h2><ShieldCheck size={17} /> Permissions & Clearance</h2></div></div>
        <div className="clearance-tiers">
          {CLEARANCE_ORDER.map((tier, idx) => {
            const allowed = user.role === 'Admin' || idx <= userClearanceIdx
            return (
              <div className={`tier-row ${allowed ? 'allowed' : 'locked'}`} key={tier}>
                {allowed ? <CheckCircle2 size={15} /> : <Lock size={14} />}
                <div>
                  <strong>{tier} documents</strong>
                  <small>
                    {tier === 'Public'
                      ? 'Company-wide policies and general documentation'
                      : user.role === 'Admin'
                        ? 'Accessible across all departments (Enterprise Admin)'
                        : `Scoped to ${user.department} department`}
                  </small>
                </div>
                <span className={`status-pill ${allowed ? 'active' : ''}`}>{allowed ? 'Granted' : 'Restricted'}</span>
              </div>
            )
          })}
        </div>
      </section>

      <section className="panel profile-card">
        <div className="panel-heading"><div><p className="eyebrow">WORKSPACE ACTIVITY</p><h2>Your summary</h2></div></div>
        <div className="profile-stats-grid">
          <Link to="/chat" className="profile-stat-box">
            <MessageSquare size={16} />
            <strong>{conversationCount ?? '—'}</strong>
            <small>Conversations</small>
          </Link>
          <Link to="/memories" className="profile-stat-box">
            <Brain size={16} />
            <strong>{memoryCount ?? '—'}</strong>
            <small>Saved memories</small>
          </Link>
        </div>
        <div className="preference-row" style={{ marginTop: 14 }}>
          <div><strong>Role capabilities</strong><small>{user.role === 'Admin' ? 'Full access to Control Room, Documents, People, and Audit trail.' : user.role === 'Manager' ? `Can upload and manage ${user.department} documents.` : 'Standard workspace search, integrations, and action drafts.'}</small></div>
        </div>
      </section>
    </div>

    {emailModalOpen && (
      <Modal title="Request Company Email Change" onClose={() => setEmailModalOpen(false)}>
        <form className="stack-form" onSubmit={handleEmailRequest}>
          <p style={{ margin: 0, fontSize: 13, color: 'var(--muted)', lineHeight: 1.5 }}>
            Corporate company email addresses are maintained by administrators. Each employee may request a company email revision once in a lifetime.
          </p>

          <div className="form-grid" style={{ gridTemplateColumns: '1fr', marginTop: 10 }}>
            <label>
              Current Company Email
              <input value={user.email} disabled style={{ background: 'var(--surface-soft)', color: 'var(--muted)' }} />
            </label>
            <label>
              Requested New Company Email *
              <input
                type="email"
                value={requestedEmail}
                onChange={(e) => setRequestedEmail(e.target.value)}
                placeholder="enter your new company email - example@cortex.com"
                required
              />
              {requestedEmail.length > 0 && !requestedEmail.trim().toLowerCase().endsWith('@cortex.com') && (
                <span style={{ fontSize: 11, color: 'var(--danger)', marginTop: 3 }}>
                  Official company email must end with @cortex.com
                </span>
              )}
            </label>
            <label>
              Reason for Request (Optional)
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="e.g. Spelling correction or name preference"
              />
            </label>
          </div>

          {reqError && <div className="inline-error">{reqError}</div>}

          <div className="modal-actions" style={{ marginTop: 16 }}>
            <button type="button" className="secondary-button" onClick={() => setEmailModalOpen(false)} disabled={submitting}>
              Cancel
            </button>
            <button
              type="submit"
              className="primary-button"
              disabled={submitting || !requestedEmail.trim().toLowerCase().endsWith('@cortex.com') || requestedEmail.trim().length <= 11}
            >
              {submitting ? 'Submitting…' : 'Submit Revision Request'}
            </button>
          </div>
        </form>
      </Modal>
    )}

    {toast && (
      <div className="toast-message">
        <span>{toast}</span>
        <button onClick={() => setToast('')}><X size={14} /></button>
      </div>
    )}
  </div>
}
