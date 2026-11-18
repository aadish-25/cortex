import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Moon, Sun } from 'lucide-react'

type Theme = 'light' | 'dark'
interface ThemeContextValue { theme: Theme; toggleTheme: () => void }
const ThemeContext = createContext<ThemeContextValue | null>(null)

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => (localStorage.getItem('cortex-theme') as Theme) || 'light')
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('cortex-theme', theme)
  }, [theme])
  const value = useMemo(() => ({ theme, toggleTheme: () => setTheme((current) => current === 'light' ? 'dark' : 'light') }), [theme])
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme() {
  const value = useContext(ThemeContext)
  if (!value) throw new Error('useTheme must be used inside ThemeProvider')
  return value
}

export function ThemeButton({ theme, onToggle }: { theme: Theme; onToggle: () => void }) {
  const Icon = theme === 'light' ? Moon : Sun
  return <button className="icon-button" onClick={onToggle} aria-label={`Switch to ${theme === 'light' ? 'dark' : 'light'} theme`}><Icon size={17} /></button>
}
