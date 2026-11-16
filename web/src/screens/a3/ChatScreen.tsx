import type { ReactNode, Ref } from 'react'
import { ChatMessage, Composer, type ChatMessageData, type Citation } from '../../ui/Chat'
import { Notice } from '../../ui/Lists'
import { ScreenStateGate } from '../../ui/StateView'
import type { ScreenState } from '../../ui/types'
import { Window } from '../../ui/Window'

/**
 * Chat ← "Agent technical window" (linked from A3.3 and install step 5).
 * There's no separate mockup on the canvas: the window is assembled from A3 components (app window,
 * banners, buttons) and the chat from the OS-3 variant. This is a window for the administrator to check
 * an agent, not a working channel for users.
 */

export interface ChatScreenProps {
  state: ScreenState
  agentName: string
  /** Window title. Defaults to "Agent check · name". */
  title?: string
  /** Administrator's agent check window: a banner says so. */
  technical?: boolean
  /** Left column: conversations, agent choice. */
  aside?: ReactNode
  /** Settings above the thread: model choice. */
  controls?: ReactNode
  /** Input field placeholder. */
  placeholder?: string
  /** End of the thread, to scroll to a new message. */
  logEndRef?: Ref<HTMLDivElement>
  /** Properties line: "native · delegated", "control: tool", "model: local/default". */
  meta: string[]
  messages: ChatMessageData[]
  draft: string
  onDraftChange: (v: string) => void
  onSend: (text: string) => void
  onStop: () => void
  /** Waiting or an answer stream is in progress. */
  busy: boolean
  /** Expected time to the first token from the hardware profile, e.g. "25–35 seconds". */
  expectedWait?: string
  /** "Local model on CPU", "Local model on GPU". */
  waitWhere?: string
  onOpenCitation?: (c: Citation) => void
  onClose?: () => void
}

export function ChatScreen(p: ChatScreenProps) {
  return (
    <div className="a3-desk__pad a3-desk__pad--app">
      <Window variant="app" title={p.title ?? `Agent check · ${p.agentName}`} onClose={p.onClose} flush>
        <div className="a3-chat-layout">
          {p.aside && <aside className="a3-chat-layout__aside">{p.aside}</aside>}
          <div className="a3-chat">
            <p className="a3-chat__meta">
              {p.meta.map((m) => (
                <span key={m}>{m}</span>
              ))}
            </p>
            {p.technical !== false && (
              <Notice tone="info">
                This is a technical window for checking the agent. Users work with the agent through their own
                channels.
              </Notice>
            )}
            {p.controls}
            <div className="a3-chat__scroll">
              <ScreenStateGate
                state={p.state}
                skeletonRows={4}
                emptyTitle="Ask the first question"
                emptyDescription="The agent will answer from its documents and show which tools it called and where the answer came from."
              >
                <ol className="a3-chat__log" aria-label="Conversation with the agent">
                  {p.messages.map((m) => (
                    <ChatMessage
                      key={m.id}
                      message={m}
                      expectedWait={p.expectedWait}
                      waitWhere={p.waitWhere}
                      onOpenCitation={p.onOpenCitation}
                    />
                  ))}
                </ol>
                <div ref={p.logEndRef} />
              </ScreenStateGate>
            </div>
            <Composer
              value={p.draft}
              onChange={p.onDraftChange}
              onSubmit={p.onSend}
              busy={p.busy}
              onStop={p.onStop}
              disabled={p.state.kind === 'forbidden' || p.state.kind === 'loading'}
              placeholder={p.placeholder}
            />
          </div>
        </div>
      </Window>
    </div>
  )
}
