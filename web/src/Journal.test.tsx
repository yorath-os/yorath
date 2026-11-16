import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { stubApi } from '../test/stub-api'
import Journal from './Journal'

const EVENT = {
  id: 'e1',
  occurred_at: '2026-11-05T01:00:00Z',
  event_type: 'data.accessed',
  execution_id: 'x1',
  parent_execution_id: null,
  actor: 'Lewis',
  effective_principal: 'Lewis',
  agent: 'document-assistant',
  revision_no: 2,
  runtime: 'native',
  model: null,
  tool: 'query_records',
  data_asset: 'hr-vacations',
  data_class: 'confidential',
  decision: 'permit',
  reason: null,
  cost_usd: null,
  control_level: 'tool',
  details: { rows: 1 },
}
const DENIED = {
  ...EVENT,
  id: 'e2',
  event_type: 'policy.denied',
  decision: 'deny',
  reason: 'no permission for the source rows',
  details: { rows_denied: 1 },
}
const BASE = {
  'GET /api/agents': [{ name: 'document-assistant' }],
  'GET /api/users': [{ id: 'u1', display_name: 'Lewis' }],
  'GET /api/journal/events?limit=50': [EVENT, DENIED],
}

describe('Journal', () => {
  it('shows the event’s chain of origin', async () => {
    const api = stubApi(BASE)
    render(<Journal />)
    expect(
      await screen.findAllByText(
        'Lewis → document-assistant r2 → query_records → hr-vacations (confidential) → permit',
      ),
    ).toHaveLength(1)
    api.assertAllAnswered()
  })

  it('an API key call shows the key in the chain and in the card', async () => {
    const started = {
      ...EVENT,
      id: 'e3',
      event_type: 'execution.started',
      agent: 'model-api',
      tool: null,
      data_asset: null,
      data_class: null,
      decision: null,
      details: { api_key: 'hermes (yorath_key_AbCdEfG…)' },
    }
    const chain = {
      id: 'x1',
      parent_execution_id: null,
      agent: 'model-api',
      revision_no: 1,
      status: 'succeeded',
      trigger: 'api',
      created_at: '',
      finished_at: null,
      actor: 'Lewis',
      effective_principal: 'Lewis',
      model: 'qwen3.5:9b',
      max_data_class_seen: null,
      api_key: 'hermes (yorath_key_AbCdEfG…)',
    }
    stubApi({
      ...BASE,
      'GET /api/journal/events?limit=50': [started],
      'GET /api/journal/executions/x1': { chain: [chain], events: [started], spans: [] },
    })
    render(<Journal />)
    expect(
      await screen.findByText('Lewis → key hermes (yorath_key_AbCdEfG…) → model-api r2'),
    ).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'execution.started' }))
    const card = await screen.findByRole('region', { name: 'Execution' })
    expect(within(card).getByText(/model-api/, { selector: 'li' })).toHaveTextContent(
      'key hermes (yorath_key_AbCdEfG…)',
    )
  })

  it('the denials tab requests only denials', async () => {
    const api = stubApi({ ...BASE, 'GET /api/journal/events?decision=deny&limit=50': [DENIED] })
    render(<Journal />)
    await screen.findByText('data.accessed')
    await userEvent.click(screen.getByRole('button', { name: 'Denials' }))
    expect(await screen.findByText('no permission for the source rows')).toBeInTheDocument()
    expect(screen.queryByText('data.accessed')).not.toBeInTheDocument()
    api.assertAllAnswered()
  })

  it('filters by agent and user', async () => {
    const api = stubApi({
      ...BASE,
      'GET /api/journal/events?agent=document-assistant&limit=50': [EVENT],
      'GET /api/journal/events?agent=document-assistant&user=u1&limit=50': [],
    })
    render(<Journal />)
    await screen.findByText('data.accessed')
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Agent' }), 'document-assistant')
    await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'User' }), 'Lewis')
    expect(await screen.findByText('No events.')).toBeInTheDocument()
    api.assertAllAnswered()
  })

  it('the card shows the parent, the child, dropped rows and the trace', async () => {
    const chain = {
      id: 'x1',
      parent_execution_id: null,
      agent: 'document-assistant',
      revision_no: 2,
      status: 'succeeded',
      trigger: 'user_message',
      created_at: '',
      finished_at: null,
      actor: 'Lewis',
      effective_principal: 'Lewis',
      model: 'qwen3.5:4b',
      max_data_class_seen: 'confidential',
    }
    const child = { ...chain, id: 'x2', parent_execution_id: 'x1', agent: 'research-agent' }
    const span = {
      span_id: 's1',
      parent_span_id: null,
      execution_id: 'x1',
      name: 'chat qwen3.5:4b',
      kind: 'llm',
      model: 'qwen3.5:4b',
      status: 'ok',
      started_at: '',
      ended_at: null,
      attributes: { 'gen_ai.input.messages': 'Peter Miller' },
    }
    stubApi({
      ...BASE,
      'GET /api/journal/executions/x1': { chain: [chain, child], events: [DENIED], spans: [span] },
    })
    render(<Journal />)
    await userEvent.click(await screen.findByRole('button', { name: 'data.accessed' }))
    const card = await screen.findByRole('region', { name: 'Execution' })
    expect(within(card).getByText(/child:/)).toHaveTextContent('research-agent')
    expect(within(card).getByText(/rows dropped: 1/)).toBeInTheDocument()
    expect(within(card).getByText(/Peter Miller/)).toBeInTheDocument()
  })

  it('the next page starts from the time of the last event', async () => {
    const page = Array.from({ length: 50 }, (_, i) => ({
      ...EVENT,
      id: `p${i}`,
      occurred_at: `2026-11-05T01:00:${String(59 - i).padStart(2, '0')}Z`,
    }))
    const before = encodeURIComponent(page[49].occurred_at)
    const api = stubApi({
      ...BASE,
      'GET /api/journal/events?limit=50': page,
      [`GET /api/journal/events?before=${before}&limit=50`]: [{ ...DENIED, id: 'last' }],
    })
    render(<Journal />)
    await userEvent.click(await screen.findByRole('button', { name: 'More' }))
    expect(await screen.findByText('no permission for the source rows')).toBeInTheDocument()
    expect(screen.getAllByRole('row')).toHaveLength(52)
    expect(screen.queryByRole('button', { name: 'More' })).not.toBeInTheDocument()
    api.assertAllAnswered()
  })

  it('the "Network" tab shows the source, domain and denials', async () => {
    stubApi({
      ...BASE,
      'GET /api/journal/egress?hours=24': [
        {
          node: 'local',
          source: 'openclaw-gw',
          domain: 'example.com',
          requests: 2,
          blocked: 0,
          bytes: 6655,
          last_at: '2026-11-12T16:00:00Z',
        },
        {
          node: 'local',
          source: 'host',
          domain: 'evil.test',
          requests: 1,
          blocked: 1,
          bytes: 0,
          last_at: '2026-11-12T16:00:00Z',
        },
      ],
    })
    render(<Journal />)
    await userEvent.click(await screen.findByRole('button', { name: 'Network' }))
    const table = await screen.findByRole('region', { name: /Node network, last 24 hours/ })
    expect(within(table).getByText('openclaw-gw')).toBeInTheDocument()
    expect(within(table).getByText('example.com')).toBeInTheDocument()
    expect(within(table).getByText('7 KB')).toBeInTheDocument()
    expect(within(table).getAllByText('1').length).toBeGreaterThan(0)
  })
})
