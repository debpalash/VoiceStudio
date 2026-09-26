import React, { useMemo, useRef, useState } from 'react';
import { getCoreRowModel, getSortedRowModel, useReactTable } from '@tanstack/react-table';
import { useVirtualizer } from '@tanstack/react-virtual';
import { Eye, EyeOff } from 'lucide-react';
import { Button } from '../../../ui';
import ModelsTable from './ModelsTable';

/**
 * One role section of the grouped Model Store catalog (TTS / ASR / Dictation /
 * Diarisation). Hosts its own table instance + virtualizer over the section's
 * rows; platform-incompatible rows (`supported === false`) stay behind a
 * per-section "Show incompatible (N)" toggle (default collapsed) instead of
 * rendering greyed-out inline. All per-row functionality (install / delete /
 * progress / incomplete-repair) rides the shared column definitions.
 */
export default function ModelSection({ sectionKey, title, group, columns, getRowRuntime, t }) {
  const { compatible, incompatible } = group;
  const [showIncompatible, setShowIncompatible] = useState(false);
  const [sorting, setSorting] = useState([]);
  const tableBodyRef = useRef(null);

  const data = useMemo(
    () => (showIncompatible ? [...compatible, ...incompatible] : compatible),
    [compatible, incompatible, showIncompatible],
  );

  const table = useReactTable({
    data,
    columns,
    getRowId: (row) => row.repo_id,
    state: {
      sorting,
      // Inside a role section the Role column is redundant noise — hide it.
      columnVisibility: { role: false },
    },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  const tableRows = table.getRowModel().rows;
  const rowVirtualizer = useVirtualizer({
    count: tableRows.length,
    getScrollElement: () => tableBodyRef.current,
    estimateSize: () => 40,
    overscan: 8,
  });

  const installed = group.models.filter((m) => m.installed).length;

  return (
    <div className="mt-[var(--space-2)]" data-testid={`models-section-${sectionKey}`}>
      <div className="mb-[4px] flex items-baseline gap-[var(--space-2)] px-[2px]">
        <span className="text-[length:var(--text-sm)] font-semibold text-[var(--chrome-fg)]">
          {title}
        </span>
        <span className="font-[family-name:var(--chrome-font-mono)] text-[length:var(--text-2xs)] text-[var(--chrome-fg-dim)]">
          {installed}/{group.models.length}
        </span>
        <span className="flex-1" />
        {incompatible.length > 0 && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setShowIncompatible((v) => !v)}
            leading={showIncompatible ? <EyeOff size={11} /> : <Eye size={11} />}
            title={t('models.incompatible_title')}
            data-testid={`models-incompatible-toggle-${sectionKey}`}
          >
            {showIncompatible
              ? t('models.hide_incompatible', { count: incompatible.length })
              : t('models.show_incompatible', { count: incompatible.length })}
          </Button>
        )}
      </div>
      {tableRows.length > 0 && (
        <ModelsTable
          table={table}
          tableRows={tableRows}
          rowVirtualizer={rowVirtualizer}
          tableBodyRef={tableBodyRef}
          getRowRuntime={getRowRuntime}
          t={t}
        />
      )}
    </div>
  );
}
