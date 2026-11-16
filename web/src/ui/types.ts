/**
 * Shared A3 design types. These are view models, not API schemas:
 * the project maps API answers into these shapes and passes them via props.
 */

/** Status color tone. */
export type Tone = 'ok' | 'warn' | 'error' | 'info' | 'neutral'

/** Data class from §7 of the MVP requirements. */
export type DataClass = 'public' | 'internal' | 'confidential' | 'restricted'

/**
 * Screen state. Every A3 screen takes it in the `state` prop
 * and draws loading, empty, error and access denial itself.
 */
export type ScreenState =
  | { kind: 'loading' }
  | { kind: 'ready' }
  | { kind: 'empty' }
  | { kind: 'error'; message: string; onRetry?: () => void }
  | { kind: 'forbidden'; reason: string }

/** Left menu item. */
export interface NavItem {
  id: string
  label: string
  icon: IconName
  /** Number to the right of the item (e.g. the number of agents). */
  count?: number | string
  /** Sub-items; shown expanded. */
  children?: NavSubItem[]
}

export interface NavSubItem {
  id: string
  label: string
  count?: number | string
}

export type IconName =
  | 'desk'
  | 'agent'
  | 'log'
  | 'folder'
  | 'shield'
  | 'users'
  | 'system'
  | 'node'
  | 'tasks'
  | 'search'
  | 'check'
  | 'close'
  | 'more'
  | 'warn'
  | 'grip'
  | 'resize'
  | 'file'
  | 'tool'
  | 'stop'
  | 'send'
  | 'key'
