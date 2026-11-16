import { Button, Chip, SegmentedControl } from '../../ui/Controls'
import { Field, FileDrop, FileList, PermissionList, type FileItem, type PermissionRow } from '../../ui/Forms'
import { Icon } from '../../ui/Icon'
import { ProgressBar, StatusBadge } from '../../ui/Indicators'
import { CheckList, CodeBlock, KeyValueList, Notice, type CheckItem, type KeyValue } from '../../ui/Lists'
import { ScreenStateGate } from '../../ui/StateView'
import type { DataClass, ScreenState } from '../../ui/types'
import { Card, CardRow, OptionCard, WizardWindow } from '../../ui/Wizard'
import { PRODUCT } from '../../brand'

/**
 * Agents (install flow) ← "Paths 1–5 · installing a new agent".
 * The same wizard for any source: check, permissions, data, test.
 */

export const INSTALL_STEPS = ['Source', 'Check', 'Permissions', 'Data', 'Test and launch']

export type InstallSource = 'catalog' | 'file' | 'git' | 'external'

export interface SelectOption {
  value: string
  label: string
}

export interface SourceStepData {
  source: InstallSource
  onSourceChange: (s: InstallSource) => void
  /** The uploaded md file. */
  file?: { name: string; sizeText: string; statusText: string }
  onFiles: (files: File[]) => void
  gitUrl?: string
  onGitUrlChange?: (url: string) => void
  /** md file parsing error. */
  fileError?: string
}

export interface CheckStepData {
  summary: KeyValue[]
  checks: CheckItem[]
  manifest: { key: string; value: string }[]
}

export interface PermissionsStepData {
  /** «svc-contract-review». */
  serviceAccount?: string
  owner: string
  /** delegated — the agent acts on behalf of the user; autonomous — on behalf of a service account. */
  mode: 'delegated' | 'autonomous'
  rows: PermissionRow[]
  onToggle: (id: string, granted: boolean) => void
  policyNote?: string
}

export interface DataStepData {
  assetName: string
  onAssetNameChange: (v: string) => void
  assetNameError?: string
  isNewAsset: boolean
  dataClass: DataClass
  onDataClassChange: (c: DataClass) => void
  viewers: string[]
  onAddViewer: () => void
  files: FileItem[]
  indexed: number
  total: number
  onFiles: (files: File[]) => void
  schedule: { value: string; options: SelectOption[]; onChange: (v: string) => void }
  node: { value: string; options: SelectOption[]; onChange: (v: string) => void; hint?: string }
  timeLimit: { value: string; options: SelectOption[]; onChange: (v: string) => void }
  /** "not needed — local model only". */
  budgetText: string
}

export interface TestEvent {
  id: string
  text: string
  hint?: string
  time: string
  result: 'ok' | 'error' | 'pending'
}

export interface TestStepData {
  documentName: string
  result: 'running' | 'passed' | 'failed'
  events: TestEvent[]
  reportText?: string
  failureText?: string
  created: KeyValue[]
  onOpenReport: () => void
  onOpenTrace: () => void
  onOpenTechWindow: () => void
  onRetest: () => void
}

export interface AgentInstallScreenProps {
  state: ScreenState
  step: 1 | 2 | 3 | 4 | 5
  /** Agent name after parsing the file — goes into the window title. */
  agentName?: string
  onClose: () => void
  onBack: () => void
  onNext: () => void
  /** Disables "Next" (e.g. no file chosen or the check didn't pass). */
  nextDisabled?: boolean
  nextBusy?: boolean
  source?: SourceStepData
  check?: CheckStepData
  permissions?: PermissionsStepData
  data?: DataStepData
  test?: TestStepData
}

const HEADINGS: Record<number, { heading: string; lead?: string; next: string }> = {
  1: {
    heading: 'Where do you install the agent from?',
    lead: 'An agent is described by an md file. Wherever it comes from, it goes through the same steps.',
    next: 'Check',
  },
  2: {
    heading: 'Agent check',
    lead: `${PRODUCT} parsed the md file header and checked that everything the agent needs exists in the system.`,
    next: 'Next: permissions',
  },
  3: {
    heading: '',
    lead: 'Every agent has the same six categories. The agent gets no more than what is allowed here and by policies.',
    next: 'Allow and continue',
  },
  4: { heading: 'Data and schedule', next: 'Next: test' },
  5: {
    heading: 'Test run',
    lead: 'The agent will process one document with the same permissions it will work with. If everything looks right, enable it.',
    next: 'Enable agent',
  },
}

const CLASSES: { value: DataClass; label: string }[] = [
  { value: 'public', label: 'Public' },
  { value: 'internal', label: 'Internal' },
  { value: 'confidential', label: 'Confidential' },
  { value: 'restricted', label: 'Restricted' },
]

export function AgentInstallScreen(p: AgentInstallScreenProps) {
  const h = HEADINGS[p.step]
  const heading = p.step === 3 ? `What ${p.agentName ?? 'the agent'} will be able to do` : h.heading
  const lead =
    p.step === 4 && p.data
      ? `The ${p.data.assetName || 'data'} source will appear in the Data section and will be available to other agents only with your permission.`
      : h.lead
  const warnCount = p.check?.checks.filter((c) => c.result === 'warn').length ?? 0
  const footerNote =
    p.step === 2 && warnCount > 0
      ? `Step 2 of 5 · warnings: ${warnCount}`
      : p.step === 3
        ? 'Step 3 of 5 · the decision will be recorded in the journal'
        : p.step === 4
          ? 'Step 4 of 5 · indexing will continue in the background'
          : undefined

  return (
    <div className="a3-desk__pad">
      <WizardWindow
        title={p.agentName ? `Agent install · ${p.agentName}` : 'Agent install'}
        steps={INSTALL_STEPS}
        current={p.step}
        heading={heading}
        lead={p.state.kind === 'ready' ? lead : undefined}
        footerNote={footerNote}
        onClose={p.onClose}
        busy={p.state.kind === 'loading'}
        actions={
          <>
            {p.step === 1 ? (
              <Button onClick={p.onClose}>Cancel</Button>
            ) : (
              <Button onClick={p.onBack}>Back</Button>
            )}
            {p.step === 5 && p.test && <Button onClick={p.test.onRetest}>Rerun test</Button>}
            <Button
              variant={p.step === 5 ? 'success' : 'primary'}
              onClick={p.onNext}
              disabled={p.nextDisabled || p.state.kind !== 'ready'}
              loading={p.nextBusy}
            >
              {h.next}
            </Button>
          </>
        }
      >
        <ScreenStateGate state={p.state} emptyTitle="No data for this step" skeletonRows={5}>
          {p.step === 1 && p.source && <SourceStep d={p.source} />}
          {p.step === 2 && p.check && <CheckStep d={p.check} />}
          {p.step === 3 && p.permissions && <PermissionsStep d={p.permissions} />}
          {p.step === 4 && p.data && <DataStep d={p.data} />}
          {p.step === 5 && p.test && <TestStep d={p.test} />}
        </ScreenStateGate>
      </WizardWindow>
    </div>
  )
}

/* ── Step 1 ── */
function SourceStep({ d }: { d: SourceStepData }) {
  const opt = (
    value: InstallSource,
    title: string,
    description: string,
    extra?: { disabled?: boolean; badge?: string },
  ) => (
    <OptionCard
      name="install-source"
      value={value}
      checked={d.source === value}
      onChange={(v) => d.onSourceChange(v as InstallSource)}
      title={title}
      description={description}
      {...extra}
    />
  )
  return (
    <>
      <fieldset className="a3-options">
        <legend className="a3-visually-hidden">Agent source</legend>
        {opt(
          'catalog',
          `${PRODUCT} catalog`,
          'Verified agents: Document Assistant, Developer Agent, Research Agent.',
        )}
        {opt(
          'file',
          'md file',
          'Upload the agent description or put it in the agents/ folder on the server.',
        )}
        {opt(
          'git',
          'Git repository',
          'A link to a repository with an agents/ folder. New versions of the file become updates.',
        )}
        {opt(
          'external',
          'External agent',
          `The agent runs on another platform; ${PRODUCT} only registers and observes it.`,
          { disabled: true, badge: 'later' },
        )}
      </fieldset>
      {d.source === 'file' &&
        (d.file ? (
          <div className="a3-file-card">
            <span className="a3-file-card__icon" aria-hidden="true">
              MD
            </span>
            <span className="a3-file-card__text">
              <b className="a3-mono a3-truncate" title={d.file.name}>
                {d.file.name}
              </b>
              <small>
                {d.file.sizeText} · {d.file.statusText}
              </small>
            </span>
            <label className="a3-button a3-button--secondary">
              Choose another
              <input
                className="a3-visually-hidden"
                type="file"
                accept=".md"
                onChange={(e) => d.onFiles(Array.from(e.target.files ?? []))}
              />
            </label>
          </div>
        ) : (
          <FileDrop
            label="Drop the agent's md file here or choose it"
            accept=".md"
            multiple={false}
            onFiles={d.onFiles}
          />
        ))}
      {d.source === 'git' && (
        <Field label="Repository URL" hint="For example, https://git.example.com/team/agents.git">
          {({ id, describedBy }) => (
            <input
              id={id}
              className="a3-input"
              type="url"
              aria-describedby={describedBy}
              value={d.gitUrl ?? ''}
              onChange={(e) => d.onGitUrlChange?.(e.target.value)}
            />
          )}
        </Field>
      )}
      {d.fileError && <Notice tone="error">{d.fileError}</Notice>}
      <div className="a3-same">
        Then, for any source:
        <span>Check</span>
        <span>Permissions</span>
        <span>Data</span>
        <span>Test</span>
      </div>
    </>
  )
}

/* ── Step 2 ── */
function CheckStep({ d }: { d: CheckStepData }) {
  return (
    <>
      <CardRow>
        <Card title="What this agent is">
          <KeyValueList items={d.summary} />
        </Card>
        <Card title="Checks">
          <CheckList items={d.checks} label="Check results" />
        </Card>
      </CardRow>
      <CodeBlock lines={d.manifest} label="md file header" />
    </>
  )
}

/* ── Step 3 ── */
function PermissionsStep({ d }: { d: PermissionsStepData }) {
  return (
    <>
      <div className="a3-identity">
        <span className="a3-identity__mark a3-mono" aria-hidden="true">
          {d.mode === 'autonomous' ? 'svc' : 'you'}
        </span>
        <span>
          <b>
            {d.mode === 'autonomous'
              ? `Runs as the service account ${d.serviceAccount ?? ''}`
              : 'Runs on behalf of the user who sets the task'}
          </b>
          <small>
            {d.mode === 'autonomous'
              ? `Owner: ${d.owner}. The owner's permissions play no part in the agent's work.`
              : 'The agent gets no more permissions than this user has.'}
          </small>
        </span>
      </div>
      <PermissionList rows={d.rows} onToggle={d.onToggle} />
      {d.policyNote && <Notice tone="info">{d.policyNote}</Notice>}
    </>
  )
}

/* ── Step 4 ── */
function DataStep({ d }: { d: DataStepData }) {
  const sel = (
    label: string,
    s: { value: string; options: SelectOption[]; onChange: (v: string) => void },
    hint?: string,
  ) => (
    <Field label={label} hint={hint}>
      {({ id, describedBy }) => (
        <select
          id={id}
          className="a3-input"
          aria-describedby={describedBy}
          value={s.value}
          onChange={(e) => s.onChange(e.target.value)}
        >
          {s.options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      )}
    </Field>
  )
  const pct = d.total ? (d.indexed / d.total) * 100 : 0
  return (
    <CardRow>
      <Card
        wide
        title={`Data source ${d.assetName || ''}`.trim()}
        titleNote={d.isNewAsset ? 'new' : undefined}
      >
        <Field label="Name" error={d.assetNameError}>
          {({ id, describedBy, invalid }) => (
            <input
              id={id}
              className="a3-input"
              value={d.assetName}
              aria-invalid={invalid || undefined}
              aria-describedby={describedBy}
              onChange={(e) => d.onAssetNameChange(e.target.value)}
            />
          )}
        </Field>
        <div className="a3-field">
          <span className="a3-field__label" id="a3-class-label">
            Data class
          </span>
          <SegmentedControl
            label="Data class"
            options={CLASSES}
            value={d.dataClass}
            onChange={d.onDataClassChange}
            tone="confidential"
          />
          <small className="a3-field__hint">
            The class follows the data: everything the agent makes from it is classified no lower than the
            chosen class.
          </small>
        </div>
        <div className="a3-field">
          <span className="a3-field__label">Which people can see it</span>
          <div className="a3-chips">
            {d.viewers.map((v) => (
              <Chip key={v}>{v}</Chip>
            ))}
            <Chip variant="add" onClick={d.onAddViewer}>
              Add
            </Chip>
          </div>
        </div>
        <FileDrop onFiles={d.onFiles} />
        {d.total > 0 && (
          <>
            <div className="a3-inline">
              <ProgressBar value={pct} label="Indexing" showValue={false} tone="ok" size="md" />
              <span className="a3-text-xs a3-text-muted">
                {d.indexed} of {d.total} files indexed
              </span>
            </div>
            <FileList files={d.files} />
          </>
        )}
      </Card>
      <Card title="When and where it runs">
        {sel('Trigger', d.schedule)}
        {sel('Node', d.node, d.node.hint)}
        {sel('Time limit per task', d.timeLimit, 'When it runs out, the task gets the "timed out" status.')}
        <Field label="Budget">
          {({ id }) => <input id={id} className="a3-input" value={d.budgetText} disabled readOnly />}
        </Field>
      </Card>
    </CardRow>
  )
}

/* ── Step 5 ── */
function TestStep({ d }: { d: TestStepData }) {
  const badge =
    d.result === 'passed' ? (
      <StatusBadge variant="pill" tone="ok" label="passed" />
    ) : d.result === 'failed' ? (
      <StatusBadge variant="pill" tone="error" label="failed" />
    ) : (
      <StatusBadge variant="pill" tone="info" label="running" />
    )
  return (
    <CardRow>
      <Card wide title={`Test on ${d.documentName}`} titleExtra={badge}>
        <ol className="a3-events" aria-label="Test run steps" aria-live="polite">
          {d.events.map((e) => (
            <li key={e.id} className={`a3-events__item a3-events__item--${e.result}`}>
              <span className="a3-events__mark" aria-hidden="true">
                {e.result === 'ok' ? <Icon name="check" size="sm" /> : e.result === 'error' ? '!' : ''}
              </span>
              <span>
                {e.text}
                {e.hint && <small>{e.hint}</small>}
              </span>
              <span className="a3-events__time a3-mono">{e.time}</span>
            </li>
          ))}
        </ol>
        {d.result === 'failed' && d.failureText && <Notice tone="error">{d.failureText}</Notice>}
        {d.reportText && (
          <div className="a3-report">
            <b>Report</b>
            <p>{d.reportText}</p>
          </div>
        )}
        <div className="a3-links">
          <Button variant="link" onClick={d.onOpenReport}>
            Open in Workspace
          </Button>
          <Button variant="link" onClick={d.onOpenTrace}>
            Trace in the journal
          </Button>
          <Button variant="link" onClick={d.onOpenTechWindow}>
            Agent technical window
          </Button>
        </div>
      </Card>
      <Card title="After enabling">
        <KeyValueList items={d.created} layout="rows" />
      </Card>
    </CardRow>
  )
}
