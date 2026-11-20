import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import SearchableSelect from './SearchableSelect';

describe('SearchableSelect menu', () => {
  // The recents cases below seed localStorage; keep them from leaking into the
  // cases that assert what an empty store renders.
  beforeEach(() => window.localStorage.clear());

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

  // A recents key is raw localStorage: an older build, a foreign app on the same
  // origin or a hand-edited devtools value can leave any JSON value there. The
  // component iterates the parsed value during render, so a non-array used to be
  // a TypeError that the error boundary turned into the crash screen.
  it.each([['a number', '5'], ['an object', '{"Alpha":1}'], ['a bare string', '"Alpha"']])(
    'renders instead of crashing when the stored recents value is %s',
    (_label, stored) => {
      window.localStorage.setItem('test.recents.invalid', stored);
      render(
        <SearchableSelect
          options={['Alpha', 'Beta']}
          recentsKey="test.recents.invalid"
          ariaLabel="Voice"
        />,
      );
      fireEvent.click(screen.getByRole('button', { name: 'Voice' }));
      expect(screen.getAllByRole('option')).toHaveLength(2);
    },
  );

  it('keeps only string entries from a mixed recents array', () => {
    window.localStorage.setItem(
      'test.recents.mixed',
      JSON.stringify(['Alpha', 7, null, { v: 'Beta' }]),
    );
    render(
      <SearchableSelect
        options={['Alpha', 'Beta']}
        recentsKey="test.recents.mixed"
        ariaLabel="Voice"
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Voice' }));
    expect(screen.getAllByRole('option', { name: 'Alpha' })).toHaveLength(1);
    expect(screen.getAllByRole('option', { name: 'Beta' })).toHaveLength(1);
  });

  it('stores only string entries after a selection on top of a bad recents value', () => {
    window.localStorage.setItem('test.recents.write', JSON.stringify(['Alpha', 7, null]));
    render(
      <SearchableSelect
        options={['Alpha', 'Beta']}
        recentsKey="test.recents.write"
        ariaLabel="Voice"
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Voice' }));
    fireEvent.mouseDown(screen.getByRole('option', { name: 'Beta' }));
    expect(JSON.parse(window.localStorage.getItem('test.recents.write'))).toEqual(['Beta', 'Alpha']);
  });

  it('writes a fresh list when the stored recents value is a bare string', () => {
    window.localStorage.setItem('test.recents.bare', '"Alpha"');
    render(
      <SearchableSelect
        options={['Alpha', 'Beta']}
        recentsKey="test.recents.bare"
        ariaLabel="Voice"
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Voice' }));
    fireEvent.mouseDown(screen.getByRole('option', { name: 'Beta' }));
    expect(JSON.parse(window.localStorage.getItem('test.recents.bare'))).toEqual(['Beta']);
  });

  it('ignores a recents entry that is not one of the options', () => {
    window.localStorage.setItem('test.recents.stale', JSON.stringify(['Ghost', 'Alpha']));
    render(
      <SearchableSelect
        options={['Alpha', 'Beta']}
        recentsKey="test.recents.stale"
        ariaLabel="Voice"
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Voice' }));
    expect(screen.queryByRole('option', { name: 'Ghost' })).toBeNull();
    expect(screen.getAllByRole('option')).toHaveLength(2);
  });
});
