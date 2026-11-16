import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { Nodes } from './Nodes'
import { stubApi } from '../test/stub-api'

// Cluster (change cluster-nodes): "System → Nodes".

const MAIN = {
  id: 'n1',
  name: 'main',
  role: 'main',
  status: 'active',
  overlay_ip: null,
  cpu_count: 4,
  ram_gb: 8,
  gpu: null,
  gpu_count: 0,
  has_model: false,
  last_seen_at: null,
  units: 1,
}
const PENDING = {
  ...MAIN,
  id: 'n2',
  name: 'node-2',
  role: 'worker',
  status: 'pending',
  overlay_ip: '10.88.0.2',
}

const NO_MODELS = { models: [], servers: [] }

describe('Nodes', () => {
  it('a token with the join command, node approval, Community units', async () => {
    let nodes = [MAIN, PENDING]
    const api = stubApi({
      'GET /api/cluster/nodes': () => nodes,
      'GET /api/agents/connected': [],
      'GET /api/cluster/models': NO_MODELS,
      'POST /api/cluster/tokens': {
        status: 201,
        body: {
          token: 'yorath_join_x.-',
          expires_at: '2026-11-14T10:00:00Z',
          command: 'sh /opt/yorath/installer/install.sh --join https://main yorath_join_x.-',
        },
      },
      'POST /api/cluster/nodes/node-2/approve': () => {
        nodes = [MAIN, { ...PENDING, status: 'active' }]
        return { status: 200, body: nodes[1] }
      },
    })
    render(<Nodes />)
    expect(await screen.findByText('2 of 3 Community units')).toBeInTheDocument()
    expect(screen.getByText('awaiting approval')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Join a server' }))
    expect(await screen.findByText(/install.sh --join https:\/\/main yorath_join_x/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Approve node-2' }))
    await waitFor(() => expect(screen.queryByText('awaiting approval')).not.toBeInTheDocument())
    expect(screen.getAllByText('running').length).toBe(2)
    api.assertAllAnswered()
  })

  it('a node card shows its agents; an agent without a node is on the main node, an unconnected one is hidden', async () => {
    const agent = (name: string, node: string | null, connected = true) => ({
      name,
      description: null,
      license: 'MIT',
      homepage: '',
      tested_version: '1',
      min_context: 0,
      connected,
      enabled: connected,
      version: null,
      untested: false,
      network_enabled: true,
      telegram: false,
      telegram_users: [],
      status: connected ? 'running' : null,
      node,
      status_detail: null,
      running_version: null,
      running_digest: null,
      reported_at: null,
    })
    const api = stubApi({
      'GET /api/cluster/nodes': [MAIN, { ...PENDING, status: 'active' }],
      'GET /api/cluster/models': NO_MODELS,
      'GET /api/agents/connected': [
        agent('openclaw', 'node-2'),
        agent('hermes', null),
        agent('other', null, false),
      ],
    })
    render(<Nodes />)
    const worker = await screen.findByRole('listitem', { name: 'Node node-2' })
    await waitFor(() => expect(within(worker).getByText('openclaw')).toBeInTheDocument())
    expect(within(worker).queryByText('hermes')).not.toBeInTheDocument()
    const main = screen.getByRole('listitem', { name: 'Node main' })
    expect(within(main).getByText('hermes')).toBeInTheDocument()
    expect(within(main).getByText('main node')).toBeInTheDocument()
    expect(screen.queryByText('other')).not.toBeInTheDocument()
    api.assertAllAnswered()
  })

  it('a worker node silent for over 35 s shows "no heartbeat" before core declares it unavailable', async () => {
    const ago = (s: number) => new Date(Date.now() - s * 1000).toISOString()
    stubApi({
      'GET /api/cluster/nodes': [
        { ...MAIN, last_seen_at: ago(50) },
        { ...PENDING, status: 'active', last_seen_at: ago(50) },
        { ...PENDING, id: 'n3', name: 'node-3', status: 'active', last_seen_at: ago(5) },
      ],
      'GET /api/agents/connected': [],
      'GET /api/cluster/models': NO_MODELS,
    })
    render(<Nodes />)
    const silent = await screen.findByRole('listitem', { name: 'Node node-2' })
    expect(within(silent).getByText('no heartbeat')).toBeInTheDocument()
    expect(within(silent).getByText(/no heartbeat for 5\d s — unavailable in \d+ s/)).toBeInTheDocument()
    const alive = screen.getByRole('listitem', { name: 'Node node-3' })
    expect(within(alive).getByText('running')).toBeInTheDocument()
    expect(within(alive).getByText(/heartbeat \d s ago/)).toBeInTheDocument()
    // the main node isn't judged by heartbeat: its answer is this very page
    expect(
      within(screen.getByRole('listitem', { name: 'Node main' })).getByText('running'),
    ).toBeInTheDocument()
  })

  it('the Community limit is reached — joining is impossible, an explanation instead of the button', async () => {
    const worker = { ...PENDING, status: 'active' }
    stubApi({
      'GET /api/cluster/nodes': [MAIN, worker, { ...worker, id: 'n3', name: 'node-3' }],
      'GET /api/agents/connected': [],
      'GET /api/cluster/models': NO_MODELS,
    })
    render(<Nodes />)
    expect(await screen.findByText(/The Community limit is reached/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Join a server' })).not.toBeInTheDocument()
  })

  it('revoking happens only after a second click; a core rejection is visible', async () => {
    const active = { ...PENDING, status: 'active' }
    const api = stubApi({
      'GET /api/cluster/nodes': [MAIN, active],
      'GET /api/agents/connected': [],
      'GET /api/cluster/models': NO_MODELS,
      'POST /api/cluster/tokens': {
        status: 409,
        body: {
          detail: {
            code: 'cluster_not_ready',
            message: 'first, once on the main node server: yorath node token',
          },
        },
      },
      'POST /api/cluster/nodes/node-2/revoke': { ...active, status: 'revoked' },
    })
    render(<Nodes />)
    await userEvent.click(await screen.findByRole('button', { name: 'Join a server' }))
    expect(await screen.findByText(/yorath node token/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Revoke node-2…' }))
    expect(api.calls.some((c) => c.key.includes('revoke'))).toBe(false)
    await userEvent.click(screen.getByRole('button', { name: /Revoke node-2: the node's agents/ }))
    await waitFor(() =>
      expect(api.calls.some((c) => c.key === 'POST /api/cluster/nodes/node-2/revoke')).toBe(true),
    )
  })

  it('a node card shows its model servers and narrowed data classes', async () => {
    const gpuNode = {
      ...PENDING,
      status: 'active',
      gpu: 'NVIDIA A10',
      gpu_count: 2,
      units: 2,
      allowed_data_classes: ['public', 'internal'],
    }
    const server = (name: string, model: string | null) => ({
      node: 'node-2',
      server: name,
      gpus: [Number(name.slice(3))],
      gpu: 'NVIDIA A10',
      vram_gb: 23,
      slots: 1,
      model,
      status: model ? 'ready' : null,
      node_status: 'active',
    })
    const api = stubApi({
      'GET /api/cluster/nodes': [
        { ...MAIN, allowed_data_classes: ['public', 'internal', 'confidential', 'restricted'] },
        gpuNode,
      ],
      'GET /api/agents/connected': [],
      'GET /api/cluster/models': {
        models: [],
        servers: [server('gpu0', 'local/qwen3.5:9b'), server('gpu1', null)],
      },
    })
    render(<Nodes />)
    const servers = await screen.findByLabelText('Model servers on node-2')
    expect(within(servers).getByText('gpu0: local/qwen3.5:9b')).toBeInTheDocument()
    expect(within(servers).getByText('gpu1: free')).toBeInTheDocument()
    expect(screen.getByText('data: public, internal only')).toBeInTheDocument()
    // the main node has no model server in this answer and allows every class: nothing extra is shown
    expect(screen.queryByLabelText('Model servers on main')).not.toBeInTheDocument()
    expect(screen.getAllByText(/^data: /).length).toBe(1)
    api.assertAllAnswered()
  })
})

describe('Nodes: a closed panel', () => {
  it('stops its chain of requests when it is closed halfway', async () => {
    // fetch is replaced by hand here: the first answer is held until the panel is closed
    let release!: () => void
    const held = new Promise<void>((r) => (release = r))
    const calls: string[] = []
    vi.stubGlobal('fetch', async (url: string) => {
      calls.push(url)
      if (url === '/api/cluster/nodes') await held
      return new Response(JSON.stringify(url === '/api/cluster/nodes' ? [MAIN] : []), { status: 200 })
    })
    const { unmount } = render(<Nodes />)
    await waitFor(() => expect(calls).toEqual(['/api/cluster/nodes']))
    unmount()
    release()
    await new Promise((r) => setTimeout(r, 20))
    expect(calls).toEqual(['/api/cluster/nodes'])
  })
})
