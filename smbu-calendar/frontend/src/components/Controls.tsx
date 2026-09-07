import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useRef, useState } from 'react'
import { LANGS, type Lang } from '../i18n'
import { useApp } from '../providers'
import type { ThemeMode } from '../lib/theme'

const THEMES: { mode: ThemeMode; icon: string; key: string }[] = [
  { mode: 'auto', icon: 'A', key: 'theme.auto' },
  { mode: 'light', icon: '☀', key: 'theme.light' },
  { mode: 'dark', icon: '☾', key: 'theme.dark' },
]

export function ThemeSwitch() {
  const { theme, setTheme, t } = useApp()
  const [hovered, setHovered] = useState<ThemeMode | null>(null)
  const showLabel = hovered ?? theme

  return (
    <div
      className="relative flex items-center gap-0.5 rounded-lg border border-ink-200 bg-white p-0.5
                 dark:border-ink-700 dark:bg-ink-900"
      onMouseLeave={() => setHovered(null)}
    >
      {THEMES.map(({ mode, icon }) => (
        <button
          key={mode}
          type="button"
          aria-label={t(`theme.${mode}`)}
          aria-pressed={theme === mode}
          onMouseEnter={() => setHovered(mode)}
          onClick={() => setTheme(mode)}
          className="relative grid h-7 w-8 place-items-center rounded-[6px] text-[13px] transition-colors
                     duration-200 hover:text-ink-900 dark:hover:text-ink-100"
        >
          {theme === mode && (
            <motion.span
              layoutId="theme-pill"
              className="absolute inset-0 rounded-[6px] bg-ink-100 dark:bg-ink-800"
              transition={{ type: 'spring', stiffness: 520, damping: 34 }}
            />
          )}
          <span className={theme === mode ? 'relative text-ink-900 dark:text-ink-50' : 'relative muted'}>
            {icon}
          </span>
        </button>
      ))}
      <AnimatePresence>
        <motion.span
          key={showLabel}
          initial={{ opacity: 0, y: 3 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -3 }}
          transition={{ duration: 0.16 }}
          className="pointer-events-none absolute -bottom-7 right-0 whitespace-nowrap rounded-md
                     bg-ink-900 px-2 py-1 text-[11px] text-white opacity-0 transition-opacity
                     group-hover:opacity-100 dark:bg-ink-100 dark:text-ink-900"
          style={{ opacity: hovered ? 1 : 0 }}
        >
          {t(showLabel === 'auto' ? 'theme.auto' : `theme.${showLabel}`)}
        </motion.span>
      </AnimatePresence>
    </div>
  )
}

export function LanguageSwitch() {
  const { lang, setLang, t } = useApp()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const current = LANGS.find((l) => l.code === lang) ?? LANGS[0]

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={t('nav.language')}
        aria-expanded={open}
        className="btn-ghost min-w-[104px] justify-between"
      >
        <span>{current.native}</span>
        <motion.svg
          width="12" height="12" viewBox="0 0 12 12" fill="none"
          animate={{ rotate: open ? 180 : 0 }}
          transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
        >
          <path d="M3 4.5 6 7.5 9 4.5" stroke="currentColor" strokeWidth="1.4"
                strokeLinecap="round" strokeLinejoin="round" />
        </motion.svg>
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -6, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.97 }}
            transition={{ duration: 0.17, ease: [0.22, 1, 0.36, 1] }}
            className="absolute right-0 z-30 mt-2 w-44 overflow-hidden rounded-xl border border-ink-200
                       bg-white p-1 shadow-lg dark:border-ink-700 dark:bg-ink-900"
          >
            {LANGS.map((l) => (
              <button
                key={l.code}
                type="button"
                onClick={() => {
                  setLang(l.code as Lang)
                  setOpen(false)
                }}
                className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm
                            transition-colors duration-150 hover:bg-ink-100 dark:hover:bg-ink-800
                            ${l.code === lang ? 'text-ink-900 dark:text-ink-50' : 'muted'}`}
              >
                <span>{l.native}</span>
                {l.code === lang && (
                  <motion.span initial={{ scale: 0 }} animate={{ scale: 1 }}
                               transition={{ type: 'spring', stiffness: 600, damping: 30 }}>
                    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                      <path d="M2.5 7.5 5.5 10.5 11.5 3.5" stroke="currentColor" strokeWidth="1.6"
                            strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </motion.span>
                )}
              </button>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
