import { useId, type FormEvent } from 'react'
import { Button } from './Controls'
import { DataClassBadge } from './Indicators'
import { Icon } from './Icon'
import type { DataClass } from './types'

/* ───────────────────────── Types ───────────────────────── */

export interface Citation {
  /** Reference number in the answer text: [1], [2]. */
  n: number
  /** Document title. */
  title: string
  /** Where in the document: "p. 3", "section 2.1". */
  location?: string
  dataClass?: DataClass
  /** External source: the citation becomes a link. */
  href?: string
}

export interface ToolCall {
  id: string
  /** Tool name: search_docs, query_records, ask_agent, web_search. */
  tool: string
  /** Short description of the call: "team-docs · 3 fragments". */
  summary: string
  status: 'running' | 'done' | 'denied' | 'failed'
  /** Policy denial reason (for denied). */
  reason?: string
  dataClass?: DataClass
}

export type AssistantStatus =
  /** The model hasn't produced the first token yet. On CPU this takes 25–35 seconds. */
  | 'waiting'
  /** Tokens are arriving. */
  | 'streaming'
  | 'done'
  /** Search found nothing relevant: an honest answer without calling the model. */
  | 'no-data'
  /** A policy denied the answer (e.g. data class and an external model). */
  | 'denied'
  | 'error'

export interface ChatMessageData {
  id: string
  role: 'user' | 'assistant'
  text: string
  status?: AssistantStatus
  /** Seconds waiting for the first token — the project runs the counter. */
  waitingSeconds?: number
  toolCalls?: ToolCall[]
  citations?: Citation[]
  /** Error text or denial reason. */
  errorText?: string
  /** Note under the answer, e.g. about unavailable sources. */
  note?: string
}

/* ───────────────────────── ToolCallCard ───────────────────────── */

const TOOL_STATUS: Record<ToolCall['status'], string> = {
  running: 'running',
  done: 'done',
  denied: 'denied by policy',
  failed: 'error',
}

export function ToolCallCard({ call }: { call: ToolCall }) {
  return (
    <div className={`a3-toolcall a3-toolcall--${call.status}`}>
      <Icon name="tool" size="sm" />
      <span className="a3-toolcall__tool a3-mono">{call.tool}</span>
      <span className="a3-toolcall__summary">
        {call.summary}
        {call.dataClass && (
          <>
            {' '}
            <DataClassBadge dataClass={call.dataClass} />
          </>
        )}
      </span>
      <span className="a3-toolcall__status">{TOOL_STATUS[call.status]}</span>
      {call.reason && <span className="a3-toolcall__reason">{call.reason}</span>}
    </div>
  )
}

/* ───────────────────────── CitationList ───────────────────────── */

export interface CitationListProps {
  citations: Citation[]
  /** Open a document (the project decides where it leads). */
  onOpen?: (c: Citation) => void
}

export function CitationList({ citations, onOpen }: CitationListProps) {
  return (
    <ol className="a3-citations" aria-label="Sources">
      {citations.map((c) => {
        const text = (
          <>
            <span className="a3-citations__n">[{c.n}]</span> {c.title}
            {c.location && <>, {c.location}</>}
          </>
        )
        return (
          <li key={c.n}>
            {c.href ? (
              <a className="a3-citations__item" href={c.href} target="_blank" rel="noreferrer noopener">
                {text}
              </a>
            ) : onOpen ? (
              <button type="button" className="a3-citations__item" onClick={() => onOpen(c)}>
                {text}
              </button>
            ) : (
              <span className="a3-citations__item">{text}</span>
            )}
          </li>
        )
      })}
    </ol>
  )
}

/* ───────────────────────── WaitingIndicator ───────────────────────── */

export interface WaitingIndicatorProps {
  seconds: number
  /** Expected time to first token, e.g. "25–35 seconds". Comes from the hardware profile. */
  expected?: string
  /** Where the model runs: "local model on CPU". */
  where?: string
}

export function WaitingIndicator({ seconds, expected, where = 'The model' }: WaitingIndicatorProps) {
  return (
    <div className="a3-waiting" role="status" aria-live="polite">
      <span className="a3-waiting__pulse" aria-hidden="true" />
      <span>
        {where} is preparing an answer.{expected && ` The first fragment usually arrives in ${expected}.`}
        <small className="a3-mono">Elapsed {seconds} s</small>
      </span>
    </div>
  )
}

/* ───────────────────────── ChatMessage ───────────────────────── */

export interface ChatMessageProps {
  message: ChatMessageData
  expectedWait?: string
  waitWhere?: string
  onOpenCitation?: (c: Citation) => void
}

export function ChatMessage({ message: m, expectedWait, waitWhere, onOpenCitation }: ChatMessageProps) {
  if (m.role === 'user') {
    return (
      <li className="a3-msg a3-msg--user">
        <span className="a3-visually-hidden">You: </span>
        {m.text}
      </li>
    )
  }
  const status = m.status ?? 'done'
  return (
    <li
      className={`a3-msg a3-msg--assistant a3-msg--${status}`}
      aria-busy={status === 'waiting' || status === 'streaming' || undefined}
    >
      <span className="a3-visually-hidden">Agent: </span>
      {m.toolCalls && m.toolCalls.length > 0 && (
        <div className="a3-msg__tools">
          {m.toolCalls.map((t) => (
            <ToolCallCard key={t.id} call={t} />
          ))}
        </div>
      )}
      {status === 'waiting' && (
        <WaitingIndicator seconds={m.waitingSeconds ?? 0} expected={expectedWait} where={waitWhere} />
      )}
      {status === 'no-data' && (
        <p className="a3-msg__text a3-msg__text--muted">
          {m.text ||
            'The uploaded documents don’t answer this question. The model was not called, so it couldn’t make up an answer.'}
        </p>
      )}
      {status === 'denied' && (
        <p className="a3-msg__text a3-msg__text--denied" role="alert">
          {m.errorText ?? 'The answer was blocked by a policy.'}
        </p>
      )}
      {status === 'error' && (
        <p className="a3-msg__text a3-msg__text--denied" role="alert">
          {m.errorText ?? 'The answer was interrupted. Try again.'}
        </p>
      )}
      {(status === 'streaming' || status === 'done') && (
        <p className="a3-msg__text" aria-live={status === 'streaming' ? 'polite' : undefined}>
          {m.text}
          {status === 'streaming' && <span className="a3-msg__caret" aria-hidden="true" />}
        </p>
      )}
      {m.note && <p className="a3-msg__note">{m.note}</p>}
      {m.citations && m.citations.length > 0 && status !== 'waiting' && (
        <CitationList citations={m.citations} onOpen={onOpenCitation} />
      )}
    </li>
  )
}

/* ───────────────────────── Composer ───────────────────────── */

export interface ComposerProps {
  value: string
  onChange: (value: string) => void
  onSubmit: (value: string) => void
  /** While the agent is answering, "Stop" replaces "Send". */
  busy?: boolean
  onStop?: () => void
  disabled?: boolean
  placeholder?: string
}

export function Composer({
  value,
  onChange,
  onSubmit,
  busy,
  onStop,
  disabled,
  placeholder = 'Ask a question about the agent’s documents',
}: ComposerProps) {
  const id = useId()
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!busy && value.trim()) onSubmit(value.trim())
  }
  return (
    <form className="a3-composer" onSubmit={submit}>
      <label htmlFor={id} className="a3-visually-hidden">
        Question for the agent
      </label>
      <textarea
        id={id}
        rows={1}
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) submit(e)
        }}
      />
      {busy ? (
        <Button variant="secondary" icon="stop" onClick={onStop}>
          Stop
        </Button>
      ) : (
        <Button variant="primary" type="submit" icon="send" disabled={disabled || !value.trim()}>
          Send
        </Button>
      )}
    </form>
  )
}
