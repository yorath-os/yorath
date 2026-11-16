import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { Icon } from './Icon'
import type { IconName } from './types'

/* ───────────────────────── Button ───────────────────────── */

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /**
   * primary — the window's main action (blue).
   * secondary — a regular button in a window.
   * danger — stop, delete.
   * success — the final "Enable agent".
   * chrome — a button on the dark desk ("Profile", "Cancel" in customize mode).
   * chrome-primary — the main button on the desk ("Customize", "Save profile").
   * link — a text link button.
   */
  variant?: 'primary' | 'secondary' | 'danger' | 'success' | 'chrome' | 'chrome-primary' | 'link'
  size?: 'md' | 'sm'
  icon?: IconName
  /** Shows "Working…" and disables the button. */
  loading?: boolean
}

export function Button({
  variant = 'secondary',
  size = 'md',
  icon,
  loading,
  children,
  className,
  disabled,
  type = 'button',
  ...rest
}: ButtonProps) {
  const cls = ['a3-button', `a3-button--${variant}`, size === 'sm' ? 'a3-button--sm' : '', className ?? '']
    .filter(Boolean)
    .join(' ')
  return (
    <button
      type={type}
      className={cls}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {icon && <Icon name={icon} size="sm" />}
      {loading ? 'Working…' : children}
    </button>
  )
}

/* ───────────────────────── SegmentedControl ───────────────────────── */

export interface SegmentOption<T extends string> {
  value: T
  label: string
  /** Number in the label: "In progress · 12". */
  count?: number
}

export interface SegmentedControlProps<T extends string> {
  /** Group label for screen readers. */
  label: string
  options: SegmentOption<T>[]
  value: T
  onChange: (value: T) => void
  /** dark — the selected segment is filled with the text color; confidential — with the Confidential class color. */
  tone?: 'dark' | 'confidential'
}

export function SegmentedControl<T extends string>({
  label,
  options,
  value,
  onChange,
  tone = 'dark',
}: SegmentedControlProps<T>) {
  return (
    <div className={`a3-segmented a3-segmented--${tone}`} role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={o.value === value}
          className={o.value === value ? 'is-active' : undefined}
          onClick={() => onChange(o.value)}
        >
          {o.label}
          {o.count !== undefined && <> · {o.count}</>}
        </button>
      ))}
    </div>
  )
}

/* ───────────────────────── FilterChip ───────────────────────── */

export interface FilterChipProps {
  /** Filter name, e.g. "Agent". */
  name: string
  /** Current value, e.g. "all". */
  value: string
  onClick?: () => void
}

/** Filter button "Agent: all". Opens a value picker (the menu is up to the project). */
export function FilterChip({ name, value, onClick }: FilterChipProps) {
  return (
    <button type="button" className="a3-filter-chip" onClick={onClick} aria-haspopup="listbox">
      <span className="a3-filter-chip__name">{name}</span>
      <span className="a3-truncate">{value}</span>
    </button>
  )
}

/* ───────────────────────── Chip ───────────────────────── */

export interface ChipProps {
  children: ReactNode
  /** For adding — a dashed button. */
  onClick?: () => void
  variant?: 'default' | 'add'
}

export function Chip({ children, onClick, variant = 'default' }: ChipProps) {
  if (variant === 'add' || onClick) {
    return (
      <button
        type="button"
        className={`a3-chip${variant === 'add' ? ' a3-chip--add' : ''}`}
        onClick={onClick}
      >
        {children}
      </button>
    )
  }
  return <span className="a3-chip">{children}</span>
}
