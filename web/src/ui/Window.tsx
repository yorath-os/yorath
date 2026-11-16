import { useId, type ReactNode } from 'react'
import { Icon } from './Icon'

export interface WindowProps {
  /**
   * widget — a desk widget: short header, filter, "⋯" menu.
   * dialog — a window over the desk (setup wizard): header with a close cross, stronger shadow.
   * app — an app window over the whole desk (task manager, agent check).
   */
  variant?: 'widget' | 'dialog' | 'app'
  title: ReactNode
  /** Heading level in the document. h2 by default. */
  headingLevel?: 2 | 3
  /** Filter label in the widget header, e.g. "24 hours". */
  filterLabel?: string
  /** The widget's "⋯" menu. If not set, there's no button. */
  onMenu?: () => void
  /** Close cross (for dialog and app). */
  onClose?: () => void
  /** Extra elements in the header to the right of the title (segments, filters). */
  toolbar?: ReactNode
  /** Window footer ("Back/Next" buttons). */
  footer?: ReactNode
  /** Desk grid: how many of 12 columns the widget takes. */
  span?: 3 | 4 | 5 | 6 | 7 | 8 | 12
  /** Remove the body's inner padding (for full-width tables and layouts). */
  flush?: boolean
  /** The body is busy: aria-busy. */
  busy?: boolean
  className?: string
  children: ReactNode
}

export function Window({
  variant = 'widget',
  title,
  headingLevel = 2,
  filterLabel,
  onMenu,
  onClose,
  toolbar,
  footer,
  span,
  flush,
  busy,
  className,
  children,
}: WindowProps) {
  const id = useId()
  const H = headingLevel === 3 ? 'h3' : 'h2'
  const titleText = typeof title === 'string' ? title : undefined
  const cls = ['a3-window', `a3-window--${variant}`, span ? `a3-span-${span}` : '', className ?? '']
    .filter(Boolean)
    .join(' ')
  return (
    <section className={cls} aria-labelledby={id} aria-busy={busy || undefined}>
      <div className="a3-window__bar">
        <H id={id} className="a3-window__title">
          {title}
        </H>
        {filterLabel && <span className="a3-window__filter">{filterLabel}</span>}
        {toolbar && <div className="a3-window__toolbar">{toolbar}</div>}
        <span className="a3-window__spacer" />
        {onMenu && (
          <button
            type="button"
            className="a3-icon-button"
            onClick={onMenu}
            aria-label={`Configure widget "${titleText ?? ''}"`}
          >
            <Icon name="more" />
          </button>
        )}
        {onClose && (
          <button
            type="button"
            className="a3-icon-button a3-icon-button--lg"
            onClick={onClose}
            aria-label="Close window"
          >
            <Icon name="close" />
          </button>
        )}
      </div>
      <div className={`a3-window__body${flush ? ' a3-window__body--flush' : ''}`}>{children}</div>
      {footer && <div className="a3-window__footer">{footer}</div>}
    </section>
  )
}
