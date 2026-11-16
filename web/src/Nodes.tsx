import { useEffect, useRef, useState } from 'react'
import {
  connectedError,
  createJoinToken,
  fetchConnected,
  fetchNodes,
  fetchPlacements,
  nodeAction,
  type ClusterNode,
  type ConnectedAgent,
  type JoinToken,
  type ModelServer,
} from './api'
import { dateTime } from './format'
import { Button } from './ui/Controls'
import { StatusBadge } from './ui/Indicators'
import { Notice } from './ui/Lists'
import type { Tone } from './ui/types'
import { Card } from './ui/Wizard'
import { DOCS_URL, PRODUCT } from './brand'

// Cluster (change cluster-nodes, decision 35): nodes join with a one-time token, WireGuard tunnel.

const STATUS: Record<ClusterNode['status'], { label: string; tone: Tone }> = {
  active: { label: 'running', tone: 'ok' },
  pending: { label: 'awaiting approval', tone: 'warn' },
  lost: { label: 'unavailable', tone: 'error' },
  revoked: { label: 'revoked', tone: 'neutral' },
}

const COMMUNITY_UNITS = 3
// Node heartbeat every 15 s; core declares a node unavailable after 60 s (LOST_AFTER in cluster/service.py).
// The panel shows two missed heartbeats right away without waiting for core (field test 13.11.2026: a powered-off
// server looked "running" for a minute).
const STALE_AFTER_S = 35
const LOST_AFTER_S = 60
const REFRESH_MS = 10_000

const ago = (s: number) =>
  s < 60 ? `${s} s` : s < 3600 ? `${Math.floor(s / 60)} min` : `${Math.floor(s / 3600)} h`

export function Nodes() {
  const [nodes, setNodes] = useState<ClusterNode[] | null>(null)
  const [agents, setAgents] = useState<ConnectedAgent[]>([])
  const [servers, setServers] = useState<ModelServer[]>([])
  const [token, setToken] = useState<JoinToken | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<string | null>(null)

  const alive = useRef(true) // a closed panel stops its chain of requests halfway
  const load = async () => {
    const r = await fetchNodes()
    if (!alive.current) return
    setNodes(r.status === 200 ? r.body : null)
    const a = await fetchConnected()
    if (!alive.current) return
    setAgents(a.status === 200 && a.body ? a.body.filter((x) => x.connected) : [])
    const m = await fetchPlacements()
    if (!alive.current) return
    setServers(m.status === 200 && m.body ? m.body.servers : [])
  }
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    alive.current = true
    void load()
    const poll = setInterval(() => void load(), REFRESH_MS)
    const tick = setInterval(() => setNow(Date.now()), 1000)
    return () => {
      alive.current = false
      clearInterval(poll)
      clearInterval(tick)
    }
  }, [])
  if (nodes === null) return null

  const issue = async () => {
    const r = await createJoinToken()
    if (r.status === 201 && r.body) {
      setToken(r.body)
      setError(null)
    } else setError(connectedError(r.status, r.body))
  }
  const act = async (name: string, action: 'approve' | 'revoke') => {
    const r = await nodeAction(name, action)
    setConfirm(null)
    if (r.status === 200) setError(null)
    else setError(connectedError(r.status, r.body))
    await load()
  }
  const mainName = nodes.find((n) => n.role === 'main')?.name
  const used = nodes.filter((n) => n.status !== 'revoked').reduce((s, n) => s + n.units, 0)

  return (
    <Card
      title="Nodes"
      titleExtra={<StatusBadge tone="neutral" label={`${used} of ${COMMUNITY_UNITS} Community units`} />}
    >
      <p>
        The main node is the single entry point: the panel, keys, journal and agent restrictions. Worker nodes
        hold agents and models and connect to the main node over a WireGuard tunnel — a node has no inbound
        ports.
      </p>
      {error && <Notice tone="error">{error}</Notice>}
      <div className="a3-nodes" role="list" aria-label="Cluster nodes">
        {nodes.map((n) => {
          const silent = n.last_seen_at
            ? Math.max(0, Math.round((now - new Date(n.last_seen_at).getTime()) / 1000))
            : null
          const stale =
            n.role === 'worker' && n.status === 'active' && silent !== null && silent > STALE_AFTER_S
          const s = stale ? { label: 'no heartbeat', tone: 'warn' as Tone } : STATUS[n.status]
          const kind = stale ? 'stale' : n.status
          // an agent without a node runs on the main node
          const here = agents.filter((a) => (a.node ?? mainName) === n.name)
          const models = servers.filter((sv) => sv.node === n.name)
          // shown only when the owner narrowed them: by default a node may receive every class
          const classes = n.allowed_data_classes ?? []
          return (
            <section
              key={n.id}
              role="listitem"
              aria-label={`Node ${n.name}`}
              className={`a3-node a3-node--${kind}${n.role === 'main' ? ' a3-node--main' : ''}`}
            >
              <header className="a3-node__head">
                <span className="a3-node__role">{n.role === 'main' ? 'main node' : 'worker node'}</span>
                <StatusBadge tone={s.tone} label={s.label} />
              </header>
              <strong className="a3-node__name">{n.name}</strong>
              <span className="a3-node__ip">{n.overlay_ip ?? (n.role === 'main' ? '10.88.0.1' : '—')}</span>
              <dl className="a3-node__res">
                <div>
                  <dt>vCPU</dt>
                  <dd>{n.cpu_count}</dd>
                </div>
                <div>
                  <dt>memory</dt>
                  <dd>{n.ram_gb} GB</dd>
                </div>
                <div>
                  <dt>GPU</dt>
                  <dd>{n.gpu_count ? `${n.gpu_count}×` : '—'}</dd>
                </div>
              </dl>
              {n.gpu_count > 0 && <span className="a3-node__gpu">{n.gpu ?? 'GPU'}</span>}
              {models.length > 0 && (
                <div className="a3-node__agents" aria-label={`Model servers on ${n.name}`}>
                  {models.map((sv) => (
                    <span
                      key={sv.server}
                      className={`a3-node__agent a3-node__agent--${sv.status === 'ready' ? 'running' : 'none'}`}
                    >
                      {sv.server}: {sv.model ?? 'free'}
                    </span>
                  ))}
                </div>
              )}
              {classes.length > 0 && classes.length < 4 && (
                <span className="a3-node__gpu">data: {classes.join(', ')} only</span>
              )}
              <div className="a3-node__agents" aria-label={`Agents on ${n.name}`}>
                {here.length === 0 ? (
                  <span className="a3-node__empty">no agents</span>
                ) : (
                  here.map((a) => (
                    <span key={a.name} className={`a3-node__agent a3-node__agent--${a.status ?? 'none'}`}>
                      {a.name}
                    </span>
                  ))
                )}
              </div>
              <footer className="a3-node__foot">
                <span title={n.last_seen_at ? dateTime(n.last_seen_at) : undefined}>
                  {silent === null
                    ? 'no heartbeat yet'
                    : stale
                      ? `no heartbeat for ${ago(silent)} — unavailable in ${Math.max(0, LOST_AFTER_S - silent)} s`
                      : n.status === 'lost'
                        ? `no heartbeat for ${ago(silent)} — it will come back on its own when the server is online again`
                        : `heartbeat ${ago(silent)} ago`}
                </span>
                {n.role === 'worker' && n.status === 'pending' && (
                  <Button size="sm" variant="primary" onClick={() => void act(n.name, 'approve')}>
                    Approve {n.name}
                  </Button>
                )}
                {n.role === 'worker' &&
                  n.status !== 'revoked' &&
                  (confirm === n.name ? (
                    <Button size="sm" variant="danger" onClick={() => void act(n.name, 'revoke')}>
                      Revoke {n.name}: the node's agents will become unavailable
                    </Button>
                  ) : (
                    <Button size="sm" onClick={() => setConfirm(n.name)}>
                      Revoke {n.name}…
                    </Button>
                  ))}
              </footer>
            </section>
          )
        })}
        {!token &&
          (used >= COMMUNITY_UNITS ? (
            <div role="listitem" className="a3-node a3-node--add a3-node--full">
              <strong>
                {used} of {COMMUNITY_UNITS} units
              </strong>
              The Community limit is reached: a new node won't join. For more, use Enterprise
            </div>
          ) : (
            <button type="button" className="a3-node a3-node--add" onClick={() => void issue()}>
              <span aria-hidden="true">+</span>
              Join a server
            </button>
          ))}
      </div>
      {token && (
        <Notice tone="info">
          The token is single-use and valid until {dateTime(token.expires_at)}. On the new server, first get
          the same {PRODUCT} version into /opt/yorath (as for the first install), then run:
          <pre className="a3-pre">{token.command}</pre>
          Then approve the node here.{' '}
          <a href={`${DOCS_URL}/cluster/add-a-server/`} target="_blank" rel="noreferrer">
            Step-by-step guide
          </a>
        </Notice>
      )}
    </Card>
  )
}
