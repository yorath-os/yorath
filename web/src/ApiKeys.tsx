import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  createApiKey,
  fetchApiAgents,
  fetchApiKeys,
  fetchKeyModels,
  revokeApiKey,
  setKeyModels,
  type ApiKey,
  type ApiKeyCreated,
  type KeyModel,
} from './api'
import { dateTime } from './format'
import { Button } from './ui/Controls'
import { DataTable } from './ui/DataTable'
import { Field } from './ui/Forms'
import { StatusBadge } from './ui/Indicators'
import { Notice } from './ui/Lists'
import { ScreenStateGate } from './ui/StateView'
import type { ScreenState } from './ui/types'
import { Card } from './ui/Wizard'
import { Window } from './ui/Window'
import { DOCS_URL, PRODUCT } from './brand'
import { CA_FILE, curlExample, endpointUrl, useOwnCa } from './endpoint'

// Without TLS the key and the text travel over the network in the clear; localhost is the same machine
const plainHttp = () =>
  window.location.protocol === 'http:' &&
  !['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname)

// Codex CLI: the provider in ~/.codex/config.toml, the profile in a separate file (Codex 0.134+)
const codexProvider = () =>
  `[model_providers.yorath]\nname = "${PRODUCT}"\nbase_url = "${endpointUrl()}"\nenv_key = "YORATH_API_KEY"\nwire_api = "responses"`
const codexProfile = (window: number) =>
  `model_provider = "yorath"\nmodel = "local/default"\nmodel_context_window = ${window}`

// A command or value the user copies as is: a dark block with its own Copy button
export function CopyBlock({ label, text, where }: { label: string; text: string; where?: string }) {
  const [done, setDone] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setDone(true)
    } catch {
      setDone(false)
    }
  }
  return (
    <div className="a3-stack">
      {where && <p className="a3-text-xs">{where}</p>}
      <pre className="a3-code" aria-label={label}>
        {text}
      </pre>
      <div>
        <Button size="sm" onClick={() => void copy()} aria-label={`Copy: ${label}`}>
          {done ? 'Copied' : 'Copy'}
        </Button>
      </div>
    </div>
  )
}

// Which models a key may call (change field-fixes-0-7): every chat model, or the ones ticked
function ModelsPicker({
  legend,
  catalog,
  value,
  onChange,
}: {
  legend: string
  catalog: KeyModel[]
  value: string[] | null
  onChange: (v: string[] | null) => void
}) {
  const ticked = value ?? []
  return (
    <fieldset className="a3-stack" aria-label={legend}>
      <legend className="a3-section-label">{legend}</legend>
      <label className="a3-check">
        <input type="radio" checked={value === null} onChange={() => onChange(null)} /> All models
      </label>
      <label className="a3-check">
        <input type="radio" checked={value !== null} onChange={() => onChange(value ?? [])} /> Only these
        models
      </label>
      {catalog.map((m) => (
        <label key={m.name} className="a3-check">
          <input
            type="checkbox"
            disabled={value === null}
            checked={value !== null && ticked.includes(m.name)}
            onChange={(e) =>
              onChange(e.target.checked ? [...ticked, m.name] : ticked.filter((n) => n !== m.name))
            }
          />{' '}
          <span className="a3-mono">{m.name}</span>, {m.context_length.toLocaleString('en-US')} tokens
          {m.external ? ' — external: the request leaves the server' : ''}
        </label>
      ))}
    </fieldset>
  )
}

const modelsText = (models: string[] | null | undefined) =>
  !models ? 'all models' : models.map((m) => (m === 'local/*' ? 'local models' : m)).join(', ')

// Model endpoint API keys: your own keys; the owner sees and revokes everyone's keys.
export default function ApiKeys({ owner }: { owner: boolean }) {
  const [keys, setKeys] = useState<ApiKey[]>([])
  const [agents, setAgents] = useState<string[]>([])
  const [catalog, setCatalog] = useState<KeyModel[]>([])
  const [newModels, setNewModels] = useState<string[] | null>(null)
  const [access, setAccess] = useState('')
  const [editing, setEditing] = useState<{ key: ApiKey; models: string[] | null } | null>(null)
  const [state, setState] = useState<ScreenState>({ kind: 'loading' })
  const [created, setCreated] = useState<ApiKeyCreated | null>(null)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const ca = useOwnCa()

  const reload = useCallback(async () => {
    try {
      const [r, a, m] = await Promise.all([fetchApiKeys(), fetchApiAgents(), fetchKeyModels()])
      if (r.status !== 200) throw new Error(`HTTP ${r.status}`)
      setKeys(r.body)
      setAgents(a.status === 200 ? a.body : [])
      setCatalog(m.status === 200 ? m.body : [])
      setState({ kind: 'ready' })
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

  const onCreate = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const form = e.currentTarget
    const data = new FormData(form)
    const name = String(data.get('name') ?? '').trim()
    const agent = String(data.get('access') ?? '') || null
    if (!agent && newModels !== null && newModels.length === 0) {
      setError('Tick at least one model, or choose "All models".')
      return
    }
    const r = await createApiKey(name, agent, agent ? null : newModels)
    if (r.status !== 201 || !r.body) {
      setError(`Could not create the key: HTTP ${r.status}`)
      return
    }
    setError(null)
    setCopied(false)
    setCreated(r.body)
    form.reset()
    setNewModels(null)
    setAccess('')
    await reload()
  }

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
    } catch {
      setError('The browser didn’t allow copying: select the key and copy it manually.')
    }
  }

  const saveModels = async () => {
    if (!editing) return
    if (editing.models !== null && editing.models.length === 0) {
      setError('Tick at least one model, or choose "All models".')
      return
    }
    const r = await setKeyModels(editing.key.id, editing.models)
    setError(r.status === 200 ? null : `Could not change the models: HTTP ${r.status}`)
    if (r.status === 200) setEditing(null)
    await reload()
  }

  const revoke = async (k: ApiKey) => {
    const r = await revokeApiKey(k.id)
    setError(r.status === 204 ? null : `Could not revoke the key: HTTP ${r.status}`)
    if (created?.id === k.id) setCreated(null)
    await reload()
  }

  return (
    <div className="a3-desk__pad a3-desk__pad--app">
      <Window variant="app" title="API keys" flush>
        <ScreenStateGate state={state} skeletonRows={3} emptyTitle="No keys">
          <div className="a3-stack">
            <Card title="Connect a client">
              <div className="a3-stack">
                <p>
                  Any client compatible with OpenAI Chat Completions or Responses (Codex) works. Details:{' '}
                  <a href={`${DOCS_URL}/reference/api/`} target="_blank" rel="noreferrer">
                    Model endpoint guide
                  </a>
                  .
                </p>
                <p>
                  <strong>1. Endpoint.</strong> Put it in the client as the base URL.
                </p>
                <CopyBlock label="Model endpoint" text={endpointUrl()} />
                {ca && (
                  <>
                    <p>
                      <strong>2. Root certificate.</strong> The server certificate is issued by {PRODUCT}, so
                      the client must trust its root certificate.
                    </p>
                    <div>
                      <a className="a3-button a3-button--secondary" href={`/${CA_FILE}`} download={CA_FILE}>
                        Download {CA_FILE}
                      </a>
                    </div>
                    <CopyBlock
                      label="Trust the certificate"
                      where="On your computer, in the folder where you saved the file:"
                      text={`# curl: add to every call\ncurl --cacert ${CA_FILE} ...\n\n# Python, Codex CLI: once per terminal session\nexport SSL_CERT_FILE="$PWD/${CA_FILE}"`}
                    />
                    <p className="a3-text-xs">
                      To trust it system-wide (browser too):{' '}
                      <a href={`${DOCS_URL}/operate/certificate/`} target="_blank" rel="noreferrer">
                        certificate on macOS, Windows and Linux
                      </a>
                      .
                    </p>
                  </>
                )}
                <p>
                  <strong>{ca ? '3' : '2'}. Key.</strong> Create a key below: a ready request example appears
                  right after.
                </p>
                <p className="a3-text-xs">
                  Every call shows in the Journal under your name. Model calls are logged without the request
                  and answer text; agent calls look like an agent run in the chat.
                </p>
              </div>
            </Card>
            {plainHttp() && (
              <Notice tone="warn">
                The panel is open without TLS: the key and request text travel over the network in the clear.
              </Notice>
            )}
            {error && <Notice tone="error">{error}</Notice>}
            {created && (
              <Card title={`Key "${created.name}" created`}>
                <div className="a3-stack">
                  <Notice tone="warn">
                    The key is shown once. Save it now and don’t share it in chats or emails: if it ends up in
                    a conversation, revoke it and create a new one.
                  </Notice>
                  <Field label="Key">
                    {({ id }) => (
                      <input
                        id={id}
                        className="a3-input a3-mono"
                        readOnly
                        value={created.key}
                        onFocus={(e) => e.currentTarget.select()}
                      />
                    )}
                  </Field>
                  <div>
                    <Button variant="primary" onClick={() => void copy(created.key)}>
                      {copied ? 'Copied' : 'Copy key'}
                    </Button>
                  </div>
                  <pre className="a3-mono a3-text-xs" aria-label="Request example">
                    {curlExample(created.key, ca, created.agent)}
                  </pre>
                  {created.agent && (
                    <p>
                      The key calls only the agent <span className="a3-mono">{created.agent}</span>: its
                      instructions, tools and the data available to you. The client sends the conversation
                      history itself in <span className="a3-mono">messages</span>.
                    </p>
                  )}
                  {!created.agent && (
                    <details>
                      <summary>Codex CLI</summary>
                      <p>
                        In <span className="a3-mono">~/.codex/config.toml</span>:
                      </p>
                      <pre className="a3-mono a3-text-xs" aria-label="Codex provider">
                        {codexProvider()}
                      </pre>
                      <p>
                        In a separate file <span className="a3-mono">~/.codex/yorath.config.toml</span>:
                      </p>
                      <pre className="a3-mono a3-text-xs" aria-label="Codex profile">
                        {codexProfile(32768)}
                      </pre>
                      <p>
                        Run: <span className="a3-mono">YORATH_API_KEY=… codex --profile yorath</span>. Codex
                        works with a local model only on a server with a GPU.
                      </p>
                    </details>
                  )}
                </div>
              </Card>
            )}
            <DataTable
              caption="API keys"
              rows={keys}
              rowKey={(k) => k.id}
              columns={[
                { id: 'name', header: 'Name', weight: 1.5, render: (k) => k.name },
                ...(owner
                  ? [
                      {
                        id: 'login',
                        header: 'User',
                        render: (k: ApiKey) => <span className="a3-mono a3-text-xs">{k.login}</span>,
                      },
                    ]
                  : []),
                {
                  id: 'access',
                  header: 'Access',
                  render: (k) =>
                    k.agent ? <span className="a3-mono a3-text-xs">agent {k.agent}</span> : 'model',
                },
                {
                  id: 'models',
                  header: 'Models',
                  render: (k) =>
                    k.agent ? (
                      '—'
                    ) : (
                      <span className="a3-text-xs">
                        {modelsText(k.models)}{' '}
                        {!k.revoked_at && (
                          <Button
                            size="sm"
                            aria-label={`Models: ${k.name}`}
                            onClick={() => setEditing({ key: k, models: k.models ?? null })}
                          >
                            Change
                          </Button>
                        )}
                      </span>
                    ),
                },
                {
                  id: 'prefix',
                  header: 'Key',
                  weight: 1.5,
                  render: (k) => <span className="a3-mono a3-text-xs">{k.prefix}…</span>,
                },
                {
                  id: 'created',
                  header: 'Created',
                  hideOnMobile: true,
                  render: (k) => dateTime(k.created_at),
                },
                {
                  id: 'used',
                  header: 'Last used',
                  hideOnMobile: true,
                  render: (k) => (k.last_used_at ? dateTime(k.last_used_at) : '—'),
                },
                {
                  id: 'state',
                  header: 'Status',
                  render: (k) => (
                    <StatusBadge
                      tone={k.revoked_at ? 'neutral' : 'ok'}
                      label={k.revoked_at ? 'revoked' : 'active'}
                    />
                  ),
                },
                {
                  id: 'actions',
                  header: '',
                  render: (k) =>
                    k.revoked_at ? null : (
                      <Button
                        size="sm"
                        variant="danger"
                        aria-label={`Revoke: ${k.name}`}
                        onClick={() => void revoke(k)}
                      >
                        Revoke
                      </Button>
                    ),
                },
              ]}
            />
            {editing && (
              <Card title={`Models of key "${editing.key.name}"`}>
                <div className="a3-stack">
                  <ModelsPicker
                    legend={`Models of ${editing.key.name}`}
                    catalog={catalog}
                    value={editing.models}
                    onChange={(models) => setEditing({ ...editing, models })}
                  />
                  <p className="a3-text-xs">The key stays the same: clients keep working with it.</p>
                  <div className="a3-agent-actions">
                    <Button variant="primary" onClick={() => void saveModels()}>
                      Save models
                    </Button>
                    <Button onClick={() => setEditing(null)}>Cancel</Button>
                  </div>
                </div>
              </Card>
            )}
            <Card title="New key">
              <form className="a3-form" onSubmit={onCreate}>
                <Field label="Key name" hint={'For example, "laptop" or "Continue"'}>
                  {({ id, describedBy }) => (
                    <input
                      id={id}
                      className="a3-input"
                      name="name"
                      maxLength={100}
                      required
                      aria-describedby={describedBy}
                    />
                  )}
                </Field>
                <Field
                  label="Access"
                  hint={
                    'An agent key calls only that agent, never the bare model. The list shows agents with "Can be called with an API key" checked. For a bot, a separate user is better: the key sees the same data you do.'
                  }
                >
                  {({ id, describedBy }) => (
                    <select
                      id={id}
                      className="a3-input"
                      name="access"
                      aria-describedby={describedBy}
                      value={access}
                      onChange={(e) => setAccess(e.target.value)}
                    >
                      <option value="">model (local/default, Codex, Continue)</option>
                      {agents.map((a) => (
                        <option key={a} value={a}>
                          agent {a}
                        </option>
                      ))}
                    </select>
                  )}
                </Field>
                {!access && catalog.length > 0 && (
                  <ModelsPicker legend="Models" catalog={catalog} value={newModels} onChange={setNewModels} />
                )}
                <div>
                  <Button variant="primary" type="submit">
                    Create key
                  </Button>
                </div>
              </form>
            </Card>
          </div>
        </ScreenStateGate>
      </Window>
    </div>
  )
}
