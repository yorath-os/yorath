import { useId, type KeyboardEvent, type ReactNode } from 'react'
import { Icon } from './Icon'

/* ───────────────────────── DeskHeader ───────────────────────── */

export interface DeskHeaderProps {
  title: string
  /** The second part of the title is lighter: "· Agent on-call". */
  subtitle?: string
  actions?: ReactNode
}

/** Title right on the desk (white text on a dark background). */
export function DeskHeader({ title, subtitle, actions }: DeskHeaderProps) {
  return (
    <div className="a3-desk-header">
      <h1 className="a3-desk-header__title">
        {title}
        {subtitle && <span className="a3-desk-header__sub"> · {subtitle}</span>}
      </h1>
      {actions && <div className="a3-desk-header__actions">{actions}</div>}
    </div>
  )
}

/* ───────────────────────── WidgetGrid ───────────────────────── */

/** Desk grid: 12 columns, widgets set their span. */
export function WidgetGrid({ children, label }: { children: ReactNode; label: string }) {
  return (
    <div className="a3-grid" aria-label={label} role="region">
      {children}
    </div>
  )
}

/* ───────────────────────── EditableWidget ───────────────────────── */

export interface EditableWidgetProps {
  id: string
  title: string
  span: 3 | 4 | 5 | 6 | 7 | 8 | 12
  /** Height in grid rows (64 px each). */
  rows: number
  /** The widget is being dragged. */
  dragging?: boolean
  /** Move by one position: -1 earlier, +1 later. Triggered by arrows on the handle. */
  onMove: (id: string, delta: -1 | 1) => void
  /** Change width by one step. Triggered by arrows on the corner. */
  onResize: (id: string, delta: -1 | 1) => void
  onRemove: (id: string) => void
  /** Mouse drag start (the DnD logic itself is in the project). */
  onDragStart?: (id: string) => void
}

/**
 * Widget in customize mode: dashed frame, handle, size, "remove", resize corner.
 * Keyboard: on the handle ←/→ move the widget; on the corner ←/→ change the width.
 */
export function EditableWidget({
  id,
  title,
  span,
  rows,
  dragging,
  onMove,
  onResize,
  onRemove,
  onDragStart,
}: EditableWidgetProps) {
  const hid = useId()
  const arrows = (fn: (d: -1 | 1) => void) => (e: KeyboardEvent) => {
    if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      e.preventDefault()
      fn(-1)
    }
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      e.preventDefault()
      fn(1)
    }
  }
  return (
    <section
      className={`a3-edit-widget a3-span-${span}${dragging ? ' is-dragging' : ''}`}
      style={{ gridRow: `span ${rows}` }}
      aria-labelledby={hid}
    >
      <div className="a3-edit-widget__bar">
        <button
          type="button"
          className="a3-edit-widget__grip"
          aria-label={`Move widget "${title}". Left and right arrows change the order`}
          draggable
          onDragStart={() => onDragStart?.(id)}
          onKeyDown={arrows((d) => onMove(id, d))}
        >
          <Icon name="grip" size="sm" />
        </button>
        <h2 id={hid} className="a3-edit-widget__title">
          {title}
        </h2>
        <span className="a3-edit-widget__size a3-mono">
          {span}×{rows}
        </span>
        <button
          type="button"
          className="a3-icon-button a3-icon-button--danger"
          aria-label={`Remove widget "${title}"`}
          onClick={() => onRemove(id)}
        >
          <Icon name="close" />
        </button>
      </div>
      <div className="a3-edit-widget__skeleton" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
      <div className="a3-edit-widget__foot">
        <button
          type="button"
          className="a3-edit-widget__resize"
          aria-label={`Width of widget "${title}": ${span} of 12. Arrows change the width`}
          onKeyDown={arrows((d) => onResize(id, d))}
        >
          <Icon name="resize" size="sm" />
        </button>
      </div>
    </section>
  )
}

/** Empty "drop here" cell. */
export function DropSlot({ text = 'Drag a widget here from the catalog' }: { text?: string }) {
  return <div className="a3-drop-slot">{text}</div>
}

/* ───────────────────────── ProfilePicker ───────────────────────── */

export interface DeskProfile {
  id: string
  name: string
  description: string
}

export interface ProfilePickerProps {
  profiles: DeskProfile[]
  value: string
  onChange: (id: string) => void
}

export function ProfilePicker({ profiles, value, onChange }: ProfilePickerProps) {
  return (
    <fieldset className="a3-profiles">
      <legend className="a3-section-label">Profile</legend>
      {profiles.map((p) => (
        <label key={p.id} className={`a3-profiles__item${p.id === value ? ' is-checked' : ''}`}>
          <input
            type="radio"
            name="desk-profile"
            value={p.id}
            checked={p.id === value}
            onChange={() => onChange(p.id)}
          />
          <span>
            <b>{p.name}</b>
            <small>{p.description}</small>
          </span>
        </label>
      ))}
    </fieldset>
  )
}

/* ───────────────────────── WidgetCatalog ───────────────────────── */

export interface CatalogWidget {
  id: string
  name: string
  description: string
  /** Already on the desk — the button is disabled, labeled "On desk". */
  placed: boolean
  category: string
}

export interface WidgetCatalogProps {
  widgets: CatalogWidget[]
  categories: string[]
  category: string
  onCategoryChange: (c: string) => void
  query: string
  onQueryChange: (q: string) => void
  onAdd: (id: string) => void
}

export function WidgetCatalog({
  widgets,
  categories,
  category,
  onCategoryChange,
  query,
  onQueryChange,
  onAdd,
}: WidgetCatalogProps) {
  const sid = useId()
  return (
    <section className="a3-catalog" aria-labelledby={`${sid}-h`}>
      <h3 id={`${sid}-h`} className="a3-section-label">
        Widget catalog
      </h3>
      <label htmlFor={sid} className="a3-visually-hidden">
        Find a widget
      </label>
      <input
        id={sid}
        className="a3-input"
        type="search"
        placeholder="Find a widget"
        value={query}
        onChange={(e) => onQueryChange(e.target.value)}
      />
      <div className="a3-catalog__cats" role="group" aria-label="Widget categories">
        {categories.map((c) => (
          <button
            key={c}
            type="button"
            className={`a3-cat${c === category ? ' is-active' : ''}`}
            aria-pressed={c === category}
            onClick={() => onCategoryChange(c)}
          >
            {c}
          </button>
        ))}
      </div>
      {widgets.length === 0 ? (
        <p className="a3-catalog__empty">Nothing found. Try another word or the "All" category.</p>
      ) : (
        <ul className="a3-catalog__list">
          {widgets.map((w) => (
            <li key={w.id}>
              <span className="a3-catalog__text">
                <b>{w.name}</b>
                <small>{w.description}</small>
              </span>
              <button
                type="button"
                className={`a3-button a3-button--sm ${w.placed ? 'a3-button--secondary' : 'a3-button--outline'}`}
                disabled={w.placed}
                onClick={() => onAdd(w.id)}
                aria-label={w.placed ? `${w.name}: already on the desk` : `Add widget "${w.name}"`}
              >
                {w.placed ? 'On desk' : 'Add'}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
