import { AnimatePresence, motion, type Variants } from 'framer-motion'
import { Fragment, useMemo, useState, type CSSProperties } from 'react'
import type { Meeting, ScheduleResponse } from '../lib/api'
import { useApp } from '../providers'

interface Cell {
  meeting: Meeting
  rowSpan: number
}

/**
 * Restrained, deterministic course tints — calm hues only (no violet/purple),
 * applied as a thin left bar + a faint wash so the grid reads at a glance the
 * way Apple / Linear calendars do. Saturation and alpha are kept low on purpose.
 */
const COURSE_HUES = [212, 190, 150, 38, 350, 16] // blue, teal, green, amber, rose, orange

function courseHue(seed: string): number {
  let h = 0
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0
  return COURSE_HUES[h % COURSE_HUES.length]
}

function courseTint(code: string | null, isDark: boolean): CSSProperties {
  const hue = courseHue(code ?? 'x')
  return {
    background: isDark ? `hsla(${hue}, 55%, 56%, 0.14)` : `hsla(${hue}, 72%, 50%, 0.08)`,
    borderLeft: `3px solid ${isDark ? `hsl(${hue}, 72%, 64%)` : `hsl(${hue}, 74%, 46%)`}`,
  }
}

const gridWrap: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.025, delayChildren: 0.04 } },
}

const cellItem: Variants = {
  hidden: { opacity: 0, y: 10, scale: 0.98 },
  show: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: { type: 'spring', stiffness: 420, damping: 32 },
  },
}

export function Timetable({ data }: { data: ScheduleResponse }) {
  const { t, isDark, lang } = useApp()
  const [active, setActive] = useState<Meeting | null>(null)

  const { grid, maxPeriod } = useMemo(() => {
    const sections = data.sections
    const maxPeriod = sections.length ? Math.max(...sections.map((s) => s.index)) : 11
    const cells = new Map<string, Cell>()
    const claimed = new Set<string>()

    for (const m of data.meetings) {
      const span = Math.max(1, m.end_section - m.start_section + 1)
      const key = `${m.day_of_week}-${m.start_section}`
      cells.set(key, { meeting: m, rowSpan: span })
      for (let i = 1; i < span; i++) claimed.add(`${m.day_of_week}-${m.start_section + i}`)
    }
    return { grid: { cells, claimed }, maxPeriod }
  }, [data])

  const days = [1, 2, 3, 4, 5, 6, 7]
  const rows = Array.from({ length: maxPeriod }, (_, i) => i + 1)

  if (!data.meetings.length) {
    return (
      <div className="card flex flex-col items-center justify-center gap-2 px-6 py-16 text-center">
        <p className="text-sm muted">{t('schedule.empty')}</p>
      </div>
    )
  }

  return (
    <>
      <div className="card overflow-hidden">
        <div className="overflow-x-auto">
          <motion.div
            key={data.term_code}
            variants={gridWrap}
            initial="hidden"
            animate="show"
            className="grid min-w-[720px] grid-cols-[64px_repeat(7,minmax(0,1fr))]"
            style={{ gridAutoRows: 'minmax(56px, auto)' }}
          >
            {/* header row */}
            <div className="sticky top-0 z-10 border-b border-r hairline bg-ink-50 dark:bg-ink-900" />
            {days.map((d) => (
              <div
                key={`h-${d}`}
                className="sticky top-0 z-10 border-b hairline bg-ink-50 py-2 text-center text-[12px]
                           font-medium muted dark:bg-ink-900"
              >
                {t(`day.${d}`)}
              </div>
            ))}

            {rows.map((period) => {
              const section = data.sections.find((s) => s.index === period)
              return (
                <div key={`p-${period}`} className="contents">
                  <div
                    className="flex flex-col items-center justify-center border-b border-r hairline
                               py-1.5 text-[11px] leading-tight muted tnum"
                  >
                    <span className="font-medium text-ink-700 dark:text-ink-300">{period}</span>
                    {section && (
                      <span className="mt-0.5 text-[9.5px] tabular-nums opacity-70">
                        {section.start}
                      </span>
                    )}
                  </div>

                  {days.map((day) => {
                    const key = `${day}-${period}`
                    if (grid.claimed.has(key)) return null
                    const cell = grid.cells.get(key)
                    return (
                      <div
                        key={key}
                        style={cell ? { gridRow: `span ${cell.rowSpan}` } : undefined}
                        className="border-b border-r hairline p-1 last:border-r-0"
                      >
                        {cell && (
                          <motion.button
                            type="button"
                            layout
                            variants={cellItem}
                            whileHover={{ y: -2 }}
                            whileTap={{ scale: 0.98 }}
                            onClick={() => setActive(cell.meeting)}
                            style={courseTint(cell.meeting.code, isDark)}
                            className="group h-full w-full overflow-hidden rounded-lg border border-ink-200/70
                                       px-2 py-1.5 text-left transition-[border-color,box-shadow] duration-200
                                       hover:border-ink-300 hover:shadow-[0_6px_18px_-10px_rgba(16,24,40,0.45)]
                                       dark:border-ink-700/70 dark:hover:border-ink-500"
                          >
                            <p className="truncate text-[12px] font-semibold leading-snug">
                              {cell.meeting.name}
                            </p>
                            {cell.meeting.location && (
                              <p className="truncate text-[10.5px] muted">{cell.meeting.location}</p>
                            )}
                            <p className="mt-0.5 truncate text-[10px] muted tnum">
                              {cell.meeting.week_label}
                            </p>
                          </motion.button>
                        )}
                      </div>
                    )
                  })}
                </div>
              )
            })}
          </motion.div>
        </div>
      </div>

      <AnimatePresence>
        {active && (
          <motion.div
            className="fixed inset-0 z-50 grid place-items-end sm:place-items-center"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <div
              className="absolute inset-0 bg-ink-950/40 backdrop-blur-[2px] dark:bg-black/60"
              onClick={() => setActive(null)}
            />
            <motion.div
              initial={{ y: 24, opacity: 0, scale: 0.98 }}
              animate={{ y: 0, opacity: 1, scale: 1 }}
              exit={{ y: 16, opacity: 0, scale: 0.98 }}
              transition={{ type: 'spring', stiffness: 460, damping: 34 }}
              className="relative m-3 w-full max-w-sm rounded-2xl border border-ink-200 bg-white p-6
                         shadow-xl dark:border-ink-700 dark:bg-ink-900 sm:m-0"
            >
              <h3 className="text-[17px] font-semibold leading-snug">{active.name}</h3>
              <dl className="mt-4 space-y-2.5 text-[13px]">
                <Row label={t('schedule.weeks')} value={active.week_label} />
                <Row label={t('schedule.period')} value={active.section_label} />
                {active.teacher && <Row label={t('schedule.teacher')} value={active.teacher} />}
                {active.location && <Row label={t('schedule.location')} value={active.location} />}
                {active.code && <Row label="Code" value={active.code} />}
                {active.class_name && <Row label="Class" value={active.class_name} />}
              </dl>
              <button onClick={() => setActive(null)} className="btn-ghost mt-6 w-full">
                {lang === 'zh' ? '关闭' : lang === 'ru' ? 'Закрыть' : 'Close'}
              </button>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="shrink-0 muted">{label}</dt>
      <dd className="truncate text-right font-medium tnum">{value}</dd>
    </div>
  )
}

/**
 * Shimmering placeholder shown while the first schedule fetch is in flight.
 * Mirrors the real grid so the swap-in feels seamless rather than jarring.
 */
export function TimetableSkeleton() {
  const days = 7
  const rows = 11
  return (
    <div className="card overflow-hidden">
      <div className="overflow-x-auto">
        <div
          className="grid min-w-[720px] grid-cols-[64px_repeat(7,minmax(0,1fr))]"
          style={{ gridAutoRows: 'minmax(56px, auto)' }}
        >
          <div className="border-b border-r hairline bg-ink-50 dark:bg-ink-900" />
          {Array.from({ length: days }).map((_, i) => (
            <div key={`h-${i}`} className="border-b hairline bg-ink-50 dark:bg-ink-900" />
          ))}
          {Array.from({ length: rows }).map((_, r) => (
            <Fragment key={`r-${r}`}>
              <div className="border-b border-r hairline" />
              {Array.from({ length: days }).map((_, c) => {
                const filled = (r * 3 + c * 5) % 4 === 0
                return (
                  <div key={`c-${r}-${c}`} className="border-b border-r hairline p-1.5">
                    {filled && (
                      <div
                        className="h-11 rounded-lg animate-shimmer"
                        style={{
                          background:
                            'linear-gradient(90deg, rgba(120,120,120,0.05) 25%, rgba(120,120,120,0.14) 37%, rgba(120,120,120,0.05) 63%)',
                          backgroundSize: '400% 100%',
                        }}
                      />
                    )}
                  </div>
                )
              })}
            </Fragment>
          ))}
        </div>
      </div>
    </div>
  )
}
