import { useEffect, useState } from 'react'
import {
  connectedError,
  createApiKey,
  fetchOutsideAgents,
  type OutsideAgent as Found,
  type OutsideNode,
} from './api'
import { checkedText, lastCallText } from './format'
import { Button } from './ui/Controls'
import { DataTable } from './ui/DataTable'
import { Notice } from './ui/Lists'
import { Card } from './ui/Wizard'

// Agents outside Yorath on every node (decision 36, change outside-agents-cluster). They are not
// Yorath agents, so there is no switch: "Connect" gives the found agent a key to the Yorath model and
// the lines to put into its settings. Its calls then show in the journal under the key's owner.

type Row = {
  agent: string
  node: string
  how: string
  url: string
  checked: string
  key?: { name: string; last_used_at: string | null }
}

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
      out.push({ agent: a.agent, node: n.node, how: how(a), url, checked, key: n.keys?.[a.agent] })
    }
    for (const t of n.traces) {
      if (seen.has(t.agent)) continue
      seen.add(t.agent)
      out.push({
        agent: t.agent,
        node: n.node,
        how: `installed (${t.path}), not running`,
        url,
        checked,
        key: n.keys?.[t.agent],
      })
    }
  }
  return out
}

// Yorath's own root certificate on the main node: an agent there reaches https://<IP>/v1 with it.
// Through the cluster tunnel (http://10.88.0.1:8000/v1) no certificate is needed.
const CA = '/opt/yorath/installer/yorath-root-ca.crt'

function settings(r: Row, key: string): { label: string; text: string } {
  const https = r.url.startsWith('https://')
  if (r.agent === 'openclaw') {
    const provider = JSON.stringify({
      baseUrl: r.url,
      apiKey: '${YORATH_API_KEY}',
      api: 'openai-completions',
      models: [{ id: 'local/default', name: 'Yorath default', contextWindow: 65536, maxTokens: 8192 }],
    })
    return {
      label: 'OpenClaw settings',
      text: `# in the shell that starts OpenClaw on ${r.node}
export YORATH_API_KEY=${key}
${https ? `export NODE_EXTRA_CA_CERTS=${CA}   # Node trusts the Yorath certificate\n` : ''}openclaw config set models.providers.yorath '${provider}' --strict-json
openclaw config set agents.defaults.model.primary yorath/local/default
openclaw gateway   # start it from this shell: it needs the variables above`,
    }
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
YORATH_API_KEY=${key}${https ? `\n# in the shell that starts Hermes: Python trusts the Yorath certificate\nexport SSL_CERT_FILE=${CA}` : ''}`,
    }
  return {
    label: 'OpenAI-compatible settings',
    text: `base URL: ${r.url}
API key:  ${key}
model:    local/default${https ? `\ncertificate: ${CA} (the client has to trust it)` : ''}`,
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

  const all = rows(nodes)
  if (all.length === 0) return null
  const found = all.filter((r) => !r.key) // still to connect
  const connected = all.filter((r) => r.key) // connected by key: no Connect button

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
          {found.length === 0 && <p>Every agent found outside Yorath is connected by key.</p>}
          {found.length > 0 && (
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
          )}
          {connected.length > 0 && (
            <DataTable
              caption="Connected by key"
              rows={connected}
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
                {
                  id: 'key',
                  header: 'Key',
                  render: (r) => <span className="a3-mono a3-text-xs">{r.key?.name}</span>,
                },
                {
                  id: 'last',
                  header: 'Last model call',
                  render: (r) => (
                    <span className="a3-text-xs">{lastCallText(r.key?.last_used_at ?? null)}</span>
                  ),
                },
              ]}
            />
          )}
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
                2. Set it up on {open.node} with these lines, then restart it.
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
