import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import Agents from './Agents'
import { stubApi } from '../test/stub-api'

const AGENT = {
  id: 'a1',
  name: 'document-assistant',
  display_name: 'Document Assistant',
  description: 'Answers from documents',
  hosting: 'managed',
  mode: 'delegated',
  enabled: true,
  runtime: 'native',
  control_level: 'tool',
  owner: null,
  service_account: null,
  revision: {
    id: 'r1',
    revision_no: 3,
    path: 'document-assistant.md',
    content_sha256: 'x',
    created_at: '',
    card: {
      model: 'local/default',
      models: ['local/default', 'openrouter/default'],
      tools: [
        { name: 'search_docs', targets: [] },
        { name: 'ask_agent', targets: ['research-agent'] },
      ],
      data: ['team-docs', 'hr-vacations'],
      max_data_class: 'confidential',
      triggers: ['chat'],
    },
  },
  executions: { total: 2, by_status: { succeeded: 1, failed: 1 }, last_at: '2026-11-05T01:00:00Z' },
  cost_usd: '0.5',
}
const OFF = { ...AGENT, id: 'a2', name: 'research-agent', display_name: 'Research Agent', enabled: false }
const RUN = {
  id: 'x1',
  status: 'failed',
  trigger: 'user_message',
  created_at: '2026-11-05T01:00:00Z',
  finished_at: null,
  initiated_by: { id: 'u', display_name: 'Anna', kind: 'user' },
  effective_principal: { id: 'u', display_name: 'Anna', kind: 'user' },
  revision_no: 3,
  model: 'qwen3.5:4b',
  usage: { input_tokens: 10, output_tokens: 5 },
  cost_usd: '0.01',
  cost_estimated: true,
  max_data_class_seen: 'internal',
  parent_execution_id: null,
  outcome: null,
}

const SOURCE = {
  name: 'document-assistant',
  path: 'document-assistant.md',
  builtin: true,
  text: '---\nname: document-assistant\n---\nYou are an assistant.\n',
  instructions: 'You are an assistant.',
}
const OPTIONS = {
  tools: [
    { name: 'search_docs', description: 'Document search' },
    { name: 'web_search', description: 'Web search' },
  ],
  assets: [{ name: 'team-docs', data_class: 'internal', kind: 'documents' }],
  models: [
    { name: 'local/default', model_id: 'qwen3.5:4b', external: false },
    { name: 'openrouter/default', model_id: 'openai/x', external: true },
  ],
}

describe('Own agent', () => {
  const card = {
    'GET /api/agents': [AGENT],
    'GET /api/agents/document-assistant/executions?limit=20': [],
    'GET /api/agents/document-assistant/source': SOURCE,
    'GET /api/agents/options': OPTIONS,
  }

  it('the card shows the example text; a built-in agent can’t be edited', async () => {
    stubApi(card)
    render(<Agents selected="document-assistant" onSelect={vi.fn()} />)
    expect(await screen.findByText(/name: document-assistant/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create from this' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument()
  })

  it('creating from an example takes the instruction and sources, doesn’t offer ask_agent', async () => {
    const api = stubApi({ ...card, 'POST /api/agents': { status: 201, body: {} } })
    const onSelect = vi.fn()
    render(<Agents selected="document-assistant" onSelect={onSelect} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Create from this' }))
    expect(await screen.findByLabelText('Instruction')).toHaveValue('You are an assistant.')
    expect(screen.queryByLabelText(/ask_agent/)).not.toBeInTheDocument()
    expect(screen.getByLabelText('Name')).toHaveValue('')

    await userEvent.type(screen.getByLabelText('Name'), 'faq-helper')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith('faq-helper'))
    expect(api.bodyOf('POST /api/agents')).toMatchObject({
      name: 'faq-helper',
      instructions: 'You are an assistant.',
      data: ['team-docs'],
      tools: ['search_docs'],
      models: ['local/default', 'openrouter/default'],
    })
  })

  it('a new agent defaults to the local model only; a server rejection is visible', async () => {
    const api = stubApi({
      'GET /api/agents': [AGENT],
      'GET /api/agents/options': OPTIONS,
      'POST /api/agents': { status: 409, body: { detail: { code: 'agent_exists', message: 'name taken' } } },
    })
    render(<Agents onSelect={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Create agent' }))
    await userEvent.type(await screen.findByLabelText('Name'), 'faq-helper')
    await userEvent.type(screen.getByLabelText('Instruction'), 'Answer from the FAQ')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText(/name taken/)).toBeInTheDocument()
    expect(api.bodyOf('POST /api/agents')).toMatchObject({ models: ['local/default'] })
  })

  it('the "API key" checkbox goes into the form and is read from triggers when editing', async () => {
    const own = {
      ...AGENT,
      name: 'sales-bot',
      revision: {
        ...AGENT.revision,
        path: 'custom/sales-bot.md',
        card: { ...AGENT.revision.card, triggers: ['chat', 'api'] },
      },
    }
    const api = stubApi({
      'GET /api/agents': [own],
      'GET /api/agents/sales-bot/executions?limit=20': [],
      'GET /api/agents/sales-bot/source': { ...SOURCE, name: 'sales-bot', builtin: false },
      'GET /api/agents/options': OPTIONS,
      'PUT /api/agents/sales-bot': { ...own },
    })
    render(<Agents selected="sales-bot" onSelect={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Edit' }))
    const box = await screen.findByRole('checkbox', { name: /Can be called with an API key/ })
    await waitFor(() => expect(box).toBeChecked())
    await userEvent.click(box)
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.bodyOf('PUT /api/agents/sales-bot')).toMatchObject({ api: false }))
  })

  it('an own agent is edited in place', async () => {
    const own = {
      ...AGENT,
      name: 'faq-helper',
      revision: { ...AGENT.revision, path: 'custom/faq-helper.md' },
    }
    const api = stubApi({
      'GET /api/agents': [own],
      'GET /api/agents/faq-helper/executions?limit=20': [],
      'GET /api/agents/faq-helper/source': { ...SOURCE, name: 'faq-helper', builtin: false },
      'GET /api/agents/options': OPTIONS,
      'PUT /api/agents/faq-helper': { ...own },
    })
    render(<Agents selected="faq-helper" onSelect={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Edit' }))
    expect(await screen.findByLabelText('Instruction')).toHaveValue('You are an assistant.')
    expect(screen.queryByLabelText('Name')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.bodyOf('PUT /api/agents/faq-helper')).toBeTruthy())
  })
})

describe('Agents', () => {
  it('shows status and mode; a disabled agent is marked', async () => {
    stubApi({ 'GET /api/agents': [AGENT, OFF] })
    render(<Agents onSelect={vi.fn()} />)
    const list = await screen.findByRole('list', { name: 'Installed agents' })
    expect(within(list).getAllByText('native · delegated · control: tool')).toHaveLength(2)
    expect(within(list).getByText('Idle')).toBeInTheDocument()
    expect(within(list).getByText('Disabled')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create agent' })).toBeInTheDocument()
  })

  it('the toggle sends the new value and reloads the list', async () => {
    const api = stubApi({
      'GET /api/agents': [AGENT],
      'PATCH /api/agents/document-assistant': { enabled: false },
    })
    render(<Agents onSelect={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Disable: Document Assistant' }))
    expect(api.bodyOf('PATCH /api/agents/document-assistant')).toEqual({ enabled: false })
    expect(api.calls.filter((c) => c.key === 'GET /api/agents')).toHaveLength(2)
  })

  it('a server rejection is shown, not swallowed', async () => {
    stubApi({
      'GET /api/agents': [AGENT],
      'PATCH /api/agents/document-assistant': { status: 403, body: {} },
    })
    render(<Agents onSelect={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Disable: Document Assistant' }))
    expect(await screen.findByText(/Could not update agent/)).toBeInTheDocument()
  })

  it('a connected agent that cannot start shows the reason from the core', async () => {
    const reason =
      'the agent needs a local model context window of 65536+ tokens, currently 8192. Use a node with a GPU (window 65,536) or a larger model window'
    stubApi({
      'GET /api/agents': [{ ...OFF, name: 'openclaw', display_name: 'openclaw' }],
      'PATCH /api/agents/openclaw': {
        status: 409,
        body: { detail: { code: 'context_too_small', message: reason } },
      },
    })
    render(<Agents onSelect={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Enable: openclaw' }))
    expect(await screen.findByText(`Agent openclaw can't be connected: ${reason}`)).toBeInTheDocument()
  })

  it('the card shows permissions and recent executions', async () => {
    const api = stubApi({
      'GET /api/cluster/outside-agents': { nodes: [] },
      'GET /api/agents': [AGENT],
      'GET /api/agents/document-assistant/executions?limit=20': [RUN],
      'GET /api/agents/document-assistant/source': SOURCE,
    })
    render(<Agents selected="document-assistant" onSelect={vi.fn()} />)
    expect(await screen.findByText('search_docs, ask_agent → research-agent')).toBeInTheDocument()
    expect(screen.getByText('team-docs, hr-vacations')).toBeInTheDocument()
    expect(screen.getByText('2 (succeeded 1, failed 1)')).toBeInTheDocument()
    expect(await screen.findByText('error')).toBeInTheDocument()
    expect(screen.getByText('qwen3.5:4b')).toBeInTheDocument()
    api.assertAllAnswered()
  })

  it('an empty list explains how an agent appears', async () => {
    stubApi({ 'GET /api/agents': [] })
    render(<Agents onSelect={vi.fn()} />)
    expect(await screen.findByText(/Create an agent/)).toBeInTheDocument()
  })

  it('a load error with retry', async () => {
    const api = stubApi({ 'GET /api/agents': { status: 500, body: {} } })
    render(<Agents onSelect={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Retry' }))
    expect(api.calls.filter((c) => c.key === 'GET /api/agents')).toHaveLength(2)
  })
})
