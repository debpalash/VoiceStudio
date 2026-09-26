import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import i18n from '../i18n';
import ModelsTable from '../components/settings/models/ModelsTable';

const t = i18n.t.bind(i18n);

// Minimal stubs — the empty state renders regardless of the virtualizer,
// which yields no items in jsdom anyway.
const stubTable = { getHeaderGroups: () => [] };
const stubVirtualizer = {
  getTotalSize: () => 0,
  getVirtualItems: () => [],
  measureElement: () => {},
};

function renderEmptyTable(props = {}) {
  return render(
    <ModelsTable
      table={stubTable}
      tableRows={[]}
      rowVirtualizer={stubVirtualizer}
      tableBodyRef={{ current: null }}
      getRowRuntime={() => ({})}
      t={t}
      {...props}
    />,
  );
}

describe('ModelsTable — actionable empty state', () => {
  it('invites the user back with "Clear filters" when nothing matches', () => {
    const onClearFilters = vi.fn();
    renderEmptyTable({ onClearFilters });
    expect(screen.getByText(t('models.no_matches'))).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('models-clear-filters'));
    expect(onClearFilters).toHaveBeenCalledTimes(1);
  });

  it('renders the plain empty message for legacy callers without the action', () => {
    renderEmptyTable();
    expect(screen.getByText(t('models.no_matches'))).toBeInTheDocument();
    expect(screen.queryByTestId('models-clear-filters')).not.toBeInTheDocument();
  });
});
