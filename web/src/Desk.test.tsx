import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import App from './App'
import { stubApi } from '../test/stub-api'

const OWNER = {
  authenticated: true,
  id: 'u1',
  login: 'alex',
  display_name: 'Alex',
  role: 'owner',
  department: null,
  is_active: true,
}
const TASK = {
  id: 'aaaaaaaa-1111',
  agent: 'document-assistant',
  agent_display_name: 'Document Assistant',
  revision_no: 3,
  status: 'running',
  trigger: 'user_message',
  started_by: 'Anna',
  created_at: new Date(Date.now() - 90_000).toISOString(),
  finished_at: null,
  model: 'qwen3.5:4b',
  error: null,
  parent_execution_id: null,
}
const FAILED = {
  ...TASK,
  id: 'bbbbbbbb-2222',
  status: 'failed',
  error: 'model failure',
  finished_at: TASK.created_at,
}
const SUMMARY = {
  running: 1,
  queued: 0,
  errors_hour: 2,
  cost_usd_24h: '0.125',
  tasks: [TASK],
  more_tasks: 0,
  nodes: [{ name: 'vps-01', cpu_count: 8, ram_gb: 8, gpu: null, vram_gb: null }],
  agent_errors: [{ agent: 'research-agent', total: 2, by_status: { failed: 1, timeout: 1 } }],
  policies: {
    allowed: 40,
    denied: 2,
    reasons: [{ reason: 'no permission for the source rows', count: 2 }],
  },
  error_log: [
    { id: 'l1', at: TASK.created_at, status: 'timeout', agent: 'research-agent', message: 'timed out' },
  ],
}
const BASE = {
  'GET /api/auth/me': OWNER,
  'GET /api/setup/status': { setup_completed: true },
  'GET /api/desk/summary': SUMMARY,
}
const AGENTS = [
  {
    name: 'document-assistant',
    revision: {
      card: {
        tools: [{ name: 'search_docs', targets: [] }],
        data: ['team-docs'],
        max_data_class: 'confidential',
      },
    },
  },
]
const TASKS = { counts: { all: 2, running: 1, queued: 0, failed: 1 }, tasks: [TASK, FAILED] }

beforeEach(() => {
  window.location.hash = ''
})

describe('Desk', () => {
  it('shows real aggregates and "—" instead of unknown progress', async () => {
    const api = stubApi(BASE)
    render(<App />)
    const tasks = await screen.findByRole('region', { name: 'Running tasks' })
    expect(within(tasks).getByText('Chat question')).toBeInTheDocument()
    expect(within(tasks).getByText('—')).toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Errors in the last hour' })).toHaveTextContent('2')
    expect(screen.getByText('$0.13')).toBeInTheDocument()
    expect(screen.getByText('8 vCPU · 8 GB')).toBeInTheDocument()
    expect(screen.getByText('no permission for the source rows')).toBeInTheDocument()
    expect(screen.getByText(/research-agent: timed out/)).toBeInTheDocument()
    api.assertAllAnswered()
  })

  it('the MVP has no widgets without data and no customize buttons', async () => {
    stubApi(BASE)
    render(<App />)
    await screen.findByRole('region', { name: 'Running tasks' })
    expect(screen.queryByRole('region', { name: 'Waiting for a human' })).not.toBeInTheDocument()
    expect(screen.queryByRole('meter')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Customize' })).not.toBeInTheDocument()
  })

  it('the bar shows background tasks and the real number of nodes', async () => {
    stubApi(BASE)
    render(<App />)
    const bar = await screen.findByRole('banner')
    expect(await within(bar).findByText('1 node')).toBeInTheDocument()
    expect(within(bar).getByRole('button', { name: /in background/ })).toHaveTextContent('1')
  })

  it('without policy decisions the bar is empty, not red', async () => {
    const empty = { ...SUMMARY, policies: { allowed: 0, denied: 0, reasons: [] } }
    stubApi({ ...BASE, 'GET /api/desk/summary': empty })
    render(<App />)
    const bar = await screen.findByRole('img', { name: 'Allowed 0, denied 0' })
    expect(bar).toBeEmptyDOMElement()
  })

  it('a core error is visible on the desk with a retry button', async () => {
    const api = stubApi({ ...BASE, 'GET /api/desk/summary': { status: 500, body: {} } })
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Retry' }))
    expect(api.calls.filter((c) => c.key === 'GET /api/desk/summary').length).toBeGreaterThan(1)
  })
})

describe('Task manager', () => {
  const detail = {
    chain: [
      {
        id: TASK.id,
        parent_execution_id: null,
        agent: 'document-assistant',
        revision_no: 3,
        status: 'running',
        trigger: 'user_message',
        created_at: TASK.created_at,
        finished_at: null,
        actor: 'Anna',
        effective_principal: 'Anna',
        model: 'qwen3.5:4b',
        max_data_class_seen: 'internal',
      },
    ],
    events: [
      {
        id: 'e1',
        event_type: 'execution.started',
        execution_id: TASK.id,
        occurred_at: TASK.created_at,
        details: {},
      },
      {
        id: 'e2',
        event_type: 'policy.denied',
        execution_id: TASK.id,
        occurred_at: TASK.created_at,
        decision: 'deny',
        reason: 'class above clearance',
        details: {},
      },
    ],
    spans: [],
  }
  const routes = {
    ...BASE,
    'GET /api/tasks': TASKS,
    'GET /api/agents': AGENTS,
    [`GET /api/journal/executions/${TASK.id}`]: detail,
  }

  it('details show steps and permissions and lead to the journal', async () => {
    window.location.hash = `agents-tasks/${TASK.id}`
    stubApi({ ...routes, 'GET /api/journal/events?limit=50': [], 'GET /api/users': [] })
    render(<App />)
    const aside = await screen.findByRole('complementary', { name: 'Selected task' })
    expect(await within(aside).findByText('Denied by policy: class above clearance')).toBeInTheDocument()
    expect(within(aside).getByText('search_docs')).toBeInTheDocument()
    expect(within(aside).queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument()
    await userEvent.click(within(aside).getByRole('button', { name: 'Open in journal' }))
    expect(window.location.hash).toBe(`#journal/${TASK.id}`)
    expect(await screen.findByRole('region', { name: 'Execution' })).toBeInTheDocument()
  })

  it('the filter requests a group, and "Waiting for a human" is not shown', async () => {
    window.location.hash = 'agents-tasks'
    const api = stubApi({
      ...routes,
      'GET /api/tasks?status=failed': { counts: TASKS.counts, tasks: [FAILED] },
    })
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Errors · 1' }))
    expect(api.calls.some((c) => c.key === 'GET /api/tasks?status=failed')).toBe(true)
    expect(screen.queryByRole('button', { name: /Waiting for a human/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Node/ })).not.toBeInTheDocument()
  })

  it('the agent filter cycles through real agents', async () => {
    window.location.hash = 'agents-tasks'
    const api = stubApi({
      ...routes,
      'GET /api/tasks?agent=document-assistant': { counts: TASKS.counts, tasks: [TASK] },
    })
    render(<App />)
    await screen.findByRole('button', { name: /All · 2/ })
    await userEvent.click(await screen.findByRole('button', { name: /Agent/ }))
    expect(api.calls.some((c) => c.key === 'GET /api/tasks?agent=document-assistant')).toBe(true)
  })

  it('an empty filter explains what to do', async () => {
    window.location.hash = 'agents-tasks'
    stubApi({
      ...routes,
      'GET /api/tasks': { counts: { all: 0, running: 0, queued: 0, failed: 0 }, tasks: [] },
    })
    render(<App />)
    expect(await screen.findByText('No tasks match these filters')).toBeInTheDocument()
  })
})

describe('The default model switched automatically', () => {
  const WARNING = {
    switched_from: 'local/qwen3.5:9b',
    switched_to: 'local/gemma4:e2b',
    node: 'vm13270',
    at: '2026-11-16T15:40:00+00:00',
    node_back: false,
    can_return: false,
    short_window: ['hermes'],
    model_unavailable: [],
  }

  it('the desk says what moved where, why, and which agents the new window doesn’t fit', async () => {
    stubApi({ ...BASE, 'GET /api/desk/summary': { ...SUMMARY, default_warning: WARNING } })
    render(<App />)
    const note = await screen.findByRole('region', { name: 'Default model' })
    expect(note).toHaveTextContent(
      'The default model switched automatically from qwen3.5:9b to gemma4:e2b: node vm13270 is unavailable.',
    )
    expect(note).toHaveTextContent('hermes needs a larger context window and won’t answer on gemma4:e2b')
    expect(within(note).queryByRole('button', { name: /Return default/ })).not.toBeInTheDocument()
  })

  it('when the node is back the owner can return the model, or dismiss the warning', async () => {
    let warning: typeof WARNING | null = { ...WARNING, node_back: true, can_return: true }
    const api = stubApi({
      ...BASE,
      'GET /api/desk/summary': () => ({ status: 200, body: { ...SUMMARY, default_warning: warning } }),
      'POST /api/cluster/models/default': () => {
        warning = null
        return { status: 200, body: { name: 'local/qwen3.5:9b', model_id: 'qwen3.5:9b' } }
      },
    })
    render(<App />)
    const note = await screen.findByRole('region', { name: 'Default model' })
    expect(note).toHaveTextContent('vm13270 is back')
    await userEvent.click(within(note).getByRole('button', { name: 'Return default to qwen3.5:9b' }))
    expect(api.bodyOf('POST /api/cluster/models/default')).toEqual({ model: 'local/qwen3.5:9b' })
    await waitFor(() =>
      expect(screen.queryByRole('region', { name: 'Default model' })).not.toBeInTheDocument(),
    )
  })
})
