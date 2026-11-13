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
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'ArrowDown' });
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith('Beta');
  });

  it.each([{ popular: [] }, { popular: ['Beta'] }])(
    'Enter retains the current value with pinned options $popular',
    ({ popular }) => {
      const onChange = vi.fn();
      render(
        <SearchableSelect
          options={['Alpha', 'Beta']}
          value="Beta"
          popular={popular}
          onChange={onChange}
          ariaLabel="Voice"
        />,
      );
      fireEvent.click(screen.getByRole('button', { name: 'Voice' }));
      fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Enter' });
      expect(onChange).toHaveBeenCalledWith('Beta');
    },
  );

  it('exposes keyboard highlight separately from selection and clears it for no matches', () => {
    render(<SearchableSelect options={['Alpha', 'Beta']} value="Beta" ariaLabel="Voice" menuPortal />);
    const trigger = screen.getByRole('button', { name: 'Voice' });
    fireEvent.click(trigger);
    const input = screen.getByRole('combobox', { name: 'Voice' });
    const list = screen.getByRole('listbox');
    expect(input).toHaveAttribute('aria-controls', list.id);
    expect(trigger).toHaveAttribute('aria-controls', list.id);
    expect(input).toHaveAttribute(
      'aria-activedescendant',
      screen.getByRole('option', { name: 'Beta' }).id,
    );
    fireEvent.keyDown(input, { key: 'ArrowUp' });
    expect(input).toHaveAttribute(
      'aria-activedescendant',
      screen.getByRole('option', { name: 'Alpha' }).id,
    );
    expect(screen.getByRole('option', { name: 'Beta' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.change(input, { target: { value: 'absent' } });
    expect(input).not.toHaveAttribute('aria-activedescendant');
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(trigger).toHaveFocus();
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
    expect(screen.getByRole('listbox').parentElement.style.width).toBe('700px');
    expect(screen.getByRole('listbox').parentElement.className).not.toContain('360px');
  });
});
