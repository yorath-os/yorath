import { Button, FilterChip, SegmentedControl } from '../../ui/Controls'
import { DataTable } from '../../ui/DataTable'
import { ProgressBar, StatusBadge } from '../../ui/Indicators'
import { KeyValueList, Notice, Timeline, type KeyValue, type TimelineStep } from '../../ui/Lists'
import { ScreenStateGate, StateView } from '../../ui/StateView'
import type { DataClass, ScreenState, Tone } from '../../ui/types'
import { Window } from '../../ui/Window'

/**
 * Journal ← A3.3 "Task manager".
 * Any task of any agent can be found, opened and understood.
 */

export type TaskFilter = 'all' | 'running' | 'queued' | 'waiting' | 'failed'

/** Execution statuses from §9 of the requirements plus "waiting for a human". */
export type TaskStatus =
  | 'created'
  | 'queued'
  | 'running'
  | 'waiting'
  | 'succeeded'
  | 'failed'
  | 'timeout'
  | 'config_error'
  | 'denied'
  | 'cancelled'

export const TASK_STATUS: Record<TaskStatus, { label: string; tone: Tone }> = {
  created: { label: 'created', tone: 'neutral' },
  queued: { label: 'queued', tone: 'neutral' },
  running: { label: 'running', tone: 'info' },
  waiting: { label: 'waiting for a human', tone: 'warn' },
  succeeded: { label: 'done', tone: 'ok' },
  failed: { label: 'error', tone: 'error' },
  timeout: { label: 'timed out', tone: 'error' },
  config_error: { label: 'configuration error', tone: 'error' },
  denied: { label: 'denied by policy', tone: 'error' },
  cancelled: { label: 'stopped', tone: 'neutral' },
}

export interface TaskRow {
  id: string
  title: string
  /** Short Execution id: "019a5c…". */
  shortId: string
  agent: string
  startedBy: string
  node?: string
  status: TaskStatus
  /** "22 min". */
  duration: string
}

export interface TaskDetail {
  id: string
  title: string
  /** "contract-review · revision 7b20e4 · execution 019a5c…". */
  meta: string
  status: TaskStatus
  /** "Running in the background" banner. Shown for running/queued. */
  backgroundText?: string
  progress?: number
  facts: KeyValue[]
  steps: TimelineStep[]
  permissions: { id: string; label: string; dataClass?: DataClass }[]
  canStop: boolean
}

export interface JournalScreenProps {
  state: ScreenState
  /** A group without a counter is hidden: the MVP has no "waiting for a human" status. */
  counts: Partial<Record<TaskFilter, number>>
  filter: TaskFilter
  onFilterChange: (f: TaskFilter) => void
  /** Current filter values. */
  agentFilter: string
  /** Without a value there is neither a filter nor a node column. */
  nodeFilter?: string
  triggerFilter: string
  onOpenFilter: (name: 'agent' | 'node' | 'trigger') => void
  tasks: TaskRow[]
  selectedId?: string
  onSelect: (id: string) => void
  detail?: TaskDetail
  /** Right panel state (detail loading is separate from the list). */
  detailState?: ScreenState
  onStop: (id: string) => void
  onOpenTrace: (id: string) => void
  onCheckAgent: (id: string) => void
  stopping?: boolean
}

const FILTERS: { value: TaskFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'running', label: 'Running' },
  { value: 'queued', label: 'Queued' },
  { value: 'waiting', label: 'Waiting for a human' },
  { value: 'failed', label: 'Errors' },
]

export function JournalScreen(p: JournalScreenProps) {
  return (
    <div className="a3-desk__pad a3-desk__pad--app">
      <Window
        variant="app"
        title="Task manager"
        headingLevel={2}
        flush
        toolbar={
          <>
            <SegmentedControl
              label="Task status"
              options={FILTERS.filter((f) => p.counts[f.value] !== undefined).map((f) => ({
                ...f,
                count: p.counts[f.value],
              }))}
              value={p.filter}
              onChange={p.onFilterChange}
            />
            <div className="a3-window__filters">
              <FilterChip name="Agent" value={p.agentFilter} onClick={() => p.onOpenFilter('agent')} />
              {p.nodeFilter !== undefined && (
                <FilterChip name="Node" value={p.nodeFilter} onClick={() => p.onOpenFilter('node')} />
              )}
              <FilterChip name="Trigger" value={p.triggerFilter} onClick={() => p.onOpenFilter('trigger')} />
            </div>
          </>
        }
      >
        <ScreenStateGate
          state={p.state}
          skeletonRows={8}
          emptyTitle="No tasks match these filters"
          emptyDescription={'Change the filter or choose "All".'}
        >
          <div className="a3-master-detail">
            <div className="a3-master-detail__list">
              <DataTable
                caption="Agent tasks"
                density="comfortable"
                rows={p.tasks}
                rowKey={(t) => t.id}
                selectedKey={p.selectedId}
                onRowClick={(t) => p.onSelect(t.id)}
                rowLabel={(t) => `${t.title}, ${t.agent}, ${TASK_STATUS[t.status].label}`}
                columns={[
                  {
                    id: 'task',
                    header: 'Task',
                    weight: 3,
                    render: (t) => (
                      <span className="a3-cell-stack">
                        <span className="a3-clamp-2">{t.title}</span>
                        <small className="a3-mono">{t.shortId}</small>
                      </span>
                    ),
                  },
                  {
                    id: 'agent',
                    header: 'Agent',
                    weight: 1.8,
                    render: (t) => (
                      <span className="a3-mono a3-text-xs a3-truncate" title={t.agent}>
                        {t.agent}
                      </span>
                    ),
                  },
                  {
                    id: 'by',
                    header: 'Started by',
                    weight: 1.8,
                    hideOnMobile: true,
                    render: (t) => <span className="a3-clamp-2">{t.startedBy}</span>,
                  },
                  ...(p.nodeFilter === undefined
                    ? []
                    : [
                        {
                          id: 'node',
                          header: 'Node',
                          weight: 1.1,
                          hideOnMobile: true,
                          render: (t: TaskRow) => <span className="a3-mono a3-text-xs">{t.node}</span>,
                        },
                      ]),
                  {
                    id: 'status',
                    header: 'Status',
                    weight: 1.8,
                    render: (t) => (
                      <StatusBadge tone={TASK_STATUS[t.status].tone} label={TASK_STATUS[t.status].label} />
                    ),
                  },
                  { id: 'dur', header: 'Time', weight: 1, hideOnMobile: true, render: (t) => t.duration },
                ]}
              />
            </div>
            <aside className="a3-master-detail__detail" aria-label="Selected task">
              <TaskDetailPanel {...p} />
            </aside>
          </div>
        </ScreenStateGate>
      </Window>
    </div>
  )
}

function TaskDetailPanel(p: JournalScreenProps) {
  const ds = p.detailState ?? { kind: 'ready' as const }
  if (!p.selectedId) {
    return (
      <StateView
        kind="empty"
        title="Select a task"
        description="Here you'll see who started it, what it is doing now and where it will save the result."
      />
    )
  }
  if (ds.kind !== 'ready' || !p.detail) {
    return (
      <ScreenStateGate state={ds.kind === 'ready' ? { kind: 'loading' } : ds} emptyTitle="No task data">
        {null}
      </ScreenStateGate>
    )
  }
  const d = p.detail
  const live = d.status === 'running' || d.status === 'queued'
  return (
    <div className="a3-detail">
      <div>
        <h3 className="a3-detail__title">{d.title}</h3>
        <p className="a3-detail__meta a3-mono">{d.meta}</p>
      </div>
      <StatusBadge tone={TASK_STATUS[d.status].tone} label={TASK_STATUS[d.status].label} />
      {live && d.backgroundText && <Notice tone="ok">{d.backgroundText}</Notice>}
      {d.progress !== undefined && <ProgressBar value={d.progress} label="Task progress" size="md" />}
      <KeyValueList items={d.facts} />
      <section>
        <h4 className="a3-section-label">What's happening</h4>
        <Timeline steps={d.steps} label="Task steps" />
      </section>
      <section>
        <h4 className="a3-section-label">Task permissions</h4>
        <ul className="a3-chips" aria-label="Task permissions">
          {d.permissions.map((perm) => (
            <li key={perm.id} className={`a3-chip${perm.dataClass ? ` a3-chip--${perm.dataClass}` : ''}`}>
              {perm.label}
            </li>
          ))}
        </ul>
      </section>
      <div className="a3-detail__actions">
        {d.canStop && (
          <Button variant="danger" onClick={() => p.onStop(d.id)} loading={p.stopping}>
            Stop
          </Button>
        )}
        <Button onClick={() => p.onOpenTrace(d.id)}>Open in journal</Button>
        <Button onClick={() => p.onCheckAgent(d.id)}>Check agent</Button>
      </div>
    </div>
  )
}
