import { useCallback, useEffect, useState } from 'react'
import { fetchMe, logout, type Me } from './api'
import Auth from './Auth'
import Shell from './Shell'
import { StateView } from './ui/StateView'
import { PRODUCT } from './brand'

type Session =
  | { kind: 'loading' }
  | { kind: 'failed'; message: string }
  | { kind: 'anonymous'; setupRequired: boolean }
  | { kind: 'signed-in'; me: Me }

// The session decides what's on screen: the sign-in window or the shell.
export default function App() {
  const [session, setSession] = useState<Session>({ kind: 'loading' })

  const refresh = useCallback(async () => {
    try {
      const r = await fetchMe()
      if (r.status !== 200) throw new Error(`HTTP ${r.status}`)
      setSession(
        r.body.authenticated
          ? { kind: 'signed-in', me: r.body }
          : { kind: 'anonymous', setupRequired: r.body.setup_required },
      )
    } catch (e) {
      setSession({ kind: 'failed', message: e instanceof Error ? e.message : String(e) })
    }
  }, [])

  useEffect(() => {
    const load = async () => {
      await refresh()
    }
    void load()
  }, [refresh])

  if (session.kind === 'signed-in') {
    return (
      <Shell
        me={session.me}
        onLogout={async () => {
          await logout()
          await refresh()
        }}
      />
    )
  }
  if (session.kind === 'anonymous') return <Auth setupRequired={session.setupRequired} onDone={refresh} />
  return (
    <div className="a3-os">
      <main className="a3-desk a3-auth-host">
        {session.kind === 'loading' ? (
          <StateView kind="loading" surface="dark" />
        ) : (
          <StateView
            kind="error"
            surface="dark"
            description={`${PRODUCT} core is not responding: ${session.message}`}
            actionLabel="Retry"
            onAction={() => void refresh()}
          />
        )}
      </main>
    </div>
  )
}
