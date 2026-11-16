// Brand colors and contrast of the tokens (change brand-green, decision 40): every text/background pair
// the components use is at least 4.5:1, large text, icons and focus rings at least 3:1.
/// <reference types="node" />
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, test } from 'vitest'

const css = readFileSync(path.resolve(process.cwd(), 'src/styles/tokens.css'), 'utf8') // vitest runs in web/
const raw: Record<string, string> = {}
for (const m of css.matchAll(/(--[\w-]+):\s*([^;]+);/g)) raw[m[1]] ??= m[2].trim()
const color = (name: string): string => {
  const v = raw[name]
  if (!v) throw new Error(`no token ${name}`)
  const ref = v.match(/^var\((--[\w-]+)\)$/)
  return ref ? color(ref[1]) : v.toUpperCase()
}
const lum = (hex: string) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
const contrast = (a: string, b: string) => {
  const [x, y] = [lum(color(a)), lum(color(b))].sort((p, q) => q - p)
  return (x + 0.05) / (y + 0.05)
}
const hue = (name: string) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(color(name).slice(i, i + 2), 16) / 255)
  const max = Math.max(r, g, b), d = max - Math.min(r, g, b)
  const h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4
  return (h * 60 + 360) % 360
}

describe('brand colors', () => {
  test('the main button and the accent are the brand green', () => {
    expect(color('--accent')).toBe('#16875E')
    expect(color('--chrome-accent')).toBe('#16875E')
  })

  test('"ok" is a different hue from the accent, so "selected" is not read as "running"', () => {
    const d = Math.abs(hue('--ok') - hue('--accent'))
    expect(Math.min(d, 360 - d)).toBeGreaterThanOrEqual(25)
  })

  test('no avatar is the accent color', () => {
    for (const a of ['--avatar-1', '--avatar-2', '--avatar-3', '--avatar-4']) expect(color(a)).not.toBe(color('--accent'))
  })
})

const TEXT: [string, string][] = [
  ['--on-accent', '--accent'],
  ['--chrome-text-strong', '--chrome-accent'],
  ['--accent-text', '--win-surface'],
  ['--accent-text', '--win-bg'],
  ['--accent-text', '--chrome-sidebar-bg'],
  ['--class-internal-text', '--class-internal-bg'],
  ['--accent-text', '--accent-soft'],
  ['--win-text', '--accent-fill'],
  ['--ok-text', '--win-surface'],
  ['--ok-text-strong', '--ok-bg'],
  ['--warn-text-strong', '--warn-bg'],
  ['--warn-text', '--win-surface'],
  ['--error-text', '--error-bg'],
  ['--error-text', '--win-surface'],
  ['--class-confidential-text', '--class-confidential-bg'],
  ['--class-restricted-text', '--class-restricted-bg'],
  ['--class-public-text', '--class-public-bg'],
  ['--win-text', '--win-surface'],
  ['--win-text-secondary', '--win-surface'],
  ['--win-text-muted', '--win-surface'],
  ['--chrome-text', '--chrome-sidebar-bg'],
  ['--chrome-text-strong', '--chrome-active'],
  ['--chrome-text', '--chrome-active'],
  ['--chrome-text-sub', '--chrome-sidebar-bg'],
  ['--chrome-text-desk', '--chrome-desk-bg'],
  ['--code-text', '--code-bg'],
  ['--code-key', '--code-bg'],
]
const LARGE: [string, string][] = [
  ['--on-avatar', '--avatar-1'],
  ['--on-avatar', '--avatar-2'],
  ['--on-avatar', '--avatar-3'],
  ['--on-avatar', '--avatar-4'],
  ['--focus-ring-color', '--win-bg'],
  ['--focus-ring-color', '--chrome-menubar-bg'],
  ['--ok-dot', '--win-surface'],
  ['--chrome-ok', '--chrome-menubar-bg'],
  ['--accent', '--win-surface'],
]

describe('contrast', () => {
  test.each(TEXT)('text %s on %s is at least 4.5:1', (fg, bg) => {
    expect(contrast(fg, bg)).toBeGreaterThanOrEqual(4.5)
  })
  test.each(LARGE)('large text, icon or ring %s on %s is at least 3:1', (fg, bg) => {
    expect(contrast(fg, bg)).toBeGreaterThanOrEqual(3)
  })
})
