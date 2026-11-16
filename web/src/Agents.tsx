import { useCallback, useEffect, useState } from 'react'
import AgentForm, { type AgentFormMode } from './AgentForm'
import { ConnectedSection, ConnectPanel } from './Connected'
import { OutsideAgents } from './OutsideAgents'
import {
  connectedError,
  fetchAgentExecutions,
  fetchAgents,
  fetchAgentSource,
  fetchConnected,
  fetchKeyModels,
  fetchNodes,
  setAgentEnabled,
  setAgentModel,
  type AgentInfo,
  type KeyModel,
  type AgentSource,
  type AgentTool,
  type ExecutionInfo,
} from './api'
import { asDataClass, dateTime, triggerLabel } from './format'
import { AgentsScreen, type AgentRow, type AgentStatus } from './screens/a3/AgentsScreen'
import { TASK_STATUS, type TaskStatus } from './screens/a3/JournalScreen'
import { Button } from './ui/Controls'
import { DataTable } from './ui/DataTable'
import { DataClassBadge, StatusBadge } from './ui/Indicators'
import { KeyValueList, Notice } from './ui/Lists'
import { StateView } from './ui/StateView'
import type { ScreenState } from './ui/types'
import { Window } from './ui/Window'

const INSTALL_HINT = 'Create an agent with the button above: describe its goal and choose its sources.'

const fmtCost = (usd: string | null) => (usd === null ? '—' : `$${Number(usd).toFixed(4)}`)
const fmtTool = (t: AgentTool) => (t.targets.length ? `${t.name} → ${t.targets.join(', ')}` : t.name)

function agentStatus(a: AgentInfo): AgentStatus {
  if (!a.enabled) return 'disabled'
  return a.executions.by_status.running ? 'running' : 'idle'
}

const toRow = (a: AgentInfo, i: number): AgentRow => ({
  id: a.name,
  name: a.display_name,
  avatarTone: ((i % 4) + 1) as AgentRow['avatarTone'],
  description: `${a.runtime} · ${a.mode} · control: ${a.control_level}`,
  status: agentStatus(a),
  actionLabel: a.enabled ? 'Disable' : 'Enable',
})

function AgentText({ source }: { source: AgentSource | null }) {
  if (!source) return null
  return (
    <section>
      <h3 className="a3-section-label">Agent text</h3>
      <details>
        <summary>{source.path}</summary>
        <pre className="a3-pre">{source.text}</pre>
      </details>
    </section>
  )
}

const CONNECTED_RUNTIMES = new Set(['openclaw', 'hermes'])

// The model the agent uses (change field-fixes-0-7): any chat model of the catalog, OpenRouter included;
// a connected agent can't take a model whose window is below its minimum
function AgentModel({ agent, onChanged }: { agent: AgentInfo; onChanged: () => void }) {
  const [catalog, setCatalog] = useState<KeyModel[]>([])
  const [minContext, setMinContext] = useState(0)
  const [value, setValue] = useState(agent.model ?? '')
  const [note, setNote] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const connected = CONNECTED_RUNTIMES.has(agent.runtime)
  useEffect(() => {
    const load = async () => {
      const m = await fetchKeyModels()
      setCatalog(m.status === 200 ? m.body : [])
      if (connected) {
        const c = await fetchConnected()
        const spec = c.status === 200 ? c.body.find((a) => a.name === agent.name) : undefined
        setMinContext(spec?.min_context ?? 0)
      }
    }
    void load()
  }, [agent.name, connected])
  const fileModel = agent.revision?.card.model ?? 'local/default'
  const save = async () => {
    const r = await setAgentModel(agent.name, value || null)
    if (r.status === 200) {
      setNote({ tone: 'ok', text: `New requests go to ${value || fileModel}.` })
      onChanged()
    } else setNote({ tone: 'error', text: connectedError(r.status, r.body) })
  }
  return (
    <section aria-label="Model">
      <h3 className="a3-section-label">Model</h3>
      <div className="a3-agent-actions">
        <select
          className="a3-input"
          aria-label="Model"
          value={value}
          onChange={(e) => {
            setValue(e.target.value)
            setNote(null)
          }}
        >
          <option value="">{fileModel} (the agent’s file)</option>
          {catalog
            .filter((m) => m.name !== fileModel)
            .map((m) => (
              <option key={m.name} value={m.name} disabled={m.context_length < minContext}>
                {m.name}, {m.context_length.toLocaleString('en-US')} tokens
                {m.external ? ', external: requests leave the server' : ''}
                {m.context_length < minContext ? ` — needs ${minContext.toLocaleString('en-US')} tokens` : ''}
              </option>
            ))}
        </select>
        <Button onClick={() => void save()} disabled={value === (agent.model ?? '')}>
          Save model
        </Button>
      </div>
      {note && <Notice tone={note.tone === 'ok' ? 'info' : 'error'}>{note.text}</Notice>}
    </section>
  )
}

function AgentCard({
  agent,
  onClose,
  onForm,
  onChanged,
}: {
  agent: AgentInfo
  onClose: () => void
  onForm: (mode: AgentFormMode) => void
  onChanged: () => void
}) {
  const connected = CONNECTED_RUNTIMES.has(agent.runtime)
  const [runs, setRuns] = useState<ExecutionInfo[] | null>(null)
  const [source, setSource] = useState<AgentSource | null>(null)

  useEffect(() => {
    const load = async () => {
      const r = await fetchAgentExecutions(agent.name)
      setRuns(r.status === 200 ? r.body : [])
      const text = await fetchAgentSource(agent.name)
      setSource(text.status === 200 ? text.body : null)
    }
    void load()
  }, [agent.name])

  const card = agent.revision?.card
  const dataClass = asDataClass(card?.max_data_class)
  const summary =
    Object.entries(agent.executions.by_status)
      .map(([status, n]) => `${status} ${n}`)
      .join(', ') || '—'
  return (
    <div className="a3-desk__pad a3-desk__pad--app">
      <Window variant="app" title={`Agent · ${agent.display_name}`} onClose={onClose}>
        <div className="a3-detail">
          {agent.description && <p>{agent.description}</p>}
          <KeyValueList
            items={[
              { key: 'Name', value: agent.name, mono: true },
              { key: 'Runtime', value: `${agent.runtime} · ${agent.mode} · control: ${agent.control_level}` },
              {
                key: 'Revision',
                value: agent.revision
                  ? `r${agent.revision.revision_no} · ${agent.revision.path}`
                  : 'no revision',
              },
              {
                key: 'Models',
                value: card?.models.join(', ') || '—',
                hint: `uses ${agent.model ?? card?.model ?? '—'}`,
              },
              { key: 'Tools', value: card?.tools.map(fmtTool).join(', ') || '—' },
              { key: 'Sources', value: card?.data.join(', ') || '—' },
              {
                key: 'Class ceiling',
                value: dataClass ? <DataClassBadge dataClass={dataClass} /> : (card?.max_data_class ?? '—'),
              },
              ...(agent.service_account
                ? [{ key: 'Service account', value: agent.service_account.display_name }]
                : []),
              { key: 'Execution', value: `${agent.executions.total} (${summary})` },
              { key: 'Cost', value: fmtCost(agent.cost_usd) },
            ]}
          />
          <AgentModel agent={agent} onChanged={onChanged} />
          {connected ? (
            <ConnectedSection name={agent.name} onChanged={onChanged} />
          ) : (
            <>
              <AgentText source={source} />
              <div className="a3-agent-actions">
                <Button onClick={() => onForm({ kind: 'base', agent })}>Create from this</Button>
                {source && !source.builtin && (
                  <Button onClick={() => onForm({ kind: 'edit', agent })}>Edit</Button>
                )}
              </div>
            </>
          )}
          <section>
            <h3 className="a3-section-label">Recent executions</h3>
            {runs === null ? (
              <StateView kind="loading" />
            ) : runs.length === 0 ? (
              <StateView kind="empty" title="No executions yet." />
            ) : (
              <DataTable
                caption="Recent executions"
                rows={runs}
                rowKey={(r) => r.id}
                columns={[
                  { id: 'at', header: 'Started', weight: 1.6, render: (r) => dateTime(r.created_at) },
                  {
                    id: 'status',
                    header: 'Status',
                    weight: 1.5,
                    render: (r) => {
                      const s = TASK_STATUS[r.status as TaskStatus]
                      return s ? <StatusBadge tone={s.tone} label={s.label} /> : r.status
                    },
                  },
                  {
                    id: 'trigger',
                    header: 'Trigger',
                    weight: 1.4,
                    hideOnMobile: true,
                    render: (r) => triggerLabel(r.trigger),
                  },
                  {
                    id: 'actor',
                    header: 'Started by',
                    weight: 1.5,
                    render: (r) => r.initiated_by?.display_name ?? '—',
                  },
                  {
                    id: 'principal',
                    header: 'On behalf of',
                    weight: 1.5,
                    hideOnMobile: true,
                    render: (r) => r.effective_principal?.display_name ?? '—',
                  },
                  {
                    id: 'model',
                    header: 'Model',
                    weight: 1.4,
                    hideOnMobile: true,
                    render: (r) => r.model ?? '—',
                  },
                  {
                    id: 'class',
                    header: 'Class',
                    weight: 1.1,
                    hideOnMobile: true,
                    render: (r) => r.max_data_class_seen ?? '—',
                  },
                  {
                    id: 'tokens',
                    header: 'Tokens',
                    weight: 1,
                    hideOnMobile: true,
                    render: (r) =>
                      r.usage ? `${r.usage.input_tokens ?? 0} / ${r.usage.output_tokens ?? 0}` : '—',
                  },
                  {
                    id: 'cost',
                    header: 'Cost',
                    weight: 1,
                    hideOnMobile: true,
                    render: (r) =>
                      `${fmtCost(r.cost_usd)}${r.cost_estimated && r.cost_usd !== null ? ' ≈' : ''}`,
                  },
                  {
                    id: 'outcome',
                    header: 'Result',
                    weight: 1.1,
                    hideOnMobile: true,
                    render: (r) => (r.outcome ? `${r.outcome.kind} (${r.outcome.status})` : '—'),
                  },
                ]}
              />
            )}
          </section>
        </div>
      </Window>
    </div>
  )
}

// Agents: the list, the card and the own-agent form. Built-in agents here are examples.
export default function Agents({
  selected,
  onSelect,
}: {
  selected?: string
  onSelect: (name: string | null) => void
}) {
  const [agents, setAgents] = useState<AgentInfo[]>([])
  const [state, setState] = useState<ScreenState>({ kind: 'loading' })
  const [actionError, setActionError] = useState<string | null>(null)
  const [form, setForm] = useState<AgentFormMode | null>(null)
  const [connecting, setConnecting] = useState(false)
  // A connected agent enabled from the list while the cluster has worker nodes: ask its node (#18)
  const [enabling, setEnabling] = useState<string | null>(null)

  const reload = useCallback(async () => {
    try {
      const r = await fetchAgents()
      if (r.status !== 200) throw new Error(`HTTP ${r.status}`)
      setAgents(r.body)
      setState(r.body.length ? { kind: 'ready' } : { kind: 'empty' })
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      setState({ kind: 'error', message, onRetry: () => void reload() })
    }
  }, [])

  useEffect(() => {
    const load = async () => {
      await reload()
    }
    void load()
  }, [reload])

  const toggle = async (name: string) => {
    const agent = agents.find((a) => a.name === name)
    if (!agent) return
    if (!agent.enabled && agent.control_level === 'gateway') {
      const n = await fetchNodes()
      if (n.status === 200 && n.body.some((x) => x.role === 'worker' && x.status === 'active')) {
        // only agents from the connected catalog run on a node; model-api and other external ones don't
        const c = await fetchConnected()
        const row = c.status === 200 ? c.body.find((a) => a.name === name && !a.connected) : undefined
        // connected before and its node still here: enabled as it was, no form (field-fixes-0-7)
        const nodeGone = !!row?.node && !n.body.some((x) => x.name === row.node && x.status === 'active')
        if (row && (!row.deployed || nodeGone)) {
          setEnabling(name)
          return
        }
      }
    }
    const r = await setAgentEnabled(name, !agent.enabled)
    setActionError(
      r.status === 200
        ? null
        : r.status === 409
          ? `Agent ${name} can't be connected: ${connectedError(r.status, r.body)}`
          : `Could not update agent ${name}: ${connectedError(r.status, r.body)}`,
    )
    await reload()
  }

  const agent = agents.find((a) => a.name === selected)
  if (form) {
    const done = async (name: string) => {
      setForm(null)
      await reload()
      onSelect(name)
    }
    return <AgentForm mode={form} onDone={(name) => void done(name)} onCancel={() => setForm(null)} />
  }
  if (connecting || enabling) {
    const close = () => {
      setConnecting(false)
      setEnabling(null)
    }
    const done = async (name: string) => {
      close()
      await reload()
      onSelect(name)
    }
    return <ConnectPanel only={enabling ?? undefined} onDone={(name) => void done(name)} onCancel={close} />
  }
  if (agent)
    return (
      <AgentCard
        agent={agent}
        onClose={() => onSelect(null)}
        onForm={setForm}
        onChanged={() => void reload()}
      />
    )
  return (
    <>
      {actionError && (
        <div className="a3-desk__pad">
          <Notice tone="error" actions={<Button onClick={() => setActionError(null)}>Close</Button>}>
            {actionError}
          </Notice>
        </div>
      )}
      <AgentsScreen
        state={state}
        agents={agents.map(toRow)}
        onOpen={onSelect}
        onAction={(name) => void toggle(name)}
        emptyDescription={INSTALL_HINT}
        onInstall={() => setForm({ kind: 'new' })}
        installLabel="Create agent"
        secondaryAction={{ label: 'Connect agent', onClick: () => setConnecting(true) }}
        footer={<OutsideAgents />}
      />
    </>
  )
}
