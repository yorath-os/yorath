import { useEffect, useState } from 'react'
import {
  fetchHealth,
  fetchHostReport,
  fetchVersion,
  type EgressState,
  type ForeignAgent,
  type Health,
  type HostReport,
  type Version,
} from './api'
import { dateTime } from './format'
import { Button } from './ui/Controls'
import { StatusBadge } from './ui/Indicators'
import { CheckList, KeyValueList, Notice } from './ui/Lists'
import { ScreenStateGate } from './ui/StateView'
import type { ScreenState } from './ui/types'
import { Card } from './ui/Wizard'
import { Window } from './ui/Window'
import { Nodes } from './Nodes'
import { PRODUCT } from './brand'

const where = (a: ForeignAgent) =>
  a.where === 'host'
    ? `on the host, pid ${a.pid}, ${a.root ? 'as root' : `as ${a.user}`}`
    : `container ${a.container} (${a.image})`

const GUIDE = 'yorath.io/docs/operate/server-network/, "Third-party agents on the server: not as root"'

// Node network through Yorath (change node-egress, decision 36): by default all traffic goes through it
function NodeNetwork({ egress }: { egress: EgressState | null }) {
  const off = !egress || egress.mode !== 'on'
  const broken = !off && !egress.enforced
  const tone = off || broken || egress?.alarm ? 'error' : 'ok'
  const label =
    !egress || egress.mode === 'unset'
      ? 'not enabled'
      : egress.mode === 'off'
        ? 'off'
        : broken
          ? 'broken'
          : 'working'
  return (
    <Card
      title="Node network"
      titleExtra={<StatusBadge tone={tone === 'ok' ? 'ok' : 'warn'} label={`control: ${label}`} />}
    >
      <div className="a3-stack">
        {off && (
          <Notice tone="error">
            Node traffic bypasses {PRODUCT}: the journal doesn't show where agents go, and their internet
            can't be turned off. On the server: <span className="a3-mono">yorath egress on</span>.
          </Notice>
        )}
        {broken && (
          <Notice tone="error">
            {!egress.table
              ? `Network rules were removed: node traffic bypasses ${PRODUCT}.`
              : !egress.intact
                ? 'Network rules were changed manually.'
                : 'The node proxy is not responding: HTTP and HTTPS on the node are closed.'}{' '}
            On the server: <span className="a3-mono">yorath egress on</span>, details —{' '}
            <span className="a3-mono">yorath doctor</span>.
          </Notice>
        )}
        {egress?.alarm && <Notice tone="warn">{egress.alarm}</Notice>}
        {!off && !broken && (
          <p>
            Node HTTP and HTTPS go through the {PRODUCT} proxy with a domain journal (Journal → Network);
            other protocols from containers are closed.
          </p>
        )}
      </div>
    </Card>
  )
}

// Agents outside Yorath per the yorath scan report: Yorath doesn't control them but must show them (decision 36)
function ForeignAgents({ report }: { report: HostReport }) {
  if (!report.available)
    return (
      <Card title={`Agents outside ${PRODUCT}`}>
        <Notice tone="info">
          The node check hasn't run: on the server, <span className="a3-mono">yorath doctor</span>.
        </Notice>
      </Card>
    )
  const checked = report.checked_at ? dateTime(report.checked_at) : '—'
  return (
    <Card
      title={`Agents outside ${PRODUCT}`}
      titleExtra={
        <StatusBadge
          tone={report.agents.length ? 'warn' : 'ok'}
          label={report.agents.length ? `found: ${report.agents.length}` : 'none found'}
        />
      }
    >
      <div className="a3-stack">
        {report.agents.length > 0 && (
          <Notice tone="warn">
            These agents run outside {PRODUCT}: it doesn't control their network, files or Docker. Move them
            to {PRODUCT}, or at least run them not as root — {GUIDE}.
          </Notice>
        )}
        {report.agents.length > 0 && (
          <ul className="a3-plain-list" aria-label={`Agents outside ${PRODUCT}`}>
            {report.agents.map((a) => (
              <li key={`${a.agent}-${a.pid ?? a.container}`}>
                <code>{a.agent}</code> — {where(a)}
              </li>
            ))}
          </ul>
        )}
        {report.containers.length > 0 && (
          <p className="a3-text-xs">
            Other containers not from {PRODUCT}: {report.containers.map((c) => c.name).join(', ')}
          </p>
        )}
        <p className="a3-text-xs">
          Checked: {checked}
          {report.stale
            ? ' — the report is stale: on the server, run yorath scan or systemctl status yorath-scan.timer'
            : ''}
        </p>
      </div>
    </Card>
  )
}

// Health of the web → core → db → ollama chain and versions from the manifest.
export default function Status({ onOpenWizard }: { onOpenWizard?: () => void }) {
  const [health, setHealth] = useState<Health | null>(null)
  const [version, setVersion] = useState<Version | null>(null)
  const [host, setHost] = useState<HostReport | null>(null)
  const [state, setState] = useState<ScreenState>({ kind: 'loading' })

  useEffect(() => {
    const load = async () => {
      try {
        const [h, v, r] = await Promise.all([fetchHealth(), fetchVersion(), fetchHostReport()])
        setHealth(h.body)
        setVersion(v.body)
        setHost(r.status === 200 ? r.body : null) // a member gets 403, no card
        setState({ kind: 'ready' })
      } catch (e) {
        setState({
          kind: 'error',
          message: `Core unavailable: ${e instanceof Error ? e.message : String(e)}`,
        })
      }
    }
    void load()
    const t = setInterval(() => void load(), 10_000)
    return () => clearInterval(t)
  }, [])

  return (
    <div className="a3-desk__pad a3-desk__pad--app">
      <Window
        variant="app"
        title="System"
        flush
        toolbar={onOpenWizard && <Button onClick={onOpenWizard}>Setup wizard</Button>}
      >
        <ScreenStateGate state={state} skeletonRows={5} emptyTitle="No data">
          <div className="a3-stack">
            {health && (
              <Card
                title="Health"
                titleExtra={
                  <StatusBadge
                    tone={health.status === 'ok' ? 'ok' : 'warn'}
                    label={health.status === 'ok' ? 'working' : 'has problems'}
                  />
                }
              >
                <CheckList
                  label="Component checks"
                  items={Object.entries(health.checks).map(([name, c]) => ({
                    id: name,
                    text: name,
                    hint: c.detail,
                    result: c.ok ? 'ok' : 'error',
                  }))}
                />
              </Card>
            )}
            {host && <Nodes />}
            {host?.available && <NodeNetwork egress={host.egress} />}
            {host && <ForeignAgents report={host} />}
            {version && (
              <Card title={`Distribution ${version.distribution.name} ${version.distribution.version}`}>
                {!version.schema_matches_manifest && (
                  <Notice tone="error">The database schema doesn't match the manifest.</Notice>
                )}
                <KeyValueList
                  items={[
                    { key: 'Core', value: version.core, mono: true },
                    { key: 'Build', value: version.build, mono: true },
                    { key: 'Schema', value: version.schema_head ?? '—', mono: true },
                    ...Object.entries(version.images).map(([k, v]) => ({ key: k, value: v, mono: true })),
                    ...Object.entries(version.models).map(([k, v]) => ({
                      key: `model ${k}`,
                      value: v,
                      mono: true,
                    })),
                  ]}
                />
              </Card>
            )}
          </div>
        </ScreenStateGate>
      </Window>
    </div>
  )
}
