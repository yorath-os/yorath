import { useEffect, useState } from 'react'
import {
  connectedError,
  createApiKey,
  fetchOutsideAgents,
  type OutsideAgent as Found,
  type OutsideNode,
} from './api'
import { checkedText } from './format'
import { Button } from './ui/Controls'
import { DataTable } from './ui/DataTable'
import { Notice } from './ui/Lists'
import { Card } from './ui/Wizard'

// Agents outside Yorath on every node (decision 36, change outside-agents-cluster). They are not
// Yorath agents, so there is no switch: "Connect" gives the found agent a key to the Yorath model and
// the lines to put into its settings. Its calls then show in the journal under the key's owner.

type Row = { agent: string; node: string; how: string; url: string; checked: string }

const how = (a: Found) =>
  a.where === 'container' ? `container ${a.container ?? ''}`.trim() : `host process${a.root ? ', root' : ''}`

function rows(nodes: OutsideNode[]): Row[] {
  const out: Row[] = []
  for (const n of nodes) {
    const url = n.model_url ?? `${window.location.origin}/v1`
    const checked = checkedText(n)
    const seen = new Set<string>()
    for (const a of n.agents) {
      if (seen.has(a.agent)) continue // several processes of one agent are one row
      seen.add(a.agent)
      out.push({ agent: a.agent, node: n.node, how: how(a), url, checked })
    }
    for (const t of n.traces) {
      if (seen.has(t.agent)) continue
      seen.add(t.agent)
      out.push({ agent: t.agent, node: n.node, how: `installed (${t.path}), not running`, url, checked })
    }
  }
  return out
}

function settings(r: Row, key: string): { label: string; text: string } {
  if (r.agent === 'openclaw')
    return {
      label: 'OpenClaw settings',
      text: `# ~/.openclaw/openclaw.json: add to "models" and "agents"
${JSON.stringify(
  {
    models: {
      mode: 'merge',
      providers: {
        yorath: {
          baseUrl: r.url,
          apiKey: '${YORATH_API_KEY}',
          api: 'openai-completions',
          models: [{ id: 'local/default', name: 'Yorath default', contextWindow: 65536, maxTokens: 8192 }],
        },
      },
    },
    agents: { defaults: { model: { primary: 'yorath/local/default' } } },
  },
  null,
  2,
)}
# in the environment of the agent
export YORATH_API_KEY=${key}`,
    }
  if (r.agent === 'hermes')
    return {
      label: 'Hermes settings',
      text: `# ~/.hermes/config.yaml
model:
  provider: custom
  base_url: ${r.url}
  default: local/default
  key_env: YORATH_API_KEY
  context_length: 65536
# ~/.hermes/.env
YORATH_API_KEY=${key}`,
    }
  return {
    label: 'OpenAI-compatible settings',
    text: `base URL: ${r.url}
API key:  ${key}
model:    local/default`,
  }
}

export function OutsideAgents() {
  const [nodes, setNodes] = useState<OutsideNode[]>([])
  const [open, setOpen] = useState<Row | null>(null)
  const [key, setKey] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    void fetchOutsideAgents().then((r) => {
      if (alive && r.status === 200 && r.body) setNodes(r.body.nodes)
    })
    return () => {
      alive = false
    }
  }, [])

  const found = rows(nodes)
  if (found.length === 0) return null

  const issue = async (r: Row) => {
    setError(null)
    const res = await createApiKey(`${r.agent}-${r.node}`)
    if (res.status === 201 && res.body) setKey(res.body.key)
    else setError(connectedError(res.status, res.body as never))
  }
  const config = open && settings(open, key ?? '<the key from the step above>')

  return (
    <div className="a3-detail">
      <Card title="Found outside Yorath">
        <div className="a3-stack">
          <p>
            Agents that run on your servers around Yorath: Yorath doesn’t start them and doesn’t see their
            tools or internet. Connect one to the Yorath model: its model calls then go through Yorath and
            show in the journal.
          </p>
          <DataTable
            caption="Found outside Yorath"
            rows={found}
            rowKey={(r) => `${r.node}/${r.agent}`}
            columns={[
              {
                id: 'agent',
                header: 'Agent',
                render: (r) => <span className="a3-mono a3-text-xs">{r.agent}</span>,
              },
              {
                id: 'node',
                header: 'Node',
                render: (r) => <span className="a3-mono a3-text-xs">{r.node}</span>,
              },
              { id: 'how', header: 'How it runs', weight: 2, render: (r) => r.how },
              {
                id: 'checked',
                header: 'Checked',
                render: (r) => <span className="a3-text-xs">{r.checked}</span>,
              },
              {
                id: 'action',
                header: '',
                render: (r) => (
                  <Button
                    size="sm"
                    aria-label={`Connect ${r.agent} on ${r.node}`}
                    onClick={() => {
                      setOpen(r)
                      setKey(null)
                      setError(null)
                    }}
                  >
                    Connect
                  </Button>
                ),
              },
            ]}
          />
          {nodes
            .filter((n) => n.truncated)
            .map((n) => (
              <Notice key={n.node} tone="warn">
                The scan on {n.node} found more agents than a report carries: the list is cut. Run{' '}
                <span className="a3-mono">yorath scan</span> on the node to see all of them.
              </Notice>
            ))}
          {open && config && (
            <div className="a3-stack" aria-label={`Connect ${open.agent} on ${open.node}`}>
              <h3>
                Connect {open.agent} on {open.node} to the Yorath model
              </h3>
              <p>
                1. A key for it. The value is shown once: copy it now.{' '}
                {!key && (
                  <Button size="sm" variant="primary" onClick={() => void issue(open)}>
                    Create a key
                  </Button>
                )}
              </p>
              {key && <code className="a3-mono">{key}</code>}
              {error && <Notice tone="error">{error}</Notice>}
              <p>
                2. Put these lines into its settings on {open.node} and restart it.
                {open.url.startsWith('http://10.88.') &&
                  ' The address goes through the cluster tunnel: no certificate and no internet needed.'}
              </p>
              <pre className="a3-code" aria-label={config.label}>
                {config.text}
              </pre>
              <p>
                Its model calls show in the Journal under the owner of the key. To cut it off, revoke the key
                in API keys.
              </p>
            </div>
          )}
        </div>
      </Card>
    </div>
  )
}
