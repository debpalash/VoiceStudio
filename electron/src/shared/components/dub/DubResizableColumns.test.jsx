import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import DubResizableColumns from './DubResizableColumns';

describe('DubResizableColumns', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.dir = '';
  });

  it('lets keyboard users enlarge the transcript column and persists the split', () => {
    const { unmount } = render(
      <DubResizableColumns resizeLabel="Resize video and transcript columns">
        <div>Video</div>
        <div>Transcript</div>
      </DubResizableColumns>,
    );
    const separator = screen.getByRole('separator');

    fireEvent.keyDown(separator, { key: 'ArrowLeft' });

    expect(separator).toHaveAttribute('aria-valuenow', '39');
    expect(separator.parentElement).toHaveStyle({ gridTemplateColumns: '39fr 12px 61fr' });
    unmount();

    render(
      <DubResizableColumns resizeLabel="Resize video and transcript columns">
        <div>Video</div>
        <div>Transcript</div>
      </DubResizableColumns>,
    );
    expect(screen.getByRole('separator')).toHaveAttribute('aria-valuenow', '39');
  });

  it('clamps pointer resizing so both editors remain usable', () => {
    render(
      <DubResizableColumns resizeLabel="Resize video and transcript columns">
        <div>Video</div>
        <div>Transcript</div>
      </DubResizableColumns>,
    );
    const separator = screen.getByRole('separator');
    separator.parentElement.getBoundingClientRect = () => ({ left: 100, width: 1000 });

    fireEvent.pointerDown(separator, { pointerId: 1, clientX: 500 });
    fireEvent.pointerMove(separator, { pointerId: 1, clientX: 50 });
    fireEvent.pointerUp(separator, { pointerId: 1, clientX: 50 });

    expect(separator).toHaveAttribute('aria-valuenow', '25');
  });

  it('follows physical pointer and arrow direction in RTL layouts', () => {
    document.documentElement.dir = 'rtl';
    render(
      <DubResizableColumns resizeLabel="Resize video and transcript columns">
        <div>Video</div>
        <div>Transcript</div>
      </DubResizableColumns>,
    );
    const separator = screen.getByRole('separator');
    separator.parentElement.getBoundingClientRect = () => ({ left: 100, right: 1100, width: 1000 });

    fireEvent.keyDown(separator, { key: 'ArrowLeft' });
    expect(separator).toHaveAttribute('aria-valuenow', '49');

    fireEvent.pointerDown(separator, { pointerId: 1, clientX: 800 });
    fireEvent.pointerMove(separator, { pointerId: 1, clientX: 500 });
    fireEvent.pointerUp(separator, { pointerId: 1, clientX: 500 });
    expect(separator).toHaveAttribute('aria-valuenow', '60');
  });
});
