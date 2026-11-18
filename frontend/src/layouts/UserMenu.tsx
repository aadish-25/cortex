import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Brain, CircleHelp, Keyboard, LogOut, Mail, Moon, Settings2, ShieldCheck, Sun, UserRound } from 'lucide-react'
import { useAuth } from '../auth/AuthContext'
import { Modal } from '../components/Modal'
import { useTheme } from '../components/ThemeToggle'

export function UserMenu() {
  const { user, signOut } = useAuth()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)
  const { theme, toggleTheme } = useTheme()
  if (!user) return null
  const initials = user.name.split(/\s+/).slice(0, 2).map((part) => part[0]).join('').toUpperCase()

  return <div className="user-menu-wrap">
    {open && <button className="popover-dismiss" aria-label="Close profile menu" onClick={() => setOpen(false)} />}
    {open && <div className="user-popover">
      <div className="user-popover-profile"><span className="user-avatar large">{initials}</span><div><strong>{user.name}</strong><small>{user.email}</small><span className="role-pill">{user.role}</span></div></div>
      <div className="user-popover-meta"><span>{user.job_title}</span><span>{user.department} · {user.clearance_level}</span></div>
      <button onClick={() => { navigate('/profile'); setOpen(false) }}><UserRound size={16} /> Profile</button>
      <button onClick={() => { navigate('/memories'); setOpen(false) }}><Brain size={16} /> Memories</button>
      <button onClick={() => { navigate('/profile#preferences'); setOpen(false) }}><Settings2 size={16} /> Preferences</button>
      <button onClick={() => { setOpen(false); setHelpOpen(true) }}><CircleHelp size={16} /> Help and support</button>
      <button onClick={toggleTheme}><span className="menu-icon-pair">{theme === 'light' ? <Moon size={16} /> : <Sun size={16} />}</span>{theme === 'light' ? 'Dark appearance' : 'Light appearance'}</button>
      <div className="popover-divider" />
      <button className="signout-item" onClick={async () => { setOpen(false); await signOut(); navigate('/login', { replace: true }) }}><LogOut size={16} /> Sign out</button>
    </div>}
    <button className={`sidebar-user-button ${open ? 'open' : ''}`} onClick={() => setOpen((value) => !value)} aria-expanded={open}>
      <span className="user-avatar">{initials}</span>
      <span className="sidebar-user-copy">
        <strong>{user.name}</strong>
        <small>{user.role} · {user.department}</small>
      </span>
    </button>

    {helpOpen && (
      <Modal title="Help & Support" onClose={() => setHelpOpen(false)}>
        <div className="help-modal-body">
          <div className="help-section">
            <p className="eyebrow"><Keyboard size={12} style={{ verticalAlign: '-1px', marginRight: 5 }} />KEYBOARD SHORTCUTS</p>
            <div className="help-shortcut-row"><span>New conversation</span><kbd>Ctrl / ⌘ + K</kbd></div>
            <div className="help-shortcut-row"><span>Send message</span><kbd>Enter</kbd></div>
            <div className="help-shortcut-row"><span>New line in composer</span><kbd>Shift + Enter</kbd></div>
          </div>
          <div className="help-section">
            <p className="eyebrow"><ShieldCheck size={12} style={{ verticalAlign: '-1px', marginRight: 5 }} />ACCESS & CITATIONS</p>
            <p className="help-copy">Answers are strictly scoped to your role ({user.role}), department ({user.department}), and clearance level ({user.clearance_level}). Click any numbered citation chip like [1] to inspect the verified source passage.</p>
          </div>
          <div className="help-section">
            <p className="eyebrow"><Mail size={12} style={{ verticalAlign: '-1px', marginRight: 5 }} />NEED ASSISTANCE?</p>
            <p className="help-copy">Contact your workspace administrator to update your department, clearance level, or request new source documents.</p>
          </div>
          <div className="modal-actions">
            <button className="primary-button compact-button" onClick={() => setHelpOpen(false)}>Got it</button>
          </div>
        </div>
      </Modal>
    )}
  </div>
}
