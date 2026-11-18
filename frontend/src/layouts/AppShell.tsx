import { useEffect, useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { Menu, PanelLeftClose } from 'lucide-react'
import { Sidebar } from './Sidebar'

export function AppShell() {
  const [mobileOpen, setMobileOpen] = useState(false)
  const location = useLocation()
  useEffect(() => setMobileOpen(false), [location.pathname])
  return <div className="app-shell">
    <Sidebar mobileOpen={mobileOpen} onNavigate={() => setMobileOpen(false)} />
    {mobileOpen && <button className="mobile-sidebar-scrim" onClick={() => setMobileOpen(false)} aria-label="Close navigation" />}
    <main className="app-main">
      <button className="mobile-menu-button" onClick={() => setMobileOpen((value) => !value)} aria-label="Toggle navigation">{mobileOpen ? <PanelLeftClose size={19} /> : <Menu size={19} />}</button>
      <Outlet />
    </main>
  </div>
}
