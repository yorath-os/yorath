import { useCallback, useEffect, useState } from 'react'
import {
  fetchAgents,
  fetchExecutionDetail,
  fetchTasks,
  type AgentInfo,
  type ExecutionDetail,
  type JournalEvent,
  type Task,
  type TaskGroup,
  type TaskList,
} from './api'
import { asDataClass, clockTime, dateTime, duration, triggerLabel } from './format'
import {
  JournalScreen,
  type TaskDetail,
  type TaskFilter,
  type TaskRow,
  type TaskStatus,
} from './screens/a3/JournalScreen'
import type { TimelineStep } from './ui/Lists'
import type { ScreenState } from './ui/types'

const POLL_MS = 15_000
const TRIGGERS = ['', 'user_message', 'agent_call', 'event', 'schedule']

const toRow = (t: Task): TaskRow => ({
  id: t.id,
  title: triggerLabel(t.trigger),
  shortId: `${t.id.slice(0, 8)}…`,
  agent: t.agent,
  startedBy: t.started_by ?? '—',
  status: t.status as TaskStatus,
  duration: duration(t.created_at, t.finished_at),
})

function stepText(e: JournalEvent): string | null {
  switch (e.event_type) {
    case 'execution.started':
      return 'Task started'
    case 'llm.called':
      return `Model call ${e.model ?? ''}`.trim()
    case 'tool.called':
      return `Tool ${e.tool ?? ''}`.trim()
    case 'data.accessed':
      return `Source read ${e.data_asset ?? ''}`.trim()
    case 'agent.invoked':
      return 'Call to another agent'
    case 'policy.denied':
      return `Denied by policy: ${e.reason ?? 'reason not recorded'}`
    case 'outcome.recorded':
      return 'Result recorded'
    case 'execution.finished':
      return 'Task finished'
    default:
      return null
  }
}

function toSteps(events: JournalEvent[], live: boolean): TimelineStep[] {
  const steps = events.flatMap((e): TimelineStep[] => {
    const text = stepText(e)
    if (!text) return []
    const state = e.decision === 'deny' ? 'failed' : 'done'
    return [{ id: e.id, text, time: clockTime(e.occurred_at), state }]
  })
  if (live && steps.length) steps[steps.length - 1].state = 'current'
  return steps
}

function toDetail(task: Task, data: ExecutionDetail, agent: AgentInfo | undefined): TaskDetail {
  const own = data.chain.find((c) => c.id === task.id)
  const events = data.events.filter((e) => e.execution_id === task.id)
  const card = agent?.revision?.card
  const live = task.status === 'running' || task.status === 'queued'
  return {
    id: task.id,
    title: triggerLabel(task.trigger),
    meta: `${task.agent} · revision ${task.revision_no} · execution ${task.id.slice(0, 8)}…`,
    status: task.status as TaskStatus,
    backgroundText: 'The task runs on the server; you can close the browser.',
    facts: [
      { key: 'Started by', value: task.started_by ?? '—' },
      { key: 'On behalf of', value: own?.effective_principal ?? '—' },
      { key: 'Model', value: task.model ?? '—', mono: true },
      { key: 'Started', value: dateTime(task.created_at) },
      { key: 'Duration', value: duration(task.created_at, task.finished_at) },
      { key: 'Data class', value: own?.max_data_class_seen ?? '—' },
      ...(task.error ? [{ key: 'Error', value: task.error }] : []),
    ],
    steps: toSteps(events, live),
    permissions: card
      ? [
          ...card.tools.map((t) => ({ id: `tool-${t.name}`, label: t.name })),
          ...card.data.map((d) => ({ id: `data-${d}`, label: d })),
          {
            id: 'class',
            label: `up to ${card.max_data_class}`,
            dataClass: asDataClass(card.max_data_class),
          },
        ]
      : [],
    canStop: false,
  }
}

// Task manager: all Executions, details from the journal. Question texts are not shown here.
export default function Tasks({
  selectedId,
  onSelect,
  onOpenJournal,
  onCheckAgent,
}: {
  selectedId?: string
  onSelect: (id: string) => void
  onOpenJournal: (id: string) => void
  onCheckAgent: () => void
}) {
  const [filter, setFilter] = useState<TaskGroup>('all')
  const [agent, setAgent] = useState('')
  const [trigger, setTrigger] = useState('')
  const [list, setList] = useState<TaskList | null>(null)
  const [state, setState] = useState<ScreenState>({ kind: 'loading' })
  const [agents, setAgents] = useState<AgentInfo[]>([])
  const [detail, setDetail] = useState<TaskDetail | undefined>()
  const [detailState, setDetailState] = useState<ScreenState>({ kind: 'loading' })

  const load = useCallback(async () => {
    try {
      const r = await fetchTasks({ status: filter, agent, trigger })
      if (r.status !== 200) throw new Error(`HTTP ${r.status}`)
      setList(r.body)
      setState(r.body.tasks.length ? { kind: 'ready' } : { kind: 'empty' })
    } catch (e) {
      setState({
        kind: 'error',
        message: e instanceof Error ? e.message : String(e),
        onRetry: () => void load(),
      })
    }
  }, [filter, agent, trigger])

  useEffect(() => {
    const first = async () => {
      await load()
    }
    void first()
    const t = setInterval(() => void load(), POLL_MS)
    return () => clearInterval(t)
  }, [load])

  useEffect(() => {
    const init = async () => {
      const r = await fetchAgents()
      if (r.status === 200) setAgents(r.body)
    }
    void init()
  }, [])

  const task = list?.tasks.find((t) => t.id === selectedId)
  const taskId = task?.id
  const taskStatus = task?.status
  useEffect(() => {
    if (!taskId || !task) return
    const open = async () => {
      setDetailState({ kind: 'loading' })
      try {
        const r = await fetchExecutionDetail(taskId)
        if (r.status !== 200) throw new Error(`HTTP ${r.status}`)
        setDetail(
          toDetail(
            task,
            r.body,
            agents.find((a) => a.name === task.agent),
          ),
        )
        setDetailState({ kind: 'ready' })
      } catch (e) {
        setDetailState({ kind: 'error', message: e instanceof Error ? e.message : String(e) })
      }
    }
    void open()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- details depend on the task and its status
  }, [taskId, taskStatus, agents])

  const next = (values: string[], current: string) => values[(values.indexOf(current) + 1) % values.length]
  const counts = list?.counts
  return (
    <JournalScreen
      state={state}
      counts={counts ?? { all: 0, running: 0, queued: 0, failed: 0 }}
      filter={filter}
      onFilterChange={(f: TaskFilter) => setFilter(f as TaskGroup)}
      agentFilter={agent || 'all'}
      triggerFilter={trigger ? triggerLabel(trigger) : 'all'}
      onOpenFilter={(name) => {
        if (name === 'agent') setAgent(next(['', ...agents.map((a) => a.name)], agent))
        if (name === 'trigger') setTrigger(next(TRIGGERS, trigger))
      }}
      tasks={(list?.tasks ?? []).map(toRow)}
      selectedId={taskId}
      onSelect={onSelect}
      detail={detail?.id === taskId ? detail : undefined}
      detailState={detailState}
      onStop={() => {}}
      onOpenTrace={onOpenJournal}
      onCheckAgent={onCheckAgent}
    />
  )
}
