import type { DataClass, Tone } from './types'

/* ───────────────────────── StatusBadge ───────────────────────── */

export interface StatusBadgeProps {
  tone: Tone
  label: string
  /** dot — dot and text (default); pill — filled tag (allow/deny, "passed"). */
  variant?: 'dot' | 'pill'
}

export function StatusBadge({ tone, label, variant = 'dot' }: StatusBadgeProps) {
  if (variant === 'pill') {
    return <span className={`a3-pill a3-pill--${tone}`}>{label}</span>
  }
  return (
    <span className={`a3-status a3-status--${tone}`}>
      <span className="a3-dot" aria-hidden="true" />
      {label}
    </span>
  )
}

/* ───────────────────────── DataClassBadge ───────────────────────── */

const CLASS_LABEL: Record<DataClass, string> = {
  public: 'Public',
  internal: 'Internal',
  confidential: 'Confidential',
  restricted: 'Restricted',
}

export interface DataClassBadgeProps {
  dataClass: DataClass
}

/** Data class. Labels are in English on purpose: that's how classes are named in policies and the journal. */
export function DataClassBadge({ dataClass }: DataClassBadgeProps) {
  return (
    <span className={`a3-class a3-class--${dataClass}`} title={`Data class: ${CLASS_LABEL[dataClass]}`}>
      {CLASS_LABEL[dataClass]}
    </span>
  )
}

/* ───────────────────────── ProgressBar ───────────────────────── */

export interface ProgressBarProps {
  /** 0–100. */
  value: number
  /** Label for screen readers, e.g. "Task progress". */
  label: string
  /** Show the percentage on the right. */
  showValue?: boolean
  tone?: 'accent' | 'ok'
  size?: 'sm' | 'md'
}

export function ProgressBar({
  value,
  label,
  showValue = true,
  tone = 'accent',
  size = 'sm',
}: ProgressBarProps) {
  const v = clamp(value)
  return (
    <span className={`a3-progress a3-progress--${size}`}>
      <span
        className="a3-track"
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={v}
      >
        <span className={`a3-track__fill a3-track__fill--${tone}`} style={{ inlineSize: `${v}%` }} />
      </span>
      {showValue && <span className="a3-mono a3-text-xs">{v}%</span>}
    </span>
  )
}

/* ───────────────────────── MeterRow ───────────────────────── */

export interface MeterRowProps {
  /** «CPU», «RAM», «GPU», «VRAM», «Ollama». */
  label: string
  /** 0–100. */
  percent: number
  /** Text on the right, e.g. "6.3 / 7 GB". If not set — percentages. */
  valueText?: string
  /** Warning — orange fill (the project sets the threshold). */
  tone?: 'accent' | 'warn'
}

export function MeterRow({ label, percent, valueText, tone = 'accent' }: MeterRowProps) {
  const v = clamp(percent)
  return (
    <div className="a3-meter">
      <span className="a3-meter__label">{label}</span>
      <span
        className="a3-track"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={v}
        aria-valuetext={valueText ?? `${v}%`}
      >
        <span className={`a3-track__fill a3-track__fill--${tone}`} style={{ inlineSize: `${v}%` }} />
      </span>
      <span className="a3-meter__value a3-mono">{valueText ?? `${v}%`}</span>
    </div>
  )
}

/* ───────────────────────── Stat ───────────────────────── */

export interface StatProps {
  value: string | number
  caption: string
  tone?: 'default' | 'ok' | 'warn' | 'error'
}

export function Stat({ value, caption, tone = 'default' }: StatProps) {
  return (
    <p className="a3-stat">
      <b className={`a3-stat__value a3-stat__value--${tone}`}>{value}</b>
      <span className="a3-stat__caption">{caption}</span>
    </p>
  )
}

/* ───────────────────────── SplitBar ───────────────────────── */

export interface SplitBarProps {
  allowed: number
  denied: number
}

/** Share of allowed and denied policy decisions. */
export function SplitBar({ allowed, denied }: SplitBarProps) {
  const total = allowed + denied
  const a = total ? (allowed / total) * 100 : 0
  return (
    <span className="a3-split" role="img" aria-label={`Allowed ${allowed}, denied ${denied}`}>
      {/* no decisions — an empty bar, not a red one */}
      {total > 0 && (
        <>
          <span className="a3-split__ok" style={{ inlineSize: `${a}%` }} />
          <span className="a3-split__error" style={{ inlineSize: `${100 - a}%` }} />
        </>
      )}
    </span>
  )
}

function clamp(n: number) {
  if (Number.isNaN(n)) return 0
  return Math.max(0, Math.min(100, Math.round(n)))
}
