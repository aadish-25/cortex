import { useState, type FormEvent } from 'react'
import { AlertCircle, Check, KeyRound, Lock, ShieldCheck } from 'lucide-react'
import { api, ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'

const DISALLOWED_DEFAULT_PASSWORDS = [
  'welcome@cortex2026!',
  'welcome@cortex26',
  'welcome@cortex26!',
  'welcome@cortex2026',
]

export function MandatoryPasswordModal() {
  const { user, updateUser } = useAuth()
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  if (!user?.must_change_password) return null

  const isDefaultAttempt = DISALLOWED_DEFAULT_PASSWORDS.includes(newPassword.trim().toLowerCase())
  const isMatch = newPassword.length >= 10 && newPassword === confirmPassword

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError('')

    if (newPassword.trim().length < 10) {
      setError('New password must be at least 10 characters.')
      return
    }

    if (isDefaultAttempt) {
      setError('You cannot keep or reuse the default company password. Please choose a unique personal password.')
      return
    }

    if (newPassword !== confirmPassword) {
      setError('New passwords do not match.')
      return
    }

    setLoading(true)
    try {
      await api('/api/v1/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword.trim(),
        }),
      })
      updateUser({ must_change_password: false })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not change password. Please verify current password.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="modal-backdrop onboarding-backdrop" style={{ zIndex: 10000, background: 'rgba(0, 0, 0, 0.75)' }}>
      <div className="modal-card onboarding-card" style={{ maxWidth: 480, width: '90%' }}>
        <div className="modal-header" style={{ borderBottom: '1px solid var(--border)', paddingBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ display: 'inline-flex', padding: 8, borderRadius: 8, background: 'var(--primary-soft)', color: 'var(--primary)' }}>
              <KeyRound size={20} />
            </span>
            <div>
              <p className="eyebrow" style={{ margin: 0, color: 'var(--primary)' }}>ACCOUNT ONBOARDING</p>
              <h2 style={{ fontSize: 18, margin: '2px 0 0' }}>Configure Personal Password</h2>
            </div>
          </div>
        </div>

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 16, marginTop: 16 }}>
          <p style={{ margin: 0, fontSize: 13, color: 'var(--muted)', lineHeight: 1.5 }}>
            Welcome to Cortex, <strong>{user.name}</strong>! Your account was initialized with a temporary organization default password. For enterprise data security, you must set a new personal password before accessing the workspace.
          </p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, fontWeight: 500 }}>
              Current Password *
              <input
                type="password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                placeholder="Enter the default password you signed in with"
                required
                style={{ padding: '8px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)' }}
              />
            </label>

            <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, fontWeight: 500 }}>
              New Personal Password *
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                placeholder="At least 10 characters"
                minLength={10}
                required
                style={{ padding: '8px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)' }}
              />
              {isDefaultAttempt && (
                <span style={{ fontSize: 11, color: 'var(--danger)', marginTop: 2, display: 'flex', alignItems: 'center', gap: 4 }}>
                  <AlertCircle size={12} /> Default password is not allowed. Choose a personal password.
                </span>
              )}
            </label>

            <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, fontWeight: 500 }}>
              Confirm New Password *
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="Re-type new personal password"
                minLength={10}
                required
                style={{ padding: '8px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)' }}
              />
              {confirmPassword.length > 0 && (
                <span style={{ fontSize: 11, color: isMatch ? 'var(--success)' : 'var(--danger)', marginTop: 2, display: 'flex', alignItems: 'center', gap: 4 }}>
                  {isMatch ? <><Check size={12} /> Passwords match</> : <><AlertCircle size={12} /> Passwords do not match</>}
                </span>
              )}
            </label>
          </div>

          {error && (
            <div className="inline-error" style={{ margin: 0 }}>
              {error}
            </div>
          )}

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 8 }}>
            <button
              type="submit"
              className="primary-button"
              disabled={loading || isDefaultAttempt || !isMatch || currentPassword.length === 0}
              style={{ width: '100%', justifyContent: 'center' }}
            >
              {loading ? 'Updating Password…' : 'Set Password & Enter Workspace'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
