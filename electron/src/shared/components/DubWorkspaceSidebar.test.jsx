import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import DubWorkspaceSidebar from './DubWorkspaceSidebar';

describe('Dubbing projects panel', () => {
  it('shows the previous dubs as Projects without separate tabs and preserves actions', () => {
    const item = {
      id: 'dub-1',
      filename: 'Completed dub.mp4',
      duration: 42,
      segments_count: 3,
      job_data: { input_type: 'video' },
    };
    const open = vi.fn();
    const remove = vi.fn();
    render(
      <DubWorkspaceSidebar
        projects={[{ id: 'old', name: 'Old saved list' }]}
        dubHistory={[item]}
        restoreDubHistory={open}
        deleteHistory={remove}
      />,
    );
    expect(screen.getByText('Projects')).toBeVisible();
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
    expect(screen.queryByText('Old saved list')).not.toBeInTheDocument();
    expect(screen.getByText(item.filename)).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: `Open: ${item.filename}` }));
    expect(open).toHaveBeenCalledTimes(1);
    open.mockClear();
    fireEvent.click(screen.getByRole('button', { name: 'Open' }));
    expect(open).toHaveBeenCalledWith(item);
    expect(open).toHaveBeenCalledTimes(1);
    open.mockClear();
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(remove).toHaveBeenCalledWith(item.id, 'dub');
    expect(open).not.toHaveBeenCalled();
  });
});
