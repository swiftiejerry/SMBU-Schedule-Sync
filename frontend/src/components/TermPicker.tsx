import { motion, type Variants } from 'framer-motion'
import { useEffect, useRef } from 'react'
import type { Term } from '../lib/api'
import { useApp } from '../providers'

interface Props {
  terms: Term[]
  value: string
  onChange: (code: string) => void
  loading?: boolean
}

const chipWrap: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.035, delayChildren: 0.05 } },
}

const chipItem: Variants = {
  hidden: { opacity: 0, y: 8 },
  show: { opacity: 1, y: 0, transition: { type: 'spring', stiffness: 460, damping: 34 } },
}

export function TermPicker({ terms, value, onChange, loading }: Props) {
  const { t } = useApp()
  const railRef = useRef<HTMLDivElement>(null)

  // Bring the selected term into view without yanking the whole page.
  useEffect(() => {
    const rail = railRef.current
    if (!rail) return
    const active = rail.querySelector<HTMLElement>(`[data-term="${CSS.escape(value)}"]`)
    if (!active) return
    const railBox = rail.getBoundingClientRect()
    const box = active.getBoundingClientRect()
    const delta = box.left - railBox.left - railBox.width / 2 + box.width / 2
    rail.scrollBy({ left: delta, behavior: 'smooth' })
  }, [value, terms.length])

  if (loading) {
    return (
      <div className="flex gap-2 overflow-hidden">
        {[0, 1, 2, 3].map((i) => (
          <div
            key={i}
            className="h-9 w-32 shrink-0 animate-pulse rounded-lg bg-ink-200/70 dark:bg-ink-800"
          />
        ))}
      </div>
    )
  }

  return (
    <motion.div
      ref={railRef}
      variants={chipWrap}
      initial="hidden"
      animate="show"
      className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1 [scrollbar-width:none]
                 [&::-webkit-scrollbar]:hidden"
      role="tablist"
      aria-label={t('terms.title')}
    >
      {terms.map((term) => {
        const active = term.code === value
        return (
          <motion.button
            key={term.code}
            data-term={term.code}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(term.code)}
            variants={chipItem}
            className={`relative shrink-0 whitespace-nowrap rounded-lg border px-3.5 py-2 text-[13px]
                        transition-colors duration-200 tnum
                        ${
                          active
                            ? 'border-ink-900 bg-ink-900 text-white dark:border-white dark:bg-white dark:text-ink-950'
                            : 'border-ink-200 bg-white muted hover:border-ink-300 hover:text-ink-800 dark:border-ink-700 dark:bg-ink-900 dark:hover:border-ink-600 dark:hover:text-ink-200'
                        }`}
          >
            {term.name}
            {term.current && (
              <span
                className={`ml-2 text-[10.5px] uppercase tracking-wide ${
                  active ? 'text-white/60 dark:text-ink-500' : 'text-ink-400'
                }`}
              >
                {t('terms.current')}
              </span>
            )}
            {active && (
              <motion.span
                layoutId="term-underline"
                className="absolute inset-0 -z-10 rounded-lg"
                transition={{ type: 'spring', stiffness: 480, damping: 36 }}
              />
            )}
          </motion.button>
        )
      })}
    </motion.div>
  )
}
