import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChevronUp, LogOut, Moon, Settings2, Sun, UserRound } from 'lucide-react'
import { useAuth } from '../auth/AuthContext'
import { useTheme } from '../components/ThemeToggle'

export function UserMenu() {
  const { user, signOut } = useAuth()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const { theme, toggleTheme } = useTheme()
  if (!user) return null
  const initials = user.name.split(/\s+/).slice(0, 2).map((part) => part[0]).join('').toUpperCase()

  return <div className="user-menu-wrap">
    {open && <button className="popover-dismiss" aria-label="Close profile menu" onClick={() => setOpen(false)} />}
    {open && <div className="user-popover">
      <div className="user-popover-profile"><span className="user-avatar large">{initials}</span><div><strong>{user.name}</strong><small>{user.email}</small><span className="role-pill">{user.role}</span></div></div>
      <div className="user-popover-meta"><span>{user.job_title}</span><span>{user.department} · {user.clearance_level}</span></div>
      <button onClick={() => { navigate('/profile'); setOpen(false) }}><UserRound size={16} /> Profile and memories</button>
      <button onClick={toggleTheme}><span className="menu-icon-pair">{theme === 'light' ? <Moon size={16} /> : <Sun size={16} />}</span>{theme === 'light' ? 'Dark appearance' : 'Light appearance'}</button>
      <button onClick={() => { navigate('/profile'); setOpen(false) }}><Settings2 size={16} /> Preferences</button>
      <div className="popover-divider" />
      <button className="signout-item" onClick={async () => { setOpen(false); await signOut(); navigate('/login', { replace: true }) }}><LogOut size={16} /> Sign out</button>
    </div>}
    <button className={`sidebar-user-button ${open ? 'open' : ''}`} onClick={() => setOpen((value) => !value)} aria-expanded={open}>
      <span className="user-avatar">{initials}</span><span className="sidebar-user-copy"><strong>{user.name}</strong><small>{user.role} · {user.department}</small></span><ChevronUp size={15} className={open ? '' : 'chevron-down'} />
    </button>
  </div>
}
