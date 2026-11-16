import { useEffect, useState } from 'react'
import { fetchAgents, setAgentEnabled, type AgentInfo, type AgentTool } from './api'
import { asDataClass } from './format'
import { DataClassBadge } from './ui/Indicators'
import { KeyValueList, Notice } from './ui/Lists'
import { StateView } from './ui/StateView'
import { Card } from './ui/Wizard'

const fmtTool = (t: AgentTool) => (t.targets.length ? `${t.name} → ${t.targets.join(', ')}` : t.name)

const MODES: Record<string, string> = {
  delegated: 'on behalf of the user',
  autonomous: 'on behalf of a service account',
}
const LEVELS: Record<string, string> = {
  tool: 'every tool call through the Gateway',
  sandbox: 'gVisor sandbox, network by allow-list',
  gateway: 'external client, model calls under control',
}

// What an agent needs to work: a built-in agent is enabled, but without setup it won't be useful
const NEEDS: Record<string, string> = {
  'developer-agent': 'Needs a GitHub App and sandbox setup (stage 5): without it the agent won’t start.',
  'model-api': 'Works with a user’s key: create a key on the "API keys" screen after the wizard.',
  'research-agent': 'Searches the internet if a Tavily key is set at the "External services" step.',
}

// The wizard's "Agents" step: built-in agents are already enabled; the owner sees their permissions and disables extras.
export default function WizardAgents() {
  const [agents, setAgents] = useState<AgentInfo[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    const r = await fetchAgents()
    if (r.status !== 200) throw new Error(`HTTP ${r.status}`)
    setAgents(r.body.filter((a) => a.builtin))
  }

  useEffect(() => {
    const first = async () => {
      try {
        await load()
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    }
    void first()
  }, [])

  const toggle = async (a: AgentInfo, enabled: boolean) => {
    const r = await setAgentEnabled(a.name, enabled)
    setError(r.status === 200 ? null : `Could not update agent ${a.name}: HTTP ${r.status}`)
    await load()
  }

  if (agents === null && !error) return <StateView kind="loading" />
  return (
    <>
      <p>
        Built-in agents are already enabled — these are ready-made examples of different types. Below are
        their permissions: what each can call, what data it sees and which data class it is allowed. You can
        disable extras now or later in the Agents section. How to check each one: see{' '}
        <code>docs/agents/built-in.md</code>.
      </p>
      {error && <Notice tone="error">{error}</Notice>}
      {(agents ?? []).map((a) => {
        const card = a.revision?.card
        const dataClass = asDataClass(card?.max_data_class)
        return (
          <Card key={a.name} title={a.name}>
            {a.description && <p>{a.description}</p>}
            <KeyValueList
              items={[
                { key: 'Mode', value: MODES[a.mode] ?? a.mode },
                { key: 'Control', value: LEVELS[a.control_level] ?? a.control_level },
                { key: 'Tools', value: card?.tools.map(fmtTool).join(', ') || '—' },
                { key: 'Sources', value: card?.data.join(', ') || '—' },
                {
                  key: 'Class ceiling',
                  value: dataClass ? <DataClassBadge dataClass={dataClass} /> : (card?.max_data_class ?? '—'),
                },
                { key: 'Models', value: card?.models.join(', ') || '—' },
              ]}
            />
            {NEEDS[a.name] && <Notice tone="info">{NEEDS[a.name]}</Notice>}
            <label className="a3-perms__toggle">
              <input type="checkbox" checked={a.enabled} onChange={(e) => void toggle(a, e.target.checked)} />
              {` Enabled: ${a.name}`}
            </label>
          </Card>
        )
      })}
    </>
  )
}
