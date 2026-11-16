import { useState } from 'react'
import { dismissDefaultWarning, setDefaultModel, type DefaultWarning } from './api'
import { Button } from './ui/Controls'
import { Notice } from './ui/Lists'

const short = (name: string) => name.replace(/^local\//, '')

// The default model moved off a lost node by itself (change field-fixes-0-7): what, why, whom it
// affects; when the node is back, one button returns the model. On the Desk and in Models.
export function DefaultWarningNotice({
  warning,
  onChanged,
}: {
  warning: DefaultWarning
  onChanged: () => void
}) {
  const [error, setError] = useState<string | null>(null)
  const from = short(warning.switched_from)
  const node = warning.node ?? 'its node'
  const act = async (call: Promise<{ status: number }>) => {
    const r = await call
    if (r.status === 200 || r.status === 204) {
      setError(null)
      onChanged()
    } else setError(`Could not do it: HTTP ${r.status}`)
  }
  const head = warning.switched_to
    ? `The default model switched automatically from ${from} to ${short(warning.switched_to)}: node ${node} is unavailable.`
    : `The default model ${from} is unavailable (node ${node}), and no other model is up: agents on the default don’t answer.`
  return (
    <section aria-label="Default model">
      <Notice
        tone={warning.node_back ? 'info' : 'warn'}
        actions={
          <>
            {warning.can_return && (
              <Button variant="primary" onClick={() => void act(setDefaultModel(warning.switched_from))}>
                Return default to {from}
              </Button>
            )}
            <Button onClick={() => void act(dismissDefaultWarning())}>Dismiss</Button>
          </>
        }
      >
        {head}
        {warning.node_back && ` ${node} is back.`}
        {warning.switched_to &&
          warning.short_window.map((a) => (
            <span key={a}>
              {' '}
              {a} needs a larger context window and won’t answer on {short(warning.switched_to ?? '')}.
            </span>
          ))}
        {warning.model_unavailable.length > 0 &&
          ` Agents with their own model on that node don’t answer: ${warning.model_unavailable.join(', ')}.`}
        {error && ` ${error}`}
      </Notice>
    </section>
  )
}
