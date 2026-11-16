import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { stubApi } from '../test/stub-api'
import Models from './Models'

const STATUS = {
  setup_completed: true,
  models: { chat: 'gemma4:e2b', embeddings: 'embeddinggemma-2:570m', external: null },
  resources: [
    {
      name: 'local/default',
      model_id: 'gemma4:e2b',
      provider: 'ollama',
      classes: ['public'],
      context_length: 32768,
      input_price_per_mtok: null,
      output_price_per_mtok: null,
    },
  ],
  openrouter_configured: false,
  web_search_configured: false,
  secrets_available: true,
}
describe('Models', () => {
  it('lists registered models and installs only through the server picker, never straight to the main node', async () => {
    const api = stubApi({
      'GET /api/setup/status': STATUS,
      'GET /api/cluster/models': {
        models: [
          {
            name: 'local/gemma4:e2b',
            model_id: 'gemma4:e2b',
            context_length: 4096,
            classes: ['public'],
            default: true,
            placements: [],
          },
        ],
        servers: [
          {
            node: 'main',
            server: 'ollama',
            gpus: [],
            gpu: null,
            vram_gb: null,
            slots: 1,
            model: 'local/gemma4:e2b',
            status: 'ready',
            node_status: 'active',
            main: true,
            window: 4096,
            recommended: 'gemma4:e2b',
            catalog: [{ name: 'gemma4:e2b', memory_gb: 5, estimated: false, fits: true, measured: 'x' }],
          },
        ],
      },
    })
    render(<Models onOpenWizard={() => {}} />)
    expect(await screen.findByRole('table', { name: 'Registered models' })).toBeInTheDocument()
    expect(await screen.findByLabelText('Install on')).toHaveValue('main/ollama')
    expect(screen.getByText(/Answers agents:/)).toHaveTextContent('local/gemma4:e2b, context window 4,096')
    expect(screen.queryByRole('button', { name: 'Pull the recommended one' })).not.toBeInTheDocument()
    expect(api.calls.some((c) => c.key.startsWith('GET /api/setup/models'))).toBe(false)
  })
})
