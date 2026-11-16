import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { fetchDeskSummary, fetchSetupStatus, type DeskSummary, type Me } from './api'
import Agents from './Agents'
import ApiKeys from './ApiKeys'
import { plural } from './format'
import Chat from './Chat'
import Data from './Data'
import Desk from './Desk'
import Journal from './Journal'
import Models from './Models'
import Policies from './Policies'
import ScreenBoundary from './ScreenBoundary'
import Status from './Status'
import Tasks from './Tasks'
import Users from './Users'
import Wizard from './Wizard'
import { AppShell } from './ui/AppShell'
import { Button } from './ui/Controls'
import { StateView } from './ui/StateView'
import type { NavItem, ScreenState } from './ui/types'
import { PRODUCT } from './brand'

const POLL_MS = 15_000

const OWNER_NAV: NavItem[] = [
  { id: 'desk', label: 'Desk', icon: 'desk' },
  { id: 'chat', label: 'Chat', icon: 'send' },
  {
    id: 'agents',
    label: 'Agents',
    icon: 'agent',
    children: [
      { id: 'agents-list', label: 'All agents' },
      { id: 'agents-tasks', label: 'Tasks' },
    ],
  },
  { id: 'journal', label: 'Journal', icon: 'log' },
  { id: 'data', label: 'Data', icon: 'folder' },
  { id: 'policies', label: 'Policies', icon: 'shield' },
  { id: 'users', label: 'Users', icon: 'users' },
  { id: 'keys', label: 'API keys', icon: 'key' },
  {
    id: 'system',
    label: 'System',
    icon: 'system',
    children: [{ id: 'system-models', label: 'Models' }],
  },
]
const MEMBER_NAV: NavItem[] = [
  { id: 'chat', label: 'Chat', icon: 'send' },
  { id: 'keys', label: 'API keys', icon: 'key' },
  { id: 'system', label: 'System', icon: 'system' },
]

const SECTIONS: Record<string, string> = {
  desk: 'Desk',
  chat: 'Chat',
  agents: 'Agents',
  'agents-list': 'Agents · All agents',
  'agents-tasks': 'Agents · Tasks',
  journal: 'Journal',
  data: 'Data',
  policies: 'Policies',
  users: 'Users',
  keys: 'API keys',
  system: 'System',
  'system-models': 'System · Models',
}
const ROLES: Record<string, string> = { owner: 'owner', member: 'member' }

const routeFromHash = () => window.location.hash.slice(1)

function useRoute(fallback: string) {
  const [route, setRoute] = useState(routeFromHash)
  useEffect(() => {
    const sync = () => setRoute(routeFromHash())
    window.addEventListener('hashchange', sync)
    return () => window.removeEventListener('hashchange', sync)
  }, [])
  // a "section/id" address: the task manager and the journal open on a specific record
  const [base, param] = (route || fallback).split('/')
  return [base, param as string | undefined, (id: string) => (window.location.hash = id)] as const
}

function useClock() {
  const format = () =>
    new Date()
      .toLocaleString('en-US', {
        weekday: 'short',
        day: 'numeric',
        month: 'short',
        hour: '2-digit',
        minute: '2-digit',
        hourCycle: 'h23',
      })
      .replace(/[.,]/g, '')
  const [clock, setClock] = useState(format)
  useEffect(() => {
    const t = setInterval(() => setClock(format()), 30_000)
    return () => clearInterval(t)
  }, [])
  return clock
}

function useDeskSummary(enabled: boolean) {
  const [summary, setSummary] = useState<DeskSummary | null>(null)
  const [state, setState] = useState<ScreenState>({ kind: 'loading' })
  const load = useCallback(async () => {
    try {
      const r = await fetchDeskSummary()
      if (r.status !== 200) throw new Error(`HTTP ${r.status}`)
      setSummary(r.body)
      setState({ kind: 'ready' })
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      setState({ kind: 'error', message, onRetry: () => void load() })
    }
  }, [])
  useEffect(() => {
    if (!enabled) return
    const first = async () => {
      await load()
    }
    void first()
    const t = setInterval(() => void load(), POLL_MS)
    return () => clearInterval(t)
  }, [enabled, load])
  return { summary, state, reload: load }
}

// The shell after sign-in: menu by role, sections on the hash, the wizard for an owner with an unfinished setup.
export default function Shell({ me, onLogout }: { me: Me; onLogout: () => Promise<void> }) {
  const owner = me.role === 'owner'
  const nav = owner ? OWNER_NAV : MEMBER_NAV
  const [route, param, navigate] = useRoute(nav[0].id)
  const [setupDone, setSetupDone] = useState<boolean | null>(owner ? null : true)
  const clock = useClock()
  const desk = useDeskSummary(owner)

  useEffect(() => {
    if (!owner) return
    const load = async () => {
      try {
        setSetupDone((await fetchSetupStatus()).body.setup_completed)
      } catch {
        setSetupDone(true)
      }
    }
    void load()
  }, [owner])

  const allowed = useMemo(
    () => new Set(nav.flatMap((n) => [n.id, ...(n.children ?? []).map((c) => c.id)])),
    [nav],
  )
  const agents = (
    <Agents selected={param} onSelect={(name) => navigate(`agents-list${name ? `/${name}` : ''}`)} />
  )
  const screens: Record<string, ReactNode> = {
    desk: (
      <Desk
        summary={desk.summary}
        state={desk.state}
        onOpenTasks={() => navigate('agents-tasks')}
        onOpenTask={(id) => navigate(`agents-tasks/${id}`)}
        onChanged={() => void desk.reload()}
      />
    ),
    chat: <Chat />,
    agents,
    'agents-list': agents,
    'agents-tasks': (
      <Tasks
        selectedId={param}
        onSelect={(id) => navigate(`agents-tasks/${id}`)}
        onOpenJournal={(id) => navigate(`journal/${id}`)}
        onCheckAgent={() => navigate('chat')}
      />
    ),
    journal: <Journal executionId={param} />,
    data: <Data />,
    policies: <Policies />,
    users: <Users currentUserId={me.id} />,
    keys: <ApiKeys owner={owner} />,
    system: <Status onOpenWizard={owner ? () => setSetupDone(false) : undefined} />,
    'system-models': <Models onOpenWizard={() => setSetupDone(false)} />,
  }

  const wizard = owner && setupDone === false
  const body = wizard ? (
    <Wizard
      onDone={() => setSetupDone(true)}
      onOpenData={() => {
        setSetupDone(true)
        navigate('data')
      }}
    />
  ) : allowed.has(route) ? (
    screens[route]
  ) : (
    <div className="a3-desk__pad">
      <StateView
        kind="forbidden"
        surface="dark"
        description="Your role doesn't have access to this section."
      />
    </div>
  )

  return (
    <AppShell
      menuBar={{
        section: wizard ? 'Setup wizard' : (SECTIONS[route] ?? PRODUCT),
        backgroundCount: desk.summary ? desk.summary.running + desk.summary.queued : undefined,
        onBackgroundClick: () => navigate('agents-tasks'),
        clusterLabel: desk.summary?.nodes.length
          ? `${desk.summary.nodes.length} ${plural(desk.summary.nodes.length, ['node', 'nodes'])}`
          : undefined,
        clock,
        userName: me.display_name,
        userRole: ROLES[me.role] ?? me.role,
      }}
      sideNav={{
        items: wizard ? [] : nav,
        activeId: route,
        onNavigate: navigate,
        footer: (
          <Button variant="chrome" onClick={() => void onLogout()}>
            Sign out
          </Button>
        ),
      }}
    >
      <ScreenBoundary key={route}>{body}</ScreenBoundary>
    </AppShell>
  )
}
