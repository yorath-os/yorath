import { useState, type FormEvent } from 'react'
import { login, setupWithCode } from './api'
import { Button } from './ui/Controls'
import { Field } from './ui/Forms'
import { Window } from './ui/Window'
import { PRODUCT } from './brand'

// Owner creation (with the one-time code from the installer link) and sign-in. A window on the desk, no menu.
export default function Auth({
  setupRequired,
  onDone,
}: {
  setupRequired: boolean
  onDone: () => Promise<void>
}) {
  const [error, setError] = useState<string | null>(null)
  const [mismatch, setMismatch] = useState(false)
  const [busy, setBusy] = useState(false)
  const code = new URLSearchParams(window.location.search).get('code')

  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const f = new FormData(e.currentTarget)
    const l = String(f.get('login') ?? '')
    const p = String(f.get('password') ?? '')
    // A typo in the owner's password locks them out: there is no reset in the panel yet.
    if (setupRequired && p !== String(f.get('password_confirm') ?? '')) {
      setMismatch(true)
      return
    }
    setMismatch(false)
    setBusy(true)
    try {
      const r = setupRequired
        ? await setupWithCode(l, p, String(f.get('display_name') ?? l), String(f.get('code') ?? code ?? ''))
        : await login(l, p)
      if (r.status >= 400) {
        setError(
          r.status === 403
            ? 'Wrong setup code: take the link from the install.sh output'
            : setupRequired
              ? 'Could not create the owner'
              : 'Wrong login or password',
        )
        return
      }
      setError(null)
      await onDone()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="a3-os">
      <main className="a3-desk a3-auth-host">
        <Window
          variant="dialog"
          title={setupRequired ? `${PRODUCT} · Create the owner` : `${PRODUCT} · Sign in`}
        >
          <form className="a3-form" onSubmit={submit}>
            {setupRequired && (
              <>
                <Field label="Setup code" hint="From the link install.sh printed">
                  {({ id, describedBy }) => (
                    <input
                      id={id}
                      className="a3-input"
                      name="code"
                      defaultValue={code ?? ''}
                      autoComplete="off"
                      aria-describedby={describedBy}
                    />
                  )}
                </Field>
                <Field label="Name">
                  {({ id }) => (
                    <input id={id} className="a3-input" name="display_name" autoComplete="name" required />
                  )}
                </Field>
              </>
            )}
            <Field label="Login">
              {({ id }) => (
                <input id={id} className="a3-input" name="login" autoComplete="username" required />
              )}
            </Field>
            <Field label="Password" hint="At least 8 characters" error={error ?? undefined}>
              {({ id, describedBy, invalid }) => (
                <input
                  id={id}
                  className="a3-input"
                  name="password"
                  type="password"
                  autoComplete={setupRequired ? 'new-password' : 'current-password'}
                  minLength={8}
                  required
                  aria-describedby={describedBy}
                  aria-invalid={invalid || undefined}
                />
              )}
            </Field>
            {setupRequired && (
              <Field label="Confirm password" error={mismatch ? 'Passwords do not match' : undefined}>
                {({ id, describedBy, invalid }) => (
                  <input
                    id={id}
                    className="a3-input"
                    name="password_confirm"
                    type="password"
                    autoComplete="new-password"
                    minLength={8}
                    required
                    aria-describedby={describedBy}
                    aria-invalid={invalid || undefined}
                  />
                )}
              </Field>
            )}
            <Button variant="primary" type="submit" loading={busy}>
              {setupRequired ? 'Create and sign in' : 'Sign in'}
            </Button>
          </form>
        </Window>
      </main>
    </div>
  )
}
