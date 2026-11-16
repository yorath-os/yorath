import { useCallback, useEffect, useState } from 'react'
import { fetchPolicies, type PolicyInfo } from './api'
import { StatusBadge } from './ui/Indicators'
import { Notice } from './ui/Lists'
import { ScreenStateGate } from './ui/StateView'
import type { ScreenState } from './ui/types'
import { Card } from './ui/Wizard'
import { Window } from './ui/Window'

// Descriptions of the built-in set: the policy's description field is internal (a version tag).
const BUILTIN: Record<string, { title: string; text: string }> = {
  users: {
    title: 'User permissions',
    text: 'Who sees what. The owner sees everything. Documents are open by role, department and author. Employee records: your own, your reports’, those of assigned departments; the HR administrator sees all.',
  },
  agents: {
    title: 'Agent restrictions',
    text: 'An agent reads only its own sources, no higher than its data class, and calls only its own tools. A tool that sends data outside is not called after reading data above its ceiling. Restrictions override user permissions.',
  },
  models: {
    title: 'Models and data classes',
    text: 'A model is called only if the data class in the request is allowed for it. Otherwise the call is blocked; the request is not switched to another model.',
  },
  routes: {
    title: 'Calls between agents',
    text: 'An agent calls only an allowed recipient, and the data class in the context is no higher than the recipient’s ceiling.',
  },
}

const changedByOwner = (p: PolicyInfo) => p.description === 'custom'
const ownDescription = (p: PolicyInfo) =>
  p.description && p.description !== 'custom' && !p.description.startsWith('builtin:') ? p.description : null

// Policies are read-only: a bug in an editor would open access; changes go through files and the API.
export default function Policies() {
  const [policies, setPolicies] = useState<PolicyInfo[]>([])
  const [state, setState] = useState<ScreenState>({ kind: 'loading' })

  const load = useCallback(async () => {
    try {
      const r = await fetchPolicies()
      if (r.status !== 200) throw new Error(`HTTP ${r.status}`)
      setPolicies(r.body)
      setState(r.body.length ? { kind: 'ready' } : { kind: 'empty' })
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
      <Window variant="app" title="Policies" flush>
        <ScreenStateGate state={state} skeletonRows={4} emptyTitle="No policies">
          <div className="a3-stack">
            <Notice tone="info">
              Cedar policies are enforced outside the model, on every call. This is a read-only view.
            </Notice>
            {policies.map((p) => (
              <Card
                key={p.name}
                title={BUILTIN[p.name]?.title ?? p.name}
                titleExtra={
                  <>
                    <StatusBadge
                      tone={p.enabled ? 'ok' : 'neutral'}
                      label={p.enabled ? 'enabled' : 'disabled'}
                    />
                    {changedByOwner(p) && <StatusBadge tone="warn" label="changed by owner" />}
                  </>
                }
              >
                {(BUILTIN[p.name]?.text ?? ownDescription(p)) && (
                  <p>{BUILTIN[p.name]?.text ?? ownDescription(p)}</p>
                )}
                <details>
                  <summary>Cedar source</summary>
                  <pre className="a3-pre">{p.cedar}</pre>
                </details>
              </Card>
            ))}
          </div>
        </ScreenStateGate>
      </Window>
    </div>
  )
}
