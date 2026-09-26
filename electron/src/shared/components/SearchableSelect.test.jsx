import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import SearchableSelect from './SearchableSelect';

describe('SearchableSelect menu', () => {
  it('shows pinned options only once and keeps keyboard selection aligned', () => {
    const onChange = vi.fn();
    render(
      <SearchableSelect
        options={['Alpha', 'Beta']}
        popular={['Alpha']}
        onChange={onChange}
        ariaLabel="Voice"
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Voice' }));
    expect(screen.getAllByRole('option')).toHaveLength(2);
    expect(screen.getAllByRole('option', { name: 'Alpha' })).toHaveLength(1);
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'ArrowDown' });
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith('Beta');
  });

  it('matches a wide trigger without the old 360px menu cap', () => {
    const { container } = render(
      <SearchableSelect options={['Alpha']} menuPortal ariaLabel="Voice" />,
    );
    vi.spyOn(container.querySelector('.ss-wrap'), 'getBoundingClientRect').mockReturnValue({
      left: 20,
      top: 100,
      bottom: 140,
      width: 700,
    });
    fireEvent.click(screen.getByRole('button', { name: 'Voice' }));
    expect(screen.getByRole('listbox').style.width).toBe('700px');
    expect(screen.getByRole('listbox').className).not.toContain('360px');
  });
});
