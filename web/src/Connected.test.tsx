import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import Agents from './Agents'
import { stubApi } from '../test/stub-api'

// Connected agents (change connected-agents): "Connect agent" and the agent card.

const CATALOG_ROW = {
  name: 'openclaw',
  description: 'OpenClaw personal agent',
  license: 'MIT',
  homepage: 'https://openclaw.ai',
  tested_version: '2026.11.3',
  min_context: 65536,
  connected: false,
  enabled: false,
  version: null,
  untested: false,
  network_enabled: true,
  telegram: false,
  telegram_users: [],
  status: null,
  status_detail: null,
  running_version: null,
  running_digest: null,
  reported_at: null,
}
const LIVE = {
  ...CATALOG_ROW,
  connected: true,
  enabled: true,
  version: 'latest',
  untested: true,
  telegram: true,
  telegram_users: ['42'],
  telegram_check: { state: 'ok', bot: 'yorath_bot', checked_at: new Date().toISOString() },
  status: 'running',
  running_version: '2026.11.9',
}
const AGENT = {
  id: 'a1',
  name: 'openclaw',
  display_name: 'openclaw',
  description: 'OpenClaw personal agent',
  hosting: 'managed',
  mode: 'autonomous',
  enabled: true,
  runtime: 'openclaw',
  control_level: 'gateway',
  owner: null,
  service_account: null,
  revision: null,
  executions: { total: 0, by_status: {}, last_at: null },
  cost_usd: null,
}
const BOT = '123456:secret-bot-token'

describe('Connected agents', () => {
  it('connecting shows the core denial with a hint, then sends the version and the bot', async () => {
    let attempt = 0
    const api = stubApi({
      'GET /api/agents': [{ ...AGENT, enabled: false }],
      'GET /api/agents/connected': [
        CATALOG_ROW,
        { ...CATALOG_ROW, name: 'hermes', tested_version: 'v0.23.1' },
      ],
      'GET /api/cluster/nodes': [
        { name: 'main', role: 'main', status: 'active', cpu_count: 4, ram_gb: 8 },
        { name: 'node-2', role: 'worker', status: 'active', cpu_count: 4, ram_gb: 8 },
        { name: 'node-3', role: 'worker', status: 'pending', cpu_count: 4, ram_gb: 8 },
      ],
      'POST /api/agents/connected/openclaw': () =>
        ++attempt === 1
          ? {
              status: 409,
              body: {
                detail: {
                  code: 'egress_off',
                  message: 'node network control is off. Turn it on: yorath egress on',
                },
              },
            }
          : { status: 200, body: LIVE },
    })
    const onSelect = vi.fn()
    render(<Agents onSelect={onSelect} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Connect agent' }))
    const section = await screen.findByRole('region', { name: 'Connect openclaw' })
    expect(screen.getByRole('region', { name: 'Connect hermes' })).toBeInTheDocument()
    expect(screen.getAllByText(/gateway level/i).length).toBeGreaterThan(0)
    await userEvent.click(screen.getByRole('button', { name: 'Connect openclaw' }))
    expect(await screen.findByText(/yorath egress on/)).toBeInTheDocument()

    const inputs = section.querySelectorAll('input')
    await userEvent.type(inputs[0], 'latest')
    await userEvent.type(inputs[1], BOT)
    expect(inputs[1]).toHaveAttribute('type', 'password')
    await userEvent.type(await screen.findByLabelText('Who to answer in Telegram'), '42, 77')
    const nodeSelect = (await screen.findAllByLabelText('Node'))[0] as HTMLSelectElement
    expect([...nodeSelect.options].map((o) => o.value)).toEqual(['', 'node-2']) // a pending node is not offered
    await userEvent.selectOptions(nodeSelect, 'node-2')
    await userEvent.click(screen.getByRole('button', { name: 'Connect openclaw' }))
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith('openclaw'))
    expect(api.bodyOf('POST /api/agents/connected/openclaw')).toEqual({
      version: 'latest',
      node: 'node-2',
      telegram_token: BOT,
      telegram_users: ['42', '77'],
    })
  })

  it('card: status, untested version, internet turns off, the token is not shown', async () => {
    let network = true
    const api = stubApi({
      'GET /api/agents': [AGENT],
      'GET /api/agents/openclaw/executions?limit=20': [],
      'GET /api/agents/openclaw/source': { status: 404, body: {} },
      'GET /api/agents/connected': () => [{ ...LIVE, network_enabled: network }],
      'GET /api/journal/egress?hours=24': [
        {
          node: 'local',
          source: 'openclaw',
          domain: 'example.com',
          requests: 3,
          blocked: 1,
          bytes: 10,
          last_at: '',
        },
        {
          node: 'local',
          source: 'host',
          domain: 'deb.debian.org',
          requests: 9,
          blocked: 0,
          bytes: 10,
          last_at: '',
        },
      ],
      'PUT /api/agents/connected/openclaw/network': (body: unknown) => {
        network = (body as { enabled: boolean }).enabled
        return { status: 200, body: { ...LIVE, network_enabled: network } }
      },
    })
    render(<Agents selected="openclaw" onSelect={vi.fn()} />)
    expect(await screen.findByText('Running')).toBeInTheDocument()
    expect(screen.getByText(/2026.11.9 — not tested/)).toBeInTheDocument()
    expect(screen.getByText(/example.com — 3, denied 1/)).toBeInTheDocument()
    expect(screen.queryByText(/deb.debian.org/)).not.toBeInTheDocument()
    expect(screen.queryByText(BOT)).not.toBeInTheDocument()
    expect(screen.getByText(/The agent sees its bot token itself/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Create from this' })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Turn off internet' }))
    expect(await screen.findByRole('button', { name: 'Turn on internet' })).toBeInTheDocument()
    expect(api.bodyOf('PUT /api/agents/connected/openclaw/network')).toEqual({ enabled: false })
  })

  it('deletion happens only after a second click', async () => {
    const api = stubApi({
      'GET /api/agents': [AGENT],
      'GET /api/agents/openclaw/executions?limit=20': [],
      'GET /api/agents/openclaw/source': { status: 404, body: {} },
      'GET /api/agents/connected': [LIVE],
      'GET /api/journal/egress?hours=24': [],
      'DELETE /api/agents/connected/openclaw?purge=true': { ...LIVE, connected: false },
    })
    render(<Agents selected="openclaw" onSelect={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Delete…' }))
    expect(api.calls.some((c) => c.key.startsWith('DELETE'))).toBe(false)
    await userEvent.click(screen.getByRole('button', { name: 'Delete along with the agent’s memory' }))
    await waitFor(() =>
      expect(api.calls.some((c) => c.key === 'DELETE /api/agents/connected/openclaw?purge=true')).toBe(true),
    )
  })

  it('How to use: chat, a key narrowed to the agent with agent/<name>, Telegram status', async () => {
    const api = stubApi({
      'GET /api/agents': [AGENT],
      'GET /api/agents/openclaw/executions?limit=20': [],
      'GET /api/agents/openclaw/source': { status: 404, body: {} },
      'GET /api/agents/connected': [LIVE],
      'GET /api/journal/egress?hours=24': [],
      'HEAD /yorath-root-ca.crt': () => new Response('', { status: 200 }),
      'POST /api/api-keys': {
        status: 201,
        body: { id: 'k1', name: 'openclaw-api', key: 'yk-agent-key', agent: 'openclaw' },
      },
    })
    render(<Agents selected="openclaw" onSelect={vi.fn()} />)
    const how = await screen.findByRole('region', { name: 'How to use' })
    expect(how).toHaveTextContent('Chat → new conversation → pick openclaw')
    expect(how).toHaveTextContent(
      'Telegram accepts the bot @yorath_bot and answers 42. Write to it in Telegram.',
    )
    const call = screen.getByLabelText('Call openclaw over the API')
    expect(call).toHaveTextContent('"model": "agent/openclaw"')
    expect(call).toHaveTextContent('Bearer $YORATH_API_KEY')

    await userEvent.click(screen.getByRole('button', { name: 'Create a key for openclaw' }))
    expect(await screen.findByText(/The key is shown once/)).toBeInTheDocument()
    expect(api.bodyOf('POST /api/api-keys')).toEqual({ name: 'openclaw-api', agent: 'openclaw' })
    expect(screen.getByLabelText('Call openclaw over the API')).toHaveTextContent(
      'curl --cacert yorath-root-ca.crt',
    )
    expect(screen.getByLabelText('Call openclaw over the API')).toHaveTextContent('Bearer yk-agent-key')
    expect(screen.queryByRole('button', { name: 'Create a key for openclaw' })).not.toBeInTheDocument()
  })

  it('Telegram: the node’s check says why the bot is silent, Check again asks the node again', async () => {
    let checked = false
    const unreachable = { state: 'unreachable', detail: 'HTTP 503', checked_at: new Date().toISOString() }
    const api = stubApi({
      'GET /api/agents': [AGENT],
      'GET /api/agents/openclaw/executions?limit=20': [],
      'GET /api/agents/openclaw/source': { status: 404, body: {} },
      'GET /api/agents/connected': () => ({
        status: 200,
        body: [{ ...LIVE, node: 'vm13239', telegram_check: checked ? LIVE.telegram_check : unreachable }],
      }),
      'GET /api/journal/egress?hours=24': [],
      'HEAD /yorath-root-ca.crt': () => new Response('', { status: 404 }),
      'POST /api/agents/connected/openclaw/telegram/check': () => {
        checked = true
        return { status: 200, body: { ...LIVE, telegram_check: null } }
      },
    })
    render(<Agents selected="openclaw" onSelect={vi.fn()} />)
    const how = await screen.findByRole('region', { name: 'How to use' })
    expect(how).toHaveTextContent('Telegram is not reachable from vm13239: the bot gets no messages.')
    expect(how).not.toHaveTextContent('Write to it in Telegram')
    expect(screen.getByText('not reachable from this server, answers: 42')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Check again' }))
    await waitFor(() => expect(how).toHaveTextContent('Telegram accepts the bot @yorath_bot'))
    expect(api.calls.some((c) => c.key === 'POST /api/agents/connected/openclaw/telegram/check')).toBe(true)
  })

  it('Telegram: a saved bot without a check says it is being checked, a rejected token asks for a new one', async () => {
    stubApi({
      'GET /api/agents': [AGENT],
      'GET /api/agents/openclaw/executions?limit=20': [],
      'GET /api/agents/openclaw/source': { status: 404, body: {} },
      'GET /api/agents/connected': [{ ...LIVE, telegram_check: null }],
      'GET /api/journal/egress?hours=24': [],
      'HEAD /yorath-root-ca.crt': () => new Response('', { status: 404 }),
    })
    const { unmount } = render(<Agents selected="openclaw" onSelect={vi.fn()} />)
    expect(await screen.findByRole('region', { name: 'How to use' })).toHaveTextContent(
      'Checking that Telegram reaches the bot…',
    )
    expect(screen.queryByRole('button', { name: 'Check again' })).not.toBeInTheDocument()
    unmount()

    const rejected = { state: 'rejected', detail: 'HTTP 401', checked_at: new Date().toISOString() }
    stubApi({
      'GET /api/agents': [AGENT],
      'GET /api/agents/openclaw/executions?limit=20': [],
      'GET /api/agents/openclaw/source': { status: 404, body: {} },
      'GET /api/agents/connected': [{ ...LIVE, telegram_check: rejected }],
      'GET /api/journal/egress?hours=24': [],
      'HEAD /yorath-root-ca.crt': () => new Response('', { status: 404 }),
    })
    render(<Agents selected="openclaw" onSelect={vi.fn()} />)
    expect(await screen.findByRole('region', { name: 'How to use' })).toHaveTextContent(
      'Telegram rejected the bot token: put a new one from @BotFather below.',
    )
  })

  it('How to use appears only for a connected agent; without a bot it says how to add one', async () => {
    stubApi({
      'GET /api/agents': [AGENT],
      'GET /api/agents/openclaw/executions?limit=20': [],
      'GET /api/agents/openclaw/source': { status: 404, body: {} },
      'GET /api/agents/connected': [{ ...LIVE, telegram: false, telegram_users: [] }],
      'GET /api/journal/egress?hours=24': [],
      'HEAD /yorath-root-ca.crt': () => new Response('', { status: 404 }),
    })
    const { unmount } = render(<Agents selected="openclaw" onSelect={vi.fn()} />)
    expect(await screen.findByRole('region', { name: 'How to use' })).toHaveTextContent(
      'No bot yet: put a token from @BotFather and your Telegram user id below.',
    )
    unmount()

    stubApi({
      'GET /api/agents': [AGENT],
      'GET /api/agents/openclaw/executions?limit=20': [],
      'GET /api/agents/openclaw/source': { status: 404, body: {} },
      'GET /api/agents/connected': [CATALOG_ROW],
      'GET /api/journal/egress?hours=24': [],
    })
    render(<Agents selected="openclaw" onSelect={vi.fn()} />)
    expect(await screen.findByText('not connected')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'How to use' })).not.toBeInTheDocument()
  })

  it('Enable in a cluster asks the node instead of putting the agent on main (#18)', async () => {
    const api = stubApi({
      'GET /api/agents': [{ ...AGENT, enabled: false }],
      'GET /api/agents/connected': [CATALOG_ROW, { ...CATALOG_ROW, name: 'hermes' }],
      'GET /api/cluster/nodes': [
        { name: 'main', role: 'main', status: 'active', cpu_count: 4, ram_gb: 8 },
        { name: 'vm13234', role: 'worker', status: 'active', cpu_count: 8, ram_gb: 32 },
      ],
      'POST /api/agents/connected/openclaw': LIVE,
    })
    const onSelect = vi.fn()
    render(<Agents onSelect={onSelect} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Enable: openclaw' }))
    expect(await screen.findByRole('region', { name: 'Connect openclaw' })).toBeInTheDocument()
    expect(screen.getByText('Enable openclaw')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Connect hermes' })).not.toBeInTheDocument()
    expect(api.calls.some((c) => c.key.startsWith('PATCH'))).toBe(false)
    await userEvent.selectOptions(await screen.findByLabelText('Node'), 'vm13234')
    await userEvent.click(screen.getByRole('button', { name: 'Connect openclaw' }))
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith('openclaw'))
    expect(api.bodyOf('POST /api/agents/connected/openclaw')).toEqual({
      version: null,
      node: 'vm13234',
      telegram_token: null,
      telegram_users: [],
    })
  })

  it('Enable on a single node switches the agent on right away', async () => {
    const api = stubApi({
      'GET /api/agents': [{ ...AGENT, enabled: false }],
      'GET /api/cluster/nodes': [
        { name: 'main', role: 'main', status: 'active', cpu_count: 4, ram_gb: 8 },
        { name: 'node-3', role: 'worker', status: 'pending', cpu_count: 4, ram_gb: 8 },
      ],
      'PATCH /api/agents/openclaw': { enabled: true },
    })
    render(<Agents onSelect={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Enable: openclaw' }))
    await waitFor(() => expect(api.bodyOf('PATCH /api/agents/openclaw')).toEqual({ enabled: true }))
    expect(screen.queryByRole('region', { name: 'Connect openclaw' })).not.toBeInTheDocument()
  })

  it('Enable of model-api in a cluster switches it on: it is not a catalog agent', async () => {
    const api = stubApi({
      'GET /api/agents': [{ ...AGENT, name: 'model-api', display_name: 'model-api', enabled: false }],
      'GET /api/cluster/nodes': [
        { name: 'main', role: 'main', status: 'active', cpu_count: 4, ram_gb: 8 },
        { name: 'vm13234', role: 'worker', status: 'active', cpu_count: 8, ram_gb: 32 },
      ],
      'GET /api/agents/connected': [CATALOG_ROW],
      'PATCH /api/agents/model-api': { enabled: true },
    })
    render(<Agents onSelect={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Enable: model-api' }))
    await waitFor(() => expect(api.bodyOf('PATCH /api/agents/model-api')).toEqual({ enabled: true }))
    expect(screen.queryByText('Enable model-api')).not.toBeInTheDocument()
  })
})
