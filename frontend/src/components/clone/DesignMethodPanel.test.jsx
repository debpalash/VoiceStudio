import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import DesignMethodPanel from './DesignMethodPanel';
import { PRESETS } from '../../utils/constants';

// #983: "Cannot read properties of undefined (reading 'replace')" — the
// identity panel crashed whenever vdStates was missing one of the 6
// CATEGORIES keys (a design profile saved by an older/foreign client, or a
// stale localStorage shape). The label helper called `val.replace(...)` on
// an undefined category value. This regression-tests the render guard added
// to DesignMethodPanel.jsx directly, independent of the upstream data-shape
// fixes in useProfiles.js / useAppData.js / profiles.py.

// A minimal i18next-compatible mock: returns the defaultValue if given, else
// echoes the key back (mirrors i18next's behavior for a missing translation,
// which is what `optLabel`'s `tl !== tKey` check relies on).
const t = (key, opts) => opts?.defaultValue ?? key;

function setup(vdStates, props = {}) {
  return render(
    <DesignMethodPanel
      t={t}
      describeText=""
      onDescribeChange={vi.fn()}
      describeMatchedAny={false}
      describeUnmatched={[]}
      chipPersonalities={[]}
      activePersonality={null}
      applyPersonality={vi.fn()}
      applyPreset={vi.fn()}
      identityOpen={true}
      setIdentityOpen={vi.fn()}
      identityRecipe="test recipe"
      vdStates={vdStates}
      onVdChange={vi.fn()}
      onChipKeyDown={vi.fn()}
      resetToDescription={vi.fn()}
      showSaveProfile={false}
      setShowSaveProfile={vi.fn()}
      profileName=""
      setProfileName={vi.fn()}
      handleSaveDesignProfile={vi.fn()}
      instruct=""
      language="Auto"
      {...props}
    />,
  );
}

describe('DesignMethodPanel — #983 partial vdStates crash', () => {
  it('does not throw when vdStates is missing 5 of the 6 CATEGORIES keys', () => {
    // Only Gender is set — Age, Pitch, Style, EnglishAccent, ChineseDialect
    // are all undefined, exercising the select-based render path.
    expect(() => setup({ Gender: 'male' })).not.toThrow();
  });

  it('does not throw when vdStates is a fully empty object', () => {
    expect(() => setup({})).not.toThrow();
  });

  it('still renders category labels and the identity recipe with a partial shape', () => {
    const { container } = setup({ Gender: 'male' });
    expect(screen.getByText('test recipe')).toBeInTheDocument();
    expect(container.textContent).toContain('clone.cat_Gender');
  });
});

// #1771 follow-up: the 12-row always-open chip block became one collapsed
// "Details" summary line that expands to a 5-field editor.
describe('DesignMethodPanel — Details collapse/expand', () => {
  it('hides the field editor when collapsed and shows it when expanded', () => {
    const { rerender } = setup({ Gender: 'Auto' }, { identityOpen: false });
    expect(document.getElementById('design-details-fields')).toBeNull();

    rerender(
      <DesignMethodPanel
        t={t}
        describeText=""
        onDescribeChange={vi.fn()}
        describeMatchedAny={false}
        describeUnmatched={[]}
        chipPersonalities={[]}
        activePersonality={null}
        applyPersonality={vi.fn()}
        applyPreset={vi.fn()}
        identityOpen={true}
        setIdentityOpen={vi.fn()}
        identityRecipe="test recipe"
        vdStates={{ Gender: 'Auto' }}
        onVdChange={vi.fn()}
        onChipKeyDown={vi.fn()}
        resetToDescription={vi.fn()}
        showSaveProfile={false}
        setShowSaveProfile={vi.fn()}
        profileName=""
        setProfileName={vi.fn()}
        handleSaveDesignProfile={vi.fn()}
        instruct=""
        language="Auto"
      />,
    );
    expect(document.getElementById('design-details-fields')).not.toBeNull();
  });

  it('toggles identityOpen via the summary button', () => {
    const setIdentityOpen = vi.fn();
    setup({ Gender: 'Auto' }, { identityOpen: false, setIdentityOpen });
    fireEvent.click(screen.getByRole('button', { expanded: false }));
    expect(setIdentityOpen).toHaveBeenCalledTimes(1);
    // The handler is a functional updater — verify it flips the boolean.
    expect(setIdentityOpen.mock.calls[0][0](false)).toBe(true);
  });

  it('never prints the recipe value a second time inside a category header', () => {
    const { container } = setup({ Gender: 'male' }, { identityRecipe: 'male' });
    // The old markup suffixed every header with "· VALUE" (e.g. "· male").
    expect(container.textContent).not.toMatch(/·\s*male/i);
  });
});

// #1771 follow-up item 3: English accent and Chinese dialect merge into one
// <select>, but must still route through the shared onVdChange (-> the real
// applyVdState exclusivity guard in CloneDesignTab) per CATEGORIES key.
describe('DesignMethodPanel — merged accent/dialect field', () => {
  it('routes an English accent pick through onVdChange for EnglishAccent', () => {
    const onVdChange = vi.fn();
    setup({ EnglishAccent: 'Auto', ChineseDialect: 'Auto' }, { identityOpen: true, onVdChange });
    const select = document.getElementById('vd-AccentDialect');
    fireEvent.change(select, { target: { value: 'british accent' } });
    expect(onVdChange).toHaveBeenCalledWith('EnglishAccent', 'british accent');
  });

  it('routes a Chinese dialect pick through onVdChange for ChineseDialect', () => {
    const onVdChange = vi.fn();
    setup({ EnglishAccent: 'Auto', ChineseDialect: 'Auto' }, { identityOpen: true, onVdChange });
    const select = document.getElementById('vd-AccentDialect');
    fireEvent.change(select, { target: { value: '四川话' } });
    expect(onVdChange).toHaveBeenCalledWith('ChineseDialect', '四川话');
  });

  it('picking "Auto" clears whichever side is currently set, not both blindly', () => {
    const onVdChange = vi.fn();
    setup({ EnglishAccent: 'Auto', ChineseDialect: '四川话' }, { identityOpen: true, onVdChange });
    const select = document.getElementById('vd-AccentDialect');
    fireEvent.change(select, { target: { value: 'Auto' } });
    expect(onVdChange).toHaveBeenCalledWith('ChineseDialect', 'Auto');
    expect(onVdChange).not.toHaveBeenCalledWith('EnglishAccent', expect.anything());
  });

  it('shows the currently-set dialect as the merged select value', () => {
    setup({ EnglishAccent: 'Auto', ChineseDialect: '四川话' }, { identityOpen: true });
    const select = document.getElementById('vd-AccentDialect');
    expect(select.value).toBe('四川话');
  });
});

// #1771 follow-up item 5: only the first 5 chips of the COMBINED lane show
// by default — the lane renders personalities (chipPersonalities, dynamic)
// AND the hardcoded PRESETS together (the "ONE preset system" comment in
// DesignMethodPanel.jsx), so both sources must be sliced as one list and the
// overflow count must cover whatever's hidden from EITHER source. Slicing
// only chipPersonalities left PRESETS always fully visible after the
// overflow toggle and undercounted "N more…" by PRESETS.length.
describe('DesignMethodPanel — starting-points chip overflow (combined personalities + PRESETS)', () => {
  const chipPersonalities = Array.from({ length: 7 }, (_, i) => ({
    id: `p${i}`,
    name: `Personality ${i}`,
  }));
  const totalChips = chipPersonalities.length + PRESETS.length;
  const overflow = totalChips - 5;

  it('shows only the first 5 combined chips, with PRESETS not yet visible', () => {
    setup({}, { chipPersonalities });
    for (let i = 0; i < 5; i++) {
      expect(screen.getByText(`Personality ${i}`)).toBeInTheDocument();
    }
    expect(screen.queryByText('Personality 5')).toBeNull();
    expect(screen.queryByText('Personality 6')).toBeNull();
    expect(screen.queryByText(/Authoritative/)).toBeNull(); // first PRESETS chip
  });

  it('counts the overflow across BOTH sources, not just the hidden personalities', () => {
    setup({}, { chipPersonalities });
    // 7 personalities + 6 PRESETS = 13 total, 5 visible -> 8 hidden.
    expect(screen.getByText(`${overflow} more…`)).toBeInTheDocument();
  });

  it('reveals every remaining chip from both sources when the overflow chip is clicked', () => {
    setup({}, { chipPersonalities });
    fireEvent.click(screen.getByText(`${overflow} more…`));
    for (let i = 0; i < 7; i++) {
      expect(screen.getByText(`Personality ${i}`)).toBeInTheDocument();
    }
    expect(screen.getByText(/Authoritative/)).toBeInTheDocument();
    expect(screen.queryByText(/more…/)).toBeNull();
  });

  it('counts PRESETS into the overflow even with zero personalities', () => {
    setup({}, { chipPersonalities: [] });
    // PRESETS alone (6) already exceed the 5-visible slice.
    expect(screen.getByText(`${PRESETS.length - 5} more…`)).toBeInTheDocument();
  });
});
