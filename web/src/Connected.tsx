import { useEffect, useState } from 'react'
import { CopyBlock } from './ApiKeys'
import { CA_FILE, curlExample, useOwnCa } from './endpoint'
import {
  checkAgentTelegram,
  connectAgent,
  connectedError,
  createApiKey,
  disconnectAgent,
  fetchConnected,
  fetchEgress,
  fetchNodes,
  setAgentChannel,
  setAgentNetwork,
  setAgentVersion,
  type ClusterNode,
  type ConnectedAgent,
  type EgressFlow,
} from './api'
import { Button } from './ui/Controls'
import { Field } from './ui/Forms'
import { StatusBadge } from './ui/Indicators'
import { KeyValueList, Notice } from './ui/Lists'
import { StateView } from './ui/StateView'
import type { Tone } from './ui/types'
import { Window } from './ui/Window'
import { PRODUCT } from './brand'

// Connected agents (change connected-agents, decision 34): OpenClaw, Hermes from the Yorath catalog.

export const GATEWAY_NOTE =
  `Gateway level: ${PRODUCT} sees model calls, requests to the agent and its network, and can disable the agent and its internet, ` +
  'but it does not see the agent’s own tools (terminal, files in its container).'
export const TOKEN_NOTE = `The agent sees its bot token itself: ${PRODUCT} stores, changes and erases it, but doesn’t hide it from the agent.`

const TELEGRAM_SHORT: Record<NonNullable<ConnectedAgent['telegram_check']>['state'], string> = {
  ok: 'works',
  unreachable: 'not reachable from this server',
  rejected: 'token rejected',
  internet_off: 'needs internet',
}

const STATUS: Record<NonNullable<ConnectedAgent['status']>, { label: string; tone: Tone }> = {
  lost: { label: 'Node unavailable', tone: 'error' },
  starting: { label: 'Starting', tone: 'warn' },
  running: { label: 'Running', tone: 'ok' },
  stopped: { label: 'Stopped', tone: 'neutral' },
  failed: { label: 'Error', tone: 'error' },
}

const users = (raw: string) =>
  raw
    .split(',')
    .map((u) => u.trim())
    .filter(Boolean)

function versionText(a: ConnectedAgent): string {
  const v = a.running_version ?? a.version ?? a.tested_version
  return a.untested ? `${v} — not tested with this ${PRODUCT} version` : `${v} (tested)`
}

function ConnectRow({
  agent,
  nodes,
  onDone,
}: {
  agent: ConnectedAgent
  nodes: ClusterNode[]
  onDone: () => void
}) {
  const [version, setVersion] = useState('')
  const [node, setNode] = useState('')
  const [token, setToken] = useState('')
  const [allowed, setAllowed] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const submit = async () => {
    setBusy(true)
    const r = await connectAgent(agent.name, {
      version: version.trim() || null,
      node: node || null,
      telegram_token: token.trim() || null,
      telegram_users: users(allowed),
    })
    setBusy(false)
    if (r.status === 200) onDone()
    else setError(connectedError(r.status, r.body))
  }
  return (
    <section className="a3-detail" aria-label={`Connect ${agent.name}`}>
      <h3 className="a3-section-label">{agent.name}</h3>
      {agent.description && <p>{agent.description}</p>}
      <KeyValueList
        items={[
          { key: 'Tested version', value: agent.tested_version, mono: true },
          { key: 'License', value: agent.license },
          { key: 'Context window', value: `at least ${agent.min_context.toLocaleString('en-US')} tokens` },
        ]}
      />
      <Field
        label="Version"
        hint={`Empty — the tested ${agent.tested_version}; your own (a tag or latest) is not tested with ${PRODUCT}`}
      >
        {({ id, describedBy }) => (
          <input
            id={id}
            aria-describedby={describedBy}
            className="a3-input"
            value={version}
            placeholder={agent.tested_version}
            onChange={(e) => setVersion(e.target.value)}
          />
        )}
      </Field>
      {nodes.length > 0 && (
        <Field
          label="Node"
          hint="Where the agent runs; the model and restrictions still go through the main node"
        >
          {({ id }) => (
            <select id={id} className="a3-input" value={node} onChange={(e) => setNode(e.target.value)}>
              <option value="">main node</option>
              {nodes.map((n) => (
                <option key={n.name} value={n.name}>
                  {n.name} ({n.cpu_count} vCPU, {n.ram_gb} GB)
                </option>
              ))}
            </select>
          )}
        </Field>
      )}
      <Field label="Telegram bot token" hint={`Optional. ${TOKEN_NOTE}`}>
        {({ id, describedBy }) => (
          <input
            id={id}
            type="password"
            autoComplete="off"
            aria-describedby={describedBy}
            className="a3-input"
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
        )}
      </Field>
      {token && (
        <Field
          label="Who to answer in Telegram"
          hint="Comma-separated user ids; empty means the agent’s rules"
        >
          {({ id }) => (
            <input
              id={id}
              className="a3-input"
              value={allowed}
              onChange={(e) => setAllowed(e.target.value)}
            />
          )}
        </Field>
      )}
      {error && <Notice tone="error">{error}</Notice>}
      <Button variant="primary" loading={busy} onClick={() => void submit()}>
        Connect {agent.name}
      </Button>
    </section>
  )
}

/** The "Connect agent" window: the catalog of agents not connected yet. */
// `only`: the agent being enabled from the list (issue #18) — in a cluster its node is asked here.
export function ConnectPanel({
  onDone,
  onCancel,
  only,
}: {
  onDone: (name: string) => void
  onCancel: () => void
  only?: string
}) {
  const [catalog, setCatalog] = useState<ConnectedAgent[] | null>(null)
  const [nodes, setNodes] = useState<ClusterNode[]>([])
  useEffect(() => {
    void fetchConnected().then((r) => setCatalog(r.status === 200 ? r.body : []))
    void fetchNodes().then((r) =>
      setNodes(r.status === 200 ? r.body.filter((n) => n.role === 'worker' && n.status === 'active') : []),
    )
  }, [])
  const free = catalog?.filter((a) => !a.connected && (!only || a.name === only)) ?? []
  return (
    <div className="a3-desk__pad a3-desk__pad--app">
      <Window variant="app" title={only ? `Enable ${only}` : 'Connect agent'} onClose={onCancel}>
        <div className="a3-detail">
          <p>
            {PRODUCT} runs the agent as a container on this server: not as root, without access to the files
            and Docker of {PRODUCT}, with the {PRODUCT} model under its own key. {GATEWAY_NOTE}
          </p>
          {catalog === null ? (
            <StateView kind="loading" />
          ) : free.length === 0 ? (
            <StateView kind="empty" title="All catalog agents are already connected." />
          ) : (
            free.map((a) => <ConnectRow key={a.name} agent={a} nodes={nodes} onDone={() => onDone(a.name)} />)
          )}
        </div>
      </Window>
    </div>
  )
}

function ago(at: string, now: number): string {
  const min = Math.max(0, Math.round((now - Date.parse(at)) / 60_000))
  return min < 1 ? 'just now' : min < 120 ? `${min} min ago` : `${Math.round(min / 60)} h ago`
}

/** What the bot can do, from the check by the agent's node (change telegram-check). */
function telegramState(info: ConnectedAgent, now: number = Date.now()): { text: string; tone: Tone } {
  if (!info.telegram)
    return {
      text: 'No bot yet: put a token from @BotFather and your Telegram user id below.',
      tone: 'neutral',
    }
  const c = info.telegram_check
  if (!c) return { text: 'Checking that Telegram reaches the bot…', tone: 'neutral' }
  const when = ` Checked ${ago(c.checked_at, now)}.`
  const node = info.node ?? 'the main node'
  if (c.state === 'unreachable')
    return {
      text: `Telegram is not reachable from ${node}: the bot gets no messages. The server’s provider may block Telegram; the bot starts working once the server reaches api.telegram.org.${when}`,
      tone: 'error',
    }
  if (c.state === 'rejected')
    return {
      text: `Telegram rejected the bot token: put a new one from @BotFather below.${when}`,
      tone: 'error',
    }
  if (c.state === 'internet_off')
    return {
      text: 'The agent’s internet is off, and the bot needs it: turn it on to use Telegram.',
      tone: 'warn',
    }
  const who = info.telegram_users.length
    ? ` and answers ${info.telegram_users.join(', ')}. Write to it in Telegram.`
    : ', but answers nobody yet: add user ids below.'
  return { text: `Telegram accepts the bot${c.bot ? ` @${c.bot}` : ''}${who}${when}`, tone: 'ok' }
}

// How to reach a connected agent (change models-by-server, 4.1): the panel chat, /v1 with
// model agent/<name> and a key narrowed to the agent, Telegram. The key is shown once.
export function HowToUse({ info, onCheck }: { info: ConnectedAgent; onCheck?: () => void }) {
  const ca = useOwnCa()
  const [key, setKey] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const issue = async () => {
    setError(null)
    const r = await createApiKey(`${info.name}-api`, info.name)
    if (r.status === 201 && r.body) setKey(r.body.key)
    else setError(connectedError(r.status, r.body as never))
  }
  return (
    <section aria-label="How to use">
      <h3 className="a3-section-label">How to use</h3>
      <ol className="a3-stack">
        <li>
          <strong>Chat.</strong> Chat → new conversation → pick <span className="a3-mono">{info.name}</span>.
          Every message goes to the agent and shows in the Journal under your name.
        </li>
        <li>
          <strong>API.</strong> {PRODUCT}’s <span className="a3-mono">/v1</span> with model{' '}
          <span className="a3-mono">agent/{info.name}</span> and a key for this agent only: it opens neither
          the bare model nor other agents. The client sends the conversation history itself.{' '}
          {!key && (
            <Button size="sm" variant="primary" onClick={() => void issue()}>
              Create a key for {info.name}
            </Button>
          )}
          {key && (
            <Notice tone="warn">
              The key is shown once: copy it now. To cut a client off, revoke the key in API keys.
            </Notice>
          )}
          {error && <Notice tone="error">{error}</Notice>}
          <CopyBlock
            label={`Call ${info.name} over the API`}
            where={ca ? `Download ${CA_FILE} in API keys and run this in its folder:` : undefined}
            text={curlExample(key ?? '$YORATH_API_KEY', ca, info.name)}
          />
        </li>
        <li>
          <strong>Telegram.</strong>{' '}
          {(() => {
            const t = telegramState(info)
            return t.tone === 'error' || t.tone === 'warn' ? <Notice tone={t.tone}>{t.text}</Notice> : t.text
          })()}{' '}
          {info.telegram && info.telegram_check && onCheck && (
            <Button size="sm" onClick={onCheck}>
              Check again
            </Button>
          )}
        </li>
      </ol>
    </section>
  )
}

/** Section of a connected agent's card: container, version, bot, internet, domains. */
export function ConnectedSection({ name, onChanged }: { name: string; onChanged: () => void }) {
  const [info, setInfo] = useState<ConnectedAgent | null>(null)
  const [flows, setFlows] = useState<EgressFlow[]>([])
  const [error, setError] = useState<string | null>(null)
  const [token, setToken] = useState('')
  const [allowed, setAllowed] = useState<string | null>(null)
  const [version, setVersion] = useState('')
  const [confirmRemove, setConfirmRemove] = useState(false)

  const load = async () => {
    const r = await fetchConnected()
    if (r.status === 200) setInfo(r.body.find((a) => a.name === name) ?? null)
    const f = await fetchEgress(24)
    setFlows(f.status === 200 ? f.body.filter((x) => x.source === name) : [])
  }
  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name])
  // the bot check comes with the node's next pass: ask again until it is there
  const checking = Boolean(info?.connected && info.telegram && !info.telegram_check)
  useEffect(() => {
    if (!checking) return
    const t = setInterval(() => void load(), 5000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checking, name])

  const act = async (call: Promise<{ status: number; body: unknown }>) => {
    const r = await call
    if (r.status === 200) {
      setError(null)
      setToken('')
      await load()
      onChanged()
    } else setError(connectedError(r.status, r.body as never))
  }

  if (!info) return null
  const status = info.status ? STATUS[info.status] : null
  const usersText = allowed ?? info.telegram_users.join(', ')
  return (
    <section aria-label="Connected agent">
      <h3 className="a3-section-label">Connected agent</h3>
      <Notice tone="info">{GATEWAY_NOTE}</Notice>
      <KeyValueList
        items={[
          {
            key: 'Container',
            value: !info.connected ? (
              'not connected'
            ) : status ? (
              <StatusBadge tone={status.tone} label={status.label} />
            ) : (
              'waiting for the node service'
            ),
            hint: info.status_detail ?? undefined,
          },
          { key: 'Node', value: info.node ?? 'main node' },
          { key: 'Version', value: versionText(info), hint: info.running_digest ?? undefined },
          {
            key: 'Internet',
            value: info.network_enabled ? 'on (through the node proxy, in the journal)' : 'off',
          },
          {
            key: 'Telegram',
            value: !info.telegram
              ? 'none'
              : !info.telegram_check
                ? 'checking…'
                : TELEGRAM_SHORT[info.telegram_check.state] +
                  (info.telegram_users.length ? `, answers: ${info.telegram_users.join(', ')}` : ''),
          },
        ]}
      />
      {error && <Notice tone="error">{error}</Notice>}
      {info.connected && <HowToUse info={info} onCheck={() => void act(checkAgentTelegram(name))} />}
      {info.connected && (
        <>
          <div className="a3-agent-actions">
            <Button onClick={() => void act(setAgentNetwork(name, !info.network_enabled))}>
              {info.network_enabled ? 'Turn off internet' : 'Turn on internet'}
            </Button>
            <Button onClick={() => void act(disconnectAgent(name))}>Disconnect</Button>
            {confirmRemove ? (
              <Button variant="danger" onClick={() => void act(disconnectAgent(name, true))}>
                Delete along with the agent’s memory
              </Button>
            ) : (
              <Button onClick={() => setConfirmRemove(true)}>Delete…</Button>
            )}
          </div>
          <Field label="Agent version" hint={`Empty — return to the tested ${info.tested_version}`}>
            {({ id, describedBy }) => (
              <input
                id={id}
                aria-describedby={describedBy}
                className="a3-input"
                value={version}
                placeholder={info.tested_version}
                onChange={(e) => setVersion(e.target.value)}
              />
            )}
          </Field>
          <Button onClick={() => void act(setAgentVersion(name, version.trim() || null))}>
            Change version
          </Button>
          <Field label="Telegram bot" hint={TOKEN_NOTE}>
            {({ id, describedBy }) => (
              <input
                id={id}
                type="password"
                autoComplete="off"
                aria-describedby={describedBy}
                className="a3-input"
                value={token}
                placeholder={info.telegram ? 'set — enter a new one to change it' : 'token from @BotFather'}
                onChange={(e) => setToken(e.target.value)}
              />
            )}
          </Field>
          <Field label="Who to answer in Telegram" hint="Comma-separated user ids">
            {({ id }) => (
              <input
                id={id}
                className="a3-input"
                value={usersText}
                onChange={(e) => setAllowed(e.target.value)}
              />
            )}
          </Field>
          <div className="a3-agent-actions">
            <Button
              disabled={!token.trim() && !info.telegram}
              onClick={() => void act(setAgentChannel(name, token.trim() || null, users(usersText)))}
            >
              {token.trim() ? 'Save bot' : 'Remove bot'}
            </Button>
          </div>
        </>
      )}
      <h3 className="a3-section-label">Agent domains in the last 24 hours</h3>
      {flows.length === 0 ? (
        <StateView kind="empty" title="No internet requests." />
      ) : (
        <ul>
          {flows.map((f) => (
            <li key={f.domain}>
              {f.domain} — {f.requests}
              {f.blocked ? `, denied ${f.blocked}` : ''}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
