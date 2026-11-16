import { useEffect, useState, type FormEvent } from 'react'
import {
  createAgent,
  fetchAgentOptions,
  fetchAgentSource,
  updateAgent,
  type AgentForm as Form,
  type AgentInfo,
  type AgentOptions,
} from './api'
import { Button } from './ui/Controls'
import { Field } from './ui/Forms'
import { Notice } from './ui/Lists'
import { StateView } from './ui/StateView'
import { Window } from './ui/Window'

const NAME_HINT = 'Latin letters, digits and hyphens: faq-helper. The name can’t be changed later.'
const TEXT_HINT = 'Describe the agent’s goal and purpose: what it answers and how. This is its instruction.'

// edit — change an own agent; base — fill the form from an example, with a new name.
export type AgentFormMode =
  { kind: 'new' } | { kind: 'base'; agent: AgentInfo } | { kind: 'edit'; agent: AgentInfo }

const toggled = (list: string[], item: string) =>
  list.includes(item) ? list.filter((x) => x !== item) : [...list, item]

function detail(body: unknown): string {
  const d = (body as { detail?: unknown } | null)?.detail
  if (d && typeof d === 'object' && 'message' in d) return String(d.message)
  return 'check the form fields'
}

function Choices({
  legend,
  items,
  chosen,
  onChange,
}: {
  legend: string
  items: { name: string; note: string }[]
  chosen: string[]
  onChange: (next: string[]) => void
}) {
  return (
    <fieldset className="a3-choices">
      <legend className="a3-field__label">{legend}</legend>
      {items.map((i) => (
        <label className="a3-check" key={i.name}>
          <input
            type="checkbox"
            checked={chosen.includes(i.name)}
            onChange={() => onChange(toggled(chosen, i.name))}
          />{' '}
          {i.name} <small>{i.note}</small>
        </label>
      ))}
    </fieldset>
  )
}

export default function AgentForm({
  mode,
  onDone,
  onCancel,
}: {
  mode: AgentFormMode
  onDone: (name: string) => void
  onCancel: () => void
}) {
  const [options, setOptions] = useState<AgentOptions | null>(null)
  const [name, setName] = useState('')
  const [form, setForm] = useState<Form>({
    description: '',
    instructions: '',
    data: [],
    tools: ['search_docs'],
    models: [],
    api: false,
  })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const source = mode.kind === 'new' ? null : mode.agent

  useEffect(() => {
    const load = async () => {
      const [o, text] = await Promise.all([
        fetchAgentOptions(),
        source ? fetchAgentSource(source.name) : Promise.resolve(null),
      ])
      if (o.status !== 200) {
        setError(`Could not load the options: HTTP ${o.status}`)
        return
      }
      const local = o.body.models.filter((m) => !m.external).map((m) => m.name)
      const card = source?.revision?.card
      const known = <T extends { name: string }>(items: T[]) => new Set(items.map((i) => i.name))
      const tools = known(o.body.tools)
      const assets = known(o.body.assets)
      const models = known(o.body.models)
      setOptions(o.body)
      setForm((f) => ({
        description: source?.description ?? f.description,
        instructions: text?.status === 200 ? text.body.instructions : f.instructions,
        data: card ? card.data.filter((d) => assets.has(d)) : f.data,
        tools: card ? card.tools.map((t) => t.name).filter((t) => tools.has(t)) : f.tools,
        models: card ? card.models.filter((m) => models.has(m)) : local,
        api: card ? card.triggers.includes('api') : false,
      }))
    }
    void load()
  }, [source])

  const title =
    mode.kind === 'edit'
      ? `Edit · ${mode.agent.name}`
      : mode.kind === 'base'
        ? `New agent based on ${mode.agent.name}`
        : 'New agent'

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    const r =
      mode.kind === 'edit' ? await updateAgent(mode.agent.name, form) : await createAgent({ ...form, name })
    setBusy(false)
    if (r.status === 200 || r.status === 201) onDone(mode.kind === 'edit' ? mode.agent.name : name)
    else setError(`Agent not saved: ${detail(r.body)}`)
  }

  const set = (patch: Partial<Form>) => setForm((f) => ({ ...f, ...patch }))
  return (
    <div className="a3-desk__pad a3-desk__pad--app">
      <Window variant="app" title={title} onClose={onCancel}>
        {error && <Notice tone="error">{error}</Notice>}
        {!options ? (
          !error && <StateView kind="loading" />
        ) : (
          <form className="a3-stack" onSubmit={(e) => void submit(e)}>
            {mode.kind !== 'edit' && (
              <Field label="Name" hint={NAME_HINT}>
                {({ id, describedBy }) => (
                  <input
                    id={id}
                    aria-describedby={describedBy}
                    className="a3-input"
                    required
                    pattern="[a-z0-9][a-z0-9\-]{1,98}"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                )}
              </Field>
            )}
            <Field label="Description">
              {({ id }) => (
                <input
                  id={id}
                  className="a3-input"
                  maxLength={500}
                  value={form.description}
                  onChange={(e) => set({ description: e.target.value })}
                />
              )}
            </Field>
            <Field label="Instruction" hint={TEXT_HINT}>
              {({ id, describedBy }) => (
                <textarea
                  id={id}
                  aria-describedby={describedBy}
                  className="a3-input a3-agent-text"
                  required
                  rows={12}
                  value={form.instructions}
                  onChange={(e) => set({ instructions: e.target.value })}
                />
              )}
            </Field>
            <Choices
              legend="Sources"
              items={options.assets.map((a) => ({ name: a.name, note: a.data_class }))}
              chosen={form.data}
              onChange={(data) => set({ data })}
            />
            <Choices
              legend="Tools"
              items={options.tools.map((t) => ({ name: t.name, note: t.description }))}
              chosen={form.tools}
              onChange={(tools) => set({ tools })}
            />
            <Choices
              legend="Models"
              items={options.models.map((m) => ({
                name: m.name,
                note: m.external ? `external, ${m.model_id}: text leaves the server` : m.model_id,
              }))}
              chosen={form.models}
              onChange={(models) => set({ models })}
            />
            <fieldset className="a3-choices">
              <legend className="a3-field__label">External calls</legend>
              <label className="a3-check">
                <input
                  type="checkbox"
                  checked={form.api ?? false}
                  onChange={(e) => set({ api: e.target.checked })}
                />{' '}
                Can be called with an API key{' '}
                <small>
                  the key for this agent is issued on the "API keys" screen; the call runs on behalf of the
                  key owner
                </small>
              </label>
            </fieldset>
            <div className="a3-agent-actions">
              <Button variant="primary" type="submit" loading={busy}>
                Save
              </Button>
              <Button onClick={onCancel}>Cancel</Button>
            </div>
          </form>
        )}
      </Window>
    </div>
  )
}
