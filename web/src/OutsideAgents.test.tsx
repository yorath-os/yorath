import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { stubApi } from '../test/stub-api'
import { checkedText } from './format'
import { OutsideAgents } from './OutsideAgents'

// Agents outside Yorath on every node (change outside-agents-cluster): listed, and connected to the
// Yorath model with a key instead of a switch.

const NODES = {
  nodes: [
    {
      node: 'main',
      main: true,
      node_status: 'active',
      checked_at: null,
      agents: [],
      traces: [],
      model_url: null,
    },
    {
      node: 'vm13234',
      main: false,
      node_status: 'active',
      checked_at: '2026-11-15T18:40:44+00:00',
      agents: [
        { agent: 'openclaw', where: 'host', pid: 18661, user: 'root', root: true, command: 'node' },
        { agent: 'openclaw', where: 'host', pid: 21069, user: 'root', root: true, command: 'openclaw' },
      ],
      traces: [{ agent: 'openclaw', path: '/root/.openclaw', owner: 'root' }],
      model_url: 'http://10.88.0.1:8000/v1',
    },
  ],
}
const KEY = { id: 'k1', name: 'openclaw-vm13234', key: 'yorath_key_SECRET', agent: null, created_at: 'x' }

describe('OutsideAgents', () => {
  it('lists an agent found on a node, with Connect instead of a switch', async () => {
    stubApi({ 'GET /api/cluster/outside-agents': NODES })
    render(<OutsideAgents />)
    const table = await screen.findByRole('table', { name: 'Found outside Yorath' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(1) // two processes of one agent on one node are one row
    expect(within(rows[0]).getByText('openclaw')).toBeInTheDocument()
    expect(within(rows[0]).getByText('vm13234')).toBeInTheDocument()
    expect(within(rows[0]).getByText('host process, root')).toBeInTheDocument()
    expect(within(rows[0]).getByRole('button', { name: 'Connect openclaw on vm13234' })).toBeInTheDocument()
    expect(within(table).queryByRole('switch')).not.toBeInTheDocument()
  })

  it('Connect issues a key once and shows the OpenClaw lines with the tunnel address', async () => {
    const api = stubApi({
      'GET /api/cluster/outside-agents': NODES,
      'POST /api/api-keys': { status: 201, body: KEY },
    })
    render(<OutsideAgents />)
    await userEvent.click(await screen.findByRole('button', { name: 'Connect openclaw on vm13234' }))
    await userEvent.click(screen.getByRole('button', { name: 'Create a key' }))
    expect(api.bodyOf('POST /api/api-keys')).toEqual({ name: 'openclaw-vm13234' })
    expect(await screen.findByText('yorath_key_SECRET')).toBeInTheDocument()
    const config = screen.getByLabelText('OpenClaw settings')
    expect(config).toHaveTextContent('"baseUrl": "http://10.88.0.1:8000/v1"')
    expect(config).toHaveTextContent('"primary": "yorath/local/default"')
    expect(screen.getByText(/revoke the key in API keys/)).toBeInTheDocument()
  })

  it('says nothing when no node found an agent', async () => {
    stubApi({ 'GET /api/cluster/outside-agents': { nodes: [NODES.nodes[0]] } })
    const { container } = render(<OutsideAgents />)
    await new Promise((r) => setTimeout(r, 20))
    expect(container).toBeEmptyDOMElement()
  })

  it('says how old each node scan is and marks an old one or a lost node stale', () => {
    const now = Date.parse('2026-11-15T19:00:44+00:00')
    const node = NODES.nodes[1]
    expect(checkedText(node, now)).toBe('20 min ago')
    expect(checkedText({ ...node, checked_at: '2026-11-15T16:40:44+00:00' }, now)).toBe('2 h ago · stale')
    expect(checkedText({ ...node, node_status: 'lost' }, now)).toBe('20 min ago · stale')
    expect(checkedText({ ...node, checked_at: null }, now)).toBe('not checked yet')
  })

  it('a cut report says so and how to see the whole list', async () => {
    stubApi({
      'GET /api/cluster/outside-agents': {
        nodes: [{ ...NODES.nodes[1], truncated: true }],
      },
    })
    render(<OutsideAgents />)
    expect(
      await screen.findByText(/The scan on vm13234 found more agents than a report carries/),
    ).toBeInTheDocument()
  })
})
