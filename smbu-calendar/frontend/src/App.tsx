import { AnimatePresence, motion } from 'framer-motion'
import { useCallback, useEffect, useState } from 'react'
import { LanguageSwitch, ThemeSwitch } from './components/Controls'
import { ExportPanel } from './components/ExportPanel'
import { LoginCard } from './components/LoginCard'
import { TermPicker } from './components/TermPicker'
import { Timetable, TimetableSkeleton } from './components/Timetable'
import { api, ApiError, tokenStore, type LoginResponse, type ScheduleResponse, type TermsResponse } from './lib/api'
import { useApp } from './providers'

type Stage = 'login' | 'terms' | 'schedule'

export default function App() {
  const { t } = useApp()
  const [stage, setStage] = useState<Stage>('login')
  const [account, setAccount] = useState<LoginResponse | null>(null)
  const [terms, setTerms] = useState<TermsResponse | null>(null)
  const [termCode, setTermCode] = useState<string>('')
  const [schedule, setSchedule] = useState<ScheduleResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fail = useCallback(
    (err: unknown) => {
      const code = err instanceof ApiError ? err.code : 'NETWORK'
      if (code === 'SESSION_EXPIRED') {
        tokenStore.clear()
        setAccount(null)
        setTerms(null)
        setSchedule(null)
        setStage('login')
      }
      setError(t(`err.${code}`) ?? t('err.UNKNOWN'))
    },
    [t],
  )

  async function onLogin(res: LoginResponse) {
    setAccount(res)
    setStage('terms')
    setLoading(true)
    setError(null)
    try {
      const data = await api.terms(res.token)
      setTerms(data)
      const code = data.default_code ?? data.terms[0]?.code ?? ''
      setTermCode(code)
      const sched = await api.schedule(res.token, code)
      setSchedule(sched)
      setStage('schedule')
    } catch (err) {
      fail(err)
      setStage('login')
    } finally {
      setLoading(false)
    }
  }

  // Re-fetch whenever the selected term changes.
  useEffect(() => {
    if (!account || !termCode) return
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .schedule(account.token, termCode)
      .then((data) => {
        if (!cancelled) setSchedule(data)
      })
      .catch((err) => !cancelled && fail(err))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [account, termCode, fail])

  async function signOut() {
    const token = tokenStore.get()
    if (token) {
      try {
        await api.logout(token)
      } catch {
        /* best effort — the local session is dropped either way */
      }
    }
    tokenStore.clear()
    setAccount(null)
    setTerms(null)
    setSchedule(null)
    setStage('login')
  }

  return (
    <div className="min-h-dvh">
      <Background />

      <header className="sticky top-0 z-20 border-b hairline bg-ink-50/80 backdrop-blur-xl
                         dark:bg-ink-950/80">
        <div className="mx-auto flex h-14 max-w-5xl items-center justify-between gap-3 px-5">
          <div className="flex items-center gap-2.5">
            <Mark />
            <div className="leading-tight">
              <p className="text-[13.5px] font-semibold tracking-[-0.01em]">{t('app.title')}</p>
              <p className="text-[10.5px] uppercase tracking-[0.08em] muted">{t('app.badge')}</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <LanguageSwitch />
            <ThemeSwitch />
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-5 pb-24 pt-10 sm:pt-14">
        <AnimatePresence mode="wait">
          {stage === 'login' && (
            <motion.div
              key="login"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0, y: -8 }}
              className="flex flex-col items-center"
            >
              <motion.div
                variants={{
                  hidden: {},
                  show: { transition: { staggerChildren: 0.09, delayChildren: 0.04 } },
                }}
                initial="hidden"
                animate="show"
                className="flex w-full flex-col items-center"
              >
                <motion.h1
                  variants={{
                    hidden: { opacity: 0, y: 14 },
                    show: {
                      opacity: 1,
                      y: 0,
                      transition: { duration: 0.55, ease: [0.22, 1, 0.36, 1] },
                    },
                  }}
                  className="mb-9 max-w-xl text-balance text-center text-[30px] font-semibold
                             leading-[1.15] tracking-[-0.02em] sm:text-[40px]"
                >
                  {t('app.subtitle')}
                </motion.h1>
                <LoginCard onSuccess={onLogin} />
              </motion.div>
            </motion.div>
          )}

          {stage !== 'login' && (
            <motion.div
              key="app"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
              className="space-y-6"
            >
              {/* account bar */}
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-[15px] font-medium">
                    {account?.display_name}
                    <span className="ml-2 tnum muted">{account?.student_id}</span>
                  </p>
                  <p className="mt-0.5 text-[12.5px] muted">{t('terms.desc')}</p>
                </div>
                <button onClick={signOut} className="btn-ghost">
                  {t('login.logout')}
                </button>
              </div>

              {/* term rail */}
              <section>
                <div className="mb-2.5 flex items-baseline justify-between">
                  <h2 className="text-[13px] font-medium muted">{t('terms.title')}</h2>
                  <span className="text-[12px] muted tnum">
                    {terms?.terms.length ?? 0}
                  </span>
                </div>
                {terms?.terms.length ? (
                  <TermPicker
                    terms={terms.terms}
                    value={termCode}
                    onChange={setTermCode}
                    loading={false}
                  />
                ) : (
                  <p className="text-[13px] muted">{t('terms.empty')}</p>
                )}
              </section>

              {/* warnings */}
              {schedule?.warnings?.length ? (
                <div className="space-y-2">
                  {schedule.warnings.map((w) => (
                    <p
                      key={w}
                      className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[12.5px]
                                 text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300"
                    >
                      {w === 'WEEK_DATES_MISSING'
                        ? t('schedule.noWeeks')
                        : w === 'SECTION_TIMES_MISSING'
                          ? t('schedule.noSections')
                          : w}
                    </p>
                  ))}
                </div>
              ) : null}

              {/* timetable */}
              <section className="space-y-3">
                <div className="flex items-baseline justify-between">
                  <h2 className="text-[13px] font-medium muted">{t('schedule.title')}</h2>
                  {schedule && (
                    <span className="text-[12px] muted tnum">
                      {schedule.term_name}
                    </span>
                  )}
                </div>

                {loading && !schedule ? (
                  <TimetableSkeleton />
                ) : schedule ? (
                  <Timetable data={schedule} />
                ) : null}
              </section>

              {schedule && (
                <ExportPanel
                  termCode={schedule.term_code}
                  eventCount={schedule.event_count}
                  disabled={loading}
                  onError={(code) => setError(t(`err.${code}`) ?? t('err.UNKNOWN'))}
                />
              )}

              <AnimatePresence>
                {error && (
                  <motion.p
                    initial={{ opacity: 0, y: -4 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }}
                    className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[13px]
                               text-red-700 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-300"
                  >
                    {error}
                  </motion.p>
                )}
              </AnimatePresence>
            </motion.div>
          )}
        </AnimatePresence>
      </main>

      <footer className="mx-auto max-w-5xl px-5 pb-10 text-[11.5px] muted">
        SMBU Schedule Sync · {t('login.privacy')}
      </footer>
    </div>
  )
}

function Mark() {
  return (
    <div className="grid h-8 w-8 place-items-center rounded-lg bg-ink-900 text-[13px] font-semibold
                    text-white dark:bg-white dark:text-ink-950">
      S
    </div>
  )
}

/**
 * A very restrained ambient layer: two soft radial washes plus a hairline grid.
 * No colour blobs, no purple — it reads as depth, not decoration.
 */
function Background() {
  const { isDark } = useApp()
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      <motion.div
        className="absolute -top-40 left-1/2 h-[520px] w-[900px] -translate-x-1/2 rounded-full"
        style={{
          background: `radial-gradient(50% 50% at 50% 50%, ${
            isDark ? 'rgba(59,109,246,0.10)' : 'rgba(37,79,235,0.06)'
          } 0%, transparent 100%)`,
        }}
        animate={{ opacity: [0.75, 1, 0.75] }}
        transition={{ duration: 12, repeat: Infinity, ease: 'easeInOut' }}
      />
      <div
        className="absolute inset-0 opacity-[0.035] dark:opacity-[0.05]"
        style={{
          backgroundImage:
            'linear-gradient(to right, currentColor 1px, transparent 1px), linear-gradient(to bottom, currentColor 1px, transparent 1px)',
          backgroundSize: '56px 56px',
        }}
      />
    </div>
  )
}
