import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { stubApi } from '../test/stub-api'
import Wizard from './Wizard'

const HARDWARE = {
  hardware: { vcpu: 8, ram_gb: 8, disk_free_gb: 40, gpu_name: null, vram_gb: null },
  tier: { tier: 'acceptable', model: 'qwen3.5:4b', expected_answer_seconds: 36, note: null },
  local_mode: true,
  recommended_model: 'qwen3.5:4b',
  embeddings_model: 'embeddinggemma-2:570m',
  expected_answer_seconds: 36,
  minimum: { vcpu: 4, ram_gb: 8 },
  external_model: 'openai/gpt',
}
const STATUS = {
  setup_completed: false,
  models: { chat: null, embeddings: null, external: null },
  resources: [],
  openrouter_configured: false,
  web_search_configured: false,
  secrets_available: true,
}
const READY = { ...STATUS, models: { chat: 'local/default', embeddings: 'local/embeddings', external: null } }
const MODELS = {
  recommended: { chat: 'qwen3.5:4b', embeddings: 'embeddinggemma-2:570m' },
  local_mode: true,
  installed: [],
  registered: [],
  budgets: {
    'qwen3.5:4b': {
      model: 'qwen3.5:4b',
      rss_gb: 9,
      vram_gb: null,
      estimated: false,
      basis: 'benchmark',
      limit_gb: 7,
      fits: false,
    },
  },
}
const world = (status: unknown = READY, extra = {}) =>
  stubApi({
    'GET /api/setup/hardware': HARDWARE,
    'GET /api/setup/status': status,
    'GET /api/setup/models': MODELS,
    ...extra,
  })
const agent = (name: string, extra: Record<string, unknown> = {}) => ({
  id: name,
  name,
  display_name: name,
  description: `description of ${name}`,
  hosting: 'managed',
  mode: 'delegated',
  enabled: true,
  builtin: true,
  runtime: 'native',
  control_level: 'tool',
  owner: null,
  service_account: null,
  revision: {
    id: 'r',
    revision_no: 1,
    path: `${name}.md`,
    content_sha256: 'x',
    created_at: '2026-11-08T00:00:00Z',
    card: {
      model: 'local/default',
      models: ['local/default'],
      tools: [
        { name: 'search_docs', targets: [] },
        { name: 'ask_agent', targets: ['research-agent'] },
      ],
      data: ['team-docs', 'hr-vacations'],
      max_data_class: 'confidential',
      triggers: ['chat'],
    },
  },
  executions: { total: 0, by_status: {}, last_at: null },
  cost_usd: '0',
  ...extra,
})
const toAgents = async () => {
  await screen.findByText('acceptable')
  for (const name of ['Next', 'Next', 'Skip', 'Next']) await next(name)
}
const next = (name = 'Next') => userEvent.click(screen.getByRole('button', { name }))

describe('Setup wizard', () => {
  it('shows the hardware and the steps', async () => {
    world()
    render(<Wizard onDone={vi.fn()} onOpenData={vi.fn()} />)
    expect(await screen.findByText('acceptable')).toBeInTheDocument()
    expect(screen.getByText(/about 36 s/)).toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Setup steps' })).toBeInTheDocument()
  })

  it('doesn’t go further without models and warns about insufficient memory', async () => {
    world(STATUS)
    render(<Wizard onDone={vi.fn()} onOpenData={vi.fn()} />)
    await screen.findByText('acceptable')
    await next()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
    expect(screen.getByText(/doesn't fit in 7 GB/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Pull anyway' })).toBeInTheDocument()
  })

  it('a key rejection is shown in the window, without a browser dialog', async () => {
    const alert = vi.fn()
    vi.stubGlobal('alert', alert)
    world(READY, { 'PUT /api/setup/openrouter': { status: 422, body: { detail: 'invalid key' } } })
    render(<Wizard onDone={vi.fn()} onOpenData={vi.fn()} />)
    await screen.findByText('acceptable')
    await next()
    await next()
    await userEvent.type(screen.getByLabelText('OpenRouter key'), 'sk-or-bad')
    await next('Check and save')
    expect(await screen.findByRole('alert')).toHaveTextContent('invalid key')
    expect(alert).not.toHaveBeenCalled()
    expect(screen.getByRole('heading', { name: 'External services' })).toBeInTheDocument()
  })

  it('a region block is named, not shown as "invalid key"', async () => {
    const detail = {
      code: 'provider_access_denied',
      message: 'OpenRouter does not allow this server (response 403)',
    }
    world(READY, { 'PUT /api/setup/openrouter': { status: 422, body: { detail } } })
    render(<Wizard onDone={vi.fn()} onOpenData={vi.fn()} />)
    await screen.findByText('acceptable')
    await next()
    await next()
    await userEvent.type(screen.getByLabelText('OpenRouter key'), 'sk-or-ok')
    await next('Check and save')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('OpenRouter does not allow this server (response 403)')
    expect(alert).not.toHaveTextContent('Key rejected')
  })

  it('a search key saved from a server Tavily refuses says why search won’t work yet', async () => {
    const warning = "Tavily refused this server's address (403) although the key is valid"
    world(READY, { 'PUT /api/setup/tavily': { leaves_server: 'The query text leaves the server.', warning } })
    render(<Wizard onDone={vi.fn()} onOpenData={vi.fn()} />)
    await screen.findByText('acceptable')
    await next()
    await next()
    await userEvent.type(screen.getByLabelText('Tavily key'), 'tvly-ok-key')
    await userEvent.click(screen.getByRole('checkbox', { name: /search queries leaves/ }))
    await next('Save search key')
    expect(await screen.findByText(warning)).toBeInTheDocument()
    expect(screen.getByText('The query text leaves the server.')).toBeInTheDocument()
  })

  it('a rejected search key is named, not shown as JSON', async () => {
    world(READY, { 'PUT /api/setup/tavily': { status: 422, body: { detail: 'invalid_api_key' } } })
    render(<Wizard onDone={vi.fn()} onOpenData={vi.fn()} />)
    await screen.findByText('acceptable')
    await next()
    await next()
    await userEvent.type(screen.getByLabelText('Tavily key'), 'tvly-bad-key')
    await userEvent.click(screen.getByRole('checkbox', { name: /search queries leaves/ }))
    await next('Save search key')
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Tavily rejected the key: it is invalid or revoked',
    )
  })

  it('finishing is recorded and closes the wizard', async () => {
    const onDone = vi.fn()
    const api = world(READY, {
      'POST /api/setup/complete': { setup_completed: true },
      'GET /api/agents': [agent('document-assistant')],
    })
    render(<Wizard onDone={onDone} onOpenData={vi.fn()} />)
    await screen.findByText('acceptable')
    await next()
    await next()
    await next('Skip')
    await next()
    await screen.findByText('Enabled: document-assistant')
    await next()
    await next('Finish the wizard')
    await vi.waitFor(() => expect(onDone).toHaveBeenCalled())
    expect(api.calls.some((c) => c.key === 'POST /api/setup/complete')).toBe(true)
  })

  it('core unavailable: the window explains', async () => {
    stubApi({
      'GET /api/setup/hardware': { status: 500, body: {} },
      'GET /api/setup/status': { status: 500, body: {} },
    })
    render(<Wizard onDone={vi.fn()} onOpenData={vi.fn()} />)
    expect(await screen.findByText('Wizard unavailable')).toBeInTheDocument()
  })

  it('the "Agents" step: built-in agents with permissions, own agents hidden, disabling is saved', async () => {
    const api = world(READY, {
      'GET /api/agents': [
        agent('document-assistant'),
        agent('developer-agent', { control_level: 'sandbox' }),
        agent('faq-helper', { builtin: false }),
      ],
      'PATCH /api/agents/document-assistant': { name: 'document-assistant', enabled: false },
    })
    render(<Wizard onDone={vi.fn()} onOpenData={vi.fn()} />)
    await toAgents()
    expect(await screen.findAllByText('search_docs, ask_agent → research-agent')).toHaveLength(2)
    expect(screen.getAllByText('team-docs, hr-vacations')).toHaveLength(2)
    expect(screen.getByText(/Needs a GitHub App/)).toBeInTheDocument()
    expect(screen.queryByText('Enabled: faq-helper')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Enabled: document-assistant')).toBeChecked()
    await userEvent.click(screen.getByLabelText('Enabled: document-assistant'))
    await vi.waitFor(() =>
      expect(api.bodyOf('PATCH /api/agents/document-assistant')).toEqual({ enabled: false }),
    )
  })

  it('GPU count at the "Hardware" step', async () => {
    stubApi({
      'GET /api/setup/hardware': {
        ...HARDWARE,
        hardware: { ...HARDWARE.hardware, gpu_name: 'A10', vram_gb: 24, gpu_count: 2 },
      },
      'GET /api/setup/status': READY,
      'GET /api/setup/models': MODELS,
    })
    render(<Wizard onDone={vi.fn()} onOpenData={vi.fn()} />)
    expect(await screen.findByText('2 × A10, 24 GB')).toBeInTheDocument()
  })
})
