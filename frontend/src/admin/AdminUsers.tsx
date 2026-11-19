import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Check, ClipboardCheck, Pencil, Plus, Search, Trash2, UserPlus, X } from 'lucide-react'
import { api, ApiError } from '../api/client'
import type { Page, User } from '../api/types'
import { Modal } from '../components/Modal'
import { Pagination } from '../components/Pagination'

const emptyForm = {
  name: '',
  username: '',
  email: '',
  password: '',
  job_title: '',
  role: 'Employee',
  department: 'Engineering',
  clearance_level: 'Internal',
}

const emptyApplicantForm = {
  first_name: '',
  last_name: '',
  personal_email: '',
  email: '',
  username: '',
  password: 'Welcome@Cortex2026!',
  job_title: '',
  role: 'Employee',
  department: 'Engineering',
  clearance_level: 'Internal',
}

export function AdminUsers() {
  const [result, setResult] = useState<Page<User> | null>(null)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [role, setRole] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [toast, setToast] = useState('')
  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(''), 3500)
      return () => clearTimeout(timer)
    }
  }, [toast])
  const [modal, setModal] = useState<'create' | 'edit' | 'review' | null>(null)
  const [selected, setSelected] = useState<User | null>(null)
  const [form, setForm] = useState(emptyForm)
  const [applicantForm, setApplicantForm] = useState(emptyApplicantForm)
  const [saving, setSaving] = useState(false)
  const [emailRequests, setEmailRequests] = useState<Record<string, { id: string; requested_email: string; reason?: string }>>({})
  const searchDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const loadEmailRequests = async () => {
    try {
      const list = await api<Array<{ id: string; user_id: string; requested_email: string; reason?: string; status: string }>>('/api/v1/admin/email-requests?status=pending')
      const map: Record<string, { id: string; requested_email: string; reason?: string }> = {}
      for (const item of list) {
        map[item.user_id] = { id: item.id, requested_email: item.requested_email, reason: item.reason }
      }
      setEmailRequests(map)
    } catch {
      // Ignore
    }
  }

  const pageRef = useRef(page)
  pageRef.current = page
  const searchRef = useRef(search)
  searchRef.current = search
  const roleRef = useRef(role)
  roleRef.current = role
  const statusFilterRef = useRef(statusFilter)
  statusFilterRef.current = statusFilter

  async function load(nextPage = 1, options?: { search?: string; role?: string; statusFilter?: string }, silent = false) {
    if (!silent) {
      setLoading(true)
      setError('')
    }
    const params = new URLSearchParams({ page: String(nextPage), page_size: '25' })
    const q = options?.search ?? searchRef.current
    const r = options?.role ?? roleRef.current
    const s = options?.statusFilter ?? statusFilterRef.current
    if (q.trim()) params.set('search', q.trim())
    if (r) params.set('role', r)
    if (s) params.set('account_status', s)
    try {
      const [data] = await Promise.all([
        api<Page<User>>(`/api/v1/users/?${params}`),
        loadEmailRequests(),
      ])
      setResult(data)
      setPage(data.page)
    } catch (err) {
      if (!silent) setError(err instanceof Error ? err.message : 'Could not load users.')
    } finally {
      if (!silent) setLoading(false)
    }
  }

  useEffect(() => {
    void load(1)
    const interval = setInterval(() => {
      void load(pageRef.current, undefined, true)
    }, 5000)
    return () => clearInterval(interval)
  }, [])

  function openCreate() {
    setSelected(null)
    setForm(emptyForm)
    setModal('create')
  }

  function openEdit(user: User) {
    setSelected(user)
    setForm({
      name: user.name,
      username: user.username,
      email: user.email,
      password: '',
      job_title: user.job_title,
      role: user.role,
      department: user.department,
      clearance_level: user.clearance_level,
    })
    setModal('edit')
  }

  async function openReview(user: User) {
    setSelected(user)
    const parts = user.name.trim().split(/\s+/)
    const firstName = parts[0] || ''
    const lastName = parts.slice(1).join(' ') || ''

    let suggestedEmail = `${firstName.toLowerCase()}.${lastName.toLowerCase()}@cortex.com`
    let suggestedUsername = `${firstName.toLowerCase()}.${lastName.toLowerCase()}`
    try {
      const resp = await api<{ suggested_email: string; suggested_username: string }>(
        `/api/v1/users/suggest-company-email?first_name=${encodeURIComponent(firstName)}&last_name=${encodeURIComponent(lastName)}&exclude_user_id=${user.id}`
      )
      suggestedEmail = resp.suggested_email
      suggestedUsername = resp.suggested_username
    } catch {
      // Fallback if network or unhandled
    }

    setApplicantForm({
      first_name: firstName,
      last_name: lastName,
      personal_email: user.email,
      email: suggestedEmail,
      username: suggestedUsername,
      password: 'Welcome@Cortex2026!',
      job_title: '',
      role: 'Employee',
      department: 'Engineering',
      clearance_level: 'Internal',
    })
    setModal('review')
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setSaving(true)
    setError('')
    try {
      if (modal === 'create') {
        await api('/api/v1/users/', { method: 'POST', body: JSON.stringify(form) })
        setToast('User created.')
      } else if (modal === 'edit' && selected) {
        const payload: Record<string, unknown> = {
          name: form.name,
          username: form.username,
          email: form.email,
          job_title: form.job_title,
          role: form.role,
          department: form.department,
          clearance_level: form.clearance_level,
        }
        await api(`/api/v1/users/${selected.id}`, { method: 'PATCH', body: JSON.stringify(payload) })
        setToast('User updated.')
      }
      setModal(null)
      await load(page)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save user.')
    } finally {
      setSaving(false)
    }
  }

  async function acceptApplication(event: FormEvent) {
    event.preventDefault()
    if (!selected) return
    setSaving(true)
    setError('')
    try {
      const fullName = `${applicantForm.first_name} ${applicantForm.last_name}`.trim()
      const payload: Record<string, unknown> = {
        name: fullName,
        email: applicantForm.email.trim(),
        username: applicantForm.username.trim(),
        job_title: applicantForm.job_title.trim() || 'Software Engineer',
        department: applicantForm.department,
        role: applicantForm.role,
        clearance_level: applicantForm.clearance_level,
        password: applicantForm.password,
        is_active: true,
      }
      await api(`/api/v1/users/${selected.id}`, { method: 'PATCH', body: JSON.stringify(payload) })
      setToast(`Application approved! Corporate account activated for ${applicantForm.email}.`)
      setModal(null)
      await load(page)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not accept application.')
    } finally {
      setSaving(false)
    }
  }

  async function rejectApplication(user?: User) {
    const target = user || selected
    if (!target) return
    if (!window.confirm(`Reject access request for ${target.name} (${target.email})? This will delete the pending request.`)) return
    setSaving(true)
    setError('')
    try {
      await api(`/api/v1/users/${target.id}`, { method: 'DELETE' })
      setToast('Application rejected and removed.')
      setModal(null)
      await load(page)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not reject application.')
    } finally {
      setSaving(false)
    }
  }

  async function toggleActive(user: User) {
    try {
      await api(`/api/v1/users/${user.id}`, { method: 'PATCH', body: JSON.stringify({ is_active: !user.is_active }) })
      setToast(user.is_active ? 'Account deactivated.' : 'Account activated.')
      await load(page)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update account status.')
    }
  }

  async function approveEmailRevision(reqId: string, requestedEmail: string) {
    try {
      await api(`/api/v1/admin/email-requests/${reqId}/approve`, { method: 'POST' })
      setToast(`Email updated to ${requestedEmail}`)
      await Promise.all([load(page, undefined, true), loadEmailRequests()])
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not approve email request.')
    }
  }

  async function rejectEmailRevision(reqId: string) {
    if (!window.confirm('Reject this corporate email revision request?')) return
    try {
      await api(`/api/v1/admin/email-requests/${reqId}/reject`, { method: 'POST' })
      setToast('Email revision request rejected.')
      await loadEmailRequests()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not reject email request.')
    }
  }

  return (
    <div className="admin-page">
      <header className="page-header">
        <div>
          <p className="eyebrow">ACCESS CONTROL</p>
          <h1>People</h1>
          <p className="page-subtitle">Manage accounts, roles, departments and clearance levels.</p>
        </div>
        <button className="primary-button" onClick={openCreate}><UserPlus size={17} /> Add user</button>
      </header>

      {Object.keys(emailRequests).length > 0 && statusFilter !== 'email_pending' && (
        <div style={{ margin: '0 0 16px', padding: '10px 16px', background: 'var(--warning-soft)', border: '1px solid var(--warning)', borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text)', fontSize: 12 }}>
            <span style={{ fontSize: 16 }}>📧</span>
            <strong>{Object.keys(emailRequests).length} pending corporate email revision request(s) awaiting review.</strong>
          </div>
          <button
            type="button"
            className="secondary-button small"
            style={{ fontSize: 11, padding: '2px 10px', height: 26, background: 'var(--surface)' }}
            onClick={() => {
              setStatusFilter('email_pending')
              void load(1, { statusFilter: 'email_pending' })
            }}
          >
            View Pending Revisions
          </button>
        </div>
      )}

      <section className="panel data-panel">
        <div className="table-toolbar">
          <div className="search-field">
            <Search size={16} />
            <input
              placeholder="Search people by name or email"
              value={search}
              onChange={(e) => {
                const v = e.target.value
                setSearch(v)
                if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current)
                searchDebounceRef.current = setTimeout(() => void load(1, { search: v }), 300)
              }}
            />
          </div>
          <div className="toolbar-filters">
            <select
              value={role}
              onChange={(e) => {
                const value = e.target.value
                setRole(value)
                void load(1, { role: value })
              }}
            >
              <option value="">All roles</option>
              <option value="Admin">Admin</option>
              <option value="Manager">Manager</option>
              <option value="Employee">Employee</option>
            </select>
            <select
              value={statusFilter}
              onChange={(e) => {
                const value = e.target.value
                setStatusFilter(value)
                void load(1, { statusFilter: value })
              }}
            >
              <option value="">All accounts</option>
              <option value="active">Active</option>
              <option value="pending">Pending approval</option>
              <option value="email_pending">Email revision requested</option>
              <option value="inactive">Inactive</option>
            </select>
            <span className="count-label">{result?.total ?? 0} people</span>
          </div>
        </div>

        {error && <div className="inline-error table-error">{error}</div>}

        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Person</th>
                <th>Department</th>
                <th>Role</th>
                <th>Clearance</th>
                <th>Account</th>
                <th>Joined</th>
                <th className="actions-col">Manage</th>
              </tr>
            </thead>
            <tbody>
              {loading && <tr><td colSpan={7} className="table-loading">Loading people…</td></tr>}
              {!loading && result?.items.length === 0 && <tr><td colSpan={7} className="table-empty">No accounts found.</td></tr>}
              {!loading && result?.items.map((person) => {
                const isApplicant = !person.is_active && (person.department === 'Unassigned' || person.job_title === 'Pending Assignment')
                const emailReq = emailRequests[person.id]
                const hasPendingReview = Boolean(isApplicant || emailReq)
                return (
                  <tr key={person.id} className={hasPendingReview ? 'pending-review-row' : ''}>
                    <td>
                      <div className="person-cell">
                        <span className="user-avatar">{person.name.split(/\s+/).slice(0, 2).map((x) => x[0]).join('').toUpperCase()}</span>
                        <span>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                            <strong>{person.name}</strong>
                            {hasPendingReview && (
                              <span style={{ fontSize: 9, fontWeight: 700, color: 'var(--warning)', background: 'var(--warning-soft)', padding: '1px 5px', borderRadius: 4, textTransform: 'uppercase' }}>
                                Action Required
                              </span>
                            )}
                          </div>
                          <small>{person.email}</small>
                        </span>
                      </div>
                    </td>
                    <td>{person.department}</td>
                    <td><span className={`role-tag ${person.role.toLowerCase()}`}>{person.role}</span></td>
                    <td><span className={`clearance-tag ${person.clearance_level.toLowerCase()}`}>{person.clearance_level}</span></td>
                    <td>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                        <span className={`status-pill ${isApplicant ? 'pending' : person.is_active ? 'active' : 'inactive'}`}>
                          {isApplicant ? 'Awaiting approval' : person.is_active ? 'Active' : 'Inactive'}
                        </span>
                        {emailReq && (
                          <span className="status-pill warning" style={{ fontSize: 10 }} title={`Requested: ${emailReq.requested_email}`}>
                            Email Revision Requested
                          </span>
                        )}
                      </div>
                    </td>
                    <td>{new Date(person.created_at).toLocaleDateString()}</td>
                    <td>
                      <div className="row-actions">
                        {isApplicant ? (
                          <>
                            <button className="primary-button small" style={{ minHeight: '30px', padding: '0 10px', fontSize: '11px' }} onClick={() => void openReview(person)} title="Review application">
                              <ClipboardCheck size={14} /> Review
                            </button>
                            <button className="icon-button small danger" onClick={() => void rejectApplication(person)} title="Reject application">
                              <Trash2 size={14} />
                            </button>
                          </>
                        ) : (
                          <>
                            {emailReq && (
                              <div style={{ display: 'inline-flex', gap: 5 }}>
                                <button
                                  className="secondary-button small danger"
                                  style={{ minHeight: '30px', padding: '0 8px', fontSize: '11px', color: 'var(--danger)', borderColor: 'var(--danger-soft)' }}
                                  onClick={() => void rejectEmailRevision(emailReq.id)}
                                  title="Reject email revision request"
                                >
                                  ✕ Reject
                                </button>
                                <button
                                  className="secondary-button small"
                                  style={{ minHeight: '30px', padding: '0 8px', fontSize: '11px', color: 'var(--primary)', borderColor: 'var(--primary-line)' }}
                                  onClick={() => void approveEmailRevision(emailReq.id, emailReq.requested_email)}
                                  title={`Approve new email: ${emailReq.requested_email}${emailReq.reason ? ` (${emailReq.reason})` : ''}`}
                                >
                                  ✓ Accept {emailReq.requested_email}
                                </button>
                              </div>
                            )}
                            <button className="icon-button small" onClick={() => openEdit(person)} title="Edit account">
                              <Pencil size={15} />
                            </button>
                            <button className={`icon-button small ${person.is_active ? 'danger' : 'success'}`} onClick={() => void toggleActive(person)} title={person.is_active ? 'Deactivate' : 'Activate'}>
                              {person.is_active ? <X size={15} /> : <Check size={15} />}
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        {result && <Pagination page={result.page} totalPages={result.total_pages} onChange={(next) => void load(next)} />}
      </section>

      {modal === 'review' && selected && (
        <Modal title="Review Application" onClose={() => setModal(null)} wide>
          <div className="applicant-info-card">
            <div>
              <strong>{applicantForm.first_name} {applicantForm.last_name}</strong>
              <small>Personal email: {selected.email}</small>
            </div>
            <span className="applicant-badge">Awaiting Approval</span>
          </div>

          <form className="stack-form" onSubmit={acceptApplication}>
            <div className="form-grid">
              <label>
                First name
                <input
                  value={applicantForm.first_name}
                  onChange={(e) => setApplicantForm((f) => ({ ...f, first_name: e.target.value }))}
                  required
                />
              </label>
              <label>
                Last name
                <input
                  value={applicantForm.last_name}
                  onChange={(e) => setApplicantForm((f) => ({ ...f, last_name: e.target.value }))}
                  required
                />
              </label>
              <label>
                Assigned Company Email *
                <input
                  type="email"
                  value={applicantForm.email}
                  onChange={(e) => setApplicantForm((f) => ({ ...f, email: e.target.value }))}
                  required
                />
              </label>
              <label>
                Username *
                <input
                  value={applicantForm.username}
                  onChange={(e) => setApplicantForm((f) => ({ ...f, username: e.target.value }))}
                  required
                />
              </label>
              <label>
                Job Title *
                <input
                  value={applicantForm.job_title}
                  onChange={(e) => setApplicantForm((f) => ({ ...f, job_title: e.target.value }))}
                  placeholder="e.g. Software Engineer"
                  required
                />
              </label>
              <label>
                Department *
                <select
                  value={applicantForm.department}
                  onChange={(e) => setApplicantForm((f) => ({ ...f, department: e.target.value }))}
                  required
                >
                  <option value="Engineering">Engineering</option>
                  <option value="HR">HR</option>
                  <option value="Finance">Finance</option>
                  <option value="Enterprise">Enterprise</option>
                </select>
              </label>
              <label>
                Role *
                <select
                  value={applicantForm.role}
                  onChange={(e) => setApplicantForm((f) => ({ ...f, role: e.target.value }))}
                >
                  <option value="Employee">Employee</option>
                  <option value="Manager">Manager</option>
                  <option value="Admin">Admin</option>
                </select>
              </label>
              <label>
                Clearance Level *
                <select
                  value={applicantForm.clearance_level}
                  onChange={(e) => setApplicantForm((f) => ({ ...f, clearance_level: e.target.value }))}
                >
                  <option value="Public">Public</option>
                  <option value="Internal">Internal</option>
                  <option value="Confidential">Confidential</option>
                </select>
              </label>
              <label style={{ gridColumn: 'span 2' }}>
                Initial Password *
                <input
                  type="text"
                  value={applicantForm.password}
                  onChange={(e) => setApplicantForm((f) => ({ ...f, password: e.target.value }))}
                  required
                  minLength={10}
                />
                <small className="form-help">Initial credentials for the employee to sign in. Minimum 10 characters.</small>
              </label>
            </div>
            {error && <div className="inline-error">{error}</div>}
            <div className="modal-actions" style={{ justifyContent: 'space-between' }}>
              <button type="button" className="secondary-button danger" onClick={() => void rejectApplication()} disabled={saving}>
                <Trash2 size={15} /> Reject Application
              </button>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button type="button" className="secondary-button" onClick={() => setModal(null)}>Cancel</button>
                <button type="submit" className="primary-button" disabled={saving}>
                  {saving ? 'Provisioning…' : 'Accept Application'}
                </button>
              </div>
            </div>
          </form>
        </Modal>
      )}

      {(modal === 'create' || modal === 'edit') && (
        <Modal title={modal === 'create' ? 'Create user account' : 'Edit user account'} onClose={() => setModal(null)} wide>
          <form className="stack-form" onSubmit={submit}>
            <div className="form-grid">
              <label>Full name<input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} required /></label>
              <label>Username<input value={form.username} onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))} required /></label>
              <label>Email<input type="email" value={form.email} onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))} required /></label>
              <label>Job title<input value={form.job_title} onChange={(e) => setForm((f) => ({ ...f, job_title: e.target.value }))} required /></label>
              <label>Department<input value={form.department} onChange={(e) => setForm((f) => ({ ...f, department: e.target.value }))} required /></label>
              <label>Role<select value={form.role} onChange={(e) => setForm((f) => ({ ...f, role: e.target.value }))}><option>Employee</option><option>Manager</option><option>Admin</option></select></label>
              <label>Clearance<select value={form.clearance_level} onChange={(e) => setForm((f) => ({ ...f, clearance_level: e.target.value }))}><option>Public</option><option>Internal</option><option>Confidential</option></select></label>
              {modal === 'create' ? (
                <label>Password *<input type="password" value={form.password} onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))} required minLength={10} /></label>
              ) : (
                <div style={{ gridColumn: 'span 2', padding: '8px 12px', background: 'var(--surface-soft)', border: '1px solid var(--border)', borderRadius: 6, fontSize: '11px', color: 'var(--muted)' }}>
                  🔒 <strong>Password security:</strong> Administrators cannot view or modify employee personal passwords. Employees configure and manage their own passwords upon login.
                </div>
              )}
            </div>
            {modal === 'create' && <p className="form-help">Accounts created here are active immediately. Use the access-request flow for employee self-registration.</p>}
            {error && <div className="inline-error">{error}</div>}
            <div className="modal-actions">
              <button type="button" className="secondary-button" onClick={() => setModal(null)}>Cancel</button>
              <button type="submit" className="primary-button" disabled={saving}>{saving ? 'Saving…' : modal === 'create' ? 'Create account' : 'Save changes'}</button>
            </div>
          </form>
        </Modal>
      )}

      {toast && <div className="toast-message"><span>{toast}</span><button onClick={() => setToast('')}><X size={14} /></button></div>}
    </div>
  )
}
