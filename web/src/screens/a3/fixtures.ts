/**
 * Sample data for the A3 screens — the same values as in the mockups.
 * Used by tests and the component showcase. In the product, data comes from the API
 * via props; these numbers and names are made up for the mockup.
 */
import type { ChatMessageData } from '../../ui/Chat'
import type { AgentRow } from './AgentsScreen'
import type { CheckStepData, PermissionsStepData, TestStepData } from './AgentInstallScreen'
import type { TaskDetail, TaskRow } from './JournalScreen'
import type { StatusData, WidgetPlacement } from './StatusScreen'
import { PRODUCT } from '../../brand'

export const statusLayout: WidgetPlacement[] = [
  { id: 'w1', kind: 'running', title: 'Running', span: 3, rows: 2 },
  { id: 'w2', kind: 'queued', title: 'Queued', span: 3, rows: 2 },
  { id: 'w3', kind: 'errors-hour', title: 'Errors in the last hour', span: 3, rows: 2 },
  { id: 'w4', kind: 'waiting-human', title: 'Waiting for a human', span: 3, rows: 2 },
  { id: 'w5', kind: 'tasks', title: 'Running tasks', span: 7, rows: 4, filterLabel: 'all agents' },
  { id: 'w6', kind: 'nodes', title: 'Node load', span: 5, rows: 4 },
  { id: 'w7', kind: 'agent-errors', title: 'Agent errors', span: 4, rows: 3, filterLabel: '24 hours' },
  { id: 'w8', kind: 'policies', title: 'Policies over agents', span: 4, rows: 3, filterLabel: '24 hours' },
  { id: 'w9', kind: 'error-log', title: 'Error log', span: 4, rows: 3 },
]

export const statusData: StatusData = {
  counters: {
    running: { value: 12, caption: 'tasks across 9 agents' },
    queued: { value: 5, caption: 'waiting for a free node' },
    'errors-hour': { value: 3, caption: 'failed · timeout' },
    'waiting-human': { value: 2, caption: 'approve a PR, permissions' },
  },
  tasks: [
    {
      id: 't1',
      agent: 'contract-review',
      title: 'Contract batch review',
      startedBy: 'scheduled',
      node: 'gpu-01',
      elapsed: '22 min',
      progress: 55,
    },
    {
      id: 't2',
      agent: 'support-triage',
      title: 'Incoming request triage',
      startedBy: 'scheduled',
      node: 'vps-01',
      elapsed: '12 min',
      progress: 64,
    },
    {
      id: 't3',
      agent: 'developer-agent',
      title: 'Issue → pull request',
      startedBy: 'issue event',
      node: 'vps-02',
      elapsed: '7 min',
    },
  ],
  moreTasks: 9,
  nodes: [
    {
      id: 'n1',
      name: 'vps-01',
      spec: '16 vCPU · 16 GB',
      agentsText: '6 agents',
      meters: [
        { label: 'CPU', percent: 62 },
        { label: 'RAM', percent: 71 },
      ],
    },
    {
      id: 'n3',
      name: 'gpu-01',
      spec: 'A10 · 24 GB',
      agentsText: '4 agents',
      meters: [
        { label: 'GPU', percent: 78, warn: true },
        { label: 'VRAM', percent: 66 },
      ],
    },
  ],
  agentErrors: [
    { id: 'e1', label: 'contract-review', value: 4, hint: 'timeout ×3 · failed ×1' },
    { id: 'e2', label: 'support-triage', value: 2, hint: 'failed: MCP server did not respond' },
  ],
  policies: {
    allowed: 303,
    denied: 9,
    reasons: [
      { id: 'r1', label: 'Data class above the agent’s clearance', value: 5 },
      { id: 'r2', label: 'External model and Confidential', value: 3 },
      { id: 'r3', label: 'Domain not on the allow-list', value: 1 },
    ],
  },
  errorLog: [
    { id: 'l1', time: '14:05', level: 'TIMEOUT', tone: 'error', text: 'contract-review: 30 min, attempt 1' },
    {
      id: 'l2',
      time: '13:52',
      level: 'FAILED',
      tone: 'error',
      text: 'support-triage: MCP server did not respond',
    },
  ],
}

export const tasks: TaskRow[] = [
  {
    id: 'x1',
    title: 'Contract batch review',
    shortId: '019a5c…',
    agent: 'contract-review',
    startedBy: 'schedule',
    node: 'gpu-01',
    status: 'running',
    duration: '22 min',
  },
  {
    id: 'x2',
    title: 'Approve a pull request',
    shortId: '019a5a…',
    agent: 'developer-agent',
    startedBy: 'issue event',
    node: 'vps-02',
    status: 'waiting',
    duration: '1 h',
  },
  {
    id: 'x3',
    title: 'October invoice review',
    shortId: '019a5b…',
    agent: 'invoice-parser',
    startedBy: 'upload to Workspace',
    node: 'vps-02',
    status: 'failed',
    duration: '9 min',
  },
]

export const taskDetail: TaskDetail = {
  id: 'x1',
  title: 'Contract batch review',
  meta: 'contract-review · revision 7b20e4 · execution 019a5c…',
  status: 'running',
  backgroundText:
    'Running in the background on gpu-01. You can close the browser — you’ll get a notification when the task finishes or needs a human.',
  progress: 55,
  facts: [
    { key: 'Trigger', value: 'scheduled, daily at 09:00' },
    { key: 'On behalf of', value: 'the agent’s service account' },
    { key: 'Owner', value: 'Alex' },
    { key: 'Model', value: 'local/default', mono: true },
    { key: 'Control', value: 'tool · every call through the Gateway' },
  ],
  steps: [
    { id: 's1', text: 'Created on schedule', time: '13:46', state: 'done' },
    { id: 's2', text: 'Waited for a free GPU', time: '0:40', state: 'done' },
    { id: 's3', text: 'Checking document 10 of 14', time: 'now', state: 'current' },
    {
      id: 's4',
      text: 'The report will be saved to Workspace: results/contract-review/',
      time: '~15 min',
      state: 'next',
    },
  ],
  permissions: [
    { id: 'p1', label: 'Read: contracts · Confidential', dataClass: 'confidential' },
    { id: 'p2', label: 'Model: local only' },
    { id: 'p3', label: 'Network: none' },
  ],
  canStop: true,
}

export const agents: AgentRow[] = [
  {
    id: 'a1',
    name: 'Document Assistant',
    avatarTone: 1,
    description: 'native · delegated · control on every call',
    status: 'running',
    actionLabel: 'Open',
  },
  {
    id: 'a2',
    name: 'Developer Agent',
    letter: 'P',
    avatarTone: 2,
    description: 'codex · autonomous · control at the sandbox boundary',
    status: 'waiting',
    statusText: 'Waiting for an issue',
    actionLabel: 'Stop',
  },
  {
    id: 'a3',
    name: 'Research Agent',
    avatarTone: 3,
    description: 'native · delegated · Public only',
    status: 'idle',
    actionLabel: 'Run',
  },
]

export const checkStep: CheckStepData = {
  summary: [
    { key: 'Name', value: 'contract-review', mono: true },
    { key: 'Purpose', value: 'Reviews contracts against a checklist and prepares a report' },
    { key: 'Runtime', value: 'native', hint: `${PRODUCT}'s own loop, every call controlled` },
    { key: 'Mode', value: 'autonomous', hint: 'as its own service account, not as people' },
  ],
  checks: [
    { id: 'c1', text: 'Header is fine', hint: 'compatible with Agent Card and MCP', result: 'ok' },
    { id: 'c2', text: 'Model local/default fits', hint: 'allows the Confidential class', result: 'ok' },
    {
      id: 'c3',
      text: 'Data source contracts not found',
      hint: 'we’ll create and upload it at the "Data" step',
      result: 'warn',
    },
  ],
  manifest: [
    { key: 'name', value: 'contract-review' },
    { key: 'runtime', value: 'native' },
    { key: 'mode', value: 'autonomous' },
  ],
}

export const permissionsStep = (onToggle: PermissionsStepData['onToggle']): PermissionsStepData => ({
  serviceAccount: 'svc-contract-review',
  owner: 'Alex',
  mode: 'autonomous',
  onToggle,
  rows: [
    {
      id: 'data',
      category: 'Data',
      text: 'Read contracts',
      dataClass: 'confidential',
      hint: 'only through search_docs',
      mode: 'ask',
      granted: true,
    },
    {
      id: 'models',
      category: 'Models',
      text: 'Local only: local/default',
      hint: 'external models are closed for Confidential',
      mode: 'policy',
    },
    {
      id: 'net',
      category: 'Network',
      text: 'No internet access',
      hint: 'the agent didn’t request network access',
      mode: 'none',
    },
  ],
  policyNote:
    'External models are closed by the default policy "Confidential doesn’t leave the server". You can change it in the Policies section, not in the agent.',
})

export const testStep = (
  fns: Pick<TestStepData, 'onOpenReport' | 'onOpenTrace' | 'onOpenTechWindow' | 'onRetest'>,
): TestStepData => ({
  documentName: 'Supply contract No. 14.pdf',
  result: 'passed',
  events: [
    { id: 'v1', text: 'Started on gpu-01', hint: 'as svc-contract-review', time: '0:00', result: 'ok' },
    {
      id: 'v2',
      text: 'search_docs → contracts',
      hint: 'Confidential · allowed',
      time: '0:03',
      result: 'ok',
    },
    { id: 'v3', text: 'Finished', hint: 'report saved', time: '2:10', result: 'ok' },
  ],
  reportText:
    'Found 2 deviations from the checklist: payment term of 60 days instead of 30, and no confidentiality clause.',
  created: [
    { key: 'Agent', value: 'contract-review, revision 7b20e4' },
    { key: 'Schedule', value: 'every day at 09:00, first run tomorrow' },
  ],
  ...fns,
})

export const chatMessages: ChatMessageData[] = [
  { id: 'm1', role: 'user', text: 'How many vacation days do I get with less than a year of service?' },
  {
    id: 'm2',
    role: 'assistant',
    status: 'done',
    text: 'You can take vacation after six months of work. Annual paid vacation is 28 calendar days [1].',
    toolCalls: [
      {
        id: 'tc1',
        tool: 'search_docs',
        summary: 'team-docs · 3 fragments',
        status: 'done',
        dataClass: 'internal',
      },
    ],
    citations: [{ n: 1, title: 'Vacation policy.pdf', location: 'p. 3' }],
  },
  { id: 'm3', role: 'user', text: 'What does the market say about a four-day week?' },
  {
    id: 'm4',
    role: 'assistant',
    status: 'denied',
    text: '',
    toolCalls: [
      {
        id: 'tc2',
        tool: 'ask_agent',
        summary: '→ research-agent',
        status: 'denied',
        reason: 'Confidential context, and research-agent is cleared only for Public.',
      },
    ],
    errorText:
      'Can’t pass the question to Research Agent: this conversation already contained Confidential data.',
  },
]
