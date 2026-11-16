import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  createAsset,
  deleteDocument,
  fetchAssets,
  fetchDocuments,
  uploadDocument,
  uploadRecords,
  type Asset,
  type Doc,
} from './api'
import DemoHr from './DemoHr'
import { asDataClass, CLASSES } from './format'
import { Button } from './ui/Controls'
import { DataTable } from './ui/DataTable'
import { Field, FileDrop } from './ui/Forms'
import { DataClassBadge, StatusBadge } from './ui/Indicators'
import { Notice } from './ui/Lists'
import { ScreenStateGate } from './ui/StateView'
import type { ScreenState, Tone } from './ui/types'
import { Card } from './ui/Wizard'
import { Window } from './ui/Window'

const DOC_STATUS: Record<Doc['status'], { label: string; tone: Tone }> = {
  uploaded: { label: 'uploaded', tone: 'info' },
  parsed: { label: 'parsed', tone: 'info' },
  indexing: { label: 'indexing', tone: 'info' },
  indexed: { label: 'ready', tone: 'ok' },
  failed: { label: 'error', tone: 'error' },
}

const list = (value: FormDataEntryValue | null) =>
  String(value ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)

function ClassBadge({ value }: { value: string }) {
  const dataClass = asDataClass(value)
  return dataClass ? <DataClassBadge dataClass={dataClass} /> : value
}

// The "Data" page (invariant 3): sources with a class and access rules, documents with parsing status.
export default function Data() {
  const [assets, setAssets] = useState<Asset[]>([])
  const [docs, setDocs] = useState<Doc[]>([])
  const [log, setLog] = useState<string[]>([])
  const [state, setState] = useState<ScreenState>({ kind: 'loading' })
  const [files, setFiles] = useState<File[]>([])
  const [csv, setCsv] = useState<File | null>(null)

  const reload = useCallback(async () => {
    try {
      const [a, d] = await Promise.all([fetchAssets(), fetchDocuments()])
      if (a.status !== 200 || d.status !== 200) throw new Error(`HTTP ${a.status}`)
      setAssets(a.body)
      setDocs(d.body)
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

  const report = (message: string) => setLog((l) => [message, ...l])

  const onCreate = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const form = e.currentTarget // React nulls currentTarget after await
    const f = new FormData(form)
    const r = await createAsset({
      name: String(f.get('name') ?? ''),
      data_class: String(f.get('data_class') ?? 'internal'),
      kind: String(f.get('kind') ?? 'documents'),
      access_rules: { roles: list(f.get('roles')), departments: list(f.get('departments')) },
    })
    report(r.status === 201 ? 'source created' : `create error: ${r.status}`)
    form.reset()
    await reload()
  }

  const onUploadRecords = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (!csv) return
    const r = await uploadRecords(String(new FormData(e.currentTarget).get('asset_id') ?? ''), csv)
    report('rows' in r.body ? `rows loaded: ${r.body.rows}` : `error: ${r.body.detail.message ?? r.status}`)
    setCsv(null)
    await reload()
  }

  const onUpload = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const f = new FormData(e.currentTarget)
    const assetId = String(f.get('asset_id') ?? '')
    const cls = String(f.get('data_class') ?? '')
    for (const file of files) {
      const r = await uploadDocument(assetId, file, cls || null)
      report(
        r.status === 201
          ? `${file.name}: ${(r.body as Doc).status}${(r.body as Doc).error ? ` (${(r.body as Doc).error})` : ''}`
          : `${file.name}: error ${r.status} ${JSON.stringify((r.body as { detail?: unknown }).detail ?? '')}`,
      )
    }
    setFiles([])
    await reload()
  }

  const tables = assets.filter((a) => a.enabled && a.kind === 'records')
  const folders = assets.filter((a) => a.enabled && a.kind !== 'records')
  return (
    <div className="a3-desk__pad a3-desk__pad--app">
      <Window variant="app" title="Data" flush>
        <ScreenStateGate state={state} skeletonRows={6} emptyTitle="No sources">
          <div className="a3-stack">
            {log.length > 0 && (
              <Notice tone="info">
                <ul className="a3-plain-list">
                  {log.slice(0, 5).map((m, i) => (
                    <li key={i}>{m}</li>
                  ))}
                </ul>
              </Notice>
            )}
            <Card title="Sources">
              <DataTable
                caption="Data sources"
                rows={assets}
                rowKey={(a) => a.id}
                columns={[
                  {
                    id: 'name',
                    header: 'Name',
                    weight: 2,
                    render: (a) => (
                      <span className="a3-mono a3-text-xs a3-truncate" title={a.name}>
                        {a.name}
                      </span>
                    ),
                  },
                  {
                    id: 'class',
                    header: 'Class',
                    weight: 1.4,
                    render: (a) => <ClassBadge value={a.data_class} />,
                  },
                  {
                    id: 'roles',
                    header: 'Roles',
                    weight: 1.4,
                    hideOnMobile: true,
                    render: (a) => (a.access_rules.roles ?? []).join(', ') || '—',
                  },
                  {
                    id: 'departments',
                    header: 'Departments',
                    weight: 1.6,
                    hideOnMobile: true,
                    render: (a) => (a.access_rules.departments ?? []).join(', ') || '—',
                  },
                  {
                    id: 'kind',
                    header: 'Kind',
                    hideOnMobile: true,
                    render: (a) => (a.kind === 'records' ? 'table' : 'documents'),
                  },
                  {
                    id: 'content',
                    header: 'Size',
                    weight: 1.9,
                    render: (a) =>
                      a.kind === 'records' ? `rows: ${a.records}` : `documents: ${a.documents}`,
                  },
                ]}
              />
            </Card>
            <Card title="New source">
              <form className="a3-form" onSubmit={onCreate}>
                <div className="a3-form-row">
                  <Field label="Name">
                    {({ id }) => (
                      <input
                        id={id}
                        className="a3-input"
                        name="name"
                        placeholder="source name (slug)"
                        pattern="[a-z0-9][a-z0-9-]{1,98}"
                        required
                      />
                    )}
                  </Field>
                  <Field label="Class">
                    {({ id }) => (
                      <select id={id} className="a3-input" name="data_class" defaultValue="internal">
                        {CLASSES.map((c) => (
                          <option key={c}>{c}</option>
                        ))}
                      </select>
                    )}
                  </Field>
                  <Field label="Source kind">
                    {({ id }) => (
                      <select id={id} className="a3-input" name="kind" defaultValue="documents">
                        <option value="documents">documents</option>
                        <option value="records">table</option>
                      </select>
                    )}
                  </Field>
                </div>
                <div className="a3-form-row">
                  <Field label="Roles" hint="Comma-separated">
                    {({ id, describedBy }) => (
                      <input id={id} className="a3-input" name="roles" aria-describedby={describedBy} />
                    )}
                  </Field>
                  <Field label="Departments" hint="Comma-separated">
                    {({ id, describedBy }) => (
                      <input id={id} className="a3-input" name="departments" aria-describedby={describedBy} />
                    )}
                  </Field>
                </div>
                <div>
                  <Button variant="primary" type="submit">
                    Add source
                  </Button>
                </div>
              </form>
            </Card>
            <DemoHr enabled={assets.some((a) => a.name === 'hr-vacations')} onEnabled={reload} />
            <Card title="Upload documents">
              <form className="a3-form" onSubmit={onUpload}>
                <div className="a3-form-row">
                  <Field label="Source">
                    {({ id }) => (
                      <select id={id} className="a3-input" name="asset_id" required>
                        {folders.map((a) => (
                          <option key={a.id} value={a.id}>
                            {a.name} ({a.data_class})
                          </option>
                        ))}
                      </select>
                    )}
                  </Field>
                  <Field label="Document class">
                    {({ id }) => (
                      <select id={id} className="a3-input" name="data_class" defaultValue="">
                        <option value="">source class</option>
                        {CLASSES.map((c) => (
                          <option key={c}>{c}</option>
                        ))}
                      </select>
                    )}
                  </Field>
                </div>
                <FileDrop
                  label={
                    files.length
                      ? files.map((f) => f.name).join(', ')
                      : 'Drop PDF, DOCX, MD or TXT files here'
                  }
                  accept=".pdf,.docx,.md,.txt"
                  onFiles={setFiles}
                />
                <div>
                  <Button variant="primary" type="submit" disabled={files.length === 0}>
                    Upload
                  </Button>
                </div>
              </form>
            </Card>
            {tables.length > 0 && (
              <Card title="Upload a table">
                <p>
                  UTF-8 CSV with the columns employee_id, full_name, department, manager_id. A new upload
                  replaces the source rows.
                </p>
                <form className="a3-form" onSubmit={onUploadRecords}>
                  <Field label="Table">
                    {({ id }) => (
                      <select id={id} className="a3-input" name="asset_id" required>
                        {tables.map((a) => (
                          <option key={a.id} value={a.id}>
                            {a.name} ({a.data_class})
                          </option>
                        ))}
                      </select>
                    )}
                  </Field>
                  <FileDrop
                    label={csv ? csv.name : 'CSV file'}
                    accept=".csv"
                    multiple={false}
                    onFiles={(picked) => setCsv(picked[0] ?? null)}
                  />
                  <div>
                    <Button variant="primary" type="submit" disabled={!csv}>
                      Upload table
                    </Button>
                  </div>
                </form>
              </Card>
            )}
            <Card title="Documents">
              {docs.length === 0 ? (
                <p>No documents yet.</p>
              ) : (
                <DataTable
                  caption="Documents"
                  rows={docs}
                  rowKey={(d) => d.id}
                  columns={[
                    {
                      id: 'file',
                      header: 'File',
                      weight: 2.4,
                      render: (d) => (
                        <span className="a3-truncate" title={d.filename}>
                          {d.filename}
                        </span>
                      ),
                    },
                    {
                      id: 'asset',
                      header: 'Source',
                      weight: 1.4,
                      hideOnMobile: true,
                      render: (d) => assets.find((a) => a.id === d.asset_id)?.name ?? '?',
                    },
                    {
                      id: 'class',
                      header: 'Class',
                      weight: 1.3,
                      render: (d) => <ClassBadge value={d.data_class} />,
                    },
                    {
                      id: 'status',
                      header: 'Status',
                      weight: 1.6,
                      render: (d) => (
                        <span title={d.error ?? ''}>
                          <StatusBadge tone={DOC_STATUS[d.status].tone} label={DOC_STATUS[d.status].label} />
                          {d.error ? ` ${d.error}` : ''}
                        </span>
                      ),
                    },
                    {
                      id: 'size',
                      header: 'Size',
                      hideOnMobile: true,
                      render: (d) => `${Math.round(d.size_bytes / 1024)} KB`,
                    },
                    {
                      id: 'actions',
                      header: '',
                      weight: 1.1,
                      render: (d) => (
                        <Button
                          size="sm"
                          variant="danger"
                          aria-label={`Delete ${d.filename}`}
                          onClick={async () => {
                            await deleteDocument(d.id)
                            await reload()
                          }}
                        >
                          Delete
                        </Button>
                      ),
                    },
                  ]}
                />
              )}
            </Card>
          </div>
        </ScreenStateGate>
      </Window>
    </div>
  )
}
