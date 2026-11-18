import { useEffect, useState, type FormEvent } from 'react'
import { Check, Pencil, Plus, Search, ShieldCheck, UserPlus, X } from 'lucide-react'
import { api, ApiError } from '../api/client'
import type { Page, User } from '../api/types'
import { Modal } from '../components/Modal'
import { Pagination } from '../components/Pagination'

const emptyForm = { name: '', username: '', email: '', password: '', job_title: '', role: 'Employee', department: 'Engineering', clearance_level: 'Internal' }

export function AdminUsers() {
  const [result, setResult] = useState<Page<User> | null>(null)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [role, setRole] = useState('')
  const [active, setActive] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [toast, setToast] = useState('')
  const [modal, setModal] = useState<'create' | 'edit' | null>(null)
  const [selected, setSelected] = useState<User | null>(null)
  const [form, setForm] = useState(emptyForm)
  const [saving, setSaving] = useState(false)

  async function load(nextPage = 1, options?: { search?: string; role?: string; active?: string }) {
    setLoading(true); setError('')
    const params = new URLSearchParams({ page: String(nextPage), page_size: '25' })
    const q = options?.search ?? search; const r = options?.role ?? role; const a = options?.active ?? active
    if (q.trim()) params.set('search', q.trim())
    if (r) params.set('role', r)
    if (a) params.set('is_active', a)
    try { const data = await api<Page<User>>(`/api/v1/users/?${params}`); setResult(data); setPage(data.page) }
    catch (err) { setError(err instanceof Error ? err.message : 'Could not load users.') }
    finally { setLoading(false) }
  }
  useEffect(() => { void load(1) }, [])

  function openCreate() { setSelected(null); setForm(emptyForm); setModal('create') }
  function openEdit(user: User) { setSelected(user); setForm({ name: user.name, username: user.username, email: user.email, password: '', job_title: user.job_title, role: user.role, department: user.department, clearance_level: user.clearance_level }); setModal('edit') }

  async function submit(event: FormEvent) {
    event.preventDefault(); setSaving(true); setError('')
    try {
      if (modal === 'create') {
        await api('/api/v1/users/', { method: 'POST', body: JSON.stringify(form) })
        setToast('User created.')
      } else if (selected) {
        const payload: Record<string, unknown> = { name: form.name, username: form.username, email: form.email, job_title: form.job_title, role: form.role, department: form.department, clearance_level: form.clearance_level }
        await api(`/api/v1/users/${selected.id}`, { method: 'PATCH', body: JSON.stringify(payload) })
        setToast('User updated.')
      }
      setModal(null); await load(page)
    } catch (err) { setError(err instanceof ApiError ? err.message : 'Could not save user.') }
    finally { setSaving(false) }
  }

  async function toggleActive(user: User) {
    try {
      await api(`/api/v1/users/${user.id}`, { method: 'PATCH', body: JSON.stringify({ is_active: !user.is_active }) })
      setToast(user.is_active ? 'Account deactivated.' : 'Account activated.'); await load(page)
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not update account status.') }
  }

  return <div className="admin-page">
    <header className="page-header"><div><p className="eyebrow">ACCESS CONTROL</p><h1>People</h1><p className="page-subtitle">Manage accounts, roles, departments and clearance levels.</p></div><button className="primary-button" onClick={openCreate}><UserPlus size={17} /> Add user</button></header>
    <section className="panel data-panel">
      <div className="table-toolbar"><div className="search-field"><Search size={16} /><input placeholder="Search people by name or email" value={search} onChange={(e) => setSearch(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') void load(1) }} /><button onClick={() => void load(1)}>Search</button></div><div className="toolbar-filters"><select value={role} onChange={(e) => { const value = e.target.value; setRole(value); void load(1, { role: value }) }}><option value="">All roles</option><option value="Admin">Admin</option><option value="Manager">Manager</option><option value="Employee">Employee</option></select><select value={active} onChange={(e) => { const value = e.target.value; setActive(value); void load(1, { active: value }) }}><option value="">All accounts</option><option value="true">Active</option><option value="false">Pending / inactive</option></select><span className="count-label">{result?.total ?? 0} people</span></div></div>
      {error && <div className="inline-error table-error">{error}</div>}
      <div className="table-scroll"><table className="data-table"><thead><tr><th>Person</th><th>Department</th><th>Role</th><th>Clearance</th><th>Account</th><th>Joined</th><th className="actions-col">Manage</th></tr></thead><tbody>
        {loading && <tr><td colSpan={7} className="table-loading">Loading people…</td></tr>}
        {!loading && result?.items.length === 0 && <tr><td colSpan={7} className="table-empty">No accounts found.</td></tr>}
        {!loading && result?.items.map((person) => <tr key={person.id}><td><div className="person-cell"><span className="user-avatar">{person.name.split(/\s+/).slice(0, 2).map((x) => x[0]).join('').toUpperCase()}</span><span><strong>{person.name}</strong><small>{person.email}</small></span></div></td><td>{person.department}</td><td><span className={`role-tag ${person.role.toLowerCase()}`}>{person.role}</span></td><td><span className={`clearance-tag ${person.clearance_level.toLowerCase()}`}>{person.clearance_level}</span></td><td><span className={`status-pill ${person.is_active ? 'active' : 'pending'}`}>{person.is_active ? 'Active' : 'Awaiting approval'}</span></td><td>{new Date(person.created_at).toLocaleDateString()}</td><td><div className="row-actions"><button className="icon-button small" onClick={() => openEdit(person)} title="Edit account"><Pencil size={15} /></button><button className={`icon-button small ${person.is_active ? 'danger' : 'success'}`} onClick={() => void toggleActive(person)} title={person.is_active ? 'Deactivate' : 'Approve account'}>{person.is_active ? <X size={15} /> : <Check size={15} />}</button></div></td></tr>)}
      </tbody></table></div>
      {result && <Pagination page={result.page} totalPages={result.total_pages} onChange={(next) => void load(next)} />}
    </section>
    {modal && <Modal title={modal === 'create' ? 'Create user account' : 'Edit user account'} onClose={() => setModal(null)} wide><form className="stack-form" onSubmit={submit}><div className="form-grid"><label>Full name<input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} required /></label><label>Username<input value={form.username} onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))} required /></label><label>Email<input type="email" value={form.email} onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))} required /></label><label>Job title<input value={form.job_title} onChange={(e) => setForm((f) => ({ ...f, job_title: e.target.value }))} required /></label><label>Department<input value={form.department} onChange={(e) => setForm((f) => ({ ...f, department: e.target.value }))} required /></label><label>Role<select value={form.role} onChange={(e) => setForm((f) => ({ ...f, role: e.target.value }))}><option>Employee</option><option>Manager</option><option>Admin</option></select></label><label>Clearance<select value={form.clearance_level} onChange={(e) => setForm((f) => ({ ...f, clearance_level: e.target.value }))}><option>Public</option><option>Internal</option><option>Confidential</option></select></label>{modal === 'create' && <label>Password<input type="password" value={form.password} onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))} required minLength={10} /></label>}</div>{modal === 'create' && <p className="form-help">Accounts created here are active immediately. Use the access-request flow for employee self-registration.</p>}{error && <div className="inline-error">{error}</div>}<div className="modal-actions"><button type="button" className="secondary-button" onClick={() => setModal(null)}>Cancel</button><button type="submit" className="primary-button" disabled={saving}>{saving ? 'Saving…' : modal === 'create' ? 'Create account' : 'Save changes'}</button></div></form></Modal>}
    {toast && <div className="toast-message"><span>{toast}</span><button onClick={() => setToast('')}><X size={14} /></button></div>}
  </div>
}
