import type { ReactNode } from 'react'

export interface Column<T> {
  id: string
  header: string
  /** Width in fractions (fr). Used for grid columns in the header and rows. */
  weight?: number
  render: (row: T) => ReactNode
  /** Hide the column on narrow screens (below 768 px). */
  hideOnMobile?: boolean
}

export interface DataTableProps<T> {
  caption: string
  /** Show the caption visually. Hidden by default (the window gives the title). */
  captionVisible?: boolean
  columns: Column<T>[]
  rows: T[]
  rowKey: (row: T) => string
  /** Selected row (highlight and aria-selected). */
  selectedKey?: string
  /** Row click. The row becomes a keyboard button (Enter / Space). */
  onRowClick?: (row: T) => void
  /** Row button label for screen readers. */
  rowLabel?: (row: T) => string
  /** comfortable — 52 px rows (task manager); compact — 44 px (widget). */
  density?: 'compact' | 'comfortable'
}

/**
 * A table on a real <table>. On a narrow screen it scrolls horizontally
 * inside its own frame; the page doesn't shift.
 */
export function DataTable<T>({
  caption,
  captionVisible,
  columns,
  rows,
  rowKey,
  selectedKey,
  onRowClick,
  rowLabel,
  density = 'compact',
}: DataTableProps<T>) {
  const totalWeight = columns.reduce((sum, c) => sum + (c.weight ?? 1), 0)
  return (
    <div className="a3-table-wrap" role="region" aria-label={`Table: ${caption}`} tabIndex={0}>
      <table className={`a3-table a3-table--${density}${onRowClick ? ' a3-table--interactive' : ''}`}>
        <caption className={captionVisible ? 'a3-table__caption' : 'a3-visually-hidden'}>{caption}</caption>
        <colgroup>
          {columns.map((c) => (
            <col
              key={c.id}
              className={c.hideOnMobile ? 'a3-hide-mobile' : undefined}
              style={{ width: `${((c.weight ?? 1) / totalWeight) * 100}%` }}
            />
          ))}
        </colgroup>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.id} scope="col" className={c.hideOnMobile ? 'a3-hide-mobile' : undefined}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const key = rowKey(r)
            const selected = key === selectedKey
            return (
              <tr
                key={key}
                className={selected ? 'is-selected' : undefined}
                aria-current={selected ? 'true' : undefined}
                tabIndex={onRowClick ? 0 : undefined}
                aria-label={onRowClick && rowLabel ? rowLabel(r) : undefined}
                onClick={onRowClick ? () => onRowClick(r) : undefined}
                onKeyDown={
                  onRowClick
                    ? (e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          onRowClick(r)
                        }
                      }
                    : undefined
                }
              >
                {columns.map((c) => (
                  <td key={c.id} className={c.hideOnMobile ? 'a3-hide-mobile' : undefined}>
                    {c.render(r)}
                  </td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
