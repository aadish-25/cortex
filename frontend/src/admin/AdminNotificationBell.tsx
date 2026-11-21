import { useEffect, useRef, useState } from 'react'
import { Bell, Check, Mail, Trash2, UserPlus, X } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { ConfirmDialog } from '../components/ConfirmDialog'

interface NotificationItem {
  id: string
  type: 'access_request' | 'email_request'
  title: string
  subtitle: string
  created_at: string
  user_id?: string
  name?: string
  email?: string
  request_id?: string
  current_email?: string
  requested_email?: string
  reason?: string
}

interface NotificationData {
  total_pending: number
  pending_registrations_count: number
  pending_email_requests_count: number
  items: NotificationItem[]
}

export function AdminNotificationBell() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [data, setData] = useState<NotificationData | null>(null)
  const [open, setOpen] = useState(false)
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [toast, setToast] = useState('')
  const [rejectReqId, setRejectReqId] = useState<string | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (toast) {
      const t = setTimeout(() => setToast(''), 3500)
      return () => clearTimeout(t)
    }
  }, [toast])

  const loadNotifications = async () => {
    if (user?.role !== 'Admin') return
    try {
      const res = await api<NotificationData>('/api/v1/admin/notifications')
      setData(res)
    } catch {
      // Ignore background poll errors
    }
  }

  useEffect(() => {
    if (user?.role !== 'Admin') return
    void loadNotifications()
    const interval = setInterval(loadNotifications, 20_000)
    return () => clearInterval(interval)
  }, [user?.role])

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    if (open) {
      document.addEventListener('mousedown', handleClickOutside)
      return () => document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [open])

  if (user?.role !== 'Admin') return null

  const total = data?.total_pending ?? 0

  async function handleApproveEmail(reqId: string, requestedEmail: string) {
    setActionLoading(reqId)
    try {
      await api(`/api/v1/admin/email-requests/${reqId}/approve`, { method: 'POST' })
      setToast(`Email updated to ${requestedEmail}`)
      await loadNotifications()
    } catch (err) {
      setToast(err instanceof ApiError ? err.message : 'Could not approve email change.')
    } finally {
      setActionLoading(null)
    }
  }

  async function handleConfirmRejectEmail(reqId: string) {
    setActionLoading(reqId)
    try {
      await api(`/api/v1/admin/email-requests/${reqId}/reject`, { method: 'POST' })
      setToast('Email revision request rejected.')
      setRejectReqId(null)
      await loadNotifications()
    } catch (err) {
      setToast(err instanceof ApiError ? err.message : 'Could not reject request.')
    } finally {
      setActionLoading(null)
    }
  }

  return (
    <div className="admin-notification-wrap" ref={menuRef} style={{ position: 'relative' }}>
      <button
        type="button"
        className="icon-button notification-bell-button"
        onClick={() => setOpen((o) => !o)}
        title="Admin Notifications"
        aria-label="Admin Notifications"
        style={{ position: 'relative', width: 34, height: 34, borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
      >
        <Bell size={17} />
        {total > 0 && (
          <span
            style={{
              position: 'absolute',
              top: -2,
              right: -2,
              background: 'var(--primary)',
              color: '#fff',
              fontSize: 10,
              fontWeight: 700,
              borderRadius: 10,
              minWidth: 16,
              height: 16,
              lineHeight: '16px',
              textAlign: 'center',
              padding: '0 4px',
              border: '2px solid var(--surface)',
            }}
          >
            {total}
          </span>
        )}
      </button>

      {open && (
        <>
          <button
            type="button"
            className="popover-dismiss"
            style={{ zIndex: 9999 }}
            aria-label="Close notification menu"
            onClick={() => setOpen(false)}
          />
          <div className="admin-notification-popover" style={{ zIndex: 10000 }}>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '12px 14px',
                borderBottom: '1px solid var(--border)',
                background: 'var(--surface-soft)',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Bell size={15} color="var(--primary)" />
                <strong style={{ fontSize: 13 }}>Workspace Notifications</strong>
              </div>
              <span className="status-pill active" style={{ fontSize: 10 }}>
                {total} pending
              </span>
            </div>

            <div style={{ maxHeight: 380, overflowY: 'auto' }}>
              {data?.items && data.items.length > 0 ? (
                data.items.map((item) => (
                  <div
                    key={`${item.type}-${item.id}`}
                    style={{
                      padding: '12px 14px',
                      borderBottom: '1px solid var(--border)',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: 6,
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
                      <span
                        style={{
                          padding: 6,
                          borderRadius: 6,
                          background: item.type === 'access_request' ? 'var(--primary-soft)' : 'var(--warning-soft)',
                          color: item.type === 'access_request' ? 'var(--primary)' : 'var(--warning)',
                          display: 'flex',
                          marginTop: 2,
                        }}
                      >
                        {item.type === 'access_request' ? <UserPlus size={14} /> : <Mail size={14} />}
                      </span>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <p style={{ margin: 0, fontSize: 12, fontWeight: 600, color: 'var(--text)' }}>
                          {item.title}
                        </p>
                        <small style={{ color: 'var(--muted)', fontSize: 11, display: 'block', marginTop: 2 }}>
                          {item.subtitle}
                        </small>
                      </div>
                    </div>

                    {item.type === 'access_request' && (
                      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 4 }}>
                        <button
                          type="button"
                          className="primary-button compact-button"
                          style={{ fontSize: 11, padding: '3px 10px', height: 24 }}
                          onClick={() => {
                            setOpen(false)
                            navigate('/admin/users?account_status=pending')
                          }}
                        >
                          Review Application
                        </button>
                      </div>
                    )}

                    {item.type === 'email_request' && item.request_id && (
                      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 6, marginTop: 4 }}>
                        <button
                          type="button"
                          className="secondary-button danger compact-button"
                          style={{ fontSize: 11, padding: '3px 8px', height: 24 }}
                          disabled={actionLoading === item.request_id}
                          onClick={() => setRejectReqId(item.request_id!)}
                        >
                          <Trash2 size={12} /> Reject
                        </button>
                        <button
                          type="button"
                          className="primary-button compact-button"
                          style={{ fontSize: 11, padding: '3px 10px', height: 24 }}
                          disabled={actionLoading === item.request_id}
                          onClick={() => handleApproveEmail(item.request_id!, item.requested_email || '')}
                        >
                          <Check size={12} /> Approve
                        </button>
                      </div>
                    )}
                  </div>
                ))
              ) : (
                <div style={{ padding: '24px 16px', textAlign: 'center', color: 'var(--muted)', fontSize: 12 }}>
                  All clear! No pending access or email revision requests.
                </div>
              )}
            </div>
          </div>
        </>
      )}

      {rejectReqId && (
        <ConfirmDialog
          title="Reject email revision request?"
          message="Are you sure you want to reject this corporate email revision request?"
          confirmText="Reject request"
          confirmVariant="danger"
          isBusy={Boolean(actionLoading)}
          onCancel={() => { if (!actionLoading) setRejectReqId(null) }}
          onConfirm={() => void handleConfirmRejectEmail(rejectReqId)}
        />
      )}

      {toast && (
        <div className="toast-message" style={{ position: 'fixed', bottom: 20, right: 20, zIndex: 10001 }}>
          <span>{toast}</span>
          <button onClick={() => setToast('')}><X size={14} /></button>
        </div>
      )}
    </div>
  )
}
