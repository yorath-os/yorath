import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { stubApi } from '../test/stub-api'
import { Placements } from './Placements'

// Model servers and placements (change model-placements, decision 44): the Models screen.

const server = (node: string, name: string, model: string | null, extra: Record<string, unknown> = {}) => ({
  node,
  server: name,
  gpus: name.startsWith('gpu') ? [Number(name.slice(3))] : [],
  gpu: name.startsWith('gpu') ? 'NVIDIA A10' : null,
  vram_gb: name.startsWith('gpu') ? 23 : null,
  slots: 1,
  model,
  status: model ? 'ready' : null,
  node_status: 'active',
  ...extra,
})
const row = (name: string, extra: Record<string, unknown> = {}) => ({
  name,
  memory_gb: 5.7,
  estimated: false,
  fits: true,
  measured: 'A10, 4.11.2026',
  ...extra,
})
const GPU = {
  window: 65536,
  recommended: 'qwen3.5:9b',
  catalog: [row('qwen3.5:9b'), row('qwen3.5:4b', { memory_gb: 2.8 }), row('gemma4:e2b', { memory_gb: 4.6 })],
}
const MAIN = {
  main: true,
  window: 4096,
  recommended: 'gemma4:e2b',
  catalog: [row('gemma4:e2b', { memory_gb: 5 }), row('qwen3.5:9b', { memory_gb: 6.8, estimated: true })],
}
const model = (name: string, isDefault: boolean) => ({
  name,
  model_id: name.replace('local/', ''),
  context_length: 65536,
  classes: ['public'],
  default: isDefault,
  placements: [],
})
const ndjson = (...lines: unknown[]) =>
  new Response(lines.map((l) => JSON.stringify(l)).join('\n') + '\n', { status: 200 })

describe('Placements', () => {
  it('picks the free GPU, installs the chosen model there and makes it the default', async () => {
    let servers = [server('main', 'ollama', 'local/gemma4:e2b', MAIN), server('node-2', 'gpu0', null, GPU)]
    let models = [{ ...model('local/gemma4:e2b', true), context_length: 4096 }]
    const api = stubApi({
      'GET /api/cluster/models': () => ({ models, servers }),
      'POST /api/cluster/models/place': () => {
        servers = [servers[0], server('node-2', 'gpu0', 'local/qwen3.5:9b', GPU)]
        return ndjson(
          { status: 'pulling abc', total: 1000, completed: 500 },
          { status: 'placed', name: 'local/qwen3.5:9b', node: 'node-2', servers: ['gpu0'], default: false },
        )
      },
      'POST /api/cluster/models/default': () => {
        models = [{ ...model('local/qwen3.5:9b', true) }]
        return { name: 'local/qwen3.5:9b', model_id: 'qwen3.5:9b' }
      },
    })
    render(<Placements />)
    expect(await screen.findByLabelText('Install on')).toHaveValue('node-2/gpu0')
    const table = screen.getByRole('table', { name: 'Models for node-2/gpu0' })
    expect(within(table).getByText('qwen3.5:9b ★')).toBeInTheDocument()
    expect(screen.getByLabelText(/Make it the default model/)).toBeChecked() // 65,536 > 4,096
    await userEvent.click(within(table).getByRole('button', { name: 'Install qwen3.5:9b on node-2/gpu0' }))
    expect(
      await screen.findByText('local/qwen3.5:9b is on node-2/gpu0 and is now the default model.'),
    ).toBeInTheDocument()
    expect(api.bodyOf('POST /api/cluster/models/place')).toEqual({
      model: 'qwen3.5:9b',
      node: 'node-2',
      gpu: 0,
      replace: false,
      force: false,
    })
    expect(api.bodyOf('POST /api/cluster/models/default')).toEqual({ model: 'qwen3.5:9b' })
    expect(api.calls.some((c) => c.key === 'POST /api/setup/models/pull')).toBe(false)
    api.assertAllAnswered()
  })

  it('offers the recommended model for a GPU that has none', async () => {
    const api = stubApi({
      'GET /api/cluster/models': {
        models: [{ ...model('local/gemma4:e2b', true), context_length: 4096 }],
        servers: [server('main', 'ollama', 'local/gemma4:e2b', MAIN), server('node-2', 'gpu0', null, GPU)],
      },
      'POST /api/cluster/models/place': ndjson({
        status: 'placed',
        name: 'local/qwen3.5:9b',
        node: 'node-2',
        servers: ['gpu0'],
        default: true,
      }),
    })
    render(<Placements />)
    expect(
      await screen.findByText(
        /node-2\/gpu0 has a NVIDIA A10, 23 GB and no model: qwen3.5:9b is recommended there/,
      ),
    ).toBeInTheDocument()
    // the offer comes first; the same model also has its row in the table below
    const [offer, inTable] = screen.getAllByRole('button', { name: 'Install qwen3.5:9b on node-2/gpu0' })
    expect(
      within(screen.getByRole('table', { name: 'Models for node-2/gpu0' })).getByRole('button', {
        name: inTable.getAttribute('aria-label') ?? '',
      }),
    ).toBe(inTable)
    await userEvent.click(offer)
    expect(await screen.findByText(/is now the default model/)).toBeInTheDocument()
    expect(api.bodyOf('POST /api/cluster/models/place')).toMatchObject({
      model: 'qwen3.5:9b',
      node: 'node-2',
      gpu: 0,
    })
  })

  it('a model with another window elsewhere is moved by the core: nothing is removed first, still the default', async () => {
    const api = stubApi({
      'GET /api/cluster/models': {
        models: [{ ...model('local/qwen3.5:9b', true), context_length: 4096 }],
        servers: [server('main', 'ollama', 'local/qwen3.5:9b', MAIN), server('node-2', 'gpu0', null, GPU)],
      },
      'POST /api/cluster/models/place': (body: unknown) =>
        (body as { move?: boolean }).move
          ? ndjson({
              status: 'placed',
              name: 'local/qwen3.5:9b',
              node: 'node-2',
              servers: ['gpu0'],
              default: false,
            })
          : {
              status: 409,
              body: {
                detail: {
                  code: 'window_mismatch',
                  message: 'local/qwen3.5:9b has a 4096 context window, and node-2/gpu0 gives 65536',
                },
              },
            },
      'POST /api/cluster/models/default': { name: 'local/qwen3.5:9b', model_id: 'qwen3.5:9b' },
    })
    render(<Placements />)
    const table = await screen.findByRole('table', { name: 'Models for node-2/gpu0' })
    await userEvent.click(within(table).getByRole('button', { name: 'Install qwen3.5:9b on node-2/gpu0' }))
    expect(await screen.findByText(/has a 4096 context window/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Move qwen3.5:9b to node-2/gpu0' }))
    expect(
      await screen.findByText(/local\/qwen3.5:9b is on node-2\/gpu0 and is now the default model/),
    ).toBeInTheDocument()
    expect(api.bodyOf('POST /api/cluster/models/place')).toEqual({
      model: 'qwen3.5:9b',
      node: 'node-2',
      gpu: 0,
      replace: false,
      force: false,
      move: true,
    })
    expect(api.calls.some((c) => c.key === 'POST /api/cluster/models/remove')).toBe(false)
    expect(api.bodyOf('POST /api/cluster/models/default')).toEqual({ model: 'qwen3.5:9b' })
  })

  it('a busy server: the reason, and a replace only on request', async () => {
    const api = stubApi({
      'GET /api/cluster/models': {
        models: [model('local/qwen3.5:9b', true)],
        servers: [server('node-2', 'gpu0', 'local/qwen3.5:9b', GPU)],
      },
      'POST /api/cluster/models/place': (body: unknown) =>
        (body as { replace: boolean }).replace
          ? ndjson({
              status: 'placed',
              name: 'local/qwen3.5:4b',
              node: 'node-2',
              servers: ['gpu0'],
              default: false,
            })
          : {
              status: 409,
              body: {
                detail: {
                  code: 'server_busy',
                  message: 'node-2/gpu0 already serves local/qwen3.5:9b: one chat model per model server',
                },
              },
            },
    })
    render(<Placements />)
    const table = await screen.findByRole('table', { name: 'Models for node-2/gpu0' })
    await userEvent.click(within(table).getByRole('button', { name: 'Install qwen3.5:4b on node-2/gpu0' }))
    expect(await screen.findByText(/node-2\/gpu0 already serves local\/qwen3.5:9b/)).toBeInTheDocument()
    expect(api.bodyOf('POST /api/cluster/models/place')).toMatchObject({ replace: false })
    await userEvent.click(screen.getByRole('button', { name: 'Replace the model on node-2/gpu0' }))
    expect(await screen.findByText(/local\/qwen3.5:4b is on node-2\/gpu0/)).toBeInTheDocument()
    expect(api.bodyOf('POST /api/cluster/models/place')).toMatchObject({ replace: true })
  })

  it('takes the last placement of the default model off only after a second click', async () => {
    let forced: unknown = null
    const api = stubApi({
      'GET /api/cluster/models': {
        models: [model('local/gemma4:e2b', true)],
        servers: [server('main', 'ollama', 'local/gemma4:e2b')],
      },
      'POST /api/cluster/models/remove': (body: unknown) => {
        if ((body as { force: boolean }).force) {
          forced = body
          return { status: 200, body: { removed: ['main/ollama'] } }
        }
        return {
          status: 409,
          body: { detail: { code: 'default_would_be_unavailable', message: 'last placement' } },
        }
      },
    })
    render(<Placements />)
    await userEvent.click(
      await screen.findByRole('button', { name: 'Take local/gemma4:e2b off main/ollama' }),
    )
    // the first request was made and refused: nothing is removed yet
    expect(forced).toBeNull()
    await userEvent.click(
      await screen.findByRole('button', { name: 'Take off main/ollama: local/default will stop answering' }),
    )
    expect(await screen.findByText('local/gemma4:e2b is taken off main/ollama.')).toBeInTheDocument()
    expect(forced).toEqual({ model: 'local/gemma4:e2b', node: 'main', gpu: null, force: true })
    api.assertAllAnswered()
  })

  it('moves the default pointer to another ready model', async () => {
    const api = stubApi({
      'GET /api/cluster/models': {
        models: [model('local/qwen3.5:9b', true), model('local/qwen3.5:4b', false)],
        servers: [server('node-2', 'gpu0', 'local/qwen3.5:9b'), server('node-2', 'gpu1', 'local/qwen3.5:4b')],
      },
      'POST /api/cluster/models/default': { name: 'local/qwen3.5:4b', model_id: 'qwen3.5:4b' },
    })
    render(<Placements />)
    // the default model has no such button, the other one has
    await userEvent.click(await screen.findByRole('button', { name: 'Make default: local/qwen3.5:4b' }))
    expect(screen.queryByRole('button', { name: 'Make default: local/qwen3.5:9b' })).not.toBeInTheDocument()
    expect(await screen.findByText('local/default now points at local/qwen3.5:4b.')).toBeInTheDocument()
    expect(api.bodyOf('POST /api/cluster/models/default')).toEqual({ model: 'local/qwen3.5:4b' })
    api.assertAllAnswered()
  })

  it('shows how many requests wait for a model and which servers are answering', async () => {
    const api = stubApi({
      'GET /api/cluster/models': {
        models: [{ ...model('local/qwen3.5:9b', true), queued: 3 }],
        servers: [
          server('node-2', 'gpu0', 'local/qwen3.5:9b', { in_flight: 1 }),
          server('node-2', 'gpu1', 'local/qwen3.5:9b', { in_flight: 0 }),
        ],
      },
    })
    render(<Placements />)
    const table = await screen.findByRole('table', { name: 'Model servers' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(within(rows[0]).getByText('answering')).toBeInTheDocument()
    expect(within(rows[1]).getByText('ready')).toBeInTheDocument()
    expect(screen.getByText(/Waiting for a free model server: local\/qwen3.5:9b — 3\./)).toBeInTheDocument()
    api.assertAllAnswered()
  })

  it('says nothing about waiting when no request waits', async () => {
    stubApi({
      'GET /api/cluster/models': {
        models: [{ ...model('local/qwen3.5:9b', true), queued: 0 }],
        servers: [server('node-2', 'gpu0', 'local/qwen3.5:9b', { in_flight: 0 })],
      },
    })
    render(<Placements />)
    // anchor: the screen is drawn, and the notice is not on it
    await screen.findByRole('table', { name: 'Model servers' })
    expect(screen.queryByText(/Waiting for a free model server/)).not.toBeInTheDocument()
  })
})
