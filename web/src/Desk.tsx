import { type DeskSummary } from './api'
import { clockTime, duration, plural, triggerLabel } from './format'
import { StatusScreen, type StatusData, type WidgetPlacement } from './screens/a3/StatusScreen'
import type { ScreenState, Tone } from './ui/types'

// Widgets with real data only: "Waiting for a human" and node load are not in the MVP (DESIGN.md §12).
const LAYOUT: WidgetPlacement[] = [
  { id: 'running', kind: 'running', title: 'Running', span: 3, rows: 2 },
  { id: 'queued', kind: 'queued', title: 'Queued', span: 3, rows: 2 },
  { id: 'errors', kind: 'errors-hour', title: 'Errors in the last hour', span: 3, rows: 2 },
  { id: 'cost', kind: 'cost', title: 'External models', span: 3, rows: 2, filterLabel: '24 hours' },
  { id: 'tasks', kind: 'tasks', title: 'Running tasks', span: 7, rows: 4, filterLabel: 'all agents' },
  { id: 'nodes', kind: 'nodes', title: 'Nodes', span: 5, rows: 4 },
  {
    id: 'agent-errors',
    kind: 'agent-errors',
    title: 'Agent errors',
    span: 4,
    rows: 3,
    filterLabel: '24 hours',
  },
  {
    id: 'policies',
    kind: 'policies',
    title: 'Policies over agents',
    span: 4,
    rows: 3,
    filterLabel: '24 hours',
  },
  { id: 'error-log', kind: 'error-log', title: 'Error log', span: 4, rows: 3 },
]

const LEVEL_TONE: Record<string, Tone> = { failed: 'error', timeout: 'error', config_error: 'warn' }

function toStatusData(s: DeskSummary): StatusData {
  const tasks = plural(s.running, ['task', 'tasks'])
  return {
    counters: {
      running: { value: s.running, caption: `${tasks} now` },
      queued: { value: s.queued, caption: 'waiting to start' },
      'errors-hour': { value: s.errors_hour, caption: 'failed · timeout · config_error' },
      cost: { value: `$${Number(s.cost_usd_24h).toFixed(2)}`, caption: 'cost of calls' },
    },
    tasks: s.tasks.map((t) => ({
      id: t.id,
      agent: t.agent,
      title: triggerLabel(t.trigger),
      startedBy: t.started_by ?? '—',
      elapsed: duration(t.created_at, null),
    })),
    moreTasks: s.more_tasks,
    nodes: s.nodes.map((n) => ({
      id: n.name,
      name: n.name,
      spec: [
        `${n.cpu_count} vCPU · ${n.ram_gb} GB`,
        n.gpu && `${n.gpu}${n.vram_gb ? ` · ${n.vram_gb} GB` : ''}`,
      ]
        .filter(Boolean)
        .join(' · '),
      agentsText: '',
      meters: [],
    })),
    agentErrors: s.agent_errors.map((a) => ({
      id: a.agent,
      label: a.agent,
      value: a.total,
      hint: Object.entries(a.by_status)
        .map(([status, n]) => `${status} ×${n}`)
        .join(' · '),
    })),
    policies: {
      allowed: s.policies.allowed,
      denied: s.policies.denied,
      reasons: s.policies.reasons.map((r) => ({ id: r.reason, label: r.reason, value: r.count })),
    },
    errorLog: s.error_log.map((e) => ({
      id: e.id,
      time: clockTime(e.at),
      level: e.status.toUpperCase(),
      tone: LEVEL_TONE[e.status] ?? 'error',
      text: `${e.agent}: ${e.message ?? 'reason not recorded'}`,
    })),
  }
}

// The desk: data comes from polling in the shell, which also feeds the counter in the bar.
export default function Desk({
  summary,
  state,
  onOpenTasks,
  onOpenTask,
}: {
  summary: DeskSummary | null
  state: ScreenState
  onOpenTasks: () => void
  onOpenTask: (id: string) => void
}) {
  return (
    <StatusScreen
      state={state}
      mode="view"
      layout={LAYOUT}
      data={summary ? toStatusData(summary) : undefined}
      onOpenTasks={onOpenTasks}
      onOpenTask={onOpenTask}
    />
  )
}
