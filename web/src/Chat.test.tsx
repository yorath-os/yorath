import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { sseStream, stubApi } from '../test/stub-api'
import Chat from './Chat'

const AGENTS = [
  { id: 'a1', name: 'document-assistant', description: 'Answers from documents', default: true },
  { id: 'a2', name: 'research-agent', description: 'Web search', default: false },
]
const CONV = { id: 'c1', agent_id: 'a1', title: 'Conversation', created_at: '', updated_at: '' }
const LOCAL = {
  name: 'local/default',
  model_id: 'qwen3.5:4b',
  provider: 'ollama',
  data_classes: ['public', 'internal', 'confidential', 'restricted'],
  default: true,
  input_price_per_mtok: null,
  output_price_per_mtok: null,
  leaves_server: null,
}
const EXTERNAL = {
  ...LOCAL,
  name: 'openrouter/default',
  model_id: 'openai/x',
  provider: 'openrouter',
  data_classes: ['public', 'internal'],
  default: false,
  leaves_server: 'The request text and fragments leave the server',
}
const CITATION = {
  n: 1,
  document_id: 'd',
  filename: 'hr-policy.md',
  heading_path: 'Accrual',
  asset: 'team-docs',
  data_class: 'internal',
}

function world(stream: ReturnType<typeof sseStream>, messages: unknown[] = []) {
  return stubApi({
    'GET /api/chat/agents': AGENTS,
    'GET /api/chat/conversations': [CONV],
    'GET /api/chat/conversations/c1/messages': messages,
    'GET /api/chat/models?agent=document-assistant': [LOCAL, EXTERNAL],
    'POST /api/chat/conversations/c1/messages': () => stream.response(),
  })
}

async function ask(text: string) {
  const user = userEvent.setup()
  await user.type(await screen.findByPlaceholderText(/Answers from documents|Question/), text)
  await user.click(screen.getByRole('button', { name: 'Send' }))
}

describe('Chat', () => {
  it('shows the tool step before the answer, then the answer with a citation', async () => {
    const stream = sseStream()
    const api = world(stream)
    render(<Chat />)
    await screen.findByRole('combobox', { name: 'Model' })
    await ask('how many days?')

    await waitFor(() => expect(api.bodyOf('POST /api/chat/conversations/c1/messages')).toBeDefined())
    stream.send('tool', { name: 'search_docs', status: 'started' })
    expect(await screen.findByText('running')).toBeInTheDocument()
    stream.send('tool', { name: 'search_docs', status: 'done', hits: 5, data_class: 'internal' })
    expect(await screen.findByText(/5 found/)).toBeInTheDocument()
    expect(screen.getByText('Internal')).toBeInTheDocument()
    expect(screen.queryByText('running')).not.toBeInTheDocument()
    stream.send('citations', { citations: [CITATION], denied: ['hr-salaries'] })
    stream.send('token', { text: 'No more than 56 days [1].' })
    expect(await screen.findByText('No more than 56 days [1].')).toBeInTheDocument()
    expect(screen.getByText(/hr-policy\.md › Accrual/)).toBeInTheDocument()
    expect(screen.getByText(/outside the agent's or user's permissions: hr-salaries/)).toBeInTheDocument()
    stream.close()
    api.assertAllAnswered()
  })

  it('shows a policy denial with the reason', async () => {
    const stream = sseStream()
    const api = world(stream)
    render(<Chat />)
    await screen.findByRole('combobox', { name: 'Model' })
    await ask('search the web')

    await waitFor(() => expect(api.bodyOf('POST /api/chat/conversations/c1/messages')).toBeDefined())
    stream.send('tool', { name: 'ask_agent', status: 'started' })
    stream.send('tool', { name: 'ask_agent', status: 'denied', message: 'class confidential' })
    expect(await screen.findByText('denied by policy')).toBeInTheDocument()
    expect(screen.getByText('class confidential')).toBeInTheDocument()
    stream.send('error', { code: 'model_denied', message: 'blocked: no permitting policy' })
    expect(await screen.findByRole('alert')).toHaveTextContent('no permitting policy')
    stream.close()
  })

  it('passes the chosen model and warns that data leaves the server', async () => {
    const stream = sseStream()
    const api = world(stream)
    render(<Chat />)
    const select = await screen.findByRole('combobox', { name: 'Model' })
    expect(screen.queryByRole('note')).not.toBeInTheDocument()

    await userEvent.selectOptions(select, 'openrouter/default')
    expect(screen.getByRole('note')).toHaveTextContent('The request text and fragments leave the server')
    expect(localStorage.getItem('yorath.chat.model')).toBe('openrouter/default')

    await ask('question')
    await waitFor(() =>
      expect(api.bodyOf('POST /api/chat/conversations/c1/messages')).toEqual({
        content: 'question',
        model: 'openrouter/default',
      }),
    )
    stream.close()
  })

  it('waiting shows the model and seconds but promises no deadline', async () => {
    const stream = sseStream()
    world(stream)
    render(<Chat />)
    await screen.findByRole('combobox', { name: 'Model' })
    await ask('question')
    const status = await screen.findByRole('status')
    expect(status).toHaveTextContent('Local model is preparing an answer.')
    expect(status).toHaveTextContent(/Elapsed \d+ s/)
    expect(status).not.toHaveTextContent('The first fragment')
    stream.close()
  })

  it('says that the answer waits for a free model server, and drops the line when the answer starts', async () => {
    const stream = sseStream()
    world(stream)
    render(<Chat />)
    await screen.findByRole('combobox', { name: 'Model' })
    await ask('question')
    stream.send('queued', { ahead: 2 })
    expect(
      await screen.findByText('Waiting for the model: every model server is busy, 2 ahead of this request.'),
    ).toBeInTheDocument()
    stream.send('queued', { ahead: 0 })
    expect(
      await screen.findByText('Waiting for the model: every model server is busy, this request is next.'),
    ).toBeInTheDocument()
    stream.send('token', { text: 'Answer.' })
    expect(await screen.findByText('Answer.')).toBeInTheDocument()
    expect(screen.queryByText(/Waiting for the model/)).not.toBeInTheDocument()
    stream.close()
  })

  it('"Stop" asks core to stop the answer; the input returns with the message from history', async () => {
    const stream = sseStream()
    let stopped = false
    const api = stubApi({
      'GET /api/chat/agents': AGENTS,
      'GET /api/chat/conversations': [CONV],
      'GET /api/chat/conversations/c1/messages': () =>
        stopped
          ? [
              {
                id: 'm1',
                role: 'user',
                content: 'question',
                execution_id: 'e1',
                citations: [],
                created_at: '',
              },
              {
                id: 'm2',
                role: 'assistant',
                content: 'Answer stopped.',
                execution_id: 'e1',
                citations: [],
                created_at: '',
              },
            ]
          : [],
      'GET /api/chat/models?agent=document-assistant': [LOCAL],
      'POST /api/chat/conversations/c1/messages': () => stream.response(),
      'POST /api/chat/conversations/c1/cancel': () => {
        stopped = true
        stream.send('done', { execution_id: 'e1', status: 'cancelled', text: 'Answer stopped.' })
        stream.close()
        return { status: 202, body: { status: 'cancelled' } }
      },
    })
    render(<Chat />)
    await ask('question')
    await userEvent.click(await screen.findByRole('button', { name: 'Stop' }))
    expect(api.calls.some((c) => c.key === 'POST /api/chat/conversations/c1/cancel')).toBe(true)
    expect(await screen.findByText('Answer stopped.')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Send' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    api.assertAllAnswered()
  })

  it('a reopened conversation with an answer in progress subscribes and shows its progress', async () => {
    const stream = sseStream()
    const started = new Date(Date.now() - 42_000).toISOString()
    const api = stubApi({
      'GET /api/chat/agents': AGENTS,
      'GET /api/chat/conversations': [{ ...CONV, answering: true }],
      'GET /api/chat/conversations/c1/messages': [
        {
          id: 'm1',
          role: 'user',
          content: 'how many days?',
          execution_id: 'e1',
          citations: [],
          created_at: '',
        },
      ],
      'GET /api/chat/models?agent=document-assistant': [LOCAL],
      'GET /api/chat/conversations/c1/stream': () => stream.response(),
    })
    render(<Chat />)
    expect(await screen.findByText('how many days?')).toBeInTheDocument()
    await waitFor(() =>
      expect(api.calls.some((c) => c.key === 'GET /api/chat/conversations/c1/stream')).toBe(true),
    )
    stream.send('started', { execution_id: 'e1', started_at: started })
    stream.send('tool', { name: 'search_docs', status: 'started' })
    expect(await screen.findByText('running')).toBeInTheDocument()
    // the counter runs from the answer start in core, not from opening the screen
    expect(await screen.findByText(/Elapsed (4[2-9]|5\d) s/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument()
    stream.close()
    api.assertAllAnswered()
  })

  it('switching to another conversation closes the subscription and doesn’t stop the answer', async () => {
    let signal: AbortSignal | undefined
    const api = stubApi({
      'GET /api/chat/agents': AGENTS,
      'GET /api/chat/conversations': [CONV, { ...CONV, id: 'c2', title: 'Second' }],
      'GET /api/chat/conversations/c1/messages': [],
      'GET /api/chat/conversations/c2/messages': [],
      'GET /api/chat/models?agent=document-assistant': [LOCAL],
    })
    const stubbed = globalThis.fetch
    vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
      if (init?.method !== 'POST') return stubbed(url, init)
      signal = init.signal ?? undefined
      return new Promise((_, reject) => {
        signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
      })
    })
    render(<Chat />)
    await ask('question')
    expect(await screen.findByRole('button', { name: 'Stop' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Second' }))
    await waitFor(() => expect(signal?.aborted).toBe(true))
    expect(api.calls.some((c) => c.key.endsWith('/cancel'))).toBe(false)
    expect(await screen.findByRole('button', { name: 'Send' })).toBeInTheDocument()
    api.assertAllAnswered()
  })

  it('the error stays after the stream ends if the answer isn’t in history', async () => {
    const stream = sseStream()
    const api = world(stream)
    render(<Chat />)
    await screen.findByRole('combobox', { name: 'Model' })
    await ask('question')
    await waitFor(() => expect(api.bodyOf('POST /api/chat/conversations/c1/messages')).toBeDefined())
    stream.send('error', { code: 'agent_disabled', message: 'agent disabled by the owner' })
    stream.close()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Send' })).toBeInTheDocument())
    expect(await screen.findByRole('alert')).toHaveTextContent('agent disabled by the owner')
  })

  it('bold markup isn’t shown as asterisks', async () => {
    const message = {
      id: 'm1',
      role: 'assistant',
      content: 'At most **56 days**.',
      execution_id: 'e1',
      created_at: '',
      citations: [],
    }
    world(sseStream(), [message])
    render(<Chat />)
    expect(await screen.findByText('At most 56 days.')).toBeInTheDocument()
  })

  it('a web citation is a link', async () => {
    const stream = sseStream()
    const message = {
      id: 'm1',
      role: 'assistant',
      content: 'Vacation is 28 days [1].',
      execution_id: 'e1',
      created_at: '',
      citations: [
        { ...CITATION, filename: 'Labor Department', asset: 'web', url: 'https://www.dol.gov/vacation' },
      ],
    }
    world(stream, [message])
    render(<Chat />)
    const link = await screen.findByRole('link', { name: /Labor Department/ })
    expect(link).toHaveAttribute('href', 'https://www.dol.gov/vacation')
    expect(link).toHaveAttribute('rel', 'noreferrer noopener')
  })

  it('shows the open conversation’s agent above the chat, not the one chosen for a new one', async () => {
    world(sseStream())
    render(<Chat />)
    const field = await screen.findByLabelText('Agent')
    await screen.findByRole('button', { name: 'Conversation' })
    await waitFor(() => expect(field).toHaveValue('document-assistant'))
    expect(field).toHaveAttribute('readonly')

    await userEvent.selectOptions(screen.getByLabelText('Agent for a new conversation'), 'research-agent')
    expect(field).toHaveValue('document-assistant')
  })
})
