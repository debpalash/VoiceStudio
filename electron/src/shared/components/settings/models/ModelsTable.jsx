import React from 'react';
import { flexRender } from '@tanstack/react-table';
import { Button, Table } from '../../../ui';

/**
 * Virtualized model table view. Purely presentational — the table instance,
 * virtualizer, and row runtime resolver are all created by the host
 * ModelStoreTab and passed in.
 */
export default function ModelsTable({
  table,
  tableRows,
  rowVirtualizer,
  tableBodyRef,
  getRowRuntime,
  t,
  // Optional: when provided, the "no matches" empty state offers a one-click
  // way back to the full list instead of a dead end.
  onClearFilters,
}) {
  return (
    <Table className="models-table">
      <div className="ui-table-header models-table__header">
        {table.getHeaderGroups().map((headerGroup) => (
          <React.Fragment key={headerGroup.id}>
            {headerGroup.headers.map((header) => {
              const meta = header.column.columnDef.meta || {};
              const canSort = header.column.getCanSort();
              return (
                <button
                  key={header.id}
                  type="button"
                  className={[
                    'ui-table-header__cell',
                    `ui-table-header__cell--align-${meta.align || 'left'}`,
                    canSort ? 'models-table__sort' : 'models-table__sort--off',
                  ].join(' ')}
                  style={{
                    width: header.column.columnDef.size,
                    flex: header.column.id === 'name' ? '1 1 auto' : '0 0 auto',
                  }}
                  onClick={canSort ? header.column.getToggleSortingHandler() : undefined}
                  disabled={!canSort}
                  title={
                    canSort
                      ? t('models.sort_by', {
                          column: String(header.column.columnDef.header || ''),
                        })
                      : undefined
                  }
                >
                  {flexRender(header.column.columnDef.header, header.getContext())}
                  {header.column.getIsSorted() === 'asc' && (
                    <span className="models-table__sortmark">↑</span>
                  )}
                  {header.column.getIsSorted() === 'desc' && (
                    <span className="models-table__sortmark">↓</span>
                  )}
                </button>
              );
            })}
          </React.Fragment>
        ))}
      </div>
      <div ref={tableBodyRef} className="models-table__body">
        <div className="models-table__virtual" style={{ height: rowVirtualizer.getTotalSize() }}>
          {rowVirtualizer.getVirtualItems().map((virtualRow) => {
            const row = tableRows[virtualRow.index];
            const m = row.original;
            const rt = getRowRuntime(m);
            return (
              <div
                key={row.id}
                className={`models-row ${m.installed ? 'is-ok' : 'is-off'}${rt.unsupported ? ' is-unsupported' : ''}`}
                data-index={virtualRow.index}
                ref={rowVirtualizer.measureElement}
                style={{ transform: `translateY(${virtualRow.start}px)` }}
              >
                {row.getVisibleCells().map((cell) => {
                  const meta = cell.column.columnDef.meta || {};
                  return (
                    <div
                      key={cell.id}
                      className={`models-row__cell ${meta.className || ''}`}
                      style={{
                        width: cell.column.columnDef.size,
                        flex: cell.column.id === 'name' ? '1 1 auto' : '0 0 auto',
                        textAlign: meta.align || undefined,
                      }}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </div>
                  );
                })}
              </div>
            );
          })}
          {tableRows.length === 0 && (
            <div className="models-table__empty">
              <span>{t('models.no_matches')}</span>
              {onClearFilters && (
                <Button
                  size="sm"
                  variant="subtle"
                  className="ml-[8px]"
                  onClick={onClearFilters}
                  data-testid="models-clear-filters"
                >
                  {t('models.clear_filters')}
                </Button>
              )}
            </div>
          )}
        </div>
      </div>
    </Table>
  );
}
