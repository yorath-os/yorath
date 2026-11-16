import { useState } from 'react'
import { enableDemoHr, type DemoUser } from './api'
import { Button } from './ui/Controls'
import { DataTable } from './ui/DataTable'
import { Notice } from './ui/Lists'
import { Card } from './ui/Wizard'

// Passwords arrive once: the server stores only the hash, enabling again is rejected
export default function DemoHr({
  enabled = false,
  onEnabled,
}: {
  enabled?: boolean
  onEnabled?: () => void | Promise<void>
}) {
  const [users, setUsers] = useState<DemoUser[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const enable = async () => {
    setError('')
    setBusy(true)
    try {
      const r = await enableDemoHr()
      if (r.status !== 201 || !r.body) {
        const body = r.body as unknown as { detail?: { message?: string } }
        setError(body.detail?.message ?? `error: ${r.status}`)
        return
      }
      setUsers(r.body.users)
      await onEnabled?.()
    } finally {
      setBusy(false)
    }
  }

  if (users.length === 0) {
    if (enabled) return null
    return (
      <Card title="HR demo">
        <p>A vacation table, a policy and three users with different permissions. The data is fictional.</p>
        <Button onClick={() => void enable()} loading={busy}>
          Enable the HR demo set
        </Button>
        {error && <Notice tone="error">{error}</Notice>}
      </Card>
    )
  }
  return (
    <Card title="HR demo enabled">
      <div role="status">
        <Notice tone="warn">
          Save the passwords: they are shown only once. Ask "How many vacation days does Miller have left?" as
          each user.
        </Notice>
      </div>
      <DataTable
        caption="Demo users"
        rows={users}
        rowKey={(u) => u.login}
        columns={[
          { id: 'title', header: 'Role', render: (u) => u.title },
          {
            id: 'name',
            header: 'User',
            weight: 2,
            hideOnMobile: true,
            render: (u) => u.display_name,
          },
          { id: 'login', header: 'Login', weight: 1.5, render: (u) => <code>{u.login}</code> },
          { id: 'password', header: 'Password', weight: 1.5, render: (u) => <code>{u.password}</code> },
        ]}
      />
    </Card>
  )
}
