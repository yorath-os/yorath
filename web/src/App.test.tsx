import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, test, vi } from 'vitest'
import App from './App'
import { stubApi } from '../test/stub-api'

const OWNER = {
  authenticated: true,
  id: 'u1',
  login: 'alex',
  display_name: 'Alex',
  role: 'owner',
  department: null,
  is_active: true,
}
const MEMBER = { ...OWNER, login: 'anna', display_name: 'Anna', role: 'member' }
const CHAT = {
  'GET /api/chat/agents': [],
  'GET /api/chat/conversations': [],
}
const SETUP_DONE = {
  'GET /api/setup/status': { setup_completed: true },
  'GET /api/desk/summary': { status: 500, body: {} },
}

beforeEach(() => {
  window.location.hash = ''
})

const nav = () => screen.findByRole('navigation', { name: 'Main navigation' })

test('a member sees chat and none of the owner sections', async () => {
  stubApi({ 'GET /api/auth/me': MEMBER, ...CHAT })
  render(<App />)
  const menu = within(await nav())
  expect(menu.getByRole('link', { name: 'Chat' })).toBeInTheDocument()
  expect(menu.queryByRole('link', { name: 'Journal' })).not.toBeInTheDocument()
  expect(menu.queryByRole('link', { name: 'Data' })).not.toBeInTheDocument()
  expect(await screen.findByRole('heading', { name: /^Chat/ })).toBeInTheDocument()
})

test('a closed section opened by address shows a denial, not the content', async () => {
  window.location.hash = 'journal'
  const api = stubApi({ 'GET /api/auth/me': MEMBER, ...CHAT })
  render(<App />)
  expect(await screen.findByText('No access')).toBeInTheDocument()
  api.assertAllAnswered()
})

test('the section survives a page reload', async () => {
  window.location.hash = 'system'
  stubApi({
    'GET /api/auth/me': OWNER,
    ...SETUP_DONE,
    'GET /api/health': { status: 'ok', checks: {} },
    'GET /api/version': {
      distribution: { name: 'yorath', version: '0.1.0', schema_version: 'x' },
      core: '0.1.0',
      build: 'b',
      schema_head: 'x',
      schema_matches_manifest: true,
      images: {},
      runtimes: {},
      models: {},
      plugins: [],
    },
  })
  render(<App />)
  expect(await screen.findByRole('heading', { name: 'System' })).toBeInTheDocument()
})

test('no search and no notifications: there is no feature behind them', async () => {
  stubApi({ 'GET /api/auth/me': MEMBER, ...CHAT })
  render(<App />)
  await nav()
  expect(screen.queryByRole('search')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Notifications/ })).not.toBeInTheDocument()
})

test('a wrong password keeps the sign-in window and shows an error', async () => {
  stubApi({
    'GET /api/auth/me': { authenticated: false, setup_required: false },
    'POST /api/auth/login': { status: 401, body: { detail: 'bad' } },
  })
  render(<App />)
  await userEvent.type(await screen.findByLabelText('Login'), 'anna')
  await userEvent.type(screen.getByLabelText('Password'), 'wrong-pass')
  await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Wrong login or password')
  expect(screen.getByRole('heading', { name: 'Yorath · Sign in' })).toBeInTheDocument()
})

const SETUP = {
  'GET /api/auth/me': { authenticated: false, setup_required: true },
  'POST /api/auth/setup': { status: 201, body: OWNER },
}

const fillOwner = async (password: string, confirm: string) => {
  await userEvent.type(await screen.findByLabelText('Name'), 'Alex')
  await userEvent.type(screen.getByLabelText('Login'), 'alex')
  await userEvent.type(screen.getByLabelText('Password'), password)
  await userEvent.type(screen.getByLabelText('Confirm password'), confirm)
  await userEvent.click(screen.getByRole('button', { name: 'Create and sign in' }))
}

test('owner creation: mismatched passwords are not sent', async () => {
  const api = stubApi(SETUP)
  render(<App />)
  await fillOwner('stand-pass-123', 'stand-pass-124')
  expect(await screen.findByText('Passwords do not match')).toBeInTheDocument()
  expect(api.calls.filter((c) => c.key === 'POST /api/auth/setup')).toHaveLength(0)
  expect(screen.getByRole('heading', { name: 'Yorath · Create the owner' })).toBeInTheDocument()
})

test('owner creation: matching passwords create the owner', async () => {
  const api = stubApi(SETUP)
  render(<App />)
  await fillOwner('stand-pass-123', 'stand-pass-123')
  await vi.waitFor(() =>
    expect(api.bodyOf('POST /api/auth/setup')).toEqual({
      login: 'alex',
      password: 'stand-pass-123',
      display_name: 'Alex',
      code: '',
    }),
  )
  expect(screen.queryByText('Passwords do not match')).not.toBeInTheDocument()
})

test('core unavailable: an error and retry', async () => {
  const api = stubApi({ 'GET /api/auth/me': { status: 500, body: {} } })
  render(<App />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Yorath core is not responding')
  await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
  expect(api.calls.filter((c) => c.key === 'GET /api/auth/me')).toHaveLength(2)
})

test('an unfinished setup takes the owner to the wizard', async () => {
  stubApi({
    'GET /api/auth/me': OWNER,
    'GET /api/setup/status': { setup_completed: false, models: {}, resources: [] },
    'GET /api/desk/summary': { status: 500, body: {} },
    'GET /api/setup/hardware': { status: 500, body: {} },
    'GET /api/setup/models': { status: 500, body: {} },
  })
  render(<App />)
  expect(await screen.findByText('Setup wizard')).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Data' })).not.toBeInTheDocument()
})

test('a screen crash keeps the menu', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {})
  window.location.hash = 'data'
  stubApi({
    'GET /api/auth/me': OWNER,
    ...SETUP_DONE,
    'GET /api/data/assets': [{ id: 'a', name: 'x', access_rules: {} }],
    'GET /api/data/documents': [{ id: 'd', status: 'new-status', filename: 'f' }],
  })
  render(<App />)
  expect(await screen.findByText('This screen failed to open')).toBeInTheDocument()
  expect(await nav()).toBeInTheDocument()
})

test('the top bar carries the Fork mark next to the word "yorath" in lower case (brand-green)', async () => {
  stubApi({ 'GET /api/auth/me': MEMBER, ...CHAT })
  render(<App />)
  await nav()
  const logo = within(screen.getByRole('banner')).getByText('yorath')
  expect(logo.closest('.a3-menubar__logo')?.querySelector('svg.a3-menubar__mark')).not.toBeNull()
})
