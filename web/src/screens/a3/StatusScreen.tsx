import type { ReactNode } from 'react'
import { Button } from '../../ui/Controls'
import { DataTable } from '../../ui/DataTable'
import {
  DeskHeader,
  DropSlot,
  EditableWidget,
  ProfilePicker,
  WidgetCatalog,
  WidgetGrid,
  type CatalogWidget,
  type DeskProfile,
} from '../../ui/Desktop'
import { MeterRow, ProgressBar, SplitBar, Stat } from '../../ui/Indicators'
import {
  BarList,
  LogList,
  Notice,
  RankedList,
  type BarListItem,
  type LogEntry,
  type RankedItem,
} from '../../ui/Lists'
import { ScreenStateGate } from '../../ui/StateView'
import type { ScreenState } from '../../ui/types'
import { Window } from '../../ui/Window'

/**
 * Status ← A3.1 "Administrator desk" and A3.2 "Desk customization".
 * The desk is assembled from widgets; the profile sets the set and the order.
 */

export type WidgetKind =
  | 'running'
  | 'queued'
  | 'errors-hour'
  | 'waiting-human'
  | 'cost'
  | 'tasks'
  | 'nodes'
  | 'agent-errors'
  | 'policies'
  | 'error-log'

export type WidgetSpan = 3 | 4 | 5 | 6 | 7 | 8 | 12

export interface WidgetPlacement {
  id: string
  kind: WidgetKind
  title: string
  span: WidgetSpan
  /** Height in customize mode, in 64 px rows. */
  rows: number
  /** Filter label in the header: "all agents", "24 hours". */
  filterLabel?: string
}

export interface CounterValue {
  value: number | string
  caption: string
}

export interface RunningTask {
  id: string
  agent: string
  title: string
  /** Who started it: "scheduled", "issue event", a person's name. */
  startedBy: string
  /** Without a node there's no column: the MVP has one node. */
  node?: string
  /** "22 min". */
  elapsed: string
  /** 0–100. If the API doesn't know the progress — undefined, then "—" is shown. */
  progress?: number
}

export interface NodeLoad {
  id: string
  name: string
  /** "16 vCPU · 16 GB", "A10 · 24 GB". */
  spec: string
  /** "6 agents". */
  agentsText: string
  meters: { label: string; percent: number; valueText?: string; warn?: boolean }[]
}

export interface StatusData {
  counters: Partial<Record<'running' | 'queued' | 'errors-hour' | 'waiting-human' | 'cost', CounterValue>>
  tasks: RunningTask[]
  /** How many more tasks didn't fit in the widget. */
  moreTasks?: number
  nodes: NodeLoad[]
  agentErrors: BarListItem[]
  policies: { allowed: number; denied: number; reasons: RankedItem[] }
  errorLog: LogEntry[]
}

export interface StatusEditProps {
  profiles: DeskProfile[]
  profileId: string
  onProfileChange: (id: string) => void
  catalog: CatalogWidget[]
  categories: string[]
  category: string
  onCategoryChange: (c: string) => void
  query: string
  onQueryChange: (q: string) => void
  onAdd: (id: string) => void
  onMove: (id: string, delta: -1 | 1) => void
  onResize: (id: string, delta: -1 | 1) => void
  onRemove: (id: string) => void
  onDragStart?: (id: string) => void
  draggingId?: string
  onSave: () => void
  onCancel: () => void
  saving?: boolean
}

export interface StatusScreenProps {
  state: ScreenState
  mode: 'view' | 'edit'
  /** Without a profile there's no subtitle: the MVP has no profiles. */
  profileName?: string
  layout: WidgetPlacement[]
  data?: StatusData
  /** Without handlers there are no customize buttons: the MVP has no profiles. */
  onConfigure?: () => void
  onChooseProfile?: () => void
  onWidgetMenu?: (id: string) => void
  onOpenTasks?: () => void
  onOpenTask?: (id: string) => void
  /** Required in edit mode. */
  edit?: StatusEditProps
}

const COUNTER_TONE: Record<string, 'default' | 'warn' | 'error'> = {
  running: 'default',
  queued: 'default',
  'errors-hour': 'error',
  'waiting-human': 'warn',
  cost: 'default',
}

export function StatusScreen(props: StatusScreenProps) {
  const { state, mode, profileName, layout, data, onConfigure, onChooseProfile, edit } = props

  if (mode === 'edit' && edit) {
    return (
      <div className="a3-desk-split">
        <div className="a3-desk__pad">
          <Notice
            tone="edit"
            actions={
              <>
                <Button variant="chrome" onClick={edit.onCancel}>
                  Cancel
                </Button>
                <Button variant="chrome-primary" onClick={edit.onSave} loading={edit.saving}>
                  Save profile
                </Button>
              </>
            }
          >
            <b>Customize mode.</b> Drag widgets by the handle, change width by the corner, add them from the
            catalog on the right. From the keyboard: arrows on the handle and on the corner.
          </Notice>
          <WidgetGrid label="Desk widgets">
            {layout.map((w) => (
              <EditableWidget
                key={w.id}
                id={w.id}
                title={w.title}
                span={w.span}
                rows={w.rows}
                dragging={edit.draggingId === w.id}
                onMove={edit.onMove}
                onResize={edit.onResize}
                onRemove={edit.onRemove}
                onDragStart={edit.onDragStart}
              />
            ))}
            <DropSlot />
          </WidgetGrid>
        </div>
        <aside className="a3-drawer" aria-label="Desk customization">
          <h2 className="a3-drawer__title">Desk</h2>
          <ProfilePicker profiles={edit.profiles} value={edit.profileId} onChange={edit.onProfileChange} />
          <p className="a3-hint-box">
            A profile can be assigned to a role: all users with that role see the desk by default and can
            adjust it for themselves.
          </p>
          <WidgetCatalog
            widgets={edit.catalog}
            categories={edit.categories}
            category={edit.category}
            onCategoryChange={edit.onCategoryChange}
            query={edit.query}
            onQueryChange={edit.onQueryChange}
            onAdd={edit.onAdd}
          />
        </aside>
      </div>
    )
  }

  return (
    <div className="a3-desk__pad">
      <DeskHeader
        title="Desk"
        subtitle={profileName}
        actions={
          onConfigure && onChooseProfile ? (
            <>
              <Button variant="chrome" onClick={onChooseProfile}>
                Profile
              </Button>
              <Button variant="chrome-primary" onClick={onConfigure}>
                Customize
              </Button>
            </>
          ) : undefined
        }
      />
      <ScreenStateGate
        state={state}
        surface="dark"
        skeletonRows={6}
        emptyTitle="No widgets on the desk"
        emptyDescription="Choose a profile or add widgets from the catalog."
        emptyActionLabel="Customize"
        onEmptyAction={onConfigure}
      >
        <WidgetGrid label="Desk widgets">
          {layout.map((w) => (
            <Window
              key={w.id}
              variant="widget"
              title={w.title}
              filterLabel={w.filterLabel}
              span={w.span}
              onMenu={props.onWidgetMenu ? () => props.onWidgetMenu?.(w.id) : undefined}
              flush={w.kind === 'tasks'}
            >
              {data ? renderWidget(w, data, props) : null}
            </Window>
          ))}
        </WidgetGrid>
      </ScreenStateGate>
    </div>
  )
}

function WidgetEmpty({ children }: { children: ReactNode }) {
  return <p className="a3-widget-empty">{children}</p>
}

function renderWidget(w: WidgetPlacement, d: StatusData, p: StatusScreenProps): ReactNode {
  switch (w.kind) {
    case 'running':
    case 'queued':
    case 'errors-hour':
    case 'waiting-human':
    case 'cost': {
      const c = d.counters[w.kind]
      if (!c) return <WidgetEmpty>No data</WidgetEmpty>
      return (
        <Stat value={c.value} caption={c.caption} tone={c.value === 0 ? 'default' : COUNTER_TONE[w.kind]} />
      )
    }
    case 'tasks':
      if (d.tasks.length === 0) return <WidgetEmpty>No agent is running right now.</WidgetEmpty>
      return (
        <>
          <DataTable
            caption="Running tasks"
            rows={d.tasks}
            rowKey={(t) => t.id}
            onRowClick={p.onOpenTask ? (t) => p.onOpenTask?.(t.id) : undefined}
            rowLabel={(t) => `${t.title}, agent ${t.agent}`}
            columns={[
              {
                id: 'agent',
                header: 'Agent',
                weight: 2.2,
                render: (t) => (
                  <span className="a3-mono a3-text-xs a3-truncate" title={t.agent}>
                    {t.agent}
                  </span>
                ),
              },
              {
                id: 'task',
                header: 'Task',
                weight: 3,
                render: (t) => (
                  <span className="a3-cell-stack">
                    <span className="a3-clamp-2">{t.title}</span>
                    <small>{t.startedBy}</small>
                  </span>
                ),
              },
              ...(d.tasks.some((t) => t.node)
                ? [
                    {
                      id: 'node',
                      header: 'Node',
                      weight: 1.4,
                      hideOnMobile: true,
                      render: (t: RunningTask) => <span className="a3-mono a3-text-xs">{t.node}</span>,
                    },
                  ]
                : []),
              { id: 'time', header: 'Time', weight: 1.2, hideOnMobile: true, render: (t) => t.elapsed },
              {
                id: 'progress',
                header: 'Progress',
                weight: 2,
                render: (t) =>
                  t.progress === undefined ? (
                    <span className="a3-text-muted">—</span>
                  ) : (
                    <ProgressBar value={t.progress} label={`Progress of task "${t.title}"`} />
                  ),
              },
            ]}
          />
          {d.moreTasks ? (
            <button type="button" className="a3-link a3-window__more" onClick={p.onOpenTasks}>
              {d.moreTasks} more in the task manager
            </button>
          ) : null}
        </>
      )
    case 'nodes':
      if (d.nodes.length === 0) return <WidgetEmpty>No nodes found. Check the cluster settings.</WidgetEmpty>
      return (
        <ul className="a3-nodes" aria-label="Nodes">
          {d.nodes.map((n) => (
            <li key={n.id} className="a3-nodes__item">
              <div className="a3-nodes__head">
                <b className="a3-mono">{n.name}</b>
                <span className="a3-text-muted a3-text-xs">{n.spec}</span>
                <span className="a3-nodes__agents a3-text-xs">{n.agentsText}</span>
              </div>
              <div className="a3-nodes__meters">
                {n.meters.map((m) => (
                  <MeterRow
                    key={m.label}
                    label={m.label}
                    percent={m.percent}
                    valueText={m.valueText}
                    tone={m.warn ? 'warn' : 'accent'}
                  />
                ))}
              </div>
            </li>
          ))}
        </ul>
      )
    case 'agent-errors':
      if (d.agentErrors.length === 0) return <WidgetEmpty>No errors in this period.</WidgetEmpty>
      return <BarList items={d.agentErrors} label="Errors by agent" />
    case 'policies':
      return (
        <>
          <Stat
            value={d.policies.allowed + d.policies.denied}
            caption={`${d.policies.allowed} allowed · ${d.policies.denied} denied`}
          />
          <SplitBar allowed={d.policies.allowed} denied={d.policies.denied} />
          {d.policies.reasons.length > 0 ? (
            <RankedList items={d.policies.reasons} label="Top denial reasons" />
          ) : (
            <WidgetEmpty>No denials in this period.</WidgetEmpty>
          )}
        </>
      )
    case 'error-log':
      if (d.errorLog.length === 0) return <WidgetEmpty>No errors in the log.</WidgetEmpty>
      return <LogList entries={d.errorLog} label="Error log" />
  }
}
