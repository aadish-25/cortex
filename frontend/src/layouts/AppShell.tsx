import { useEffect, useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { Menu, PanelLeftClose } from 'lucide-react'
import { Sidebar } from './Sidebar'
import { ThemeButton, useTheme } from '../components/ThemeToggle'
import { MandatoryPasswordModal } from '../components/MandatoryPasswordModal'
import { AdminNotificationBell } from '../admin/AdminNotificationBell'

function pageBreadcrumb(pathname: string): { section: string; label: string } {
  if (pathname.startsWith('/admin/overview')) return { section: 'ADMINISTRATION', label: 'Overview' }
  if (pathname.startsWith('/admin/documents')) return { section: 'ADMINISTRATION', label: 'Documents' }
  if (pathname.startsWith('/admin/users')) return { section: 'ADMINISTRATION', label: 'People' }
  if (pathname.startsWith('/admin/quality')) return { section: 'ADMINISTRATION', label: 'Answer quality' }
  if (pathname.startsWith('/admin/audit')) return { section: 'ADMINISTRATION', label: 'Audit trail' }
  if (pathname.startsWith('/memories')) return { section: 'ACCOUNT', label: 'Memories' }
  if (pathname.startsWith('/profile')) return { section: 'ACCOUNT', label: 'Profile' }
  return { section: 'WORKSPACE', label: 'Cortex' }
}

export function AppShell() {
  const [mobileOpen, setMobileOpen] = useState(false)
  const location = useLocation()
  const { theme, toggleTheme } = useTheme()
  useEffect(() => setMobileOpen(false), [location.pathname])
  const isChatRoute = location.pathname === '/' || location.pathname.startsWith('/chat')
  const crumb = pageBreadcrumb(location.pathname)

  return <div className="app-shell">
    <Sidebar mobileOpen={mobileOpen} onNavigate={() => setMobileOpen(false)} />
    {mobileOpen && <button className="mobile-sidebar-scrim" onClick={() => setMobileOpen(false)} aria-label="Close navigation" />}
    <main className="app-main">
      <button className="mobile-menu-button" onClick={() => setMobileOpen((value) => !value)} aria-label="Toggle navigation">{mobileOpen ? <PanelLeftClose size={19} /> : <Menu size={19} />}</button>
      {!isChatRoute && (
        <header className="chat-topbar">
          <div className="chat-topbar-left">
            <span className="topbar-context">{crumb.section}</span>
            <span className="topbar-divider">/</span>
            <span>{crumb.label}</span>
          </div>
          <div className="chat-topbar-right" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <AdminNotificationBell />
            <ThemeButton theme={theme} onToggle={toggleTheme} />
          </div>
        </header>
      )}
      <div className="app-main-content">
        <Outlet />
      </div>
      <MandatoryPasswordModal />
    </main>
  </div>
}
