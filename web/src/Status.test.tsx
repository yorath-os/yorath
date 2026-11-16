import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { stubApi } from '../test/stub-api'
import Status from './Status'

const BASE = {
  'GET /api/health': { status: 'ok', checks: {} },
  'GET /api/version': {
    distribution: { name: 'yorath', version: '0.2.6', schema_version: 'x' },
    core: '0.2.6',
    build: 'abc',
    schema_head: 'x',
    schema_matches_manifest: true,
    images: {},
    models: {},
  },
}
const REPORT = {
  available: true,
  stale: false,
  checked_at: '2026-11-12T12:00:00+00:00',
  agents: [
    { agent: 'hermes', where: 'host', pid: 101, user: 'root', root: true },
    {
      agent: 'openclaw',
      where: 'container',
      container: 'openclaw-test-1',
      image: 'ghcr.io/openclaw/openclaw:2026.11.3',
    },
  ],
  traces: [],
  containers: [{ name: 'supabase_db', image: 'x', project: 'supabase' }],
  egress: {
    mode: 'on',
    table: true,
    intact: true,
    proxy: true,
    service: 'active',
    alarm: null,
    enforced: true,
  },
}

describe('System: agents outside Yorath', () => {
  it('shows agents on the host as root and in third-party containers', async () => {
    stubApi({ ...BASE, 'GET /api/setup/host-report': REPORT })
    render(<Status />)
    const list = await screen.findByRole('list', { name: 'Agents outside Yorath' })
    expect(within(list).getByText(/on the host, pid 101, as root/)).toHaveTextContent('hermes')
    expect(within(list).getByText(/container openclaw-test-1/)).toHaveTextContent('openclaw')
    expect(screen.getByText('found: 2')).toBeInTheDocument()
    expect(screen.getByText(/run outside Yorath/)).toBeInTheDocument()
    expect(screen.getByText(/Other containers not from Yorath: supabase_db/)).toBeInTheDocument()
  })

  it('without a report suggests running the check; marks a stale one', async () => {
    stubApi({
      ...BASE,
      'GET /api/setup/host-report': { ...REPORT, available: false, agents: [], containers: [] },
    })
    const { unmount } = render(<Status />)
    expect(await screen.findByText(/The node check hasn't run/)).toBeInTheDocument()
    unmount()
    stubApi({ ...BASE, 'GET /api/setup/host-report': { ...REPORT, stale: true, agents: [] } })
    render(<Status />)
    expect(await screen.findByText('none found')).toBeInTheDocument()
    expect(screen.getByText(/the report is stale/)).toBeInTheDocument()
  })

  it('a member gets no card', async () => {
    stubApi({ ...BASE, 'GET /api/setup/host-report': { status: 403, body: { detail: 'owner_only' } } })
    render(<Status />)
    expect(await screen.findByText(/Distribution yorath 0.2.6/)).toBeInTheDocument() // anchor: the screen loaded
    expect(screen.queryByText('Agents outside Yorath')).not.toBeInTheDocument()
  })

  it('node network: working, rules removed, off', async () => {
    stubApi({ ...BASE, 'GET /api/setup/host-report': REPORT })
    const { unmount } = render(<Status />)
    expect(await screen.findByText('control: working')).toBeInTheDocument()
    unmount()
    const removed = { ...REPORT.egress, table: false, intact: false, enforced: false }
    stubApi({ ...BASE, 'GET /api/setup/host-report': { ...REPORT, egress: removed } })
    const second = render(<Status />)
    expect(await screen.findByText(/Network rules were removed/)).toBeInTheDocument()
    expect(screen.getByText('control: broken')).toBeInTheDocument()
    second.unmount()
    stubApi({
      ...BASE,
      'GET /api/setup/host-report': { ...REPORT, egress: { ...REPORT.egress, mode: 'off', enforced: false } },
    })
    render(<Status />)
    expect(await screen.findByText(/Node traffic bypasses Yorath/)).toBeInTheDocument()
    expect(screen.getByText('control: off')).toBeInTheDocument()
  })
})
