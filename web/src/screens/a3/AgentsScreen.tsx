import type { ReactNode } from 'react'
import { Button } from '../../ui/Controls'
import { StatusBadge } from '../../ui/Indicators'
import { Notice } from '../../ui/Lists'
import { ScreenStateGate } from '../../ui/StateView'
import type { ScreenState, Tone } from '../../ui/types'
import { Window } from '../../ui/Window'
import { PRODUCT } from '../../brand'

/**
 * Agents ← the "Agents" window from A3 "OS style" in the A3.1 shell.
 * The list of installed agents; installing a new one is AgentInstallScreen.
 */

export type AgentStatus = 'running' | 'idle' | 'waiting' | 'pending-approval' | 'error' | 'disabled'

const AGENT_STATUS: Record<AgentStatus, { label: string; tone: Tone }> = {
  running: { label: 'Running', tone: 'ok' },
  idle: { label: 'Idle', tone: 'neutral' },
  waiting: { label: 'Waiting for an event', tone: 'warn' },
  'pending-approval': { label: 'Awaiting approval', tone: 'warn' },
  error: { label: 'Error', tone: 'error' },
  disabled: { label: 'Disabled', tone: 'neutral' },
}

export interface AgentRow {
  id: string
  /** Human name: "Document Assistant". */
  name: string
  /** Letter on the avatar. Defaults to the first letter of the name. */
  letter?: string
  /** 1–4: avatar color from the --avatar-N tokens. */
  avatarTone?: 1 | 2 | 3 | 4
  /** Line under the name: "native · delegated · control on every call". */
  description: string
  status: AgentStatus
  /** Custom status text, e.g. "Waiting for an issue". */
  statusText?: string
  /** Right-hand button label: "Open", "Approve", "Run". */
  actionLabel: string
}

export interface AgentsScreenProps {
  state: ScreenState
  agents: AgentRow[]
  onOpen: (id: string) => void
  onAction: (id: string) => void
  /** Without a handler there is no button: the MVP has no install from the interface. */
  onInstall?: () => void
  /** Button label: in the MVP agents are created, not installed from a catalog. */
  installLabel?: string
  /** Explanation on the empty screen. */
  emptyDescription?: string
  /** Second button: "Connect agent" from the catalog (change connected-agents). */
  secondaryAction?: { label: string; onClick: () => void }
  /** Banner about an agent waiting for permission approval. */
  pendingNotice?: { text: string; onReview: () => void }
  /** Below the list, inside the window: agents found outside Yorath (outside-agents-cluster). */
  footer?: ReactNode
}

export function AgentsScreen({
  state,
  agents,
  onOpen,
  onAction,
  onInstall,
  emptyDescription = `Install an agent from the ${PRODUCT} catalog or from your own md file.`,
  pendingNotice,
  installLabel = 'Install agent',
  secondaryAction,
  footer,
}: AgentsScreenProps) {
  return (
    <div className="a3-desk__pad a3-desk__pad--app">
      <Window
        variant="app"
        title="Agents"
        toolbar={
          (onInstall || secondaryAction) && (
            <>
              {secondaryAction && <Button onClick={secondaryAction.onClick}>{secondaryAction.label}</Button>}
              {onInstall && (
                <Button variant="primary" onClick={onInstall}>
                  {installLabel}
                </Button>
              )}
            </>
          )
        }
      >
        <ScreenStateGate
          state={state}
          skeletonRows={4}
          emptyTitle="No agents yet"
          emptyDescription={emptyDescription}
          emptyActionLabel={onInstall ? 'Install agent' : undefined}
          onEmptyAction={onInstall}
        >
          {pendingNotice && (
            <Notice
              tone="warn"
              actions={
                <Button size="sm" onClick={pendingNotice.onReview}>
                  Review permissions
                </Button>
              }
            >
              {pendingNotice.text}
            </Notice>
          )}
          <ul className="a3-apps" aria-label="Installed agents">
            {agents.map((a) => {
              const st = AGENT_STATUS[a.status]
              return (
                <li key={a.id} className="a3-apps__row">
                  <span className={`a3-avatar a3-avatar--${a.avatarTone ?? 1}`} aria-hidden="true">
                    {a.letter ?? a.name.charAt(0).toUpperCase()}
                  </span>
                  <span className="a3-apps__text">
                    <button type="button" className="a3-apps__name" onClick={() => onOpen(a.id)}>
                      {a.name}
                    </button>
                    <span className="a3-apps__desc">{a.description}</span>
                  </span>
                  <StatusBadge tone={st.tone} label={a.statusText ?? st.label} />
                  <Button size="sm" onClick={() => onAction(a.id)} aria-label={`${a.actionLabel}: ${a.name}`}>
                    {a.actionLabel}
                  </Button>
                </li>
              )
            })}
          </ul>
        </ScreenStateGate>
        {footer}
      </Window>
    </div>
  )
}
