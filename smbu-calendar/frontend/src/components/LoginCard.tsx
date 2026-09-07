import { AnimatePresence, motion } from 'framer-motion'
import { useRef, useState, type FormEvent } from 'react'
import { api, ApiError, tokenStore } from '../lib/api'
import { useApp } from '../providers'
import type { LoginResponse } from '../lib/api'

interface Props {
  onSuccess: (res: LoginResponse) => void
}

export function LoginCard({ onSuccess }: Props) {
  const { t } = useApp()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [captcha, setCaptcha] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const formRef = useRef<HTMLFormElement>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (busy || !username.trim() || !password) return
    setBusy(true)
    setError(null)
    try {
      const res = await api.login(username.trim(), password, captcha.trim())
      tokenStore.set(res.token)
      // Drop the plaintext immediately — the token is all we keep.
      setPassword('')
      onSuccess(res)
    } catch (err) {
      const code = err instanceof ApiError ? err.code : 'NETWORK'
      setError(t(`err.${code}`) ?? t('err.UNKNOWN'))
      formRef.current?.animate(
        [
          { transform: 'translateX(0)' },
          { transform: 'translateX(-7px)' },
          { transform: 'translateX(6px)' },
          { transform: 'translateX(-3px)' },
          { transform: 'translateX(0)' },
        ],
        { duration: 320, easing: 'ease-out' },
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <motion.div
      variants={{
        hidden: { opacity: 0, y: 16 },
        show: { opacity: 1, y: 0, transition: { duration: 0.5, ease: [0.22, 1, 0.36, 1] } },
      }}
      className="w-full max-w-[420px]"
    >
      <form ref={formRef} onSubmit={submit} className="card p-7 sm:p-8">
        <h2 className="text-[19px] font-semibold tracking-[-0.01em]">{t('login.title')}</h2>
        <p className="mt-1.5 text-[13.5px] leading-relaxed muted">{t('login.desc')}</p>

        <div className="mt-6 space-y-3.5">
          <label className="block">
            <span className="mb-1.5 block text-[12.5px] font-medium muted">
              {t('login.username')}
            </span>
            <input
              className="field tnum"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              inputMode="numeric"
              autoCapitalize="off"
              spellCheck={false}
              required
            />
          </label>

          <label className="block">
            <span className="mb-1.5 block text-[12.5px] font-medium muted">
              {t('login.password')}
            </span>
            <input
              className="field"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </label>

          <AnimatePresence initial={false}>
            {error && error.includes('验证码') && (
              <motion.label
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                exit={{ opacity: 0, height: 0 }}
                className="block overflow-hidden"
              >
                <span className="mb-1.5 block text-[12.5px] font-medium muted">
                  {t('login.captcha')}
                </span>
                <input
                  className="field"
                  value={captcha}
                  onChange={(e) => setCaptcha(e.target.value)}
                  autoComplete="off"
                />
              </motion.label>
            )}
          </AnimatePresence>
        </div>

        <button type="submit" disabled={busy || !username.trim() || !password} className="btn-primary mt-6 w-full">
          {busy ? (
            <>
              <Spinner />
              {t('login.submitting')}
            </>
          ) : (
            t('login.submit')
          )}
        </button>

        <AnimatePresence>
          {error && (
            <motion.p
              initial={{ opacity: 0, y: -4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              className="mt-3.5 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[13px]
                         text-red-700 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-300"
            >
              {error}
            </motion.p>
          )}
        </AnimatePresence>

        <p className="mt-5 flex items-start gap-2 text-[12px] leading-relaxed muted">
          <svg width="13" height="13" viewBox="0 0 14 14" fill="none" className="mt-[2px] shrink-0">
            <path d="M7 1 12 3v3.5c0 3-2.1 5.6-5 6.5-2.9-.9-5-3.5-5-6.5V3l5-2Z"
                  stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
          </svg>
          <span>{t('login.privacy')}</span>
        </p>
      </form>
    </motion.div>
  )
}

export function Spinner({ className = '' }: { className?: string }) {
  return (
    <svg className={`h-4 w-4 animate-spin ${className}`} viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeOpacity="0.25" strokeWidth="2" />
      <path d="M14.5 8A6.5 6.5 0 0 0 8 1.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  )
}
