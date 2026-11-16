import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { stubApi } from '../test/stub-api'
import Data from './Data'

const BASE = { description: null, access_rules: {}, enabled: true, documents: 0, records: 0 }
const DOCS = { ...BASE, id: 'a1', name: 'team-docs', kind: 'documents', data_class: 'internal', documents: 3 }
const TABLE = {
  ...BASE,
  id: 'a2',
  name: 'hr-vacations',
  kind: 'records',
  data_class: 'confidential',
  records: 4,
}
// jsdom doesn't see a file from userEvent when checking required, so the form is submitted directly
const submit = (name: string) => fireEvent.submit(screen.getByRole('button', { name }).closest('form')!)
const pickCsv = async (input: HTMLElement) => userEvent.upload(input, CSV)
const CSV = new File(['employee_id,full_name,department,manager_id\n'], 'hr.csv', { type: 'text/csv' })

describe('Data', () => {
  it('shows the source kind and uploads a table only into a table source', async () => {
    const api = stubApi({
      'GET /api/data/assets': [DOCS, TABLE],
      'GET /api/data/documents': [],
      'POST /api/data/assets/a2/records': { status: 201, body: { rows: 4 } },
    })
    render(<Data />)
    expect(await screen.findByText('rows: 4')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Enable the HR demo set' })).not.toBeInTheDocument()
    expect(screen.getByText('documents: 3')).toBeInTheDocument()
    const table = screen.getByRole('combobox', { name: 'Table' })
    expect(
      within(table)
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['hr-vacations (confidential)'])

    await pickCsv(screen.getByLabelText('CSV file'))
    submit('Upload table')
    expect(await screen.findByText('rows loaded: 4')).toBeInTheDocument()
    api.assertAllAnswered()
  })

  it('shows every document status core returns', async () => {
    const doc = {
      asset_id: 'a1',
      size_bytes: 2048,
      data_class: 'internal',
      error: null,
      pages: 1,
      created_at: '',
    }
    stubApi({
      'GET /api/data/assets': [DOCS],
      'GET /api/data/documents': ['uploaded', 'parsed', 'indexing', 'indexed', 'failed'].map((status) => ({
        ...doc,
        id: status,
        filename: `${status}.md`,
        status,
      })),
    })
    render(<Data />)
    for (const label of ['uploaded', 'parsed', 'indexing', 'ready', 'error']) {
      expect(await screen.findByText(label)).toBeInTheDocument()
    }
  })

  it('shows the reason an upload was rejected', async () => {
    stubApi({
      'GET /api/data/assets': [TABLE],
      'GET /api/data/documents': [],
      'POST /api/data/assets/a2/records': {
        status: 422,
        body: { detail: { message: 'missing columns: manager_id' } },
      },
    })
    render(<Data />)
    await pickCsv(await screen.findByLabelText('CSV file'))
    submit('Upload table')
    expect(await screen.findByText('error: missing columns: manager_id')).toBeInTheDocument()
  })

  it('shows the demo set passwords and refreshes the sources', async () => {
    const user = { login: 'anna.parker', password: 'pw-1', display_name: 'Parker', title: 'Employee' }
    const api = stubApi({
      'GET /api/data/assets': [DOCS],
      'GET /api/data/documents': [],
      'POST /api/data/demo-hr': { status: 201, body: { users: [user], rows: 15 } },
    })
    render(<Data />)
    await userEvent.click(await screen.findByRole('button', { name: 'Enable the HR demo set' }))
    expect(await screen.findByText('pw-1')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('shown only once')
    expect(api.calls.filter((c) => c.key === 'GET /api/data/assets')).toHaveLength(2)
  })

  it('shows the rejection of enabling it again', async () => {
    stubApi({
      'GET /api/data/assets': [DOCS],
      'GET /api/data/documents': [],
      'POST /api/data/demo-hr': {
        status: 409,
        body: { detail: { message: 'the demo set is already enabled' } },
      },
    })
    render(<Data />)
    await userEvent.click(await screen.findByRole('button', { name: 'Enable the HR demo set' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('the demo set is already enabled')
  })

  it('creates a table source', async () => {
    const api = stubApi({
      'GET /api/data/assets': [DOCS],
      'GET /api/data/documents': [],
      'POST /api/data/assets': { status: 201, body: TABLE },
    })
    render(<Data />)
    expect(screen.queryByRole('button', { name: 'Upload table' })).not.toBeInTheDocument()
    await userEvent.type(await screen.findByPlaceholderText('source name (slug)'), 'hr-vacations')
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Source kind' }), 'records')
    await userEvent.click(screen.getByRole('button', { name: 'Add source' }))
    expect(await screen.findByText('source created')).toBeInTheDocument()
    expect(api.calls.filter((c) => c.key === 'GET /api/data/assets')).toHaveLength(2) // the list was refreshed
    expect(api.bodyOf('POST /api/data/assets')).toMatchObject({ name: 'hr-vacations', kind: 'records' })
  })
})
