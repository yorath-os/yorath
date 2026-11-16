import type { ReactNode } from 'react'
import { Icon } from './Icon'
import { Window } from './Window'

/* ───────────────────────── Stepper ───────────────────────── */

export interface StepperProps {
  steps: string[]
  /** Current step number, starting at 1. */
  current: number
}

export function Stepper({ steps, current }: StepperProps) {
  return (
    <ol className="a3-stepper" aria-label="Setup steps">
      {steps.map((label, i) => {
        const n = i + 1
        const state = n < current ? 'done' : n === current ? 'current' : 'next'
        return (
          <li
            key={label}
            className={`a3-stepper__step a3-stepper__step--${state}`}
            aria-current={state === 'current' ? 'step' : undefined}
          >
            <span className="a3-stepper__marker" aria-hidden="true">
              {state === 'done' ? <Icon name="check" size="sm" /> : n}
            </span>
            <span className="a3-stepper__label">{label}</span>
            {state === 'done' && <span className="a3-visually-hidden"> (done)</span>}
          </li>
        )
      })}
    </ol>
  )
}

/* ───────────────────────── WizardWindow ───────────────────────── */

export interface WizardWindowProps {
  /** "Agent install" or "Agent install · contract-review". */
  title: string
  steps: string[]
  current: number
  /** Step title (h3 inside the window). */
  heading: string
  lead?: string
  /** Label on the left of the footer, e.g. "Step 2 of 5 · one warning". */
  footerNote?: string
  /** Footer buttons. */
  actions: ReactNode
  onClose?: () => void
  busy?: boolean
  children: ReactNode
}

/** Wizard window over the desk: steps on the left, content on the right, buttons at the bottom. */
export function WizardWindow({
  title,
  steps,
  current,
  heading,
  lead,
  footerNote,
  actions,
  onClose,
  busy,
  children,
}: WizardWindowProps) {
  return (
    <div className="a3-wizard-host">
      <Window
        variant="dialog"
        title={title}
        onClose={onClose}
        busy={busy}
        flush
        footer={
          <>
            <span className="a3-window__footer-note">
              {footerNote ?? `Step ${current} of ${steps.length}`}
            </span>
            <span className="a3-window__spacer" />
            {actions}
          </>
        }
      >
        <div className="a3-wizard">
          <Stepper steps={steps} current={current} />
          <div className="a3-wizard__main">
            <h3 className="a3-wizard__heading">{heading}</h3>
            {lead && <p className="a3-wizard__lead">{lead}</p>}
            {children}
          </div>
        </div>
      </Window>
    </div>
  )
}

/* ───────────────────────── OptionCard ───────────────────────── */

export interface OptionCardProps {
  name: string
  value: string
  checked: boolean
  onChange: (value: string) => void
  title: string
  description: string
  disabled?: boolean
  /** "Later" mark next to the title. */
  badge?: string
}

export function OptionCard({
  name,
  value,
  checked,
  onChange,
  title,
  description,
  disabled,
  badge,
}: OptionCardProps) {
  return (
    <label className={`a3-option${checked ? ' is-checked' : ''}${disabled ? ' is-disabled' : ''}`}>
      <input
        type="radio"
        name={name}
        value={value}
        checked={checked}
        disabled={disabled}
        onChange={() => onChange(value)}
      />
      <span>
        <b className="a3-option__title">
          {title}
          {badge && <span className="a3-option__badge">{badge}</span>}
        </b>
        <small className="a3-option__text">{description}</small>
      </span>
    </label>
  )
}

/* ───────────────────────── Card ───────────────────────── */

export interface CardProps {
  title?: string
  /** Mark after the title, e.g. "new". */
  titleNote?: string
  /** Tag after the title (e.g. a "passed" StatusBadge). */
  titleExtra?: ReactNode
  /** wide — wider than the neighboring card in the row. */
  wide?: boolean
  children: ReactNode
}

/** White card inside a window. Cards in a row wrap on narrow screens. */
export function Card({ title, titleNote, titleExtra, wide, children }: CardProps) {
  return (
    <section className={`a3-card${wide ? ' a3-card--wide' : ''}`}>
      {title && (
        <h4 className="a3-card__title">
          {title}
          {titleNote && <span className="a3-card__note">{titleNote}</span>}
          {titleExtra}
        </h4>
      )}
      {children}
    </section>
  )
}

export function CardRow({ children }: { children: ReactNode }) {
  return <div className="a3-card-row">{children}</div>
}
