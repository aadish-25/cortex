import { Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider } from './auth/AuthContext'
import { ThemeProvider } from './components/ThemeToggle'
import { ProtectedRoute, AdminRoute } from './components/ProtectedRoute'
import { AppShell } from './layouts/AppShell'
import { LoginPage } from './pages/LoginPage'
import { RegisterPage } from './pages/RegisterPage'
import { ChatPage } from './chat/ChatPage'
import { ProfilePage } from './pages/ProfilePage'
import { AdminOverview } from './admin/AdminOverview'
import { AdminDocuments } from './admin/AdminDocuments'
import { AdminUsers } from './admin/AdminUsers'
import { AdminAudit } from './admin/AdminAudit'
import { AdminQuality } from './admin/AdminQuality'

export default function App() {
  return <ThemeProvider><AuthProvider><Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route path="/register" element={<RegisterPage />} />
    <Route element={<ProtectedRoute />}>
      <Route element={<AppShell />}>
        <Route path="/" element={<Navigate to="/chat" replace />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/chat/:conversationId" element={<ChatPage />} />
        <Route path="/profile" element={<ProfilePage />} />
        <Route element={<AdminRoute />}>
          <Route path="/admin" element={<Navigate to="/admin/overview" replace />} />
          <Route path="/admin/overview" element={<AdminOverview />} />
          <Route path="/admin/documents" element={<AdminDocuments />} />
          <Route path="/admin/users" element={<AdminUsers />} />
          <Route path="/admin/audit" element={<AdminAudit />} />
          <Route path="/admin/quality" element={<AdminQuality />} />
        </Route>
      </Route>
    </Route>
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes></AuthProvider></ThemeProvider>
}
