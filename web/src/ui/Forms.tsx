import { useId, useRef, type ReactNode } from 'react'
import { DataClassBadge } from './Indicators'
import { Icon } from './Icon'
import type { DataClass } from './types'

/* ───────────────────────── Field ───────────────────────── */

export interface FieldProps {
  label: string
  hint?: string
  error?: string
  /** Render function receives the id and aria-describedby for the field. */
  children: (ids: { id: string; describedBy?: string; invalid: boolean }) => ReactNode
}

export function Field({ label, hint, error, children }: FieldProps) {
  const id = useId()
  const hintId = hint ? `${id}-hint` : undefined
  const errId = error ? `${id}-err` : undefined
  const describedBy = [hintId, errId].filter(Boolean).join(' ') || undefined
  return (
    <div className="a3-field">
      <label className="a3-field__label" htmlFor={id}>
        {label}
      </label>
      {children({ id, describedBy, invalid: Boolean(error) })}
      {hint && (
        <small id={hintId} className="a3-field__hint">
          {hint}
        </small>
      )}
      {error && (
        <small id={errId} className="a3-field__error" role="alert">
          {error}
        </small>
      )}
    </div>
  )
}

/* ───────────────────────── PermissionList ───────────────────────── */

export interface PermissionRow {
  id: string
  /** One of six categories: Data, Workspace, Models, Network, Other agents, Secrets. */
  category: string
  text: string
  hint?: string
  dataClass?: DataClass
  /**
   * ask — the administrator allows it with a checkbox;
   * policy — set by a policy, can't be changed here;
   * none — the agent didn't request it.
   */
  mode: 'ask' | 'policy' | 'none'
  /** For mode="ask". */
  granted?: boolean
}

export interface PermissionListProps {
  rows: PermissionRow[]
  onToggle: (id: string, granted: boolean) => void
  /** Label for mode="policy", "By policy" by default. */
  policyLabel?: string
}

export function PermissionList({ rows, onToggle, policyLabel = 'By policy' }: PermissionListProps) {
  return (
    <ul className="a3-perms" aria-label="Agent permissions">
      {rows.map((r) => {
        const textId = `perm-${r.id}`
        return (
          <li className="a3-perms__row" key={r.id}>
            <span className="a3-perms__cat">{r.category}</span>
            <span id={textId} className="a3-perms__text">
              {r.text}
              {r.dataClass && (
                <>
                  {' '}
                  <DataClassBadge dataClass={r.dataClass} />
                </>
              )}
              {r.hint && <small>{r.hint}</small>}
            </span>
            {r.mode === 'ask' ? (
              <label className="a3-perms__toggle">
                <input
                  type="checkbox"
                  checked={Boolean(r.granted)}
                  onChange={(e) => onToggle(r.id, e.target.checked)}
                  aria-describedby={textId}
                />
                Allow
              </label>
            ) : (
              <span className="a3-perms__state">{r.mode === 'policy' ? policyLabel : 'No'}</span>
            )}
          </li>
        )
      })}
    </ul>
  )
}

/* ───────────────────────── FileDrop ───────────────────────── */

export interface FileDropProps {
  /** Hint inside the zone. */
  label?: string
  accept?: string
  multiple?: boolean
  onFiles: (files: File[]) => void
  disabled?: boolean
}

/** Upload zone: drag and drop, plus regular keyboard file selection. */
export function FileDrop({
  label = 'Drop PDF, DOCX or MD files here or choose files',
  accept = '.pdf,.docx,.md',
  multiple = true,
  onFiles,
  disabled,
}: FileDropProps) {
  const input = useRef<HTMLInputElement>(null)
  const id = useId()
  return (
    <div
      className={`a3-drop${disabled ? ' is-disabled' : ''}`}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault()
        if (!disabled) onFiles(Array.from(e.dataTransfer.files))
      }}
    >
      <Icon name="file" />
      <label htmlFor={id} className="a3-drop__label">
        {label}
      </label>
      <input
        ref={input}
        id={id}
        className="a3-visually-hidden"
        type="file"
        accept={accept}
        multiple={multiple}
        disabled={disabled}
        onChange={(e) => onFiles(Array.from(e.target.files ?? []))}
      />
      <button
        type="button"
        className="a3-button a3-button--secondary a3-button--sm"
        onClick={() => input.current?.click()}
        disabled={disabled}
      >
        Choose files
      </button>
    </div>
  )
}

/* ───────────────────────── FileList ───────────────────────── */

export interface FileItem {
  id: string
  name: string
  state: 'ready' | 'indexing' | 'failed'
  /** Indexing error reason. */
  error?: string
}

const FILE_STATE: Record<FileItem['state'], string> = {
  ready: 'ready',
  indexing: 'indexing…',
  failed: 'could not be read',
}

export function FileList({ files }: { files: FileItem[] }) {
  return (
    <ul className="a3-files" aria-label="Uploaded files">
      {files.map((f) => (
        <li key={f.id}>
          <span className="a3-truncate" title={f.name}>
            {f.name}
          </span>
          <span className={`a3-files__state a3-files__state--${f.state}`}>
            {f.error ?? FILE_STATE[f.state]}
          </span>
        </li>
      ))}
    </ul>
  )
}
