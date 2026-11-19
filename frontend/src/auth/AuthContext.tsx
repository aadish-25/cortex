import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, getMe } from '../api/client'
import type { User } from '../api/types'

const SESSION_USER_KEY = 'cortex_user_session'

function getSavedUser(): User | null {
  try {
    const raw = sessionStorage.getItem(SESSION_USER_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

interface AuthContextValue {
  user: User | null
  loading: boolean
  signIn: (identifier: string, password: string) => Promise<User>
  signOut: () => Promise<void>
  refreshUser: () => Promise<void>
  updateUser: (updated: Partial<User>) => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const initialUser = getSavedUser()
  const [user, setUser] = useState<User | null>(initialUser)
  // If we already have a valid session in sessionStorage, loading starts as false immediately.
  const [loading, setLoading] = useState<boolean>(() => !initialUser)

  const refreshUser = useCallback(async () => {
    try {
      const fresh = await getMe()
      setUser(fresh)
      try { sessionStorage.setItem(SESSION_USER_KEY, JSON.stringify(fresh)) } catch {}
    } catch {
      setUser(null)
      try { sessionStorage.removeItem(SESSION_USER_KEY) } catch {}
    } finally {
      setLoading(false)
    }
  }, [])

  const updateUser = useCallback((updated: Partial<User>) => {
    setUser((curr) => {
      if (!curr) return null
      const next = { ...curr, ...updated }
      try { sessionStorage.setItem(SESSION_USER_KEY, JSON.stringify(next)) } catch {}
      return next
    })
  }, [])

  useEffect(() => { void refreshUser() }, [refreshUser])

  const signIn = useCallback(async (identifier: string, password: string) => {
    const result = await api<{ user: User; access_token: string }>('/api/v1/auth/login', {
      method: 'POST', body: JSON.stringify({ identifier, password }),
    })
    // The browser uses the HttpOnly cookie. The returned token is deliberately not persisted in JS storage.
    setUser(result.user)
    try { sessionStorage.setItem(SESSION_USER_KEY, JSON.stringify(result.user)) } catch {}
    return result.user
  }, [])

  const signOut = useCallback(async () => {
    try { await api('/api/v1/auth/logout', { method: 'POST' }) }
    catch { /* Expired/revoked cookies are already unusable. */ }
    finally {
      setUser(null)
      try { sessionStorage.removeItem(SESSION_USER_KEY) } catch {}
    }
  }, [])

  const value = useMemo(() => ({ user, loading, signIn, signOut, refreshUser, updateUser }), [user, loading, signIn, signOut, refreshUser, updateUser])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside AuthProvider')
  return value
}
