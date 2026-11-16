import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { createUser, fetchUsers, setUserActive, type UserInfo } from './api'
import { Button } from './ui/Controls'
import { DataTable } from './ui/DataTable'
import { Field } from './ui/Forms'
import { StatusBadge } from './ui/Indicators'
import { Notice } from './ui/Lists'
import { ScreenStateGate } from './ui/StateView'
import type { ScreenState } from './ui/types'
import { Card } from './ui/Wizard'
import { Window } from './ui/Window'

const CREATE_ERRORS: Record<number, string> = {
  409: 'This login is already taken.',
  422: 'Check the fields: login in Latin letters, password of at least 8 characters, role in Latin letters.',
}

// Users: employee_id links the account to a table row for row-level rules.
export default function Users({ currentUserId }: { currentUserId: string }) {
  const [users, setUsers] = useState<UserInfo[]>([])
  const [state, setState] = useState<ScreenState>({ kind: 'loading' })
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    try {
      const r = await fetchUsers()
      if (r.status !== 200) throw new Error(`HTTP ${r.status}`)
      setUsers(r.body)
      setState({ kind: 'ready' })
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      setState({ kind: 'error', message, onRetry: () => void reload() })
    }
  }, [])

  useEffect(() => {
    const load = async () => {
      await reload()
    }
    void load()
  }, [reload])

  const onCreate = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const form = e.currentTarget
    const f = new FormData(form)
    const employeeId = String(f.get('employee_id') ?? '').trim()
    const r = await createUser({
      login: String(f.get('login') ?? ''),
      password: String(f.get('password') ?? ''),
      display_name: String(f.get('display_name') ?? ''),
      role: String(f.get('role') ?? 'member'),
      department: String(f.get('department') ?? '').trim() || null,
      attributes: employeeId ? { employee_id: employeeId } : {},
    })
    if (r.status !== 201) {
      setError(CREATE_ERRORS[r.status] ?? `Could not create the user: HTTP ${r.status}`)
      return
    }
    setError(null)
    form.reset()
    await reload()
  }

  const toggle = async (u: UserInfo) => {
    const r = await setUserActive(u.id, !u.is_active)
    setError(r.status === 200 ? null : `Could not update the user: HTTP ${r.status}`)
    await reload()
  }

  return (
    <div className="a3-desk__pad a3-desk__pad--app">
      <Window variant="app" title="Users" flush>
        <ScreenStateGate state={state} skeletonRows={5} emptyTitle="No users">
          <div className="a3-stack">
            {error && <Notice tone="error">{error}</Notice>}
            <DataTable
              caption="Users"
              rows={users}
              rowKey={(u) => u.id}
              columns={[
                { id: 'name', header: 'Name', weight: 2, render: (u) => u.display_name },
                {
                  id: 'login',
                  header: 'Login',
                  weight: 1.5,
                  hideOnMobile: true,
                  render: (u) => <span className="a3-mono a3-text-xs">{u.login}</span>,
                },
                { id: 'role', header: 'Role', render: (u) => u.role },
                {
                  id: 'department',
                  header: 'Department',
                  weight: 1.5,
                  hideOnMobile: true,
                  render: (u) => u.department ?? '—',
                },
                {
                  id: 'state',
                  header: 'Status',
                  weight: 1.2,
                  render: (u) => (
                    <StatusBadge
                      tone={u.is_active ? 'ok' : 'neutral'}
                      label={u.is_active ? 'active' : 'disabled'}
                    />
                  ),
                },
                {
                  id: 'actions',
                  header: '',
                  weight: 1.2,
                  render: (u) =>
                    u.id === currentUserId ? null : (
                      <Button
                        size="sm"
                        variant={u.is_active ? 'danger' : 'secondary'}
                        aria-label={`${u.is_active ? 'Disable' : 'Enable'}: ${u.display_name}`}
                        onClick={() => void toggle(u)}
                      >
                        {u.is_active ? 'Disable' : 'Enable'}
                      </Button>
                    ),
                },
              ]}
            />
            <Card title="New user">
              <form className="a3-form" onSubmit={onCreate}>
                <div className="a3-form-row">
                  <Field label="Name">
                    {({ id }) => <input id={id} className="a3-input" name="display_name" required />}
                  </Field>
                  <Field label="Login">
                    {({ id }) => (
                      <input id={id} className="a3-input" name="login" autoComplete="off" required />
                    )}
                  </Field>
                  <Field label="Password" hint="At least 8 characters">
                    {({ id, describedBy }) => (
                      <input
                        id={id}
                        className="a3-input"
                        name="password"
                        type="password"
                        autoComplete="new-password"
                        minLength={8}
                        required
                        aria-describedby={describedBy}
                      />
                    )}
                  </Field>
                </div>
                <div className="a3-form-row">
                  <Field label="Role">
                    {({ id }) => (
                      <input id={id} className="a3-input" name="role" defaultValue="member" required />
                    )}
                  </Field>
                  <Field label="Department">
                    {({ id }) => <input id={id} className="a3-input" name="department" />}
                  </Field>
                  <Field label="employee_id" hint="Link to a table row, if any">
                    {({ id, describedBy }) => (
                      <input id={id} className="a3-input" name="employee_id" aria-describedby={describedBy} />
                    )}
                  </Field>
                </div>
                <div>
                  <Button variant="primary" type="submit">
                    Create user
                  </Button>
                </div>
              </form>
            </Card>
          </div>
        </ScreenStateGate>
      </Window>
    </div>
  )
}
