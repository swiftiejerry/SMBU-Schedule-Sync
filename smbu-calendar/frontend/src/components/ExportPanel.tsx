import { motion } from 'framer-motion'
import { useState } from 'react'
import { api, ApiError, tokenStore } from '../lib/api'
import { useApp } from '../providers'
import { Spinner } from './LoginCard'

const REMINDERS = [0, 10, 15, 20, 30]

interface Props {
  termCode: string
  eventCount: number
  disabled?: boolean
  onError: (code: string) => void
}

export function ExportPanel({ termCode, eventCount, disabled, onError }: Props) {
  const { t, lang } = useApp()
  const [reminder, setReminder] = useState(15)
  const [busy, setBusy] = useState(false)

  async function download() {
    if (busy) return
    setBusy(true)
    try {
      const token = tokenStore.get()
      const res = await fetch(api.icsUrl(termCode, lang, reminder), {
        headers: token ? { 'X-Session-Token': token } : {},
      })
      if (!res.ok) {
        let code = 'UNKNOWN'
        try {
          const body = await res.json()
          code = body?.error?.code ?? code
        } catch {
          /* non-JSON error body */
        }
        onError(code)
        return
      }
      const blob = await res.blob()
      const filename =
        res.headers.get('Content-Disposition')?.match(/filename="(.+?)"/)?.[1] ??
        `smbu-${termCode}.ics`
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      document.body.appendChild(a)
      a.click()
      a.remove()
      // Revoke on the next tick so Safari has time to start the download.
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (err) {
      onError(err instanceof ApiError ? err.code : 'NETWORK')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h3 className="text-[15.5px] font-semibold">{t('export.title')}</h3>
          <p className="mt-1 max-w-md text-[13px] leading-relaxed muted">{t('export.desc')}</p>
        </div>
        <div className="text-right">
          <p className="text-[26px] font-semibold leading-none tnum">{eventCount}</p>
          <p className="mt-1 text-[11.5px] muted">{t('schedule.events')}</p>
        </div>
      </div>

      <div className="mt-6">
        <p className="mb-2 text-[12.5px] font-medium muted">{t('export.reminder')}</p>
        <div className="flex flex-wrap gap-2">
          {REMINDERS.map((m) => {
            const active = reminder === m
            return (
              <button
                key={m}
                type="button"
                onClick={() => setReminder(m)}
                aria-pressed={active}
                className={`relative rounded-lg border px-3 py-1.5 text-[13px] transition-colors tnum
                            ${
                              active
                                ? 'border-ink-900 bg-ink-900 text-white dark:border-white dark:bg-white dark:text-ink-950'
                                : 'border-ink-200 bg-white muted hover:border-ink-300 dark:border-ink-700 dark:bg-ink-900'
                            }`}
              >
                {m === 0 ? t('export.reminder.none') : `${m} ${t('export.minutes')}`}
              </button>
            )
          })}
        </div>
      </div>

      <motion.button
        type="button"
        onClick={download}
        disabled={busy || disabled || eventCount === 0}
        whileTap={{ scale: 0.985 }}
        className="btn-primary mt-6 w-full"
      >
        {busy ? (
          <>
            <Spinner />
            {t('export.downloading')}
          </>
        ) : (
          <>
            <svg width="15" height="15" viewBox="0 0 16 16" fill="none">
              <path d="M8 2v8m0 0 3-3m-3 3L5 7M2.5 11.5v1A1.5 1.5 0 0 0 4 14h8a1.5 1.5 0 0 0 1.5-1.5v-1"
                    stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            {t('export.download')}
          </>
        )}
      </motion.button>

      <p className="mt-4 text-[12px] leading-relaxed muted">{t('export.hint')}</p>
    </div>
  )
}
