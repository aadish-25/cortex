import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, getMe } from '../api/client'
import type { User } from '../api/types'

interface AuthContextValue {
  user: User | null
  loading: boolean
  signIn: (identifier: string, password: string) => Promise<User>
  signOut: () => Promise<void>
  refreshUser: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  const refreshUser = useCallback(async () => {
    try { setUser(await getMe()) }
    catch { setUser(null) }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { void refreshUser() }, [refreshUser])

  const signIn = useCallback(async (identifier: string, password: string) => {
    const result = await api<{ user: User; access_token: string }>('/api/v1/auth/login', {
      method: 'POST', body: JSON.stringify({ identifier, password }),
    })
    // The browser uses the HttpOnly cookie. The returned token is deliberately not persisted in JS storage.
    setUser(result.user)
    return result.user
  }, [])

  const signOut = useCallback(async () => {
    try { await api('/api/v1/auth/logout', { method: 'POST' }) }
    catch { /* Expired/revoked cookies are already unusable. */ }
    finally { setUser(null) }
  }, [])

  const value = useMemo(() => ({ user, loading, signIn, signOut, refreshUser }), [user, loading, signIn, signOut, refreshUser])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside AuthProvider')
  return value
}
