import { useEffect, useMemo, useRef, useState } from 'react'
import {
  AnswerInProgress,
  cancelAnswer,
  createConversation,
  fetchChatAgents,
  fetchChatModels,
  fetchConversations,
  fetchMessages,
  sendMessage,
  streamAnswer,
  type ChatAgent,
  type ChatEvent,
  type ChatMessage,
  type ChatModel,
  type Citation,
  type Conversation,
  type ToolStep,
} from './api'
import { asDataClass } from './format'
import { ChatScreen } from './screens/a3/ChatScreen'
import type { ChatMessageData, Citation as ViewCitation, ToolCall } from './ui/Chat'
import { Button } from './ui/Controls'
import { Field } from './ui/Forms'
import { Notice } from './ui/Lists'
import type { ScreenState } from './ui/types'

type Draft = {
  text: string
  citations: Citation[]
  denied: string[]
  steps: ToolStep[]
  error: { code: string; message: string } | null
  startedAt: number | null // answer start in core: the counter stays right after returning to the screen
  queued: number | null // every model server is busy: requests ahead of this one (change model-queue)
}

const emptyDraft = (): Draft => ({
  text: '',
  citations: [],
  denied: [],
  steps: [],
  queued: null,
  error: null,
  startedAt: null,
})

const DENIED_CODES = new Set(['model_denied', 'denied', 'policy_denied'])
const TOOL_STATUS: Record<ToolStep['status'], ToolCall['status']> = {
  started: 'running',
  done: 'done',
  denied: 'denied',
  error: 'failed',
}

function toToolCall(s: ToolStep, i: number): ToolCall {
  const hits = s.hits === null || s.hits === undefined ? '' : `${s.hits} found`
  return {
    id: `${s.name}-${i}`,
    tool: s.name,
    summary: s.status === 'started' ? '' : hits,
    status: TOOL_STATUS[s.status],
    reason: s.status === 'denied' || s.status === 'error' ? (s.message ?? undefined) : undefined,
    dataClass: asDataClass(s.data_class),
  }
}

// The model cites numbers [n] in the answer; show those, and if there are no references, everything found.
function toCitations(list: Citation[], text: string): ViewCitation[] {
  const used = new Set(Array.from(text.matchAll(/\[(\d+)\]/g), (m) => Number(m[1])))
  const cited = list.filter((c) => used.has(c.n))
  return (cited.length ? cited : list).map((c) => ({
    n: c.n,
    title: c.url ? c.filename : [c.filename, c.heading_path].filter(Boolean).join(' › '),
    dataClass: asDataClass(c.data_class),
    href: c.url ?? undefined,
  }))
}

// Models mark up the answer; the chat has no Markdown renderer, so bold is stripped.
const plainText = (text: string) => text.replace(/\*\*(.+?)\*\*/g, '$1')

function toMessage(m: ChatMessage): ChatMessageData {
  return {
    id: m.id,
    role: m.role === 'user' ? 'user' : 'assistant',
    text: plainText(m.content),
    citations: m.role === 'assistant' ? toCitations(m.citations, m.content) : [],
  }
}

function draftMessage(d: Draft, seconds: number): ChatMessageData {
  const failed = d.error && (DENIED_CODES.has(d.error.code) ? 'denied' : 'error')
  return {
    id: 'draft',
    role: 'assistant',
    text: plainText(d.text),
    status: failed || (d.text ? 'streaming' : 'waiting'),
    waitingSeconds: seconds,
    toolCalls: d.steps.map(toToolCall),
    citations: toCitations(d.citations, d.text),
    errorText: d.error?.message,
    note: d.denied.length
      ? `Sources outside the agent's or user's permissions: ${d.denied.join(', ')}`
      : d.queued !== null && !d.text && !d.error
        ? d.queued === 0
          ? 'Waiting for the model: every model server is busy, this request is next.'
          : `Waiting for the model: every model server is busy, ${d.queued} ahead of this request.`
        : undefined,
  }
}

function applyEvent(d: Draft, ev: ChatEvent): Draft {
  switch (ev.event) {
    case 'started':
      return { ...d, startedAt: Date.parse(ev.data.started_at) || null }
    case 'tool': {
      const step = ev.data
      // tool step: "started" opens a row, other statuses close it; text before the call is not the answer
      if (step.status === 'started') return { ...d, text: '', queued: null, steps: [...d.steps, step] }
      const idx = d.steps.findLastIndex((s) => s.name === step.name && s.status === 'started')
      const steps = idx >= 0 ? d.steps.map((s, i) => (i === idx ? step : s)) : [...d.steps, step]
      return { ...d, steps }
    }
    case 'citations':
      return { ...d, citations: ev.data.citations, denied: ev.data.denied }
    case 'queued':
      return { ...d, queued: ev.data.ahead }
    case 'token':
      return { ...d, queued: null, text: d.text + ev.data.text }
    case 'error':
      return { ...d, error: ev.data }
    default:
      return d
  }
}

// Chat: each question is an agent Execution on its own loop; tool steps are visible before the answer.
export default function Chat() {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [current, setCurrent] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState<Draft | null>(null)
  const [failure, setFailure] = useState<ChatMessageData | null>(null)
  const [input, setInput] = useState('')
  const [seconds, setSeconds] = useState(0)
  const [state, setState] = useState<ScreenState>({ kind: 'loading' })
  const [notice, setNotice] = useState<string | null>(null)
  const [agents, setAgents] = useState<ChatAgent[]>([])
  const [newAgent, setNewAgent] = useState('')
  const [models, setModels] = useState<ChatModel[]>([])
  const bottom = useRef<HTMLDivElement>(null)
  const abort = useRef<AbortController | null>(null)
  const busy = draft !== null

  const loadConversations = async () => {
    const r = await fetchConversations()
    setConversations(r.body)
    return r.body
  }
  const loadMessages = async (id: string) => {
    const list = (await fetchMessages(id)).body
    setMessages(list)
    return list
  }

  // The current conversation's agent determines the available models; without a conversation, the agent for a new one.
  const agentId = conversations.find((c) => c.id === current)?.agent_id
  const agent =
    agents.find((a) => a.id === agentId) ??
    agents.find((a) => a.name === newAgent) ??
    agents.find((a) => a.default)
  const agentName = agent?.name

  useEffect(() => {
    if (!agentName) return
    const load = async () => {
      const r = await fetchChatModels(agentName)
      setModels(r.status === 200 ? r.body : [])
    }
    void load()
  }, [agentName])

  // Only the conversation scrolls, never the page (Safari moved the whole desk away), and only while the user
  // is at its end: reading an older answer is not interrupted by the next tokens.
  const atEnd = useRef(true)
  useEffect(() => {
    const log = bottom.current?.closest<HTMLElement>('.a3-chat__scroll')
    if (!log) return
    const onScroll = () => {
      atEnd.current = log.scrollHeight - log.scrollTop - log.clientHeight < 80
    }
    log.addEventListener('scroll', onScroll)
    return () => log.removeEventListener('scroll', onScroll)
  })
  useEffect(() => {
    const log = bottom.current?.closest<HTMLElement>('.a3-chat__scroll')
    if (log && atEnd.current) log.scrollTop = log.scrollHeight
  }, [messages, draft, failure])

  const waiting = draft !== null && !draft.text && !draft.error
  const startedAt = draft?.startedAt ?? null
  useEffect(() => {
    if (!waiting) return
    const started = startedAt ?? Date.now()
    const tick = () => setSeconds(Math.max(0, Math.round((Date.now() - started) / 1000)))
    tick()
    const t = setInterval(tick, 1000)
    return () => {
      clearInterval(t)
      setSeconds(0)
    }
  }, [waiting, startedAt])

  // Leaving the screen closes only the subscription: the answer completes in core and lands in history.
  useEffect(() => () => abort.current?.abort(), [])

  // Subscribe to a conversation's answer: the draft lives while events arrive; after the end, history from core.
  async function follow(
    id: string,
    run: (onEvent: (ev: ChatEvent) => void, signal: AbortSignal) => Promise<void>,
  ) {
    abort.current?.abort()
    let latest = emptyDraft()
    setDraft(latest)
    const controller = new AbortController()
    abort.current = controller
    try {
      await run((ev) => {
        latest = applyEvent(latest, ev)
        if (abort.current === controller) setDraft(latest)
      }, controller.signal)
    } catch (err) {
      if (!controller.signal.aborted) {
        latest = { ...latest, error: { code: 'network', message: String(err) } }
      }
    }
    if (abort.current !== controller) return // another conversation is open: this answer completes off-screen
    abort.current = null
    setDraft(null)
    const saved = await loadMessages(id)
    await loadConversations()
    // core didn't record the answer: the reason stays on screen until the next question
    if (latest.error && saved.at(-1)?.role !== 'assistant') setFailure(draftMessage(latest, 0))
  }

  const subscribe = (id: string) => follow(id, (onEvent, signal) => streamAnswer(id, onEvent, signal))

  const open = async (id: string) => {
    abort.current?.abort()
    abort.current = null
    setCurrent(id)
    setDraft(null)
    setFailure(null)
    setNotice(null)
    await loadMessages(id)
    const list = await loadConversations()
    if (list.find((c) => c.id === id)?.answering) void subscribe(id)
  }

  useEffect(() => {
    const load = async () => {
      try {
        const available = await fetchChatAgents()
        if (available.status === 200) setAgents(available.body)
        const list = await loadConversations()
        if (list[0]) {
          setCurrent(list[0].id)
          await loadMessages(list[0].id)
          if (list[0].answering) void subscribe(list[0].id)
        }
        setState({ kind: 'ready' })
      } catch (e) {
        setState({ kind: 'error', message: e instanceof Error ? e.message : String(e) })
      }
    }
    void load()
  }, [])

  const newConversation = async () => {
    const r = await createConversation(newAgent || undefined)
    if (r.status !== 201 || !r.body) {
      setNotice('Could not create a conversation: the agent is not registered or is disabled.')
      return
    }
    setNotice(null)
    await loadConversations()
    await open(r.body.id)
  }

  // The agent's model is chosen in its settings, not here (change field-fixes-0-7)
  const currentModel = models.find((m) => m.default) ?? models[0]

  const send = async (text: string) => {
    let id = current
    if (!id) {
      const r = await createConversation(newAgent || undefined)
      if (r.status !== 201 || !r.body) {
        setNotice('Could not create a conversation: the agent is not registered or is disabled.')
        return
      }
      id = r.body.id
      setCurrent(id)
    }
    setInput('')
    setFailure(null)
    setMessages((m) => [
      ...m,
      {
        id: `tmp-${Date.now()}`,
        role: 'user',
        content: text,
        execution_id: null,
        citations: [],
        created_at: '',
      },
    ])
    await follow(id, async (onEvent, signal) => {
      try {
        await sendMessage(id, text, onEvent, currentModel?.name, signal)
      } catch (err) {
        if (!(err instanceof AnswerInProgress)) throw err
        setNotice('An answer is still running in this conversation: wait for it or stop it.')
        await streamAnswer(id, onEvent, signal)
      }
    })
  }

  const stop = async () => {
    if (!current) return
    // core closes the Execution and writes "Answer stopped.", the subscription ends on its own
    const r = await cancelAnswer(current)
    if (r.status !== 202) abort.current?.abort()
  }

  const log = useMemo(() => {
    const list = messages.map(toMessage)
    if (draft) list.push(draftMessage(draft, seconds))
    else if (failure) list.push(failure)
    return list
  }, [messages, draft, seconds, failure])

  const where = currentModel ? (currentModel.leaves_server ? 'External model' : 'Local model') : undefined
  return (
    <ChatScreen
      state={state.kind === 'ready' && log.length === 0 ? { kind: 'empty' } : state}
      title={`Chat · ${agent?.name ?? 'agent'}`}
      technical={false}
      agentName={agent?.name ?? ''}
      meta={[]}
      messages={log}
      draft={input}
      onDraftChange={setInput}
      onSend={(text) => void send(text)}
      onStop={() => void stop()}
      busy={busy}
      waitWhere={where}
      placeholder={agent?.description ?? 'Question'}
      logEndRef={bottom}
      aside={
        <>
          {agents.length > 1 && (
            <Field label="Agent for a new conversation">
              {({ id }) => (
                <select
                  id={id}
                  className="a3-input"
                  value={newAgent || (agents.find((a) => a.default)?.name ?? '')}
                  onChange={(e) => setNewAgent(e.target.value)}
                >
                  {agents.map((a) => (
                    <option key={a.id} value={a.name} title={a.description ?? ''}>
                      {a.name}
                    </option>
                  ))}
                </select>
              )}
            </Field>
          )}
          <Button onClick={() => void newConversation()}>New conversation</Button>
          <ul className="a3-conversations" aria-label="Conversations">
            {conversations.map((c) => (
              <li key={c.id}>
                <button type="button" aria-current={c.id === current} onClick={() => void open(c.id)}>
                  {c.title}
                </button>
              </li>
            ))}
          </ul>
        </>
      }
      controls={
        <>
          {notice && <Notice tone="error">{notice}</Notice>}
          <div className="a3-chat-fields">
            {agent && (
              <Field label="Agent">
                {({ id }) => <input id={id} className="a3-input" value={agent.name} readOnly />}
              </Field>
            )}
            {currentModel && (
              <Field label="Model">
                {({ id }) => (
                  <input
                    id={id}
                    className="a3-input"
                    readOnly
                    value={`${currentModel.leaves_server ? 'External' : 'Local'}: ${currentModel.model_id}`}
                  />
                )}
              </Field>
            )}
            {agent && (
              <p className="a3-text-xs">
                <a href={`#agents-list/${agent.name}`}>Change the model in the agent’s settings</a>
              </p>
            )}
          </div>
          {currentModel?.leaves_server && (
            <div role="note">
              <Notice tone="warn">{currentModel.leaves_server}</Notice>
            </div>
          )}
        </>
      }
    />
  )
}
