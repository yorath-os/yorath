export type Check = { ok: boolean; detail: string }
export type Health = { status: 'ok' | 'degraded'; checks: Record<string, Check> }
export type Version = {
  distribution: { name: string; version: string; schema_version: string }
  core: string
  build: string
  schema_head: string | null
  schema_matches_manifest: boolean
  images: Record<string, string>
  runtimes: Record<string, string | null>
  models: Record<string, string>
  plugins: string[]
}

async function get<T>(path: string): Promise<{ status: number; body: T }> {
  const r = await fetch(`/api${path}`)
  return { status: r.status, body: (await r.json()) as T }
}

export const fetchHealth = () => get<Health>('/health')
export const fetchVersion = () => get<Version>('/version')

// Agents outside Yorath (decision 36): the yorath scan report from the host, owner only
export type ForeignAgent = {
  agent: string
  where: 'host' | 'container'
  pid?: number
  user?: string
  root?: boolean
  container?: string
  image?: string
}
export type HostReport = {
  available: boolean
  stale: boolean
  checked_at: string | null
  agents: ForeignAgent[]
  traces: { agent: string; path: string; owner: string }[]
  containers: { name: string; image: string; project: string }[]
  egress: EgressState | null
}
export const fetchHostReport = () => get<HostReport>('/setup/host-report')

// Node network (change node-egress): mode, rules, proxy, alert
export type EgressState = {
  mode: 'on' | 'off' | 'unset'
  table: boolean
  intact: boolean
  proxy: boolean
  service: string | null
  alarm: string | null
  enforced: boolean
}
export type EgressFlow = {
  node: string
  source: string
  domain: string
  requests: number
  blocked: number
  bytes: number
  last_at: string
}
export const fetchEgress = (hours = 24) => get<EgressFlow[]>(`/journal/egress?hours=${hours}`)

export type Me = {
  id: string
  login: string
  display_name: string
  role: string
  department: string | null
  is_active: boolean
}

async function post<T>(path: string, body?: unknown): Promise<{ status: number; body: T | null }> {
  const r = await fetch(`/api${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const text = await r.text()
  return { status: r.status, body: text ? (JSON.parse(text) as T) : null }
}

async function put<T>(path: string, body: unknown): Promise<{ status: number; body: T | null }> {
  const r = await fetch(`/api${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const text = await r.text()
  return { status: r.status, body: text ? (JSON.parse(text) as T) : null }
}

export type MeResponse = (Me & { authenticated: true }) | { authenticated: false; setup_required: boolean }
export const fetchMe = () => get<MeResponse>('/auth/me')
export const login = (login: string, password: string) => post<Me>('/auth/login', { login, password })
export const setup = (login: string, password: string, display_name: string) =>
  post<Me>('/auth/setup', { login, password, display_name })
export const logout = () => post<null>('/auth/logout')

export type SetupStatus = {
  setup_completed: boolean
  models: { chat: string | null; embeddings: string | null; external: string | null }
  resources: {
    name: string
    model_id: string
    provider: string
    classes: string[]
    context_length?: number
    input_price_per_mtok: string | null
    output_price_per_mtok: string | null
  }[]
  openrouter_configured: boolean
  web_search_configured: boolean
  secrets_available: boolean
}
export type Hardware = {
  hardware: {
    vcpu: number
    ram_gb: number
    disk_free_gb: number
    gpu_name: string | null
    vram_gb: number | null
    gpu_count?: number
  }
  tier: { tier: string; model: string | null; expected_answer_seconds: number | null; note: string | null }
  local_mode: boolean
  recommended_model: string
  embeddings_model: string
  expected_answer_seconds: number | null
  minimum: { vcpu: number; ram_gb: number }
  external_model: string
}
export type ModelBudget = {
  model: string
  rss_gb: number | null
  vram_gb: number | null
  estimated: boolean
  basis: string
  limit_gb: number
  fits: boolean | null
}
export type CatalogModel = {
  name: string
  memory_gb: number | null
  estimated: boolean
  fits: boolean | null
  recommended: boolean
  installed: boolean
  current: boolean
  measured: string
}
export type ModelsInfo = {
  recommended: { chat: string; embeddings: string }
  window?: number
  catalog?: CatalogModel[]
  local_mode: boolean
  installed: string[]
  registered: { name: string; model_id: string; provider: string }[]
  budgets: Record<string, ModelBudget>
}
export type PullEvent = {
  status?: string
  total?: number
  completed?: number
  error?: string
  name?: string
  warning?: string
}

// 409 from a pull: by estimate the model won't fit — retry with force
export class PullRefused extends Error {
  readonly status: number
  constructor(status: number) {
    super(status === 409 ? 'does_not_fit' : `pull: HTTP ${status}`)
    this.status = status
  }
}

export const setupWithCode = (login: string, password: string, display_name: string, code: string | null) =>
  post<Me>('/auth/setup', { login, password, display_name, code })
export const fetchSetupStatus = () => get<SetupStatus>('/setup/status')
export const fetchHardware = () => get<Hardware>('/setup/hardware')
export const fetchModels = () => get<ModelsInfo>('/setup/models')
export const setOpenRouter = (api_key: string, consent: boolean) =>
  fetch('/api/setup/openrouter', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key, consent }),
  }).then(async (r) => ({
    status: r.status,
    body: (await r.json()) as { leaves_server?: string; warnings?: string[]; detail?: unknown },
  }))
export const setTavily = (api_key: string, consent: boolean) =>
  fetch('/api/setup/tavily', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key, consent }),
  }).then(async (r) => ({
    status: r.status,
    body: (await r.json()) as { leaves_server?: string; detail?: unknown },
  }))
export const completeSetup = () => post<{ setup_completed: boolean }>('/setup/complete')

// Model pull progress stream: NDJSON from core (it proxies Ollama /api/pull).
export async function pullModel(
  model: string,
  role: 'chat' | 'embeddings',
  onEvent: (e: PullEvent) => void,
  force = false,
) {
  const r = await fetch('/api/setup/models/pull', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model, role, force }),
  })
  if (!r.ok || !r.body) throw new PullRefused(r.status)
  const reader = r.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    for (const line of lines) if (line.trim()) onEvent(JSON.parse(line) as PullEvent)
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer) as PullEvent)
}

export type Asset = {
  id: string
  name: string
  kind: string
  description: string | null
  data_class: string
  access_rules: { roles?: string[]; departments?: string[] }
  enabled: boolean
  documents: number
  records: number
}
export type Doc = {
  id: string
  asset_id: string
  filename: string
  size_bytes: number
  data_class: string
  status: 'uploaded' | 'parsed' | 'indexing' | 'indexed' | 'failed'
  error: string | null
  pages: number | null
  created_at: string
}

export const fetchAssets = () => get<Asset[]>('/data/assets')
export const createAsset = (body: {
  name: string
  data_class: string
  kind: string
  access_rules: { roles: string[]; departments: string[] }
}) => post<Asset>('/data/assets', body)
export const fetchDocuments = (assetId?: string) =>
  get<Doc[]>(`/data/documents${assetId ? `?asset_id=${assetId}` : ''}`)
export const deleteDocument = (id: string) => fetch(`/api/data/documents/${id}`, { method: 'DELETE' })
export async function uploadDocument(assetId: string, file: File, dataClass: string | null) {
  const form = new FormData()
  form.append('file', file)
  if (dataClass) form.append('data_class', dataClass)
  const r = await fetch(`/api/data/assets/${assetId}/documents`, { method: 'POST', body: form })
  return { status: r.status, body: (await r.json()) as Doc | { detail: { code?: string; message?: string } } }
}

export type DemoUser = { login: string; password: string; display_name: string; title: string }
export const enableDemoHr = () => post<{ users: DemoUser[]; rows: number }>('/data/demo-hr', {})

export async function uploadRecords(assetId: string, file: File) {
  const form = new FormData()
  form.append('file', file)
  const r = await fetch(`/api/data/assets/${assetId}/records`, { method: 'POST', body: form })
  return { status: r.status, body: (await r.json()) as { rows: number } | { detail: { message?: string } } }
}

export type ChatModel = {
  name: string
  model_id: string
  provider: string
  data_classes: string[]
  default: boolean
  input_price_per_mtok: string | null
  output_price_per_mtok: string | null
  leaves_server: string | null
}
export type ChatAgent = { id: string; name: string; description: string | null; default: boolean }
export const fetchChatAgents = () => get<ChatAgent[]>('/chat/agents')
export const fetchChatModels = (agent: string) =>
  get<ChatModel[]>(`/chat/models?agent=${encodeURIComponent(agent)}`)

export type Conversation = {
  id: string
  agent_id: string
  title: string
  created_at: string
  updated_at: string
  answering?: boolean // the answer is running in core; the screen subscribes via /stream
}
export type Citation = {
  n: number
  document_id: string
  filename: string
  heading_path: string
  asset: string
  data_class: string
  url?: string | null
}
export type ChatMessage = {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  execution_id: string | null
  citations: Citation[]
  created_at: string
}
export type ToolStep = {
  name: string
  status: 'started' | 'done' | 'denied' | 'error'
  data_class?: string | null
  hits?: number | null
  message?: string | null
  arguments?: Record<string, unknown>
}
export type ChatEvent =
  | { event: 'started'; data: { execution_id: string; started_at: string } }
  | { event: 'tool'; data: ToolStep }
  | { event: 'citations'; data: { citations: Citation[]; denied: string[] } }
  | { event: 'queued'; data: { ahead: number } }
  | { event: 'token'; data: { text: string } }
  | { event: 'error'; data: { code: string; message: string } }
  | { event: 'done'; data: { execution_id: string; status: string; text: string } }

export const fetchConversations = () => get<Conversation[]>('/chat/conversations')
export const createConversation = (agent?: string) => post<Conversation>('/chat/conversations', { agent })
export const fetchMessages = (id: string) => get<ChatMessage[]>(`/chat/conversations/${id}/messages`)

// The answer is computed by core in the background: the stream is only a subscription; dropping it doesn't cancel the answer.
export class AnswerInProgress extends Error {}

async function readSse(r: Response, onEvent: (e: ChatEvent) => void) {
  if (!r.body) return
  const reader = r.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  const flush = (block: string) => {
    let event = ''
    let data = ''
    for (const line of block.split('\n')) {
      if (line.startsWith('event: ')) event = line.slice(7)
      else if (line.startsWith('data: ')) data += line.slice(6)
    }
    if (event && data) onEvent({ event, data: JSON.parse(data) } as ChatEvent)
  }
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const blocks = buffer.split('\n\n')
    buffer = blocks.pop() ?? ''
    blocks.forEach(flush)
  }
  if (buffer.trim()) flush(buffer)
}

// SSE over fetch (EventSource can't POST): started → tool* / citations* → token* → done | error.
export async function sendMessage(
  conversationId: string,
  content: string,
  onEvent: (e: ChatEvent) => void,
  model?: string,
  signal?: AbortSignal,
) {
  const r = await fetch(`/api/chat/conversations/${conversationId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, model }),
    signal,
  })
  if (r.status === 409) throw new AnswerInProgress('answer_in_progress')
  if (!r.ok || !r.body) throw new Error(`chat: HTTP ${r.status}`)
  await readSse(r, onEvent)
}

// Subscribe to an answer in progress: events from the start of the answer; no answer — 204 and nothing.
export async function streamAnswer(
  conversationId: string,
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
) {
  const r = await fetch(`/api/chat/conversations/${conversationId}/stream`, { signal })
  if (r.status === 204) return
  if (!r.ok) throw new Error(`chat: HTTP ${r.status}`)
  await readSse(r, onEvent)
}

export const cancelAnswer = (conversationId: string) =>
  post<{ status: string }>(`/chat/conversations/${conversationId}/cancel`, {})

export type PrincipalRef = { id: string; display_name: string; kind: 'user' | 'service' }
export type AgentTool = { name: string; targets: string[] }
export type AgentInfo = {
  id: string
  name: string
  display_name: string
  description: string | null
  hosting: string
  mode: 'delegated' | 'autonomous'
  enabled: boolean
  builtin?: boolean
  runtime: string
  control_level: 'tool' | 'sandbox' | 'gateway'
  owner: PrincipalRef | null
  service_account: PrincipalRef | null
  revision: {
    id: string
    revision_no: number
    path: string
    content_sha256: string
    created_at: string
    card: {
      model: string
      models: string[]
      tools: AgentTool[]
      data: string[]
      max_data_class: string
      triggers: string[]
    }
  } | null
  executions: { total: number; by_status: Record<string, number>; last_at: string | null }
  cost_usd: string
}
export type ExecutionInfo = {
  id: string
  status: string
  trigger: string
  created_at: string
  finished_at: string | null
  initiated_by: PrincipalRef | null
  effective_principal: PrincipalRef | null
  revision_no: number
  model: string | null
  usage: { input_tokens?: number; output_tokens?: number } | null
  cost_usd: string | null
  cost_estimated: boolean
  max_data_class_seen: string | null
  parent_execution_id: string | null
  outcome: { kind: string; status: string; ref: string | null; useful: boolean | null } | null
}

export const fetchAgents = () => get<AgentInfo[]>('/agents')

export type AgentSource = { name: string; path: string; builtin: boolean; text: string; instructions: string }
export const fetchAgentSource = (name: string) => get<AgentSource>(`/agents/${name}/source`)

export type AgentOptions = {
  tools: { name: string; description: string }[]
  assets: { name: string; data_class: string; kind: string }[]
  models: { name: string; model_id: string; external: boolean }[]
}
export const fetchAgentOptions = () => get<AgentOptions>('/agents/options')

export type AgentForm = {
  description: string
  instructions: string
  data: string[]
  tools: string[]
  models: string[]
  api?: boolean // api trigger: a call with an API key narrowed to the agent
}
export const createAgent = (form: AgentForm & { name: string }) => post<AgentInfo>('/agents', form)
export const updateAgent = (name: string, form: AgentForm) => put<AgentInfo>(`/agents/${name}`, form)
export const setAgentEnabled = (name: string, enabled: boolean) =>
  fetch(`/api/agents/${encodeURIComponent(name)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  }).then(async (r) => ({
    status: r.status,
    // a refusal carries {detail: {code, message}}: the reason must reach the screen
    body: r.ok ? null : ((await r.json().catch(() => null)) as ConnectedError | null),
  }))
export const fetchAgentExecutions = (name: string, limit = 20) =>
  get<ExecutionInfo[]>(`/agents/${encodeURIComponent(name)}/executions?limit=${limit}`)

export type JournalEvent = {
  id: string
  occurred_at: string
  event_type: string
  execution_id: string | null
  parent_execution_id: string | null
  actor: string | null
  effective_principal: string | null
  agent: string | null
  revision_no: number | null
  runtime: string | null
  model: string | null
  tool: string | null
  data_asset: string | null
  data_class: string | null
  decision: string | null
  reason: string | null
  cost_usd: string | null
  control_level: string | null
  details: Record<string, unknown>
}
export type ChainItem = {
  id: string
  parent_execution_id: string | null
  agent: string
  revision_no: number
  status: string
  trigger: string
  created_at: string
  finished_at: string | null
  actor: string | null
  effective_principal: string | null
  model: string | null
  max_data_class_seen: string | null
  api_key?: string | null // "name (prefix…)" of the key the /v1 call came with
}
export type TraceSpan = {
  span_id: string
  parent_span_id: string | null
  execution_id: string
  name: string
  kind: string
  model: string | null
  status: string
  started_at: string
  ended_at: string | null
  attributes: Record<string, unknown>
}
export type ExecutionDetail = { chain: ChainItem[]; events: JournalEvent[]; spans: TraceSpan[] }
export type JournalFilters = {
  agent?: string
  user?: string
  decision?: string
  before?: string
  limit: number
}

export function fetchJournal(filters: JournalFilters) {
  const query = new URLSearchParams()
  for (const key of ['agent', 'user', 'decision', 'before', 'limit'] as const) {
    const value = filters[key]
    if (value) query.set(key, String(value))
  }
  return get<JournalEvent[]>(`/journal/events?${query.toString()}`)
}
export const fetchExecutionDetail = (id: string) => get<ExecutionDetail>(`/journal/executions/${id}`)
export type UserInfo = {
  id: string
  login: string
  display_name: string
  role: string
  department: string | null
  is_active: boolean
}
export const fetchUsers = () => get<UserInfo[]>('/users')
export const createUser = (body: {
  login: string
  password: string
  display_name: string
  role: string
  department: string | null
  attributes: Record<string, string>
}) => post<UserInfo>('/users', body)
export const setUserActive = (id: string, is_active: boolean) =>
  fetch(`/api/users/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_active }),
  }).then((r) => ({ status: r.status }))

export type PolicyInfo = {
  name: string
  cedar: string
  enabled: boolean
  description: string | null
  updated_at: string | null
}
export const fetchPolicies = () => get<PolicyInfo[]>('/policies')

export type Task = {
  id: string
  agent: string
  agent_display_name: string
  revision_no: number
  status: string
  trigger: string
  started_by: string | null
  created_at: string
  finished_at: string | null
  model: string | null
  error: string | null
  parent_execution_id: string | null
}
export type TaskGroup = 'all' | 'running' | 'queued' | 'failed'
export type TaskList = { counts: Record<TaskGroup, number>; tasks: Task[] }
export type TaskQuery = { status?: TaskGroup; agent?: string; trigger?: string }

export function fetchTasks(filters: TaskQuery) {
  const query = new URLSearchParams()
  for (const key of ['status', 'agent', 'trigger'] as const) {
    const value = filters[key]
    if (value && value !== 'all') query.set(key, value)
  }
  const text = query.toString()
  return get<TaskList>(`/tasks${text ? `?${text}` : ''}`)
}

export type DeskSummary = {
  running: number
  queued: number
  errors_hour: number
  cost_usd_24h: string
  tasks: Task[]
  more_tasks: number
  nodes: { name: string; cpu_count: number; ram_gb: number; gpu: string | null; vram_gb: number | null }[]
  agent_errors: { agent: string; total: number; by_status: Record<string, number> }[]
  policies: { allowed: number; denied: number; reasons: { reason: string; count: number }[] }
  error_log: { id: string; at: string; status: string; agent: string; message: string | null }[]
}
export const fetchDeskSummary = () => get<DeskSummary>('/desk/summary')

// Model endpoint API keys (change model-endpoint): the value arrives once — in the create response
export type ApiKey = {
  id: string
  name: string
  prefix: string
  user_id: string
  login: string
  created_at: string
  last_used_at: string | null
  revoked_at: string | null
  agent: string | null // key narrowed to an agent: calls only agent/<name>
}
export type ApiKeyCreated = ApiKey & { key: string }
export const fetchApiKeys = () => get<ApiKey[]>('/api-keys')
export const createApiKey = (name: string, agent: string | null = null) =>
  post<ApiKeyCreated>('/api-keys', agent ? { name, agent } : { name })
export const fetchApiAgents = () => get<string[]>('/api-keys/agents')
export const revokeApiKey = (id: string) => fetch(`/api/api-keys/${id}`, { method: 'DELETE' })

// Connected agents (change connected-agents): manifest catalog, connection, version, bot, internet
export type ConnectedAgent = {
  name: string
  description: string | null
  license: string
  homepage: string
  tested_version: string
  min_context: number
  connected: boolean
  enabled: boolean
  version: string | null
  untested: boolean
  network_enabled: boolean
  telegram: boolean
  telegram_users: string[]
  /** the bot as the agent's node sees it (change telegram-check); null until the first check */
  telegram_check?: TelegramCheck | null
  status: 'starting' | 'running' | 'stopped' | 'failed' | 'lost' | null
  node: string | null
  status_detail: string | null
  running_version: string | null
  running_digest: string | null
  reported_at: string | null
}
export type TelegramCheck = {
  state: 'ok' | 'unreachable' | 'rejected' | 'internet_off'
  bot?: string | null
  detail?: string | null
  checked_at: string
}
export type ConnectedError = { detail?: { code?: string; message?: string } | string }
export type ConnectIn = {
  version?: string | null
  node?: string | null
  telegram_token?: string | null
  telegram_users?: string[]
}

const enc = encodeURIComponent
export const fetchConnected = () => get<ConnectedAgent[]>('/agents/connected')
export const connectAgent = (name: string, body: ConnectIn) =>
  post<ConnectedAgent & ConnectedError>(`/agents/connected/${enc(name)}`, body)
export const disconnectAgent = async (name: string, purge = false) => {
  const r = await fetch(`/api/agents/connected/${enc(name)}${purge ? '?purge=true' : ''}`, {
    method: 'DELETE',
  })
  const text = await r.text()
  return { status: r.status, body: text ? (JSON.parse(text) as ConnectedAgent & ConnectedError) : null }
}
export const setAgentNetwork = (name: string, enabled: boolean) =>
  put<ConnectedAgent & ConnectedError>(`/agents/connected/${enc(name)}/network`, { enabled })
export const setAgentChannel = (name: string, telegram_token: string | null, telegram_users: string[]) =>
  put<ConnectedAgent & ConnectedError>(`/agents/connected/${enc(name)}/channel`, {
    telegram_token,
    telegram_users,
  })
export const checkAgentTelegram = (name: string) =>
  post<ConnectedAgent & ConnectedError>(`/agents/connected/${enc(name)}/telegram/check`, {})
export const setAgentVersion = (name: string, version: string | null) =>
  put<ConnectedAgent & ConnectedError>(`/agents/connected/${enc(name)}/version`, { version })

/** Core's denial text: message with a hint, or the code. */
export function connectedError(status: number, body: ConnectedError | null): string {
  const d = body?.detail
  if (d && typeof d === 'object') return d.message ?? d.code ?? `HTTP ${status}`
  return typeof d === 'string' ? d : `HTTP ${status}`
}

// Cluster (change cluster-nodes): nodes, join token, approve and revoke
export type ClusterNode = {
  id: string
  name: string
  role: 'main' | 'worker'
  status: 'active' | 'pending' | 'lost' | 'revoked'
  overlay_ip: string | null
  cpu_count: number
  ram_gb: number
  gpu: string | null
  gpu_count: number
  has_model: boolean
  last_seen_at: string | null
  units: number
  vram_gb?: number | null
  allowed_data_classes?: string[]
}
// Agents outside Yorath on every node (change outside-agents-cluster)
export type OutsideAgent = {
  agent: string
  where: string
  root?: boolean
  container?: string
  image?: string
}
export type OutsideNode = {
  node: string
  main: boolean
  node_status: string
  checked_at: string | null
  agents: OutsideAgent[]
  traces: { agent: string; path: string }[]
  truncated?: boolean // the scan found more than a report carries
  model_url: string | null // a worker node: the model through the cluster tunnel
}
export const fetchOutsideAgents = () => get<{ nodes: OutsideNode[] }>('/cluster/outside-agents')
export type JoinToken = { token: string; expires_at: string; command: string }
export const fetchNodes = () => get<ClusterNode[]>('/cluster/nodes')
export const createJoinToken = () => post<JoinToken & ConnectedError>('/cluster/tokens')
export const nodeAction = (name: string, action: 'approve' | 'revoke') =>
  post<ClusterNode & ConnectedError>(`/cluster/nodes/${encodeURIComponent(name)}/${action}`)

// Model catalog and placements (change model-placements, decision 44): a model on a model server
export type Placement = {
  node: string
  server: string
  status: 'pulling' | 'ready' | 'failed'
  detail: string | null
  progress: number
  node_status: ClusterNode['status']
}
export type PlacedModel = {
  queued?: number // requests waiting for a free model server
  name: string
  model_id: string
  context_length: number
  classes: string[]
  default: boolean
  placements: Placement[]
}
export type ServerModel = {
  name: string
  memory_gb: number | null // video memory on a GPU, RAM without one
  estimated: boolean
  fits: boolean | null
  measured: string
}
export type ModelServer = {
  main?: boolean
  window?: number | null // context window a model gets on this server
  recommended?: string | null // the model the hardware table picks for this server
  catalog?: ServerModel[]
  in_flight?: number // requests running on the server right now
  node: string
  server: string
  gpus: number[]
  gpu: string | null
  vram_gb: number | null
  slots: number
  model: string | null
  status: Placement['status'] | null
  node_status: ClusterNode['status']
}
export type Placements = { models: PlacedModel[]; servers: ModelServer[] }
export type PlaceEvent = PullEvent & { servers?: string[]; node?: string; default?: boolean; code?: string }
export const fetchPlacements = () => get<Placements>('/cluster/models')
export const removePlacement = (model: string, node: string, gpu: number | null, force = false) =>
  post<{ removed: string[] } & ConnectedError>('/cluster/models/remove', { model, node, gpu, force })
export const setDefaultModel = (model: string) =>
  post<{ name: string; model_id: string } & ConnectedError>('/cluster/models/default', { model })

// A refusal (busy server, data class of the node, memory) is an HTTP error with the reason; the pull
// progress is the same NDJSON stream as in pullModel.
export async function placeModel(
  body: {
    model: string
    node: string
    gpu: number | null
    replace: boolean
    force: boolean
    move?: boolean
  },
  onEvent: (e: PlaceEvent) => void,
): Promise<{ code: string; message: string } | null> {
  const r = await fetch('/api/cluster/models/place', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok || !r.body) {
    const detail = (
      (await r.json().catch(() => null)) as { detail?: { code?: string; message?: string } } | null
    )?.detail
    return { code: detail?.code ?? `http_${r.status}`, message: detail?.message ?? `HTTP ${r.status}` }
  }
  const reader = r.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    for (const line of lines) if (line.trim()) onEvent(JSON.parse(line) as PlaceEvent)
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer) as PlaceEvent)
  return null
}
