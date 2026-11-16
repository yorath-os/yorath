import { useEffect, useState, type FormEvent } from 'react'
import {
  completeSetup,
  fetchHardware,
  fetchModels,
  fetchSetupStatus,
  pullModel,
  setOpenRouter,
  setTavily,
  type Hardware,
  type ModelsInfo,
  type PullEvent,
  type SetupStatus,
} from './api'
import DemoHr from './DemoHr'
import WizardAgents from './WizardAgents'
import { Button } from './ui/Controls'
import { Field } from './ui/Forms'
import { ProgressBar, StatusBadge } from './ui/Indicators'
import { KeyValueList, Notice } from './ui/Lists'
import { StateView } from './ui/StateView'
import { Card, WizardWindow } from './ui/Wizard'

const STEPS = ['Hardware', 'Model', 'External services', 'Documents', 'Agents', 'Done']
type Progress = { label: string; pct: number | null; error?: string }

function Failure({ children }: { children: string }) {
  return <Notice tone="error">{children}</Notice>
}

function ModelRow({
  role,
  models,
  status,
  progress,
  onPull,
}: {
  role: 'chat' | 'embeddings'
  models: ModelsInfo
  status: SetupStatus
  progress?: Progress
  onPull: (name: string, force: boolean) => void
}) {
  const name = models.recommended[role]
  const registered = status.models[role]
  const budget = models.budgets[name]
  const tooBig = role === 'chat' && budget?.fits === false
  return (
    <Card
      title={role === 'chat' ? 'Answer model' : 'Embeddings model'}
      titleExtra={registered ? <StatusBadge tone="ok" label={registered} /> : undefined}
    >
      <p>
        <code>{name}</code>
        {budget?.rss_gb != null && (
          <small title={budget.basis}>
            {' '}
            {budget.estimated ? 'estimate ' : ''}≈{budget.rss_gb} GB
            {role === 'chat' &&
              (tooBig
                ? `, doesn't fit in ${budget.limit_gb} GB: the runner will crash`
                : `, fits in ${budget.limit_gb} GB`)}
          </small>
        )}
      </p>
      {!registered && (
        <div>
          <Button onClick={() => onPull(name, tooBig)} disabled={!!progress?.pct}>
            {tooBig ? 'Pull anyway' : models.installed.includes(name) ? 'Register' : 'Pull'}
          </Button>
        </div>
      )}
      {progress && (
        <>
          {progress.pct !== null && <ProgressBar value={progress.pct} label={`Pulling ${name}`} />}
          <p>{progress.label}</p>
          {progress.error && <Failure>{progress.error}</Failure>}
        </>
      )}
    </Card>
  )
}

// Setup wizard (Requirements §3): hardware → model → external services → documents → agents → done.
export default function Wizard({ onDone, onOpenData }: { onDone: () => void; onOpenData: () => void }) {
  const [step, setStep] = useState(1)
  const [hw, setHw] = useState<Hardware | null>(null)
  const [models, setModels] = useState<ModelsInfo | null>(null)
  const [status, setStatus] = useState<SetupStatus | null>(null)
  const [progress, setProgress] = useState<Record<string, Progress>>({})
  const [error, setError] = useState<string | null>(null)
  const [tavilyNote, setTavilyNote] = useState<string | null>(null)
  const [tavilyWarning, setTavilyWarning] = useState<string | null>(null)
  const [tavilyError, setTavilyError] = useState<string | null>(null)
  const [keyError, setKeyError] = useState<string | null>(null)
  const [leaves, setLeaves] = useState<string | null>(null)

  const reload = async () => {
    const [h, s] = await Promise.all([fetchHardware(), fetchSetupStatus()])
    if (h.status !== 200 || s.status !== 200) throw new Error(`HTTP ${h.status}`)
    setHw(h.body)
    setStatus(s.body)
    try {
      const m = await fetchModels()
      setModels(m.status === 200 ? m.body : null)
    } catch {
      setModels(null)
    }
  }

  useEffect(() => {
    const load = async () => {
      try {
        await reload()
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    }
    void load()
  }, [])

  const pull = async (model: string, role: 'chat' | 'embeddings', force: boolean) => {
    setProgress((p) => ({ ...p, [role]: { label: 'starting…', pct: null } }))
    try {
      await pullModel(
        model,
        role,
        (e: PullEvent) => {
          if (e.error) {
            setProgress((p) => ({ ...p, [role]: { label: 'error', pct: null, error: e.error } }))
            return
          }
          const pct = e.total && e.completed ? Math.round((100 * e.completed) / e.total) : null
          const label = e.status === 'registered' ? `registered as ${e.name}` : (e.status ?? '')
          setProgress((p) => ({ ...p, [role]: { label, pct } }))
        },
        force,
      )
      await reload()
    } catch (e) {
      // the stream may break at the proxy while the pull in core continues and registers the model
      setProgress((p) => ({
        ...p,
        [role]: {
          label: 'connection lost',
          pct: null,
          error: `${String(e)}. The pull continues on the server: reload the page in a few minutes or click "Pull" again; a retry resumes the download.`,
        },
      }))
      await reload().catch(() => undefined)
    }
  }

  const saveTavily = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const form = e.currentTarget
    const f = new FormData(form)
    const r = await setTavily(String(f.get('tavily_key') ?? ''), f.get('tavily_consent') === 'on')
    if (r.status >= 400) {
      setTavilyNote(null)
      setTavilyError(
        r.body.detail === 'invalid_api_key'
          ? 'Tavily rejected the key: it is invalid or revoked.'
          : `Key rejected: ${JSON.stringify(r.body.detail)}`,
      )
      return
    }
    setTavilyError(null)
    setTavilyNote(r.body.leaves_server ?? null)
    setTavilyWarning(r.body.warning ?? null)
    form.reset()
    await reload()
  }

  const saveOpenRouter = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const f = new FormData(e.currentTarget)
    const r = await setOpenRouter(String(f.get('api_key') ?? ''), f.get('consent') === 'on')
    if (r.status >= 400) {
      setLeaves(null)
      const d = r.body.detail
      const message = d && typeof d === 'object' && 'message' in d ? String(d.message) : null
      setKeyError(message ?? `Key rejected: ${JSON.stringify(d)}`)
      return
    }
    setKeyError(null)
    setLeaves(
      [
        r.body.leaves_server,
        r.body.warnings?.includes('pricing_unavailable')
          ? 'Could not get the model prices: the cost of external calls won’t be counted.'
          : null,
        r.body.warnings?.includes('window_unavailable')
          ? 'Could not get the model’s context window from OpenRouter: 32,768 tokens are assumed.'
          : null,
      ]
        .filter(Boolean)
        .join(' ') || null,
    )
    await reload()
  }

  if (error)
    return (
      <div className="a3-desk__pad">
        <StateView kind="error" surface="dark" title="Wizard unavailable" description={error} />
      </div>
    )
  if (!hw || !status)
    return (
      <div className="a3-desk__pad">
        <StateView kind="loading" surface="dark" />
      </div>
    )

  const modelsReady = Boolean(status.models.chat && status.models.embeddings)
  const finish = async (then: () => void) => {
    await completeSetup()
    then()
  }
  const back = step > 1 && <Button onClick={() => setStep(step - 1)}>Back</Button>
  const next = (label = 'Next', disabled = false) => (
    <Button variant="primary" onClick={() => setStep(step + 1)} disabled={disabled}>
      {label}
    </Button>
  )
  const actions = [
    next(),
    next('Next', hw.local_mode && !modelsReady),
    next(status.openrouter_configured ? 'Next' : 'Skip'),
    <>
      <Button onClick={() => void finish(onOpenData)}>Finish the wizard and open Data</Button>
      {next()}
    </>,
    next(),
    <Button variant="success" onClick={() => void finish(onDone)}>
      Finish the wizard
    </Button>,
  ][step - 1]

  return (
    <div className="a3-desk__pad">
      <WizardWindow
        title="Setup wizard"
        steps={STEPS}
        current={step}
        heading={STEPS[step - 1]}
        actions={
          <>
            {back}
            {actions}
          </>
        }
      >
        {step === 1 && (
          <>
            <KeyValueList
              items={[
                { key: 'Cores', value: hw.hardware.vcpu },
                { key: 'Memory', value: `${hw.hardware.ram_gb} GB` },
                { key: 'Free disk space', value: `${hw.hardware.disk_free_gb} GB` },
                {
                  key: 'GPU',
                  value: hw.hardware.gpu_name
                    ? `${(hw.hardware.gpu_count ?? 1) > 1 ? `${hw.hardware.gpu_count} × ` : ''}${hw.hardware.gpu_name}, ${hw.hardware.vram_gb} GB`
                    : 'none',
                },
                { key: 'Hardware class', value: hw.tier.tier },
              ]}
            />
            {hw.local_mode ? (
              <Notice tone="ok">
                Local mode is available. Recommended model: <code>{hw.recommended_model}</code>; the expected
                answer time on documents is about {hw.expected_answer_seconds} s.
              </Notice>
            ) : (
              <Failure>
                {`Below the minimum (${hw.minimum.vcpu} vCPU / ${hw.minimum.ram_gb} GB): a local model would be too slow. Only the external model mode via OpenRouter is available, in which Public and Internal data leaves the server.`}
              </Failure>
            )}
          </>
        )}

        {step === 2 && (
          <>
            {!models && <Failure>Ollama is unavailable; models can’t be pulled.</Failure>}
            {models &&
              (['chat', 'embeddings'] as const).map((role) => (
                <ModelRow
                  key={role}
                  role={role}
                  models={models}
                  status={status}
                  progress={progress[role]}
                  onPull={(name, force) => void pull(name, role, force)}
                />
              ))}
            {hw.local_mode && !modelsReady && <Notice tone="info">Pull both models to continue.</Notice>}
          </>
        )}

        {step === 3 && (
          <>
            <Card
              title="Web search"
              titleNote="optional"
              titleExtra={
                status.web_search_configured ? <StatusBadge tone="ok" label="configured" /> : undefined
              }
            >
              <p>Research Agent searches with Tavily.</p>
              <form className="a3-form" onSubmit={saveTavily}>
                <Field label="Tavily key">
                  {({ id }) => (
                    <input
                      id={id}
                      className="a3-input"
                      name="tavily_key"
                      type="password"
                      autoComplete="off"
                      required
                    />
                  )}
                </Field>
                <label className="a3-check">
                  <input name="tavily_consent" type="checkbox" /> I agree: the text of search queries leaves
                  the server
                </label>
                <div>
                  <Button type="submit">Save search key</Button>
                </div>
                {tavilyError && <Failure>{tavilyError}</Failure>}
                {tavilyWarning && <Notice tone="warn">{tavilyWarning}</Notice>}
                {tavilyNote && <Notice tone="info">{tavilyNote}</Notice>}
              </form>
            </Card>
            <Card
              title="External model"
              titleNote="optional"
              titleExtra={
                status.openrouter_configured ? <StatusBadge tone="ok" label="connected" /> : undefined
              }
            >
              <p>
                The model <code>{hw.external_model}</code> via OpenRouter receives only Public and Internal
                data; calls with Confidential and Restricted are blocked by policy.
              </p>
              {status.openrouter_configured ? (
                <ul className="a3-plain-list">
                  {status.resources
                    .filter((m) => m.provider === 'openrouter')
                    .map((m) => (
                      <li key={m.name}>
                        {m.name}: classes {m.classes.join(', ')}
                        {m.input_price_per_mtok
                          ? `; $${Number(m.input_price_per_mtok).toFixed(2)} / $${Number(m.output_price_per_mtok).toFixed(2)} per 1M tokens`
                          : '; price unknown'}
                      </li>
                    ))}
                </ul>
              ) : (
                <form className="a3-form" onSubmit={saveOpenRouter}>
                  <Field label="OpenRouter key">
                    {({ id }) => (
                      <input
                        id={id}
                        className="a3-input"
                        name="api_key"
                        placeholder="sk-or-…"
                        autoComplete="off"
                      />
                    )}
                  </Field>
                  <label className="a3-check">
                    <input type="checkbox" name="consent" /> I understand that when the external model is
                    called, the request text and fragments of Public and Internal documents leave the server
                  </label>
                  <div>
                    <Button type="submit">Check and save</Button>
                  </div>
                </form>
              )}
              {keyError && <Failure>{keyError}</Failure>}
              {leaves && <Notice tone="info">{leaves}</Notice>}
            </Card>
          </>
        )}

        {step === 4 && (
          <>
            <p>
              Documents (PDF, DOCX, MD, TXT) are uploaded in the Data section: each source has a class and
              access rules by role and department; the filter is applied at search time, before the model. You
              can also do this after the wizard.
            </p>
            <DemoHr />
          </>
        )}

        {step === 5 && <WizardAgents />}

        {step === 6 && (
          <KeyValueList
            items={[
              { key: 'Local model', value: status.models.chat ?? 'not configured' },
              { key: 'Embeddings', value: status.models.embeddings ?? 'not configured' },
              { key: 'External model', value: status.models.external ?? 'none' },
            ]}
          />
        )}
      </WizardWindow>
    </div>
  )
}
