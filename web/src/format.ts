// Labels and durations for screens: API answers → interface words.

const TRIGGERS: Record<string, string> = {
  user_message: 'Chat question',
  agent_call: 'Call from another agent',
  event: 'Event',
  schedule: 'Scheduled',
  api: 'API call',
}

export const triggerLabel = (trigger: string) => TRIGGERS[trigger] ?? trigger

export const CLASSES = ['public', 'internal', 'confidential', 'restricted'] as const
export type DataClassName = (typeof CLASSES)[number]
export const asDataClass = (value: string | null | undefined): DataClassName | undefined =>
  CLASSES.find((c) => c === value)

/** "45 s", "12 min", "2 h 5 min". */
export function duration(fromIso: string, toIso: string | null, now = Date.now()) {
  const seconds = Math.max(0, Math.round(((toIso ? Date.parse(toIso) : now) - Date.parse(fromIso)) / 1000))
  if (seconds < 60) return `${seconds} s`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min`
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`
}

export const clockTime = (iso: string) =>
  new Date(iso).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  })

export const dateTime = (iso: string) => new Date(iso).toLocaleString('en-US')

/** English form by count: plural(3, ['agent', 'agents']). */
export function plural(n: number, [one, many]: [string, string]) {
  return n === 1 ? one : many
}

// A scan runs every 5 minutes; a report older than 30, or from a node that is not active, may be out
// of date (pre-public-hardening R6)
const STALE_MIN = 30
export function checkedText(
  n: { checked_at: string | null; node_status: string },
  now: number = Date.now(),
): string {
  if (!n.checked_at) return 'not checked yet'
  const min = Math.max(0, Math.round((now - Date.parse(n.checked_at)) / 60_000))
  const age = min < 1 ? 'just now' : min < 120 ? `${min} min ago` : `${Math.round(min / 60)} h ago`
  return min > STALE_MIN || n.node_status !== 'active' ? `${age} · stale` : age
}

// When a key was last used, for agents connected by key (outside-agents-cluster).
export function lastCallText(at: string | null, now: number = Date.now()): string {
  if (!at) return 'no calls yet'
  const min = Math.max(0, Math.round((now - Date.parse(at)) / 60_000))
  return min < 1
    ? 'just now'
    : min < 120
      ? `${min} min ago`
      : min < 2880
        ? `${Math.round(min / 60)} h ago`
        : `${Math.round(min / 1440)} d ago`
}
