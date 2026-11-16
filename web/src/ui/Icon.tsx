import type { IconName } from './types'

/**
 * A3 icons. Drawn for this mockup (outline, 24×24, no fill),
 * they live right in the code; there are no external icon sets or fonts.
 * License: the same as the project's (original work).
 */
const PATHS: Record<IconName, string> = {
  desk: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z',
  agent: 'M7 8h10v10H7zM12 4v4M10 13h.01M14 13h.01',
  log: 'M5 5h14M5 10h14M5 15h9M5 20h6',
  folder: 'M3 7h6l2 2h10v10H3z',
  shield: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z',
  users:
    'M9 11a3 3 0 100-6 3 3 0 000 6zM3 20c0-3 2.7-5 6-5s6 2 6 5M17 8a3 3 0 010 6M21 20c0-2.5-1.6-4.2-4-4.8',
  system:
    'M12 9a3 3 0 100 6 3 3 0 000-6zM12 2v3M12 19v3M4.9 4.9L7 7M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1L7 17M17 7l2.1-2.1',
  node: 'M4 5h16v6H4zM4 13h16v6H4zM8 8h.01M8 16h.01',
  tasks: 'M9 6h11M9 12h11M9 18h11M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2',
  search: 'M11 4a7 7 0 100 14 7 7 0 000-14zM20 20l-4-4',
  check: 'M5 12l4 4 10-10',
  close: 'M6 6l12 12M18 6L6 18',
  more: 'M5 12h.01M12 12h.01M19 12h.01',
  warn: 'M12 4l9 16H3zM12 10v4M12 17h.01',
  grip: 'M9 6h.01M15 6h.01M9 12h.01M15 12h.01M9 18h.01M15 18h.01',
  resize: 'M20 10v10H10M20 15v5h-5',
  file: 'M6 3h8l4 4v14H6zM14 3v4h4',
  tool: 'M14 6a4 4 0 00-5 5l-5 5 3 3 5-5a4 4 0 005-5l-2 2-3-3z',
  stop: 'M7 7h10v10H7z',
  send: 'M4 12l16-8-6 16-2-6z',
  key: 'M15 4a5 5 0 100 10 5 5 0 000-10zM11.5 12.5L4 20M7 17l2 2M5 19l2 2',
}

export interface IconProps {
  name: IconName
  /** Size comes from tokens: md = --size-icon, sm = --size-icon-sm. */
  size?: 'sm' | 'md'
  /** If set, the icon becomes meaningful and is read by screen readers. */
  label?: string
  className?: string
}

export function Icon({ name, size = 'md', label, className }: IconProps) {
  const cls = ['a3-icon', size === 'sm' ? 'a3-icon--sm' : '', className ?? ''].filter(Boolean).join(' ')
  return (
    <svg
      className={cls}
      viewBox="0 0 24 24"
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
    >
      <path d={PATHS[name]} />
    </svg>
  )
}
