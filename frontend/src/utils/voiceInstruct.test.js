import { describe, it, expect } from 'vitest';
import {
  buildDesignInstruct,
  instructToFormValue,
  instructToVdStates,
  designModeProfileId,
  applyVdState,
  mergeDescribedAttrs,
} from './voiceInstruct';

// plan-05 (#132): the Voice Design payload must be a validator-safe instruct —
// one valid tag per category, no unsupported free-text — so Synthesize stops
// failing with "Unsupported instruct items" (#115) / "conflicting items within
// the same category" (#114).

describe('buildDesignInstruct', () => {
  it('keeps one valid tag per category from the dropdowns', () => {
    const { instruct, unsupported, duplicates } = buildDesignInstruct(
      {
        Gender: 'male',
        Age: 'middle-aged',
        Pitch: 'low pitch',
        Style: 'Auto',
        EnglishAccent: 'british accent',
        ChineseDialect: 'Auto',
      },
      '',
    );
    expect(instruct.split(', ').sort()).toEqual(
      ['british accent', 'low pitch', 'male', 'middle-aged'].sort(),
    );
    expect(unsupported).toEqual([]);
    expect(duplicates).toEqual([]);
  });

  it('buckets free-text prose as unsupported, not a duplicate (#115)', () => {
    const { instruct, unsupported, duplicates } = buildDesignInstruct(
      { Gender: 'male' },
      'Speak as a calm documentary narrator',
    );
    expect(instruct).toBe('male');
    expect(unsupported).toContain('Speak as a calm documentary narrator');
    expect(duplicates).toEqual([]);
  });

  it('clone path (#612): non-EN/ZH free-text yields no instruct, all items flagged unsupported', () => {
    // The clone synthesize path runs free-text through buildDesignInstruct({}, …)
    // exactly like this. A Vietnamese description must NOT reach the backend (it
    // 400s with "Unsupported instruct items"); it drops to "" + a warn bucket so
    // the UI shows a localized toast and synthesis still proceeds (no style).
    const { instruct, unsupported } = buildDesignInstruct({}, 'quảng cáo, sôi nổi và thu hút');
    expect(instruct).toBe('');
    expect(unsupported).toEqual(['quảng cáo', 'sôi nổi và thu hút']);
  });

  it('clone path keeps valid style tags while dropping prose in the same field', () => {
    const { instruct, unsupported } = buildDesignInstruct({}, 'whisper, sôi nổi');
    expect(instruct).toBe('whisper');
    expect(unsupported).toEqual(['sôi nổi']);
  });

  it('clone path (#980): RTL/non-Latin script (Hebrew) is dropped like any other unsupported free-text, not a crash', () => {
    // #612 covered Latin-script-with-diacritics (Vietnamese); #980 was the same
    // failure mode with a right-to-left script — a name typed into the Style
    // field must degrade the same way (dropped client-side + unsupported bucket),
    // never round-trip to the backend's 400.
    const { instruct, unsupported } = buildDesignInstruct({}, 'שמואל');
    expect(instruct).toBe('');
    expect(unsupported).toEqual(['שמואל']);
  });

  it('clone path (#980): RTL script mixed with a valid tag keeps the tag, drops the rest', () => {
    const { instruct, unsupported } = buildDesignInstruct({}, 'whisper, שמואל');
    expect(instruct).toBe('whisper');
    expect(unsupported).toEqual(['שמואל']);
  });

  it('buckets a valid tag outranked by a dropdown as a duplicate, not unsupported (#114)', () => {
    const { instruct, unsupported, duplicates } = buildDesignInstruct(
      { Pitch: 'low pitch' },
      'high pitch',
    );
    expect(instruct).toBe('low pitch'); // dropdown wins the category
    expect(duplicates).toContain('high pitch');
    expect(unsupported).toEqual([]);
  });

  it('accepts a valid free-text tag when its category is open', () => {
    const { instruct } = buildDesignInstruct({ Gender: 'male' }, 'whisper');
    expect(instruct.split(', ').sort()).toEqual(['male', 'whisper'].sort());
  });

  it('normalises casing and full-width commas in free-text', () => {
    const { instruct } = buildDesignInstruct({}, 'MALE，WHISPER');
    expect(instruct.split(', ').sort()).toEqual(['male', 'whisper'].sort());
  });

  it('ignores Auto and empty input', () => {
    expect(buildDesignInstruct({ Gender: 'Auto', Age: 'Auto' }, '').instruct).toBe('');
    expect(buildDesignInstruct({}, '').instruct).toBe('');
  });

  it('does not count an unknown dropdown value as unsupported free-text', () => {
    // CATEGORIES↔dropdown drift: warned in dev, excluded from instruct, NOT a
    // free-text "unsupported" item.
    const { instruct, unsupported } = buildDesignInstruct({ Gender: 'nonbinary' }, '');
    expect(instruct).toBe('');
    expect(unsupported).toEqual([]);
  });

  // #1771: the engine (omnivoice/models/omnivoice.py::_resolve_instruct)
  // rejects an instruct that sets both an English accent and a Chinese
  // dialect: "Cannot mix Chinese dialect and English accent in a single
  // instruct." Before this fix, EnglishAccent and ChineseDialect were
  // independent CATEGORIES entries with no cross-check, so the picker could
  // (and did, per the bug report) build exactly that payload.
  describe('dialect/accent exclusivity (#1771)', () => {
    it('drops a dropdown-set dialect when a dropdown-set accent already claimed the group', () => {
      const { instruct, conflicts } = buildDesignInstruct(
        { EnglishAccent: 'indian accent', ChineseDialect: '四川话' },
        '',
      );
      expect(instruct).toBe('indian accent');
      expect(conflicts).toEqual(['四川话']);
    });

    it('drops a free-text dialect when a dropdown accent already claimed the group', () => {
      const { instruct, conflicts } = buildDesignInstruct(
        { EnglishAccent: 'british accent' },
        '四川话',
      );
      expect(instruct).toBe('british accent');
      expect(conflicts).toContain('四川话');
    });

    it('drops a free-text accent when a dropdown dialect already claimed the group', () => {
      const { instruct, conflicts } = buildDesignInstruct(
        { ChineseDialect: '东北话' },
        'american accent',
      );
      expect(instruct).toBe('东北话');
      expect(conflicts).toContain('american accent');
    });

    it('drops the second of two free-text conflicting items, keeping the first', () => {
      const { instruct, conflicts, unsupported } = buildDesignInstruct({}, 'indian accent, 四川话');
      expect(instruct).toBe('indian accent');
      expect(conflicts).toEqual(['四川话']);
      expect(unsupported).toEqual([]);
    });

    it('does not conflict with itself when only one of the pair is set', () => {
      const { instruct, conflicts } = buildDesignInstruct({ ChineseDialect: '四川话' }, '');
      expect(instruct).toBe('四川话');
      expect(conflicts).toEqual([]);
    });
  });
});

describe('instructToVdStates', () => {
  const allAuto = {
    Gender: 'Auto',
    Age: 'Auto',
    Pitch: 'Auto',
    Style: 'Auto',
    EnglishAccent: 'Auto',
    ChineseDialect: 'Auto',
  };

  it('returns a complete shape and replaces every stale category', () => {
    const stale = {
      Gender: 'male',
      Age: 'elderly',
      Pitch: 'low pitch',
      Style: 'whisper',
      EnglishAccent: 'british accent',
      ChineseDialect: 'Auto',
    };

    const replacement = instructToVdStates('female, high pitch');

    expect({ ...stale, ...replacement }).toEqual({
      ...allAuto,
      Gender: 'female',
      Pitch: 'high pitch',
    });
  });

  it('ignores unknown and conflicting tokens while keeping the first valid category value', () => {
    expect(
      instructToVdStates('MALE, female, cinematic, high pitch, very low pitch, whisper'),
    ).toEqual({
      ...allAuto,
      Gender: 'male',
      Pitch: 'high pitch',
      Style: 'whisper',
    });
  });

  it('accepts a full-width Chinese comma and defaults omitted categories to Auto', () => {
    expect(instructToVdStates('female，young adult，american accent')).toEqual({
      ...allAuto,
      Gender: 'female',
      Age: 'young adult',
      EnglishAccent: 'american accent',
    });
  });

  it('returns the complete all-Auto shape for empty or unusable input', () => {
    expect(instructToVdStates('')).toEqual(allAuto);
    expect(instructToVdStates('unknown prose')).toEqual(allAuto);
    expect(instructToVdStates(null)).toEqual(allAuto);
  });

  // #1771: a legacy/imported/hand-edited instruct string can carry both an
  // accent and a dialect — restoring it must not resurrect a picker state the
  // engine will reject on next Synthesize.
  it('keeps only the first-seen of a conflicting accent/dialect pair (#1771)', () => {
    expect(instructToVdStates('indian accent, 四川话')).toEqual({
      ...allAuto,
      EnglishAccent: 'indian accent',
    });
    expect(instructToVdStates('四川话, indian accent')).toEqual({
      ...allAuto,
      ChineseDialect: '四川话',
    });
  });
});

describe('applyVdState (#1771 — live picker guard)', () => {
  const allAuto = {
    Gender: 'Auto',
    Age: 'Auto',
    Pitch: 'Auto',
    Style: 'Auto',
    EnglishAccent: 'Auto',
    ChineseDialect: 'Auto',
  };

  it('picking a dialect clears an already-set accent and reports it', () => {
    const { vdStates, clearedCategory } = applyVdState(
      { ...allAuto, EnglishAccent: 'british accent' },
      'ChineseDialect',
      '四川话',
    );
    expect(vdStates).toEqual({ ...allAuto, ChineseDialect: '四川话', EnglishAccent: 'Auto' });
    expect(clearedCategory).toBe('EnglishAccent');
  });

  it('picking an accent clears an already-set dialect and reports it', () => {
    const { vdStates, clearedCategory } = applyVdState(
      { ...allAuto, ChineseDialect: '东北话' },
      'EnglishAccent',
      'korean accent',
    );
    expect(vdStates).toEqual({
      ...allAuto,
      EnglishAccent: 'korean accent',
      ChineseDialect: 'Auto',
    });
    expect(clearedCategory).toBe('ChineseDialect');
  });

  it('does not clear anything for non-exclusive categories or Auto', () => {
    expect(applyVdState(allAuto, 'Gender', 'male').clearedCategory).toBeNull();
    expect(
      applyVdState({ ...allAuto, EnglishAccent: 'british accent' }, 'ChineseDialect', 'Auto')
        .clearedCategory,
    ).toBeNull();
  });
});

describe('mergeDescribedAttrs dialect/accent exclusivity (#1771)', () => {
  // Covers the "restored preset" / "saved profile" / "imported project"
  // routes: hooks/useProfiles.js and hooks/useAppData.js both feed raw,
  // externally-sourced attrs through mergeDescribedAttrs to rebuild vdStates.
  it('keeps only the first CATEGORIES-order pick when external attrs carry both', () => {
    expect(
      mergeDescribedAttrs({ EnglishAccent: 'russian accent', ChineseDialect: '甘肃话' }),
    ).toEqual({
      Gender: 'Auto',
      Age: 'Auto',
      Pitch: 'Auto',
      Style: 'Auto',
      EnglishAccent: 'russian accent',
      ChineseDialect: 'Auto',
    });
  });
});

describe('designModeProfileId (#674 — clone must not hijack design attributes)', () => {
  const profiles = [
    { id: 'clone1', name: 'My Clone' }, // no instruct → clone
    { id: 'clone2', name: 'Demo', instruct: '' }, // empty instruct → clone
    { id: 'design1', name: 'Narrator', instruct: 'male, low pitch' }, // design
  ];

  it('suppresses a known clone profile (so gender/timbre comes from the attributes)', () => {
    expect(designModeProfileId('clone1', profiles)).toBeNull();
    expect(designModeProfileId('clone2', profiles)).toBeNull();
  });

  it('forwards a design profile (re-render a designed voice)', () => {
    expect(designModeProfileId('design1', profiles)).toBe('design1');
  });

  it('omits when nothing is selected; passes through an unknown id (profiles not loaded)', () => {
    expect(designModeProfileId('', profiles)).toBeNull();
    expect(designModeProfileId(null, profiles)).toBeNull();
    expect(designModeProfileId('not-loaded-yet', [])).toBe('not-loaded-yet');
    expect(designModeProfileId('x', undefined)).toBe('x');
  });
});

describe('instructToFormValue (#550 [object Object] guard)', () => {
  it('extracts the string from a buildDesignInstruct() object, never "[object Object]"', () => {
    const built = buildDesignInstruct({ Gender: 'male' }, '');
    // the bug: appending the raw object to FormData string-coerces to this
    expect(String(built)).toBe('[object Object]');
    expect(typeof instructToFormValue(built)).toBe('string');
    expect(instructToFormValue(built)).toBe('male');
    expect(instructToFormValue(built)).not.toBe('[object Object]');
  });

  it('passes a plain string through and coerces null/garbage to ""', () => {
    expect(instructToFormValue('male, high pitch')).toBe('male, high pitch');
    expect(instructToFormValue(null)).toBe('');
    expect(instructToFormValue(undefined)).toBe('');
    expect(instructToFormValue({})).toBe('');
  });
});
