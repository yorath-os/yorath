import type { ReactNode } from 'react'
import { Icon } from './Icon'
import type { Tone } from './types'

/* ───────────────────────── Notice ───────────────────────── */

export interface NoticeProps {
  /** ok — "running in the background"; info — neutral hint; warn; error; edit — customize-mode banner on the desk. */
  tone: 'ok' | 'info' | 'warn' | 'error' | 'edit'
  children: ReactNode
  /** Buttons on the right (for tone="edit"). */
  actions?: ReactNode
}

export function Notice({ tone, children, actions }: NoticeProps) {
  const role = tone === 'error' ? 'alert' : undefined
  return (
    <div className={`a3-notice a3-notice--${tone}`} role={role}>
      {(tone === 'ok' || tone === 'warn' || tone === 'error') && (
        <span className="a3-dot" aria-hidden="true" />
      )}
      <div className="a3-notice__text">{children}</div>
      {actions && <div className="a3-notice__actions">{actions}</div>}
    </div>
  )
}

/* ───────────────────────── KeyValueList ───────────────────────── */

export interface KeyValue {
  key: string
  value: ReactNode
  /** Small explanatory text under the value. */
  hint?: string
  mono?: boolean
}

export interface KeyValueListProps {
  items: KeyValue[]
  /** columns — key on the left (cards); rows — lines with dividers (the "After enabling" summary). */
  layout?: 'columns' | 'rows'
}

export function KeyValueList({ items, layout = 'columns' }: KeyValueListProps) {
  return (
    <dl className={`a3-kv a3-kv--${layout}`}>
      {items.map((it) => (
        <div className="a3-kv__row" key={it.key}>
          <dt>{it.key}</dt>
          <dd className={it.mono ? 'a3-mono' : undefined}>
            {it.value}
            {it.hint && <small>{it.hint}</small>}
          </dd>
        </div>
      ))}
    </dl>
  )
}

/* ───────────────────────── Timeline ───────────────────────── */

export interface TimelineStep {
  id: string
  text: string
  /** Time or duration on the right: "13:46", "0:40", "now", "~15 min". */
  time?: string
  state: 'done' | 'current' | 'next' | 'failed'
}

export interface TimelineProps {
  steps: TimelineStep[]
  label: string
}

export function Timeline({ steps, label }: TimelineProps) {
  return (
    <ol className="a3-timeline" aria-label={label}>
      {steps.map((s) => (
        <li
          key={s.id}
          className={`a3-timeline__step a3-timeline__step--${s.state}`}
          aria-current={s.state === 'current' ? 'step' : undefined}
        >
          <span className="a3-timeline__marker" aria-hidden="true" />
          <span className="a3-timeline__text">
            {s.text}
            {s.state === 'failed' && <span className="a3-visually-hidden"> (error)</span>}
          </span>
          {s.time && <span className="a3-timeline__time">{s.time}</span>}
        </li>
      ))}
    </ol>
  )
}

/* ───────────────────────── LogList ───────────────────────── */

export interface LogEntry {
  id: string
  time: string
  /** TIMEOUT, FAILED, CONFIG, DENY, OK. */
  level: string
  tone: Tone
  text: string
}

export function LogList({ entries, label }: { entries: LogEntry[]; label: string }) {
  return (
    <ul className="a3-log" aria-label={label}>
      {entries.map((e) => (
        <li key={e.id}>
          <time className="a3-log__time">{e.time}</time>{' '}
          <span className={`a3-log__level a3-tone-text--${e.tone}`}>{e.level}</span>{' '}
          <span className="a3-log__text">{e.text}</span>
        </li>
      ))}
    </ul>
  )
}

/* ───────────────────────── CheckList ───────────────────────── */

export interface CheckItem {
  id: string
  text: string
  hint?: string
  result: 'ok' | 'warn' | 'error' | 'pending'
}

const CHECK_SR: Record<CheckItem['result'], string> = {
  ok: 'passed',
  warn: 'warning',
  error: 'error',
  pending: 'checking',
}

export function CheckList({ items, label }: { items: CheckItem[]; label: string }) {
  return (
    <ul className="a3-checks" aria-label={label}>
      {items.map((c) => (
        <li key={c.id} className="a3-checks__item">
          <span className={`a3-checks__mark a3-checks__mark--${c.result}`} aria-hidden="true">
            {c.result === 'ok' ? <Icon name="check" size="sm" /> : c.result === 'pending' ? null : '!'}
          </span>
          <span>
            <span className="a3-visually-hidden">{CHECK_SR[c.result]}: </span>
            {c.text}
            {c.hint && <small>{c.hint}</small>}
          </span>
        </li>
      ))}
    </ul>
  )
}

/* ───────────────────────── BarList ───────────────────────── */

export interface BarListItem {
  id: string
  label: string
  value: number
  /** Label under the bar, e.g. "timeout ×3 · failed ×1". */
  hint?: string
}

/** Horizontal "errors by agent" bars. Scaled to the maximum value. */
export function BarList({ items, label }: { items: BarListItem[]; label: string }) {
  const max = Math.max(1, ...items.map((i) => i.value))
  return (
    <ul className="a3-bars" aria-label={label}>
      {items.map((i) => (
        <li key={i.id} className="a3-bars__item">
          <span className="a3-bars__label a3-mono a3-truncate" title={i.label}>
            {i.label}
          </span>
          <span className="a3-bars__track" aria-hidden="true">
            <span className="a3-bars__fill" style={{ inlineSize: `${(i.value / max) * 100}%` }} />
          </span>
          <b className="a3-bars__value">{i.value}</b>
          {i.hint && <span className="a3-bars__hint">{i.hint}</span>}
        </li>
      ))}
    </ul>
  )
}

/* ───────────────────────── RankedList ───────────────────────── */

export interface RankedItem {
  id: string
  label: string
  value: string | number
}

/** "Reason — count" rows (top denial reasons). */
export function RankedList({ items, label }: { items: RankedItem[]; label: string }) {
  return (
    <ul className="a3-ranked" aria-label={label}>
      {items.map((i) => (
        <li key={i.id}>
          <span>{i.label}</span>
          <b>{i.value}</b>
        </li>
      ))}
    </ul>
  )
}

/* ───────────────────────── CodeBlock ───────────────────────── */

export interface CodeBlockProps {
  /** "key: value" pairs of the md file header. Keys are highlighted. */
  lines: { key: string; value: string }[]
  label: string
}

export function CodeBlock({ lines, label }: CodeBlockProps) {
  return (
    <pre className="a3-code" aria-label={label} tabIndex={0}>
      <code>
        {lines.map((l, i) => (
          <span key={`${l.key}-${i}`}>
            <span className="a3-code__key">{l.key}</span>: {l.value}
            {'\n'}
          </span>
        ))}
      </code>
    </pre>
  )
}
