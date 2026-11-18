import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { Spinner } from './Spinner'

export function ProtectedRoute() {
  const { user, loading } = useAuth()
  const location = useLocation()
  if (loading) return <Spinner full />
  return user ? <Outlet /> : <Navigate to="/login" replace state={{ from: location.pathname }} />
}

export function AdminRoute() {
  const { user, loading } = useAuth()
  if (loading) return <Spinner full />
  return user?.role === 'Admin' ? <Outlet /> : <Navigate to="/chat" replace />
}
