export type ThemeMode = 'auto' | 'light' | 'dark'

const KEY = 'smbu.theme'

export function storedMode(): ThemeMode {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw === 'light' || raw === 'dark' || raw === 'auto') return raw
  } catch {
    /* storage unavailable */
  }
  return 'auto'
}

export function setStoredMode(mode: ThemeMode) {
  try {
    localStorage.setItem(KEY, mode)
  } catch {
    /* storage unavailable */
  }
}

export function systemPrefersDark(): boolean {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-color-scheme: dark)').matches === true
  )
}

export function applyTheme(mode: ThemeMode) {
  const dark = mode === 'dark' || (mode === 'auto' && systemPrefersDark())
  const el = document.documentElement
  el.classList.toggle('dark', dark)
  el.classList.toggle('light', !dark)
  el.style.colorScheme = dark ? 'dark' : 'light'
  return dark
}

/** Returns an unsubscribe function. */
export function watchSystemTheme(onChange: (dark: boolean) => void): () => void {
  if (typeof window === 'undefined' || !window.matchMedia) return () => {}
  const mq = window.matchMedia('(prefers-color-scheme: dark)')
  const handler = (e: MediaQueryListEvent) => onChange(e.matches)
  mq.addEventListener('change', handler)
  return () => mq.removeEventListener('change', handler)
}
