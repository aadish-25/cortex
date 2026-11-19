import { useState, type FormEvent } from 'react'
import { AlertCircle, ArrowRight, Check, Eye, EyeOff, KeyRound, Lock, ShieldCheck } from 'lucide-react'
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
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showNewPassword, setShowNewPassword] = useState(false)
  const [showConfirmPassword, setShowConfirmPassword] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  if (!user?.must_change_password) return null

  const trimmedNew = newPassword.trim()
  const isDefaultAttempt = DISALLOWED_DEFAULT_PASSWORDS.includes(trimmedNew.toLowerCase())
  const hasMinLength = trimmedNew.length >= 10
  const isMatch = trimmedNew.length > 0 && confirmPassword.length > 0 && trimmedNew === confirmPassword
  const canSubmit = hasMinLength && !isDefaultAttempt && isMatch && !loading

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError('')

    if (!hasMinLength) {
      setError('New password must be at least 10 characters.')
      return
    }

    if (isDefaultAttempt) {
      setError('You cannot keep or reuse the temporary default company password. Please choose a unique personal password.')
      return
    }

    if (trimmedNew !== confirmPassword) {
      setError('New passwords do not match.')
      return
    }

    setLoading(true)
    try {
      await api('/api/v1/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({
          new_password: trimmedNew,
        }),
      })
      updateUser({ must_change_password: false })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not set password. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      className="modal-backdrop onboarding-backdrop"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 10000,
        background: 'rgba(9, 11, 17, 0.72)',
        backdropFilter: 'blur(8px)',
        WebkitBackdropFilter: 'blur(8px)',
        display: 'grid',
        placeItems: 'center',
        padding: 20,
      }}
    >
      <div
        className="modal-card onboarding-card"
        style={{
          maxWidth: 460,
          width: '100%',
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 16,
          boxShadow: '0 24px 64px -12px rgba(0, 0, 0, 0.35), 0 0 1px 1px var(--border)',
          padding: '28px 28px 24px',
          display: 'flex',
          flexDirection: 'column',
          gap: 18,
          boxSizing: 'border-box',
        }}
      >
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div
            style={{
              width: 44,
              height: 44,
              borderRadius: 12,
              background: 'var(--primary-soft)',
              border: '1px solid var(--primary-line)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0,
              color: 'var(--primary)',
            }}
          >
            <KeyRound size={22} />
          </div>
          <div>
            <p
              style={{
                margin: 0,
                fontSize: 11,
                fontWeight: 700,
                letterSpacing: '0.08em',
                color: 'var(--primary)',
                textTransform: 'uppercase',
              }}
            >
              Account Onboarding
            </p>
            <h2
              style={{
                margin: '2px 0 0',
                fontSize: 19,
                fontWeight: 700,
                color: 'var(--text-strong)',
                letterSpacing: '-0.02em',
              }}
            >
              Set Personal Password
            </h2>
          </div>
        </div>

        {/* Welcome Message Card */}
        <div
          style={{
            background: 'var(--surface-soft)',
            border: '1px solid var(--border)',
            borderRadius: 10,
            padding: '12px 14px',
            fontSize: 12.5,
            color: 'var(--muted)',
            lineHeight: 1.55,
          }}
        >
          Welcome to Cortex, <strong style={{ color: 'var(--text-strong)' }}>{user.name}</strong>! Your corporate account has been provisioned. To secure your access, please choose a private personal password before entering the workspace.
        </div>

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* New Password Input */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <label style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-strong)' }}>
              New Password *
            </label>
            <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
              <Lock
                size={16}
                style={{
                  position: 'absolute',
                  left: 12,
                  color: 'var(--muted)',
                  pointerEvents: 'none',
                }}
              />
              <input
                type={showNewPassword ? 'text' : 'password'}
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                placeholder="At least 10 characters"
                minLength={10}
                required
                style={{
                  width: '100%',
                  height: 42,
                  padding: '0 40px 0 38px',
                  borderRadius: 8,
                  border: '1px solid var(--border)',
                  background: 'var(--surface)',
                  color: 'var(--text)',
                  fontSize: 13.5,
                  boxSizing: 'border-box',
                }}
              />
              <button
                type="button"
                onClick={() => setShowNewPassword(!showNewPassword)}
                style={{
                  position: 'absolute',
                  right: 8,
                  background: 'transparent',
                  border: 'none',
                  color: 'var(--muted)',
                  cursor: 'pointer',
                  padding: 6,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
                title={showNewPassword ? 'Hide password' : 'Show password'}
              >
                {showNewPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </div>

          {/* Confirm Password Input */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <label style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-strong)' }}>
              Confirm Password *
            </label>
            <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
              <Lock
                size={16}
                style={{
                  position: 'absolute',
                  left: 12,
                  color: 'var(--muted)',
                  pointerEvents: 'none',
                }}
              />
              <input
                type={showConfirmPassword ? 'text' : 'password'}
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="Re-type new personal password"
                minLength={10}
                required
                style={{
                  width: '100%',
                  height: 42,
                  padding: '0 40px 0 38px',
                  borderRadius: 8,
                  border: '1px solid var(--border)',
                  background: 'var(--surface)',
                  color: 'var(--text)',
                  fontSize: 13.5,
                  boxSizing: 'border-box',
                }}
              />
              <button
                type="button"
                onClick={() => setShowConfirmPassword(!showConfirmPassword)}
                style={{
                  position: 'absolute',
                  right: 8,
                  background: 'transparent',
                  border: 'none',
                  color: 'var(--muted)',
                  cursor: 'pointer',
                  padding: 6,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
                title={showConfirmPassword ? 'Hide password' : 'Show password'}
              >
                {showConfirmPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </div>

          {/* Validation Checklist */}
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 5,
              padding: '10px 12px',
              borderRadius: 8,
              background: 'var(--surface-soft)',
              border: '1px solid var(--border)',
              fontSize: 12,
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                color: hasMinLength ? 'var(--success)' : 'var(--muted)',
              }}
            >
              {hasMinLength ? <Check size={13} /> : <span style={{ width: 13, textAlign: 'center' }}>•</span>}
              <span>At least 10 characters</span>
            </div>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                color: isDefaultAttempt
                  ? 'var(--danger)'
                  : trimmedNew.length > 0
                  ? 'var(--success)'
                  : 'var(--muted)',
              }}
            >
              {isDefaultAttempt ? (
                <AlertCircle size={13} />
              ) : trimmedNew.length > 0 ? (
                <Check size={13} />
              ) : (
                <span style={{ width: 13, textAlign: 'center' }}>•</span>
              )}
              <span>
                {isDefaultAttempt
                  ? 'Default company password is not allowed'
                  : 'Must not be default company password'}
              </span>
            </div>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                color: confirmPassword.length === 0
                  ? 'var(--muted)'
                  : isMatch
                  ? 'var(--success)'
                  : 'var(--danger)',
              }}
            >
              {confirmPassword.length === 0 ? (
                <span style={{ width: 13, textAlign: 'center' }}>•</span>
              ) : isMatch ? (
                <Check size={13} />
              ) : (
                <AlertCircle size={13} />
              )}
              <span>
                {confirmPassword.length === 0
                  ? 'Passwords match'
                  : isMatch
                  ? 'Passwords match'
                  : 'Passwords do not match'}
              </span>
            </div>
          </div>

          {/* Error Message */}
          {error && (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '10px 12px',
                borderRadius: 8,
                background: 'var(--danger-soft)',
                border: '1px solid var(--danger)',
                color: 'var(--danger)',
                fontSize: 12.5,
              }}
            >
              <AlertCircle size={15} style={{ flexShrink: 0 }} />
              <span>{error}</span>
            </div>
          )}

          {/* Submit Action */}
          <div style={{ marginTop: 4 }}>
            <button
              type="submit"
              className="primary-button"
              disabled={!canSubmit}
              style={{
                width: '100%',
                height: 44,
                justifyContent: 'center',
                fontSize: 14,
                fontWeight: 600,
                borderRadius: 8,
                background: canSubmit ? 'var(--primary)' : undefined,
                color: '#ffffff',
                boxShadow: canSubmit ? '0 4px 12px rgba(49, 94, 232, 0.28)' : undefined,
                gap: 8,
              }}
            >
              {loading ? (
                'Updating Password…'
              ) : (
                <>
                  <span>Save Password & Enter Workspace</span>
                  <ArrowRight size={16} />
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
