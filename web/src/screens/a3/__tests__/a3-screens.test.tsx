import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { AgentInstallScreen } from '../AgentInstallScreen'
import { AgentsScreen } from '../AgentsScreen'
import { ChatScreen, type ChatScreenProps } from '../ChatScreen'
import { JournalScreen, type JournalScreenProps } from '../JournalScreen'
import { StatusScreen } from '../StatusScreen'
import * as f from '../fixtures'
import type { ScreenState } from '../../../ui/types'

const ready: ScreenState = { kind: 'ready' }

describe('StatusScreen', () => {
  const base = {
    mode: 'view' as const,
    profileName: 'Agent on-call',
    layout: f.statusLayout,
    onConfigure: vi.fn(),
    onChooseProfile: vi.fn(),
  }

  it('renders widgets from the layout with data', () => {
    render(<StatusScreen {...base} state={ready} data={f.statusData} />)
    expect(screen.getByRole('heading', { name: /Desk/ })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Running tasks' })).toBeInTheDocument()
    expect(
      screen.getByRole('progressbar', { name: 'Progress of task "Contract batch review"' }),
    ).toHaveAttribute('aria-valuenow', '55')
    expect(screen.getByRole('meter', { name: 'GPU' })).toHaveAttribute('aria-valuenow', '78')
    expect(screen.getByText('Data class above the agent’s clearance')).toBeInTheDocument()
  })

  it('loading, empty, error with retry, access denied', async () => {
    const retry = vi.fn()
    const { rerender } = render(<StatusScreen {...base} state={{ kind: 'loading' }} />)
    expect(screen.getByRole('status')).toHaveAttribute('aria-busy', 'true')

    rerender(<StatusScreen {...base} state={{ kind: 'empty' }} />)
    expect(screen.getByText('No widgets on the desk')).toBeInTheDocument()

    rerender(
      <StatusScreen {...base} state={{ kind: 'error', message: 'Core is not responding', onRetry: retry }} />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Core is not responding')
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(retry).toHaveBeenCalled()

    rerender(
      <StatusScreen
        {...base}
        state={{ kind: 'forbidden', reason: 'The desk is available to administrators.' }}
      />,
    )
    expect(screen.getByText('No access')).toBeInTheDocument()
  })

  it('empty widget data is shown inside the widget', () => {
    render(<StatusScreen {...base} state={ready} data={{ ...f.statusData, tasks: [] }} />)
    expect(screen.getByText('No agent is running right now.')).toBeInTheDocument()
  })

  it('customize mode: arrows on the handle move the widget, the cross removes it', async () => {
    const onMove = vi.fn()
    const onRemove = vi.fn()
    render(
      <StatusScreen
        {...base}
        mode="edit"
        state={ready}
        edit={{
          profiles: [{ id: 'duty', name: 'Agent on-call', description: 'Tasks, errors' }],
          profileId: 'duty',
          onProfileChange: vi.fn(),
          catalog: [
            {
              id: 'log',
              name: 'Error log',
              description: 'Latest errors',
              placed: false,
              category: 'Errors',
            },
          ],
          categories: ['All', 'Errors'],
          category: 'All',
          onCategoryChange: vi.fn(),
          query: '',
          onQueryChange: vi.fn(),
          onAdd: vi.fn(),
          onMove,
          onResize: vi.fn(),
          onRemove,
          onSave: vi.fn(),
          onCancel: vi.fn(),
        }}
      />,
    )
    const grip = screen.getByRole('button', { name: /Move widget "Running"/ })
    grip.focus()
    await userEvent.keyboard('{ArrowRight}')
    expect(onMove).toHaveBeenCalledWith('w1', 1)
    await userEvent.click(screen.getByRole('button', { name: 'Remove widget "Queued"' }))
    expect(onRemove).toHaveBeenCalledWith('w2')
    expect(screen.getByRole('button', { name: 'Add widget "Error log"' })).toBeEnabled()
  })
})

describe('JournalScreen', () => {
  const props = (over: Partial<JournalScreenProps> = {}): JournalScreenProps => ({
    state: ready,
    counts: { all: 24, running: 12, queued: 5, waiting: 2, failed: 3 },
    filter: 'running',
    onFilterChange: vi.fn(),
    agentFilter: 'all',
    nodeFilter: 'all',
    triggerFilter: 'any',
    onOpenFilter: vi.fn(),
    tasks: f.tasks,
    selectedId: 'x1',
    onSelect: vi.fn(),
    detail: f.taskDetail,
    onStop: vi.fn(),
    onOpenTrace: vi.fn(),
    onCheckAgent: vi.fn(),
    ...over,
  })

  it('shows filters, the list and task details', async () => {
    const p = props()
    render(<JournalScreen {...p} />)
    expect(screen.getByRole('button', { name: 'Running · 12' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText(/Running in the background on gpu-01/)).toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Task steps' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('row', { name: /October invoice review/ }))
    expect(p.onSelect).toHaveBeenCalledWith('x3')
    await userEvent.click(screen.getByRole('button', { name: 'Stop' }))
    expect(p.onStop).toHaveBeenCalledWith('x1')
  })

  it('no selected task shows a hint; an empty filter shows the empty state', () => {
    const { rerender } = render(<JournalScreen {...props({ selectedId: undefined, detail: undefined })} />)
    expect(screen.getByText('Select a task')).toBeInTheDocument()
    rerender(<JournalScreen {...props({ state: { kind: 'empty' } })} />)
    expect(screen.getByText('No tasks match these filters')).toBeInTheDocument()
  })

  it('long names don’t break the table: the full agent name is in title', () => {
    const long = 'very-long-agent-name-that-reviews-supply-and-lease-contracts-every-day'
    render(<JournalScreen {...props({ tasks: [{ ...f.tasks[0], agent: long }] })} />)
    expect(screen.getByTitle(long)).toBeInTheDocument()
  })
})

describe('AgentsScreen', () => {
  it('agent list and actions', async () => {
    const onAction = vi.fn()
    render(
      <AgentsScreen
        state={ready}
        agents={f.agents}
        onOpen={vi.fn()}
        onAction={onAction}
        onInstall={vi.fn()}
      />,
    )
    const list = screen.getByRole('list', { name: 'Installed agents' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(3)
    expect(screen.getByText('Waiting for an issue')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Run: Research Agent' }))
    expect(onAction).toHaveBeenCalledWith('a3')
  })

  it('empty — offers to install an agent', async () => {
    const onInstall = vi.fn()
    render(
      <AgentsScreen
        state={{ kind: 'empty' }}
        agents={[]}
        onOpen={vi.fn()}
        onAction={vi.fn()}
        onInstall={onInstall}
      />,
    )
    expect(screen.getByText('No agents yet')).toBeInTheDocument()
    await userEvent.click(screen.getAllByRole('button', { name: 'Install agent' })[1])
    expect(onInstall).toHaveBeenCalled()
  })
})

describe('AgentInstallScreen', () => {
  const nav = { onClose: vi.fn(), onBack: vi.fn(), onNext: vi.fn() }

  it('step 2: the current step is marked, a warning in the footer', () => {
    render(
      <AgentInstallScreen state={ready} step={2} agentName="contract-review" check={f.checkStep} {...nav} />,
    )
    expect(screen.getByText('Check', { selector: '.a3-stepper__label' }).closest('li')).toHaveAttribute(
      'aria-current',
      'step',
    )
    expect(screen.getByText('Step 2 of 5 · warnings: 1')).toBeInTheDocument()
    expect(screen.getByLabelText('md file header')).toHaveTextContent('runtime: native')
  })

  it('step 3: the permission checkbox is linked to its text', async () => {
    const onToggle = vi.fn()
    render(
      <AgentInstallScreen
        state={ready}
        step={3}
        agentName="contract-review"
        permissions={f.permissionsStep(onToggle)}
        {...nav}
      />,
    )
    expect(
      screen.getByRole('heading', { name: 'What contract-review will be able to do' }),
    ).toBeInTheDocument()
    await userEvent.click(screen.getByRole('checkbox', { name: 'Allow' }))
    expect(onToggle).toHaveBeenCalledWith('data', false)
    expect(screen.getByText('By policy')).toBeInTheDocument()
  })

  it('step 5: the green "Enable agent" button and test rerun', async () => {
    const onRetest = vi.fn()
    render(
      <AgentInstallScreen
        state={ready}
        step={5}
        test={f.testStep({
          onOpenReport: vi.fn(),
          onOpenTrace: vi.fn(),
          onOpenTechWindow: vi.fn(),
          onRetest,
        })}
        {...nav}
      />,
    )
    expect(screen.getByText('passed')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Rerun test' }))
    expect(onRetest).toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Enable agent' }))
    expect(nav.onNext).toHaveBeenCalled()
  })

  it('a step error disables "Next"', () => {
    render(
      <AgentInstallScreen
        state={{ kind: 'error', message: 'Could not parse the md file header: no name field.' }}
        step={2}
        {...nav}
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('no name field')
    expect(screen.getByRole('button', { name: 'Next: permissions' })).toBeDisabled()
  })
})

describe('ChatScreen', () => {
  const base = (over: Partial<ChatScreenProps> = {}): ChatScreenProps => ({
    state: ready,
    agentName: 'document-assistant',
    meta: ['native · delegated', 'control: tool', 'model: local/default'],
    messages: f.chatMessages,
    draft: '',
    onDraftChange: vi.fn(),
    onSend: vi.fn(),
    onStop: vi.fn(),
    busy: false,
    ...over,
  })

  it('citations, a tool call and a policy denial', () => {
    render(<ChatScreen {...base()} />)
    expect(screen.getByRole('list', { name: 'Sources' })).toHaveTextContent('Vacation policy.pdf, p. 3')
    expect(screen.getByText('search_docs')).toBeInTheDocument()
    expect(screen.getByText('denied by policy')).toBeInTheDocument()
    expect(screen.getByText(/already contained Confidential data/)).toBeInTheDocument()
  })

  it('waiting for the first token on CPU shows elapsed time', () => {
    render(
      <ChatScreen
        {...base({
          busy: true,
          expectedWait: '25–35 seconds',
          messages: [
            f.chatMessages[0],
            { id: 'w', role: 'assistant', text: '', status: 'waiting', waitingSeconds: 18 },
          ],
        })}
      />,
    )
    const st = screen.getByText(/The first fragment usually arrives in 25–35 seconds/)
    expect(st.closest('[role="status"]')).toBeInTheDocument()
    expect(screen.getByText('Elapsed 18 s')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument()
  })

  it('a streaming answer is announced politely and marked busy', () => {
    render(
      <ChatScreen
        {...base({
          busy: true,
          messages: [{ id: 's', role: 'assistant', text: 'Annual vacation is', status: 'streaming' }],
        })}
      />,
    )
    const p = screen.getByText('Annual vacation is')
    expect(p).toHaveAttribute('aria-live', 'polite')
    expect(p.closest('li')).toHaveAttribute('aria-busy', 'true')
  })

  it('"no data" — an honest answer without calling the model', () => {
    render(
      <ChatScreen {...base({ messages: [{ id: 'n', role: 'assistant', text: '', status: 'no-data' }] })} />,
    )
    expect(screen.getByText(/The uploaded documents don’t answer this question/)).toBeInTheDocument()
  })

  it('sends on button click; with an empty field the button is disabled', async () => {
    const onSend = vi.fn()
    const { rerender } = render(<ChatScreen {...base({ onSend })} />)
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    rerender(<ChatScreen {...base({ onSend, draft: 'How do I request vacation?' })} />)
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(onSend).toHaveBeenCalledWith('How do I request vacation?')
    expect(screen.getByLabelText('Question for the agent')).toBeInTheDocument()
  })
})
