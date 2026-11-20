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

  it('opens the menu from the keyboard on the collapsed trigger', () => {
    const onChange = vi.fn();
    render(
      <SearchableSelect options={['Alpha', 'Beta']} value="Beta" onChange={onChange} ariaLabel="Voice" />,
    );
    const trigger = screen.getByRole('button', { name: 'Voice' });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('listbox')).toBeNull();

    trigger.focus();
    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    expect(screen.getByRole('listbox')).toBeVisible();
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    // The highlight was set before opening, so Enter commits the opened row.
    fireEvent.keyDown(screen.getByRole('combobox', { name: 'Voice' }), { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith('Beta');
  });

  it('wraps to the last option on ArrowUp from the collapsed trigger', () => {
    const onChange = vi.fn();
    render(
      <SearchableSelect options={['Alpha', 'Beta']} value="Alpha" onChange={onChange} ariaLabel="Voice" />,
    );
    const trigger = screen.getByRole('button', { name: 'Voice' });
    trigger.focus();
    fireEvent.keyDown(trigger, { key: 'ArrowUp' });
    fireEvent.keyDown(screen.getByRole('combobox', { name: 'Voice' }), { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith('Beta');
  });

  it('commits the first option on ArrowDown when nothing is selected', () => {
    const onChange = vi.fn();
    render(<SearchableSelect options={['Alpha', 'Beta']} onChange={onChange} ariaLabel="Voice" />);
    const trigger = screen.getByRole('button', { name: 'Voice' });
    trigger.focus();
    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    fireEvent.keyDown(screen.getByRole('combobox', { name: 'Voice' }), { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith('Alpha');
  });

  it('leaves a disabled trigger inert', () => {
    const onChange = vi.fn();
    render(<SearchableSelect options={['Alpha']} disabled onChange={onChange} ariaLabel="Voice" />);
    const trigger = screen.getByRole('button', { name: 'Voice' });
    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
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


// The menu renders at most MAX_DISPLAY (200) rows. A saved value past the cap has
// no row of its own, so the opening highlight used to fall back to the first row
// and Enter replaced the saved value without the user choosing anything. The
// committed row is kept in the list, so the arrow keys start on it.
it('starts from a saved value that is past the display cap', () => {
  const options = Array.from({ length: 250 }, (_, index) => `Language ${index}`);
  const onChange = vi.fn();
  render(
    <SearchableSelect options={options} value="Language 240" onChange={onChange} ariaLabel="Voice" />,
  );
  const trigger = screen.getByRole('button', { name: 'Voice' });
  trigger.focus();

  fireEvent.keyDown(trigger, { key: 'ArrowDown' });
  const input = screen.getByRole('combobox', { name: 'Voice' });
  const saved = screen.getByRole('option', { name: 'Language 240' });
  expect(input).toHaveAttribute('aria-activedescendant', saved.id);

  fireEvent.keyDown(input, { key: 'Enter' });
  expect(onChange).toHaveBeenCalledWith('Language 240');
});

it('opens an empty picker from the keyboard while its list is still loading', () => {
  // A picker that fetches options when it opens (the gallery, for example) has no
  // rows yet; a click already opened it, and the keyboard should too.
  render(<SearchableSelect options={[]} ariaLabel="Voice" />);
  const trigger = screen.getByRole('button', { name: 'Voice' });
  trigger.focus();
  expect(screen.queryByRole('listbox')).toBeNull();

  fireEvent.keyDown(trigger, { key: 'ArrowDown' });
  expect(screen.getByRole('listbox')).toBeVisible();
});


// A value that is both selected and pinned is rendered by the pinned group; adding
// it to the main list again would show it twice and change where the arrows land.
it('shows a selected option that is also pinned only once', () => {
  const onChange = vi.fn();
  render(
    <SearchableSelect
      options={['Alpha', 'Beta']}
      value="Alpha"
      popular={['Alpha']}
      onChange={onChange}
      ariaLabel="Voice"
    />,
  );
  const trigger = screen.getByRole('button', { name: 'Voice' });
  trigger.focus();
  fireEvent.keyDown(trigger, { key: 'ArrowDown' });
  expect(screen.getAllByRole('option', { name: 'Alpha' })).toHaveLength(1);

  const input = screen.getByRole('combobox', { name: 'Voice' });
  fireEvent.keyDown(input, { key: 'ArrowDown' });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(onChange).toHaveBeenCalledWith('Beta');
});
