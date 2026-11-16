import type { ReactNode } from 'react'
import { Button } from './Controls'
import { Icon } from './Icon'
import type { ScreenState } from './types'
import { PRODUCT } from '../brand'

export interface StateViewProps {
  kind: 'loading' | 'empty' | 'error' | 'forbidden'
  /** Title. Each kind has a default text. */
  title?: string
  /** Explanation under the title. */
  description?: string
  /** Action button (e.g. "Retry" or "Install agent"). */
  actionLabel?: string
  onAction?: () => void
  /** dark — on the desk; light — inside a window. */
  surface?: 'light' | 'dark'
  /** For loading: how many placeholder rows to draw. */
  skeletonRows?: number
}

const DEFAULTS = {
  loading: { title: 'Loading data…', description: '' },
  empty: { title: 'Nothing here yet', description: '' },
  error: {
    title: 'Could not load data',
    description: `Check that ${PRODUCT} core is running and try again.`,
  },
  forbidden: { title: 'No access', description: 'This section is available to administrators.' },
} as const

/** One look for empty screens, loading, errors and access denial. */
export function StateView({
  kind,
  title,
  description,
  actionLabel,
  onAction,
  surface = 'light',
  skeletonRows = 3,
}: StateViewProps) {
  const t = title ?? DEFAULTS[kind].title
  const d = description ?? DEFAULTS[kind].description

  if (kind === 'loading') {
    return (
      <div className={`a3-state a3-state--${surface}`} role="status" aria-live="polite" aria-busy="true">
        <span className="a3-visually-hidden">{t}</span>
        <div className="a3-skeleton" aria-hidden="true">
          {Array.from({ length: skeletonRows }, (_, i) => (
            <span key={i} className="a3-skeleton__line" />
          ))}
        </div>
      </div>
    )
  }

  const role = kind === 'error' ? 'alert' : 'status'
  return (
    <div className={`a3-state a3-state--${surface} a3-state--${kind}`} role={role}>
      {(kind === 'error' || kind === 'forbidden') && (
        <span className="a3-state__icon">
          <Icon name={kind === 'error' ? 'warn' : 'shield'} />
        </span>
      )}
      <p className="a3-state__title">{t}</p>
      {d && <p className="a3-state__text">{d}</p>}
      {actionLabel && onAction && (
        <Button variant={surface === 'dark' ? 'chrome-primary' : 'primary'} onClick={onAction}>
          {actionLabel}
        </Button>
      )}
    </div>
  )
}

export interface ScreenStateGateProps {
  state: ScreenState
  /** Text for this screen's empty state. */
  emptyTitle: string
  emptyDescription?: string
  emptyActionLabel?: string
  onEmptyAction?: () => void
  surface?: 'light' | 'dark'
  skeletonRows?: number
  children: ReactNode
}

/** Renders children only in the ready state, otherwise the matching StateView. */
export function ScreenStateGate({
  state,
  emptyTitle,
  emptyDescription,
  emptyActionLabel,
  onEmptyAction,
  surface = 'light',
  skeletonRows,
  children,
}: ScreenStateGateProps) {
  switch (state.kind) {
    case 'ready':
      return <>{children}</>
    case 'loading':
      return <StateView kind="loading" surface={surface} skeletonRows={skeletonRows} />
    case 'empty':
      return (
        <StateView
          kind="empty"
          surface={surface}
          title={emptyTitle}
          description={emptyDescription}
          actionLabel={emptyActionLabel}
          onAction={onEmptyAction}
        />
      )
    case 'error':
      return (
        <StateView
          kind="error"
          surface={surface}
          description={state.message}
          actionLabel={state.onRetry ? 'Retry' : undefined}
          onAction={state.onRetry}
        />
      )
    case 'forbidden':
      return <StateView kind="forbidden" surface={surface} description={state.reason} />
  }
}
