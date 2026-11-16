import type { ReactNode } from 'react'
import { Icon } from './Icon'
import type { NavItem } from './types'
import { PRODUCT, WORDMARK } from '../brand'

/* ───────────────────────── MenuBar ───────────────────────── */

export interface MenuBarProps {
  /** Current section name, e.g. "Desk" or "Agents · Tasks". */
  section: string
  /** Number of tasks running in the background. */
  backgroundCount?: number
  onBackgroundClick?: () => void
  /** Unread notifications. 0 or undefined hides the badge. */
  notificationCount?: number
  onNotificationsClick?: () => void
  /** Cluster label, e.g. "3 nodes". */
  clusterLabel?: string
  /** Already formatted date and time, e.g. "Wed Nov 4 14:08". */
  clock: string
  userName: string
  userRole: string
  onUserClick?: () => void
  searchValue?: string
  onSearchChange?: (value: string) => void
  onSearchSubmit?: (value: string) => void
}

export function MenuBar(props: MenuBarProps) {
  const {
    section,
    backgroundCount,
    onBackgroundClick,
    notificationCount,
    onNotificationsClick,
    clusterLabel,
    clock,
    userName,
    userRole,
    onUserClick,
    searchValue,
    onSearchChange,
    onSearchSubmit,
  } = props
  return (
    <header className="a3-menubar">
      <span className="a3-menubar__logo" aria-label={PRODUCT}>
        <svg
          className="a3-menubar__mark"
          viewBox="0 0 64 64"
          fill="none"
          aria-hidden="true"
          focusable="false"
        >
          <path
            d="M32 54V35M32 35L15 13M32 35L49 13"
            stroke="currentColor"
            strokeWidth="7"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <circle cx="15" cy="13" r="7.5" fill="currentColor" />
          <circle cx="49" cy="13" r="7.5" fill="currentColor" />
          <circle cx="32" cy="54" r="7.5" fill="currentColor" />
        </svg>
        <span aria-hidden="true">{WORDMARK}</span>
      </span>
      <span className="a3-menubar__section" aria-current="page">
        {section}
      </span>
      {onSearchSubmit ? (
        <form
          className="a3-menubar__search"
          role="search"
          onSubmit={(e) => {
            e.preventDefault()
            onSearchSubmit?.(searchValue ?? '')
          }}
        >
          <label className="a3-visually-hidden" htmlFor="a3-global-search">
            Global search
          </label>
          <input
            id="a3-global-search"
            type="search"
            placeholder="Find a task, agent, document or event"
            aria-keyshortcuts="Meta+K Control+K"
            value={searchValue}
            readOnly={searchValue !== undefined && !onSearchChange}
            onChange={(e) => onSearchChange?.(e.target.value)}
          />
        </form>
      ) : (
        <span className="a3-menubar__search" />
      )}
      <div className="a3-menubar__items">
        {backgroundCount !== undefined && (
          <button type="button" className="a3-menubar__item" onClick={onBackgroundClick}>
            <span className="a3-dot a3-dot--live" aria-hidden="true" />
            <b>{backgroundCount}</b>&nbsp;in background
          </button>
        )}
        {onNotificationsClick && (
          <button
            type="button"
            className="a3-menubar__item a3-menubar__item--secondary"
            onClick={onNotificationsClick}
            aria-label={notificationCount ? `Notifications: ${notificationCount} new` : 'Notifications'}
          >
            Notifications
            {notificationCount ? (
              <span className="a3-menubar__badge" aria-hidden="true">
                {notificationCount}
              </span>
            ) : null}
          </button>
        )}
        {clusterLabel && (
          <span className="a3-menubar__item a3-menubar__item--secondary a3-mono">{clusterLabel}</span>
        )}
        <span className="a3-menubar__item a3-menubar__item--secondary">{clock}</span>
        <button type="button" className="a3-menubar__item" onClick={onUserClick}>
          <b className="a3-truncate">{userName}</b>&nbsp;· {userRole}
        </button>
      </div>
    </header>
  )
}

/* ───────────────────────── SideNav ───────────────────────── */

export interface SideNavProps {
  items: NavItem[]
  /** id of the active item or sub-item. */
  activeId: string
  onNavigate: (id: string) => void
  /** Bottom pinned item "Cluster settings". */
  footerItem?: NavItem
  /** Extra block at the bottom (e.g. the desk profile). */
  footer?: ReactNode
}

export function SideNav({ items, activeId, onNavigate, footerItem, footer }: SideNavProps) {
  const renderItem = (item: NavItem) => {
    const childActive = item.children?.some((c) => c.id === activeId) ?? false
    const active = item.id === activeId || childActive
    return (
      <li key={item.id}>
        <a
          href={`#${item.id}`}
          className={`a3-nav__item${active ? ' is-active' : ''}`}
          aria-current={item.id === activeId ? 'page' : undefined}
          title={item.label}
          onClick={(e) => {
            e.preventDefault()
            onNavigate(item.id)
          }}
        >
          <Icon name={item.icon} />
          <span className="a3-nav__label">{item.label}</span>
          {item.count !== undefined && item.count !== '' && (
            <span className="a3-nav__count">{item.count}</span>
          )}
        </a>
        {item.children && item.children.length > 0 && (
          <ul className="a3-nav__sub" aria-label={item.label}>
            {item.children.map((c) => (
              <li key={c.id}>
                <a
                  href={`#${c.id}`}
                  className={`a3-nav__subitem${c.id === activeId ? ' is-active' : ''}`}
                  aria-current={c.id === activeId ? 'page' : undefined}
                  onClick={(e) => {
                    e.preventDefault()
                    onNavigate(c.id)
                  }}
                >
                  <span className="a3-truncate">{c.label}</span>
                  {c.count !== undefined && c.count !== '' && (
                    <span className="a3-nav__badge">{c.count}</span>
                  )}
                </a>
              </li>
            ))}
          </ul>
        )}
      </li>
    )
  }

  return (
    <nav className="a3-sidenav" aria-label="Main navigation">
      <ul className="a3-nav">{items.map(renderItem)}</ul>
      {(footerItem || footer) && (
        <div className="a3-sidenav__footer">
          {footerItem && <ul className="a3-nav">{renderItem(footerItem)}</ul>}
          {footer}
        </div>
      )}
    </nav>
  )
}

/* ───────────────────────── AppShell ───────────────────────── */

export interface AppShellProps {
  menuBar: MenuBarProps
  sideNav: SideNavProps
  /** Desk mode: in customize mode the dot grid is lighter. */
  deskMode?: 'normal' | 'edit'
  children: ReactNode
}

/** The "OS" shell: top bar, left menu and the desk the screen goes into. */
export function AppShell({ menuBar, sideNav, deskMode = 'normal', children }: AppShellProps) {
  return (
    <div className="a3-os">
      <a className="a3-skip-link" href="#a3-desk">
        Skip to content
      </a>
      <MenuBar {...menuBar} />
      <div className="a3-os__body">
        <SideNav {...sideNav} />
        <main id="a3-desk" className={`a3-desk${deskMode === 'edit' ? ' a3-desk--edit' : ''}`} tabIndex={-1}>
          {children}
        </main>
      </div>
    </div>
  )
}

/** Standard A3 menu (decision of 05.11.2026). The project may override labels and counters. */
export const DEFAULT_NAV: NavItem[] = [
  { id: 'desk', label: 'Desk', icon: 'desk' },
  {
    id: 'agents',
    label: 'Agents',
    icon: 'agent',
    children: [
      { id: 'agents-list', label: 'All agents' },
      { id: 'agents-tasks', label: 'Tasks' },
      { id: 'agents-install', label: 'Install' },
    ],
  },
  { id: 'journal', label: 'Journal', icon: 'log' },
  { id: 'data', label: 'Data', icon: 'folder' },
  { id: 'policies', label: 'Policies', icon: 'shield' },
  { id: 'users', label: 'Users', icon: 'users' },
  {
    id: 'system',
    label: 'System',
    icon: 'system',
    children: [
      { id: 'system-models', label: 'Models' },
      { id: 'system-access', label: 'Permissions and access' },
      { id: 'system-update', label: 'Update' },
    ],
  },
]

export const CLUSTER_NAV_ITEM: NavItem = { id: 'cluster', label: 'Cluster settings', icon: 'node' }
