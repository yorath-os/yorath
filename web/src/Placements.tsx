import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  connectedError,
  fetchPlacements,
  placeModel,
  removePlacement,
  setDefaultModel,
  type ModelServer,
  type ServerModel,
  type Placements as PlacementsData,
} from './api'
import { Button } from './ui/Controls'
import { DataTable } from './ui/DataTable'
import { Field } from './ui/Forms'
import { ProgressBar, StatusBadge } from './ui/Indicators'
import { Notice } from './ui/Lists'
import type { Tone } from './ui/types'
import { Card } from './ui/Wizard'

// Model servers of the cluster and what stands on them (change model-placements, decision 44), and
// installing a model on the server the user picks (change models-by-server): the Models screen never
// pulls a model to the main node unless its server is chosen. A model server is one Ollama: the main
// node's, or one per GPU on a worker node. One chat model per server.

const REFRESH_MS = 5_000

const label = (s: ModelServer) => `${s.node}/${s.server}`
const gpuIndex = (s: ModelServer) => (s.server.startsWith('gpu') ? Number(s.server.slice(3)) : null)
const card = (s: ModelServer) =>
  s.gpus.length ? `${s.gpu ?? 'GPU'}${s.vram_gb ? `, ${s.vram_gb} GB` : ''}` : 'no GPU'
const num = (n: number) => n.toLocaleString('en-US')
const bare = (name: string) => name.replace(/^local\//, '')
const memory = (m: ServerModel) =>
  m.memory_gb === null ? '?' : `${m.estimated ? '~' : ''}${num(m.memory_gb)} GB`
const FITS = { true: 'yes', false: 'no', null: '?' } as const
const option = (s: ModelServer) =>
  `${label(s)} — ${s.main ? 'main node, ' : ''}${card(s)}${s.window ? `, window ${num(s.window)}` : ''}`

// The server picked first: a free GPU of an active node, then any GPU, then the main node.
const firstChoice = (servers: ModelServer[]) =>
  servers.find((s) => s.gpus.length && !s.model) ?? servers.find((s) => s.gpus.length) ?? servers[0]

type Ask = { kind: 'move' | 'replace' | 'force'; model: string; target: ModelServer; message: string }

function state(s: ModelServer): { label: string; tone: Tone } {
  if (s.node_status !== 'active') return { label: `node ${s.node_status}`, tone: 'error' }
  if (!s.model) return { label: 'free', tone: 'neutral' }
  if (s.status === 'ready' && s.in_flight) return { label: 'answering', tone: 'ok' }
  if (s.status === 'ready') return { label: 'ready', tone: 'ok' }
  return s.status === 'failed' ? { label: 'failed', tone: 'error' } : { label: 'pulling', tone: 'warn' }
}

export function Placements({ onChanged }: { onChanged?: () => void }) {
  const [data, setData] = useState<PlacementsData | null>(null)
  const [progress, setProgress] = useState<{ text: string; pct: number | null } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<string | null>(null)
  const [picked, setPicked] = useState<string | null>(null)
  const [ask, setAsk] = useState<Ask | null>(null)
  const [asDefault, setAsDefault] = useState<boolean | null>(null) // null: decided by the server

  const load = useCallback(async () => {
    const r = await fetchPlacements()
    setData(r.status === 200 ? r.body : null)
  }, [])
  useEffect(() => {
    const first = async () => {
      await load()
    }
    void first()
    // the queue and the load of the servers change by themselves: re-read them while the screen is open
    const poll = setInterval(() => void load(), REFRESH_MS)
    return () => clearInterval(poll)
  }, [load])
  if (!data || data.servers.length === 0) return null

  const busy = progress !== null
  const finish = async () => {
    setProgress(null)
    setConfirm(null)
    await load()
    onChanged?.()
  }

  const usable = data.servers.filter((s) => s.node_status === 'active')
  const current = data.models.find((m) => m.default) ?? null
  const defaultModel = current?.name ?? null
  const target = usable.find((s) => label(s) === picked) ?? firstChoice(usable)
  // "make it the default": on by default when the server gives a bigger window than the current model
  const wantDefault = asDefault ?? (!current || (target?.window ?? 0) > current.context_length)

  const place = async (
    model: string,
    to: ModelServer,
    opts: { replace?: boolean; force?: boolean; move?: boolean; makeDefault?: boolean } = {},
  ): Promise<boolean> => {
    setError(null)
    setDone(null)
    setAsk(null)
    setProgress({ text: 'starting…', pct: null })
    const outcome: { failed: string | null; placed: string | null; isDefault: boolean } = {
      failed: null,
      placed: null,
      isDefault: false,
    }
    const refusal = await placeModel(
      {
        model,
        node: to.node,
        gpu: gpuIndex(to),
        replace: opts.replace ?? false,
        force: opts.force ?? false,
        ...(opts.move ? { move: true } : {}),
      },
      (e) => {
        if (e.error) outcome.failed = e.error
        else if (e.status === 'placed') {
          outcome.placed = e.name ?? `local/${model}`
          outcome.isDefault = Boolean(e.default)
        } else {
          const pct = e.total && e.completed ? Math.round((100 * e.completed) / e.total) : null
          setProgress({ text: e.status ?? '', pct })
        }
      },
    )
    if (refusal) {
      const kind =
        refusal.code === 'window_mismatch'
          ? 'move'
          : refusal.code === 'server_busy'
            ? 'replace'
            : refusal.code === 'does_not_fit'
              ? 'force'
              : null
      if (kind) setAsk({ kind, model, target: to, message: refusal.message })
      else setError(refusal.message)
      await finish()
      return false
    }
    if (outcome.failed) {
      setError(`Pulling ${model} to ${label(to)}: ${outcome.failed}`)
      await finish()
      return false
    }
    let text = `${outcome.placed} is on ${label(to)}`
    if (outcome.isDefault) text += ' and is now the default model'
    else if (opts.makeDefault) {
      const r = await setDefaultModel(bare(outcome.placed ?? model))
      if (r.status === 200) text += ' and is now the default model'
      else setError(connectedError(r.status, r.body))
    }
    setDone(`${text}.`)
    await finish()
    return true
  }

  // window_mismatch: the model already runs with another window. Take it off those servers (the default
  // pointer stays on it), then install it here.
  // The core moves it (pre-public-hardening R2): the old servers answer until the new one is ready
  const move = async (model: string, to: ModelServer) => {
    const name = `local/${bare(model)}`
    setAsk(null)
    await place(model, to, { move: true, makeDefault: defaultModel === name || wantDefault })
  }

  const remove = async (s: ModelServer, force: boolean) => {
    if (!s.model) return
    setError(null)
    setDone(null)
    const r = await removePlacement(s.model, s.node, gpuIndex(s), force)
    if (r.status === 200) setDone(`${s.model} is taken off ${label(s)}.`)
    else if (
      r.body?.detail &&
      typeof r.body.detail === 'object' &&
      r.body.detail.code === 'default_would_be_unavailable'
    ) {
      setConfirm(label(s))
      return
    } else setError(connectedError(r.status, r.body))
    await finish()
  }

  const makeDefault = async (model: string) => {
    setError(null)
    const r = await setDefaultModel(model)
    if (r.status === 200) setDone(`local/default now points at ${model}.`)
    else setError(connectedError(r.status, r.body))
    await finish()
  }

  const onCustom = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const model = String(new FormData(e.currentTarget).get('model') ?? '').trim()
    if (model && target) void place(model, target, { makeDefault: wantDefault })
  }

  const queued = data.models.filter((m) => (m.queued ?? 0) > 0)
  // a GPU with nothing on it while the default model has a smaller window: say what to install there
  const offer = usable.find(
    (s) =>
      s.gpus.length > 0 &&
      !s.model &&
      s.recommended &&
      (!current || (s.window ?? 0) > current.context_length),
  )

  return (
    <>
      <Card title="Install a model">
        <div className="a3-stack">
          <p>
            Answers agents: <code>{defaultModel ?? 'no model'}</code>
            {current ? `, context window ${num(current.context_length)}` : ''}. A model is installed on the
            model server you pick, so the main node keeps only what you put there.
          </p>
          {offer && offer.recommended && (
            <Notice tone="info">
              {label(offer)} has a {card(offer)} and no model: {offer.recommended} is recommended there,
              context window {num(offer.window ?? 0)}.{' '}
              <Button
                size="sm"
                variant="primary"
                disabled={busy}
                onClick={() =>
                  void place(offer.recommended as string, offer, {
                    makeDefault: !current || (offer.window ?? 0) > current.context_length,
                  })
                }
              >
                Install {offer.recommended} on {label(offer)}
              </Button>
            </Notice>
          )}
          {progress && (
            <div role="status">
              {progress.pct !== null && <ProgressBar value={progress.pct} label="Pulling the model" />}
              <p>{progress.text}</p>
            </div>
          )}
          {done && <Notice tone="info">{done}</Notice>}
          {error && <Notice tone="error">{error}</Notice>}
          {ask && (
            <Notice tone="warn">
              {ask.message}{' '}
              {ask.kind === 'move' && (
                <Button size="sm" disabled={busy} onClick={() => void move(ask.model, ask.target)}>
                  Move {bare(ask.model)} to {label(ask.target)}
                </Button>
              )}
              {ask.kind === 'replace' && (
                <Button
                  size="sm"
                  disabled={busy}
                  onClick={() =>
                    void place(ask.model, ask.target, { replace: true, makeDefault: wantDefault })
                  }
                >
                  Replace the model on {label(ask.target)}
                </Button>
              )}
              {ask.kind === 'force' && (
                <Button
                  size="sm"
                  disabled={busy}
                  onClick={() => void place(ask.model, ask.target, { force: true, makeDefault: wantDefault })}
                >
                  Install anyway
                </Button>
              )}
            </Notice>
          )}
          {target && (
            <>
              <Field label="Install on">
                {({ id }) => (
                  <select
                    id={id}
                    className="a3-input"
                    value={label(target)}
                    onChange={(e) => {
                      setPicked(e.target.value)
                      setAsDefault(null)
                      setAsk(null)
                    }}
                  >
                    {usable.map((s) => (
                      <option key={label(s)} value={label(s)}>
                        {option(s)}
                      </option>
                    ))}
                  </select>
                )}
              </Field>
              <label className="a3-check">
                <input
                  type="checkbox"
                  checked={wantDefault}
                  onChange={(e) => setAsDefault(e.target.checked)}
                />{' '}
                Make it the default model (agents answer with <code>local/default</code>)
              </label>
              <DataTable
                caption={`Models for ${label(target)}`}
                rows={target.catalog ?? []}
                rowKey={(m) => m.name}
                columns={[
                  {
                    id: 'name',
                    header: 'Model',
                    weight: 2,
                    render: (m) => (
                      <span className="a3-mono a3-text-xs">
                        {m.name}
                        {m.name === target.recommended ? ' ★' : ''}
                      </span>
                    ),
                  },
                  { id: 'memory', header: target.gpus.length ? 'Video memory' : 'Memory', render: memory },
                  { id: 'fits', header: 'Fits', render: (m) => FITS[`${m.fits}`] },
                  {
                    id: 'measured',
                    header: 'Benchmark',
                    weight: 2.5,
                    hideOnMobile: true,
                    render: (m) => <span className="a3-text-xs">{m.measured}</span>,
                  },
                  {
                    id: 'action',
                    header: '',
                    weight: 1.2,
                    render: (m) =>
                      target.model === `local/${m.name}` ? (
                        <StatusBadge tone="ok" label="installed" />
                      ) : (
                        <Button
                          size="sm"
                          disabled={busy}
                          aria-label={`Install ${m.name} on ${label(target)}`}
                          onClick={() => void place(m.name, target, { makeDefault: wantDefault })}
                        >
                          Install
                        </Button>
                      ),
                  },
                ]}
              />
              <form className="a3-form" onSubmit={onCustom} aria-label="Install another model">
                <Field
                  label="Another model"
                  hint="A name from ollama.com (needs the tools tag) or hf.co/<author>/<repository>:<quantization>"
                >
                  {({ id, describedBy }) => (
                    <input
                      id={id}
                      className="a3-input a3-mono"
                      name="model"
                      maxLength={200}
                      required
                      aria-describedby={describedBy}
                    />
                  )}
                </Field>
                <div>
                  <Button type="submit" disabled={busy}>
                    Install on {label(target)}
                  </Button>
                </div>
              </form>
            </>
          )}
        </div>
      </Card>
      <Card title="Model servers">
        <div className="a3-stack">
          <p>
            A model server is one Ollama: the main node’s, or one per GPU on a worker node. One chat model
            stands on a server; the same model on several servers answers several requests at once.{' '}
            <code>local/default</code> points at <code>{defaultModel ?? 'no model'}</code>.
          </p>
          {queued.length > 0 && (
            <Notice tone="info">
              Waiting for a free model server: {queued.map((m) => `${m.name} — ${m.queued}`).join(', ')}. A
              request goes to a server only when it is free; the rest wait here, in turn between users.
            </Notice>
          )}
          <DataTable
            caption="Model servers"
            rows={data.servers}
            rowKey={label}
            columns={[
              {
                id: 'server',
                header: 'Server',
                weight: 1.4,
                render: (s) => <span className="a3-mono a3-text-xs">{label(s)}</span>,
              },
              { id: 'gpu', header: 'GPU', weight: 1.4, hideOnMobile: true, render: card },
              {
                id: 'model',
                header: 'Model',
                weight: 2,
                render: (s) => (
                  <span className="a3-mono a3-text-xs">
                    {s.model ?? '—'}
                    {s.model && s.model === defaultModel ? ' ★' : ''}
                  </span>
                ),
              },
              {
                id: 'state',
                header: 'State',
                render: (s) => {
                  const st = state(s)
                  return <StatusBadge tone={st.tone} label={st.label} />
                },
              },
              {
                id: 'action',
                header: '',
                weight: 2.2,
                render: (s) =>
                  !s.model ? null : confirm === label(s) ? (
                    <Button size="sm" variant="danger" disabled={busy} onClick={() => void remove(s, true)}>
                      Take off {label(s)}: local/default will stop answering
                    </Button>
                  ) : (
                    <span className="a3-chips">
                      {s.status === 'ready' && s.model !== defaultModel && (
                        <Button
                          size="sm"
                          disabled={busy}
                          aria-label={`Make default: ${s.model}`}
                          onClick={() => void makeDefault(s.model as string)}
                        >
                          Make default
                        </Button>
                      )}
                      <Button
                        size="sm"
                        disabled={busy}
                        aria-label={`Take ${s.model} off ${label(s)}`}
                        onClick={() => void remove(s, false)}
                      >
                        Take off
                      </Button>
                    </span>
                  ),
              },
            ]}
          />
        </div>
      </Card>
    </>
  )
}
