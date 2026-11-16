import { useCallback, useEffect, useState } from 'react'
import { fetchDeskSummary, fetchSetupStatus, type DefaultWarning, type SetupStatus } from './api'
import { DefaultWarningNotice } from './DefaultWarning'
import { asDataClass } from './format'
import { Button } from './ui/Controls'
import { DataTable } from './ui/DataTable'
import { DataClassBadge } from './ui/Indicators'
import { ScreenStateGate } from './ui/StateView'
import type { ScreenState } from './ui/types'
import { Window } from './ui/Window'
import { Placements } from './Placements'

type Resource = SetupStatus['resources'][number]

const price = (m: Resource) =>
  m.input_price_per_mtok
    ? `$${Number(m.input_price_per_mtok).toFixed(2)} / $${Number(m.output_price_per_mtok).toFixed(2)}`
    : '—'

// Models as resources; installing a local model goes through "Install a model" on the server the user
// picks (change models-by-server). The first model of a fresh install comes from the setup wizard.
export default function Models({ onOpenWizard }: { onOpenWizard: () => void }) {
  const [models, setModels] = useState<Resource[]>([])
  const [state, setState] = useState<ScreenState>({ kind: 'loading' })

  const load = useCallback(async () => {
    try {
      const r = await fetchSetupStatus()
      if (r.status !== 200) throw new Error(`HTTP ${r.status}`)
      setModels(r.body.resources)
      setState({ kind: 'ready' }) // models are installed in "Install a model" below, even with none yet
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      setState({ kind: 'error', message, onRetry: () => void load() })
    }
  }, [])

  useEffect(() => {
    const first = async () => {
      await load()
    }
    void first()
  }, [load])

  return (
    <div className="a3-desk__pad a3-desk__pad--app">
      <Window
        variant="app"
        title="Models"
        flush
        toolbar={<Button onClick={onOpenWizard}>Setup wizard</Button>}
      >
        <ScreenStateGate
          state={state}
          skeletonRows={3}
          emptyTitle="No models"
          emptyDescription="Pull a model in the setup wizard."
          emptyActionLabel="Setup wizard"
          onEmptyAction={onOpenWizard}
        >
          <div className="a3-stack">
            <ModelsDefaultWarning />
            <Placements onChanged={() => void load()} />
            <DataTable
              caption="Registered models"
              rows={models}
              rowKey={(m) => m.name}
              columns={[
                {
                  id: 'name',
                  header: 'Resource',
                  weight: 1.6,
                  render: (m) => <span className="a3-mono a3-text-xs">{m.name}</span>,
                },
                {
                  id: 'model',
                  header: 'Model',
                  weight: 1.8,
                  render: (m) => <span className="a3-mono a3-text-xs">{m.model_id}</span>,
                },
                { id: 'provider', header: 'Provider', hideOnMobile: true, render: (m) => m.provider },
                {
                  id: 'window',
                  header: 'Context window',
                  hideOnMobile: true,
                  render: (m) => (m.context_length ? m.context_length.toLocaleString('en-US') : '—'),
                },
                {
                  id: 'classes',
                  header: 'Data classes',
                  weight: 3,
                  render: (m) => (
                    <span className="a3-chips">
                      {m.classes.map((c) => {
                        const dataClass = asDataClass(c)
                        return dataClass ? <DataClassBadge key={c} dataClass={dataClass} /> : c
                      })}
                    </span>
                  ),
                },
                {
                  id: 'price',
                  header: 'Price per 1M tokens',
                  weight: 1.6,
                  hideOnMobile: true,
                  render: price,
                },
              ]}
            />
          </div>
        </ScreenStateGate>
      </Window>
    </div>
  )
}

// The same warning as on the Desk: the default moved off a lost node (change field-fixes-0-7)
function ModelsDefaultWarning() {
  const [warning, setWarning] = useState<DefaultWarning | null>(null)
  const load = useCallback(async () => {
    const r = await fetchDeskSummary()
    setWarning(r.status === 200 ? (r.body.default_warning ?? null) : null)
  }, [])
  useEffect(() => {
    const first = async () => {
      await load()
    }
    void first()
  }, [load])
  return warning ? <DefaultWarningNotice warning={warning} onChanged={() => void load()} /> : null
}
