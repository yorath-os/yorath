import { expect, vi } from 'vitest'

// The frontend test seam is fetch: requests go through the real api.ts,
// with real paths and bodies. A request the test didn't answer fails the test.
type Reply = unknown | ((body: unknown) => Response | { status: number; body: unknown })
type Call = { key: string; body: unknown }

export function sseStream() {
  const encoder = new TextEncoder()
  let controller!: ReadableStreamDefaultController<Uint8Array>
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c
    },
  })
  return {
    response: () => new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } }),
    send: (event: string, data: unknown) =>
      controller.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)),
    close: () => controller.close(),
  }
}

export function stubApi(routes: Record<string, Reply>) {
  const calls: Call[] = []
  const unanswered: string[] = []
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? 'GET'} ${url}`
    const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined
    calls.push({ key, body })
    if (!(key in routes)) {
      unanswered.push(key)
      return new Response(JSON.stringify({ detail: `unanswered: ${key}` }), { status: 599 })
    }
    const reply = routes[key]
    const value = typeof reply === 'function' ? (reply as (b: unknown) => unknown)(body) : reply
    if (value instanceof Response) return value
    const shaped = value as { status?: number; body?: unknown }
    const status = shaped && typeof shaped.status === 'number' ? shaped.status : 200
    const payload = shaped && typeof shaped.status === 'number' ? shaped.body : value
    return new Response(JSON.stringify(payload), { status })
  })
  return {
    calls,
    bodyOf: (key: string) => calls.findLast((c) => c.key === key)?.body,
    assertAllAnswered: () => expect(unanswered).toEqual([]),
  }
}
