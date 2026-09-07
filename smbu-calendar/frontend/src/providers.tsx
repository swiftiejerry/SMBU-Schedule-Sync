import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { detectLang, translate, type Lang } from './i18n'
import {
  applyTheme,
  setStoredMode,
  storedMode,
  watchSystemTheme,
  type ThemeMode,
} from './lib/theme'

interface AppCtx {
  lang: Lang
  setLang: (lang: Lang) => void
  t: (key: string) => string
  theme: ThemeMode
  setTheme: (mode: ThemeMode) => void
  isDark: boolean
}

const Ctx = createContext<AppCtx | null>(null)

const LANG_KEY = 'smbu.lang'

export function AppProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => detectLang())
  const [theme, setThemeState] = useState<ThemeMode>(() => storedMode())
  const [isDark, setIsDark] = useState(() => applyTheme(storedMode()))

  // Keep <html lang> in sync so screen readers and browser translation work.
  useEffect(() => {
    const tag: Record<Lang, string> = { zh: 'zh-CN', en: 'en', ru: 'ru' }
    document.documentElement.lang = tag[lang]
  }, [lang])

  // Auto mode must follow the OS live, not only at load.
  useEffect(() => {
    return watchSystemTheme((dark) => {
      setThemeState((current) => {
        if (current !== 'auto') return current
        const el = document.documentElement
        el.classList.toggle('dark', dark)
        el.classList.toggle('light', !dark)
        el.style.colorScheme = dark ? 'dark' : 'light'
        setIsDark(dark)
        return current
      })
    })
  }, [])

  const setLang = useCallback((next: Lang) => {
    setLangState(next)
    try {
      localStorage.setItem(LANG_KEY, next)
    } catch {
      /* storage unavailable */
    }
  }, [])

  const setTheme = useCallback((next: ThemeMode) => {
    setThemeState(next)
    setStoredMode(next)
    setIsDark(applyTheme(next))
  }, [])

  const value = useMemo<AppCtx>(
    () => ({
      lang,
      setLang,
      t: (key: string) => translate(lang, key),
      theme,
      setTheme,
      isDark,
    }),
    [lang, setLang, theme, setTheme, isDark],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useApp(): AppCtx {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useApp must be used inside <AppProvider>')
  return ctx
}
