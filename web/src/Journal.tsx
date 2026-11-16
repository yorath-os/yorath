import { useCallback, useEffect, useState } from 'react'
import {
  fetchAgents,
  fetchEgress,
  fetchExecutionDetail,
  fetchJournal,
  fetchUsers,
  type EgressFlow,
  type ExecutionDetail,
  type JournalEvent,
  type JournalFilters,
} from './api'
import { dateTime } from './format'
import { Button, SegmentedControl } from './ui/Controls'
import { DataTable } from './ui/DataTable'
import { StateView } from './ui/StateView'
import { Window } from './ui/Window'

const PAGE = 50

// Criterion 8 chain: actor → principal → revision → model → tool → asset → decision
function origin(e: JournalEvent) {
  const key = typeof e.details.api_key === 'string' ? `key ${e.details.api_key}` : null
  return [
    e.actor,
    key,
    e.effective_principal !== e.actor ? e.effective_principal : null,
    e.agent && `${e.agent}${e.revision_no ? ` r${e.revision_no}` : ''}`,
    e.model,
    e.tool,
    e.data_asset && `${e.data_asset} (${e.data_class})`,
    e.decision,
  ]
    .filter(Boolean)
    .join(' → ')
}

function Card({ detail, onClose }: { detail: ExecutionDetail; onClose: () => void }) {
  return (
    <Window variant="widget" title="Execution" headingLevel={3} onClose={onClose}>
      <div className="a3-detail">
        <ol className="a3-plain-list">
          {detail.chain.map((c) => (
            <li key={c.id}>
              {c.parent_execution_id ? 'child: ' : ''}
              <code>{c.agent}</code> r{c.revision_no}, {c.status}, {c.actor}
              {c.model ? `, model ${c.model}` : ''}
              {c.max_data_class_seen ? `, class ${c.max_data_class_seen}` : ''}
              {c.api_key ? `, key ${c.api_key}` : ''}
            </li>
          ))}
        </ol>
        <section>
          <h4 className="a3-section-label">Events</h4>
          <ul className="a3-plain-list">
            {detail.events.map((e) => (
              <li key={e.id}>
                <code>{e.event_type}</code> {origin(e)}
                {e.reason ? ` — ${e.reason}` : ''}
                {e.details.rows_denied ? `, rows dropped: ${String(e.details.rows_denied)}` : ''}
              </li>
            ))}
          </ul>
        </section>
        <section>
          <h4 className="a3-section-label">Trace</h4>
          {detail.spans.map((s) => (
            <details key={s.span_id}>
              <summary>
                {s.name} ({s.kind}, {s.status})
              </summary>
              <pre className="a3-pre">{JSON.stringify(s.attributes, null, 2)}</pre>
            </details>
          ))}
        </section>
      </div>
    </Window>
  )
}

const size = (b: number) =>
  b >= 1e6 ? `${(b / 1e6).toFixed(1)} MB` : b >= 1e3 ? `${Math.round(b / 1e3)} KB` : `${b} B`

// Node network (change node-egress, decision 36): who went where in the last 24 hours, denials separately
function NetworkLog() {
  const [rows, setRows] = useState<EgressFlow[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    const load = async () => {
      const r = await fetchEgress(24)
      if (r.status === 200) setRows(r.body)
      else setError(`HTTP ${r.status}`)
    }
    void load()
  }, [])
  if (error) return <StateView kind="error" description={error} />
  if (rows === null) return <StateView kind="loading" />
  if (rows.length === 0)
    return (
      <StateView
        kind="empty"
        title="No network requests in the last 24 hours — or node network control is off."
      />
    )
  return (
    <DataTable
      caption="Node network, last 24 hours"
      rows={rows}
      rowKey={(r) => `${r.node}-${r.source}-${r.domain}`}
      columns={[
        {
          id: 'source',
          header: 'Source',
          weight: 1.6,
          render: (r) => <span className="a3-mono a3-text-xs">{r.source}</span>,
        },
        {
          id: 'domain',
          header: 'Domain',
          weight: 2,
          render: (r) => <span className="a3-mono a3-text-xs">{r.domain}</span>,
        },
        { id: 'requests', header: 'Requests', render: (r) => r.requests },
        {
          id: 'blocked',
          header: 'Denied',
          render: (r) => (r.blocked ? <strong>{r.blocked}</strong> : '—'),
        },
        { id: 'bytes', header: 'Volume', hideOnMobile: true, render: (r) => size(r.bytes) },
        { id: 'last', header: 'Last seen', hideOnMobile: true, render: (r) => dateTime(r.last_at) },
      ]}
    />
  )
}

// Audit journal (criterion 8). What happens to the work is in the task manager; here is who did what.
export default function Journal({ executionId }: { executionId?: string }) {
  const [tab, setTab] = useState<'all' | 'denied' | 'network'>('all')
  const [agent, setAgent] = useState('')
  const [user, setUser] = useState('')
  const [decision, setDecision] = useState('')
  const [events, setEvents] = useState<JournalEvent[]>([])
  const [more, setMore] = useState(false)
  const [agents, setAgents] = useState<string[]>([])
  const [users, setUsers] = useState<{ id: string; display_name: string }[]>([])
  const [detail, setDetail] = useState<ExecutionDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(
    async (before?: string) => {
      const filters: JournalFilters = {
        agent,
        user,
        decision: tab === 'denied' ? 'deny' : decision,
        before,
        limit: PAGE,
      }
      try {
        const r = await fetchJournal(filters)
        if (r.status !== 200) throw new Error(`HTTP ${r.status}`)
        setEvents((old) => (before ? [...old, ...r.body] : r.body))
        setMore(r.body.length === PAGE)
        setError(null)
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    },
    [agent, user, decision, tab],
  )

  useEffect(() => {
    const init = async () => {
      const [a, u] = await Promise.all([fetchAgents(), fetchUsers()])
      setAgents(a.body.map((x) => x.name))
      setUsers(u.body)
    }
    void init()
  }, [])

  useEffect(() => {
    const refresh = async () => {
      await load()
    }
    void refresh()
  }, [load])

  const open = useCallback(async (id: string) => setDetail((await fetchExecutionDetail(id)).body), [])

  useEffect(() => {
    if (!executionId) return
    const init = async () => {
      await open(executionId)
    }
    void init()
  }, [executionId, open])

  return (
    <div className="a3-desk__pad a3-desk__pad--app">
      <Window
        variant="app"
        title="Journal"
        flush
        toolbar={
          <>
            <SegmentedControl
              label="Journal view"
              options={[
                { value: 'all', label: 'All events' },
                { value: 'denied', label: 'Denials' },
                { value: 'network', label: 'Network' },
              ]}
              value={tab}
              onChange={setTab}
            />
            {tab !== 'network' && (
              <div className="a3-window__filters">
                <select
                  className="a3-input"
                  aria-label="Agent"
                  value={agent}
                  onChange={(e) => setAgent(e.target.value)}
                >
                  <option value="">all agents</option>
                  {agents.map((a) => (
                    <option key={a}>{a}</option>
                  ))}
                </select>
                <select
                  className="a3-input"
                  aria-label="User"
                  value={user}
                  onChange={(e) => setUser(e.target.value)}
                >
                  <option value="">all users</option>
                  {users.map((u) => (
                    <option key={u.id} value={u.id}>
                      {u.display_name}
                    </option>
                  ))}
                </select>
                {tab === 'all' && (
                  <select
                    className="a3-input"
                    aria-label="Decision"
                    value={decision}
                    onChange={(e) => setDecision(e.target.value)}
                  >
                    <option value="">any decision</option>
                    <option value="permit">permitted</option>
                    <option value="deny">denied</option>
                  </select>
                )}
              </div>
            )}
          </>
        }
      >
        <div className="a3-stack">
          {detail && <Card detail={detail} onClose={() => setDetail(null)} />}
          {tab === 'network' ? (
            <NetworkLog />
          ) : error ? (
            <StateView kind="error" description={error} actionLabel="Retry" onAction={() => void load()} />
          ) : events.length === 0 ? (
            <StateView kind="empty" title="No events." />
          ) : (
            <DataTable
              caption="Journal events"
              rows={events}
              rowKey={(e) => e.id}
              columns={[
                {
                  id: 'time',
                  header: 'Time',
                  weight: 1.4,
                  hideOnMobile: true,
                  render: (e) => dateTime(e.occurred_at),
                },
                {
                  id: 'event',
                  header: 'Event',
                  weight: 1.5,
                  render: (e) =>
                    e.execution_id ? (
                      <button
                        type="button"
                        className="a3-link a3-mono a3-text-xs"
                        onClick={() => void open(e.execution_id as string)}
                      >
                        {e.event_type}
                      </button>
                    ) : (
                      <span className="a3-mono a3-text-xs">{e.event_type}</span>
                    ),
                },
                { id: 'chain', header: 'Chain', weight: 4, render: (e) => origin(e) },
                { id: 'reason', header: 'Reason', weight: 2, render: (e) => e.reason ?? '—' },
              ]}
            />
          )}
          {more && tab !== 'network' && (
            <Button onClick={() => void load(events[events.length - 1].occurred_at)}>More</Button>
          )}
        </div>
      </Window>
    </div>
  )
}
