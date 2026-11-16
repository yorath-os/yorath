import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { stubApi } from '../test/stub-api'
import Models from './Models'
import Policies from './Policies'
import Users from './Users'

const ME = {
  id: 'u1',
  login: 'alex',
  display_name: 'Alex',
  role: 'owner',
  department: null,
  is_active: true,
}
const ANNA = {
  ...ME,
  id: 'u2',
  login: 'anna',
  display_name: 'Anna',
  role: 'member',
  department: 'Engineering',
}

describe('Users', () => {
  const fill = async () => {
    await userEvent.type(await screen.findByLabelText('Name'), 'Peter')
    await userEvent.type(screen.getByLabelText('Login'), 'peter')
    await userEvent.type(screen.getByLabelText('Password'), 'password-1')
  }

  it('creates a user linked to a table row', async () => {
    const api = stubApi({ 'GET /api/users': [ME], 'POST /api/users': { status: 201, body: ANNA } })
    render(<Users currentUserId="u1" />)
    await fill()
    await userEvent.type(screen.getByLabelText('employee_id'), 'E001')
    await userEvent.click(screen.getByRole('button', { name: 'Create user' }))
    await vi.waitFor(() =>
      expect(api.bodyOf('POST /api/users')).toEqual({
        login: 'peter',
        password: 'password-1',
        display_name: 'Peter',
        role: 'member',
        department: null,
        attributes: { employee_id: 'E001' },
      }),
    )
  })

  it('a taken login is explained on screen', async () => {
    stubApi({ 'GET /api/users': [ME], 'POST /api/users': { status: 409, body: { detail: 'login_taken' } } })
    render(<Users currentUserId="u1" />)
    await fill()
    await userEvent.click(screen.getByRole('button', { name: 'Create user' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('login is already taken')
  })

  it('disables another user but not yourself', async () => {
    const api = stubApi({ 'GET /api/users': [ME, ANNA], 'PATCH /api/users/u2': { is_active: false } })
    render(<Users currentUserId="u1" />)
    await userEvent.click(await screen.findByRole('button', { name: 'Disable: Anna' }))
    expect(api.bodyOf('PATCH /api/users/u2')).toEqual({ is_active: false })
    expect(screen.queryByRole('button', { name: 'Disable: Alex' })).not.toBeInTheDocument()
  })
})

describe('Policies', () => {
  it('shows the text and allows no changes', async () => {
    stubApi({
      'GET /api/policies': [
        {
          name: 'base',
          cedar: 'permit(principal, action, resource);',
          enabled: true,
          description: null,
          updated_at: null,
        },
      ],
    })
    render(<Policies />)
    expect(await screen.findByText('permit(principal, action, resource);')).toBeInTheDocument()
    expect(screen.getByText('enabled')).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('explains a built-in policy in words and hides the internal tag', async () => {
    const policy = { cedar: 'forbid(principal, action, resource);', enabled: true, updated_at: null }
    stubApi({
      'GET /api/policies': [
        { ...policy, name: 'models', description: 'builtin:9e2e03db' },
        { ...policy, name: 'users', description: 'custom' },
      ],
    })
    render(<Policies />)
    expect(await screen.findByText('Models and data classes')).toBeInTheDocument()
    expect(screen.getByText(/the call is blocked/)).toBeInTheDocument()
    expect(screen.queryByText(/builtin:/)).not.toBeInTheDocument()
    expect(screen.getByText('changed by owner')).toBeInTheDocument()
    expect(screen.queryByText('custom')).not.toBeInTheDocument()
  })
})

describe('Models', () => {
  it('shows classes and an unknown price as a dash', async () => {
    stubApi({
      'GET /api/setup/status': {
        resources: [
          {
            name: 'local/default',
            model_id: 'qwen3.5:4b',
            provider: 'ollama',
            classes: ['internal', 'restricted'],
            context_length: 32768,
            input_price_per_mtok: null,
            output_price_per_mtok: null,
          },
        ],
      },
    })
    render(<Models onOpenWizard={vi.fn()} />)
    const row = (await screen.findByText('qwen3.5:4b')).closest('tr') as HTMLElement
    expect(within(row).getByText('Restricted')).toBeInTheDocument()
    expect(within(row).getByText('—')).toBeInTheDocument()
    expect(within(row).getByText('32,768')).toBeInTheDocument()
  })
})
