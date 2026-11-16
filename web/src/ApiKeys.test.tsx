import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { stubApi } from '../test/stub-api'
import ApiKeys from './ApiKeys'

const KEY = {
  id: 'k1',
  name: 'laptop',
  prefix: 'yorath_key_AbCdEfG',
  user_id: 'u2',
  login: 'anna',
  created_at: '2026-11-08T10:00:00Z',
  last_used_at: null,
  revoked_at: null,
  agent: null,
}
const SECRET = 'yorath_key_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd'

describe('API keys', () => {
  it('a new key is shown once together with a curl example', async () => {
    let listed: unknown[] = []
    const api = stubApi({
      'GET /api/api-keys': () => ({ status: 200, body: listed }),
      'POST /api/api-keys': () => {
        listed = [KEY]
        return { status: 201, body: { ...KEY, key: SECRET } }
      },
    })
    const { unmount } = render(<ApiKeys owner={false} />)
    await userEvent.type(await screen.findByLabelText('Key name'), 'laptop')
    await userEvent.click(screen.getByRole('button', { name: 'Create key' }))
    expect(await screen.findByDisplayValue(SECRET)).toBeInTheDocument()
    expect(screen.getByLabelText('Request example')).toHaveTextContent(`Bearer ${SECRET}`)
    expect(screen.getByLabelText('Request example')).toHaveTextContent('/v1/chat/completions')
    expect(api.bodyOf('POST /api/api-keys')).toEqual({ name: 'laptop' })

    unmount() // page reload: the value no longer arrives
    render(<ApiKeys owner={false} />)
    expect(await screen.findByText('yorath_key_AbCdEfG…')).toBeInTheDocument()
    expect(screen.queryByDisplayValue(SECRET)).not.toBeInTheDocument()
    expect(screen.queryByText('User')).not.toBeInTheDocument()
  })

  it('an agent key: access choice, curl with agent/<name>, access in the list', async () => {
    let listed: unknown[] = []
    const bot = { ...KEY, id: 'k2', name: 'bot', agent: 'sales-bot' }
    const api = stubApi({
      'GET /api/api-keys': () => ({ status: 200, body: listed }),
      'GET /api/api-keys/agents': ['sales-bot'],
      'POST /api/api-keys': () => {
        listed = [bot, KEY]
        return { status: 201, body: { ...bot, key: SECRET } }
      },
    })
    render(<ApiKeys owner={false} />)
    await userEvent.type(await screen.findByLabelText('Key name'), 'bot')
    await userEvent.selectOptions(await screen.findByLabelText('Access'), 'agent sales-bot')
    await userEvent.click(screen.getByRole('button', { name: 'Create key' }))
    expect(await screen.findByLabelText('Request example')).toHaveTextContent('"model": "agent/sales-bot"')
    expect(api.bodyOf('POST /api/api-keys')).toEqual({ name: 'bot', agent: 'sales-bot' })
    expect(screen.queryByLabelText('Codex provider')).not.toBeInTheDocument()
    const rows = screen.getAllByRole('row')
    expect(within(rows[1]).getByText('agent sales-bot')).toBeInTheDocument()
    expect(within(rows[2]).getByText('model')).toBeInTheDocument()
  })

  it('with the Yorath certificate: curl with --cacert, a root link and Codex setup', async () => {
    stubApi({
      'HEAD /yorath-root-ca.crt': () => new Response('', { status: 200 }),
      'GET /api/api-keys': () => ({ status: 200, body: [] }),
      'POST /api/api-keys': () => ({ status: 201, body: { ...KEY, key: SECRET } }),
    })
    render(<ApiKeys owner={false} />)
    expect(await screen.findByRole('link', { name: 'Download yorath-root-ca.crt' })).toHaveAttribute(
      'href',
      '/yorath-root-ca.crt',
    )
    expect(screen.getByLabelText('Model endpoint')).toHaveTextContent(`${window.location.origin}/v1`)
    expect(screen.getByLabelText('Trust the certificate')).toHaveTextContent(
      'export SSL_CERT_FILE="$PWD/yorath-root-ca.crt"',
    )
    expect(screen.getByRole('link', { name: 'Model endpoint guide' })).toHaveAttribute(
      'href',
      'https://yorath.io/docs/reference/api/',
    )
    await userEvent.type(screen.getByLabelText('Key name'), 'laptop')
    await userEvent.click(screen.getByRole('button', { name: 'Create key' }))
    expect(await screen.findByLabelText('Request example')).toHaveTextContent(
      'curl --cacert yorath-root-ca.crt',
    )
    expect(screen.getByText(/don’t share it in chats/)).toBeInTheDocument()
    expect(screen.getByLabelText('Codex provider')).toHaveTextContent('wire_api = "responses"')
    expect(screen.getByLabelText('Codex provider')).toHaveTextContent(
      `base_url = "${window.location.origin}/v1"`,
    )
    expect(screen.getByLabelText('Codex profile')).toHaveTextContent('model = "local/default"')
  })

  it('without its own CA: curl without --cacert and no link', async () => {
    stubApi({
      'HEAD /yorath-root-ca.crt': () => new Response('', { status: 404 }),
      'GET /api/api-keys': () => ({ status: 200, body: [] }),
      'POST /api/api-keys': () => ({ status: 201, body: { ...KEY, key: SECRET } }),
    })
    render(<ApiKeys owner={false} />)
    await userEvent.type(await screen.findByLabelText('Key name'), 'laptop')
    await userEvent.click(screen.getByRole('button', { name: 'Create key' }))
    expect(await screen.findByLabelText('Request example')).toHaveTextContent(`Bearer ${SECRET}`) // anchor
    expect(screen.getByLabelText('Request example')).not.toHaveTextContent('--cacert')
    expect(screen.queryByRole('link', { name: 'Download yorath-root-ca.crt' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Trust the certificate')).not.toBeInTheDocument()
    expect(screen.getByText(/2\. Key\./)).toBeInTheDocument() // anchor: steps renumber without the CA
  })

  it('copies the endpoint with its own button', async () => {
    stubApi({
      'HEAD /yorath-root-ca.crt': () => new Response('', { status: 404 }),
      'GET /api/api-keys': () => ({ status: 200, body: [] }),
    })
    const user = userEvent.setup()
    render(<ApiKeys owner={false} />)
    await user.click(await screen.findByRole('button', { name: 'Copy: Model endpoint' }))
    expect(await navigator.clipboard.readText()).toBe(`${window.location.origin}/v1`)
    expect(screen.getByRole('button', { name: 'Copy: Model endpoint' })).toHaveTextContent('Copied')
  })

  it('the owner sees members’ keys and revokes them', async () => {
    const api = stubApi({
      'GET /api/api-keys': [KEY],
      'DELETE /api/api-keys/k1': () => new Response(null, { status: 204 }),
    })
    render(<ApiKeys owner />)
    const row = (await screen.findByText('laptop')).closest('tr') as HTMLElement
    expect(within(row).getByText('anna')).toBeInTheDocument()
    await userEvent.click(within(row).getByRole('button', { name: 'Revoke: laptop' }))
    await vi.waitFor(() => expect(api.calls.some((c) => c.key === 'DELETE /api/api-keys/k1')).toBe(true))
  })
})

describe('Models of a key', () => {
  const CATALOG = [
    { name: 'local/default', context_length: 4096, external: false },
    { name: 'openrouter/default', context_length: 131072, external: true },
  ]

  it('a new key gets every model by default; ticking narrows it', async () => {
    const api = stubApi({
      'GET /api/api-keys': [],
      'GET /api/api-keys/models': CATALOG,
      'POST /api/api-keys': { status: 201, body: { ...KEY, key: SECRET } },
    })
    render(<ApiKeys owner={false} />)
    await userEvent.type(await screen.findByLabelText('Key name'), 'laptop')
    expect(screen.getByRole('radio', { name: 'All models' })).toBeChecked()
    await userEvent.click(screen.getByRole('radio', { name: 'Only these models' }))
    await userEvent.click(screen.getByRole('checkbox', { name: /local\/default/ }))
    expect(screen.getByRole('checkbox', { name: /openrouter\/default.*leaves the server/ })).not.toBeChecked()
    await userEvent.click(screen.getByRole('button', { name: 'Create key' }))
    expect(await screen.findByDisplayValue(SECRET)).toBeInTheDocument()
    expect(api.bodyOf('POST /api/api-keys')).toEqual({ name: 'laptop', models: ['local/default'] })
  })

  it('the list shows each key’s models; they change without a new key', async () => {
    let listed: { models: string[] | null }[] = [{ ...KEY, models: null }]
    const api = stubApi({
      'GET /api/api-keys': () => ({ status: 200, body: listed }),
      'GET /api/api-keys/models': CATALOG,
      'PATCH /api/api-keys/k1': (body: unknown) => {
        listed = [{ ...KEY, models: (body as { models: string[] }).models }]
        return { status: 200, body: listed[0] }
      },
    })
    render(<ApiKeys owner={false} />)
    const table = await screen.findByRole('table', { name: 'API keys' })
    expect(await within(table).findByText('all models')).toBeInTheDocument()
    await userEvent.click(within(table).getByRole('button', { name: 'Models: laptop' }))
    const editor = screen.getByRole('group', { name: 'Models of laptop' })
    await userEvent.click(within(editor).getByRole('radio', { name: 'Only these models' }))
    await userEvent.click(within(editor).getByRole('checkbox', { name: /openrouter\/default/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Save models' }))
    expect(api.bodyOf('PATCH /api/api-keys/k1')).toEqual({ models: ['openrouter/default'] })
    expect(await within(table).findByText('openrouter/default')).toBeInTheDocument()
  })
})
