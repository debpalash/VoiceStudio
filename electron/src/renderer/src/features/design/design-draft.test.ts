import { beforeEach, describe, expect, it } from 'vitest';
import type { HistoryItem, Profile } from '@/lib/api/types';
import { mergeDescribedAttrs } from '@shared/utils/voiceInstruct';
import {
  applyDescription,
  canResetToDescription,
  designDraftFromTake,
  designInstruct,
  designRecipe,
  editVoice,
  designRequestSeed,
  linkedDesignProfile,
  pickDetail,
  readDraft,
  replaceRecipe,
  restoreDesignProfile,
  STORAGE,
} from './design-draft';

const profile = {
  id: 'designed-voice',
  name: 'Narrator',
  kind: 'design',
  ref_audio_path: 'preview.wav',
  ref_text: null,
  instruct: 'female, low pitch',
  language: 'French',
  seed: 42,
  personality: null,
  vd_states: JSON.stringify({ Gender: 'male' }),
  created_at: 1,
  is_locked: false,
} satisfies Profile;

describe('designed voice drafts', () => {
  beforeEach(() => localStorage.clear());

  it('restores the native free-form prompt and explicit picks from a saved voice', () => {
    const description = 'a gentle, slightly raspy Japanese narrator';
    const native = {
      ...profile,
      design_recipe: JSON.stringify({
        description,
        picks: { Pitch: 'low pitch' },
        mapped: { Gender: 'female', Age: 'young adult' },
        engine: 'voxcpm2',
      }),
    };
    const restored = restoreDesignProfile(native, 9);
    expect(restored).toMatchObject({
      description,
      profileId: profile.id,
      picks: { Pitch: { value: 'low pitch', description } },
      mapped: { Gender: 'female', Age: 'young adult' },
    });
    const reopened = replaceRecipe(readDraft(), restored);
    expect(reopened.description).toBe(description);
    expect(reopened.profileId).toBe(profile.id);
    expect(designInstruct(reopened, 'freeform')).toBe(description + ', low pitch');
  });

  it('persists the selected profile identity with the draft', () => {
    localStorage.setItem(
      STORAGE,
      JSON.stringify({ text: 'Hello', attrs: { Gender: 'female' }, seed: 7, profileId: 'voice' }),
    );
    expect(readDraft()).toMatchObject({ text: 'Hello', seed: 7, profileId: 'voice' });
  });

  it('restores profile language, seed and a complete recipe without stale values', () => {
    const restored = restoreDesignProfile(profile, 9);
    expect(restored).toMatchObject({
      profileId: 'designed-voice',
      language: 'French',
      seed: 42,
      attrs: { Gender: 'male', Pitch: 'low pitch', Age: 'Auto' },
    });

    expect(
      restoreDesignProfile({ ...profile, vd_states: '{broken', seed: null, language: null }, 9),
    ).toMatchObject({
      language: 'Auto',
      seed: 9,
      attrs: { Gender: 'female', Pitch: 'low pitch' },
    });
  });
});

describe('saved design voice link', () => {
  // A linked draft sends its profile, and the backend re-renders the saved
  // sample. Edits must unlink it, or new attributes, gender and seed are
  // barely heard ("always a female voice", reported on Discord).
  const restored = () => {
    const saved = restoreDesignProfile(profile, 9);
    return replaceRecipe(readDraft(), {
      attrs: saved.attrs,
      seed: saved.seed,
      profileId: saved.profileId,
    });
  };

  it('re-renders the saved voice while the draft is unchanged', () => {
    const draft = restored();
    expect(draft.profileId).toBe('designed-voice');
    expect(linkedDesignProfile(draft, [profile])?.id).toBe('designed-voice');
  });

  it('unlinks on a detail, seed or description edit', () => {
    const draft = restored();
    expect(pickDetail(draft, 'Gender', 'female').draft.profileId).toBeNull();
    expect(editVoice(draft, { seed: 43 }).profileId).toBeNull();
    expect(editVoice(draft, { description: 'a deep voice' }).profileId).toBeNull();
  });

  it('stays linked when a value is set to what it already is', () => {
    const draft = restored();
    expect(pickDetail(draft, 'Gender', 'male').draft.profileId).toBe('designed-voice');
    expect(editVoice(draft, { seed: 42 }).profileId).toBe('designed-voice');
  });

  it('starts an unsaved design from a preset, personality or demo', () => {
    expect(replaceRecipe(restored(), { attrs: { Gender: 'female' } }).profileId).toBeNull();
  });

  it('does not send a profile the draft no longer matches (stale persisted drafts)', () => {
    const draft = restored();
    expect(linkedDesignProfile({ ...draft, seed: 7 }, [profile])).toBeNull();
    expect(
      linkedDesignProfile({ ...draft, attrs: { ...draft.attrs, Gender: 'female' } }, [profile]),
    ).toBeNull();
    expect(linkedDesignProfile(draft, [{ ...profile, kind: 'clone' }])).toBeNull();
    expect(linkedDesignProfile(draft, undefined)).toBeNull();
  });

  it('keeps a seedless saved voice seedless so it re-renders instead of redesigning', () => {
    const seedless = { ...profile, seed: null };
    const draft = { ...restored(), seed: 9 };
    expect(linkedDesignProfile(draft, [seedless])?.id).toBe('designed-voice');
    expect(designRequestSeed(draft, seedless)).toBeUndefined();
    expect(designRequestSeed(draft, profile)).toBe(9);
    expect(designRequestSeed(draft, null)).toBe(9);
  });
});

function historyRow(overrides: Partial<HistoryItem> = {}): HistoryItem {
  return {
    id: 'row-9',
    text: 'Opening line',
    mode: 'design',
    language: 'English',
    instruct: null,
    profile_id: null,
    audio_path: 'row-9.wav',
    duration_seconds: 3,
    generation_time: 2,
    seed: 77,
    starred: 0,
    created_at: 100,
    ...overrides,
  };
}

/** Fresh draft with `text` as its description, already mapped to `mapping`. */
function mappedDraft(text: string, mapping: Record<string, string>) {
  localStorage.clear();
  const typed = editVoice(readDraft(), { description: text });
  return applyDescription(typed, mapping, text);
}

describe('designInstruct', () => {
  it.each([
    ['tags', 'female, young adult'],
    ['freeform', 'a bright storyteller'],
  ] as const)('%s engines read their own form', (vocabulary, expected) => {
    const draft = mappedDraft('a bright storyteller', { Gender: 'female', Age: 'young adult' });
    expect(designInstruct(draft, vocabulary)).toBe(expected);
  });

  it('adds only hand-picked details after the prose for free-form engines', () => {
    const base = mappedDraft('  a bright storyteller  ', { Gender: 'female' });
    const picked = pickDetail(base, 'Style', 'whisper').draft;
    expect(designInstruct(picked, 'freeform')).toBe('a bright storyteller, whisper');
    expect(designInstruct(picked, 'tags')).toBe('female, whisper');
  });

  it('can consist of picks alone', () => {
    let draft = mappedDraft('   ', {});
    for (const [category, value] of [
      ['Age', 'teenager'],
      ['Gender', 'male'],
    ] as const) {
      draft = pickDetail(draft, category, value).draft;
    }
    expect(designInstruct(draft, 'freeform')).toBe('teenager, male');
  });

  it('repeats a pick even when it equals what the text mapped to', () => {
    const draft = pickDetail(mappedDraft('a gent', { Gender: 'male' }), 'Gender', 'male').draft;
    expect(designInstruct(draft, 'freeform')).toBe('a gent, male');
  });
});

describe('how picks and the description interact', () => {
  it('a pick survives a rewording that is silent about it', () => {
    const picked = pickDetail(
      mappedDraft('a guide', { Gender: 'female' }),
      'Pitch',
      'high pitch',
    ).draft;
    const reworded = editVoice(picked, { description: 'a friendly guide' });
    const merged = applyDescription(reworded, { Gender: 'female' }, 'a friendly guide');
    expect(merged.attrs).toMatchObject({ Gender: 'female', Pitch: 'high pitch' });
    expect(Object.keys(merged.picks)).toEqual(['Pitch']);
  });

  it('a rewording that speaks to the same detail wins over an old pick', () => {
    const picked = pickDetail(mappedDraft('a guide', {}), 'Age', 'teenager').draft;
    const reworded = editVoice(picked, { description: 'a retired guide' });
    const merged = applyDescription(reworded, { Age: 'elderly' }, 'a retired guide');
    expect(merged.attrs.Age).toBe('elderly');
    expect(merged.picks).toEqual({});
  });

  it('an old accent pick yields to a dialect the new text implies', () => {
    const picked = pickDetail(mappedDraft('a guide', {}), 'EnglishAccent', 'indian accent').draft;
    const reworded = editVoice(picked, { description: 'a guide from Chengdu' });
    const merged = applyDescription(reworded, { ChineseDialect: '四川话' }, 'a guide from Chengdu');
    expect(merged.attrs.ChineseDialect).toBe('四川话');
    expect(merged.attrs.EnglishAccent).toBe('Auto');
    expect(merged.picks).toEqual({});
  });

  it('a mapping that arrives late cannot overrule a pick made under the same text', () => {
    const picked = pickDetail(
      editVoice(readDraft(), { description: 'a guide' }),
      'Gender',
      'male',
    ).draft;
    const merged = applyDescription(picked, { Gender: 'female' }, 'a guide');
    expect([merged.attrs.Gender, merged.picks.Gender?.value, merged.mapped.Gender]).toEqual([
      'male',
      'male',
      'female',
    ]);
  });

  it('picking across an exclusive pair names the casualty and forgets its pick', () => {
    localStorage.clear();
    const accent = pickDetail(readDraft(), 'EnglishAccent', 'american accent').draft;
    const { draft, clearedCategory } = pickDetail(accent, 'ChineseDialect', '东北话');
    expect(clearedCategory).toBe('EnglishAccent');
    expect(Object.keys(draft.picks)).toEqual(['ChineseDialect']);
    expect(draft.attrs.EnglishAccent).toBe('Auto');
  });
});

describe('designDraftFromTake', () => {
  it('preserves zero as a stored take seed', () => {
    expect(designDraftFromTake(historyRow({ seed: 0 })).seed).toBe(0);
  });

  it('treats a take with no recipe as prose whose recognised tags become details', () => {
    const draft = designDraftFromTake(historyRow({ instruct: 'male, very low pitch' }));
    expect(draft.attrs).toMatchObject({ Gender: 'male', Pitch: 'very low pitch' });
    expect(draft.picks).toEqual({});
    expect(draft.seed).toBe(77);
    expect(designInstruct(draft, 'tags')).toBe('male, very low pitch');
    expect(designInstruct(draft, 'freeform')).toBe('male, very low pitch');
  });

  it('separates recognised tags from prose between the two vocabularies', () => {
    const draft = designDraftFromTake(historyRow({ instruct: 'male, hums while talking' }));
    expect(designInstruct(draft, 'tags')).toBe('male');
    expect(designInstruct(draft, 'freeform')).toBe('male, hums while talking');
  });

  it('brings back everything a stored recipe held', () => {
    const original = pickDetail(
      mappedDraft('a warm host', { Gender: 'female' }),
      'Age',
      'elderly',
    ).draft;
    const recipe = JSON.stringify({ ...designRecipe(original), mapped: original.mapped });
    const reopened = designDraftFromTake(
      historyRow({ design_recipe: recipe, profile_id: 'saved-1', instruct: 'ignored' }),
    );
    expect(reopened.description).toBe('a warm host');
    expect(reopened.profileId).toBe('saved-1');
    expect(reopened.attrs).toMatchObject({ Gender: 'female', Age: 'elderly' });
    expect(reopened.picks).toEqual({ Age: { value: 'elderly', description: 'a warm host' } });
    expect(reopened.mapped).toMatchObject({ Gender: 'female' });
    (['tags', 'freeform'] as const).forEach((v) =>
      expect(designInstruct(reopened, v)).toBe(designInstruct(original, v)),
    );
    const edited = applyDescription(
      editVoice(reopened, { description: 'a warm host with energy' }),
      { Gender: 'female' },
      'a warm host with energy',
    );
    expect(edited.picks.Age?.value).toBe('elderly');
  });

  it.each([
    [{ raw: '{not json' }],
    [{ raw: '"just text"' }],
    [{ raw: JSON.stringify({ picks: { Age: 'child' } }) }],
    [{ raw: null }],
  ])('uses the instruct when the stored recipe is unusable (%j)', ({ raw }) => {
    const draft = designDraftFromTake(historyRow({ instruct: 'child', design_recipe: raw }));
    expect([draft.description, draft.attrs.Age]).toEqual(['child', 'child']);
  });
});

describe('replaceRecipe and stored drafts', () => {
  it('wipes the description and mapping and records the new details as picks', () => {
    const before = mappedDraft('a gent', { Gender: 'male' });
    const after = replaceRecipe(before, { attrs: mergeDescribedAttrs({ Pitch: 'low pitch' }) });
    expect([after.description, after.mapped]).toEqual(['', {}]);
    expect(after.picks).toEqual({ Pitch: { value: 'low pitch', description: '' } });
  });

  it('upgrades a draft stored without picks by treating its details as picks', () => {
    localStorage.setItem(
      STORAGE,
      JSON.stringify({ text: 'old', seed: 5, attrs: { Style: 'whisper', Pitch: 'high pitch' } }),
    );
    expect(Object.keys(readDraft().picks).sort()).toEqual(['Pitch', 'Style']);
  });

  it.each([
    [{ picks: 7, mapped: 'x' }],
    [{ picks: { Age: { value: 3, description: '' } }, mapped: [1] }],
  ])('treats damaged picks and mapped (%j) as empty', (damage) => {
    localStorage.setItem(STORAGE, JSON.stringify({ seed: 5, attrs: {}, ...damage }));
    const draft = readDraft();
    expect([draft.picks, draft.mapped]).toEqual([{}, {}]);
  });

  it('starts blank when storage holds garbage', () => {
    localStorage.setItem(STORAGE, 'nope{');
    const draft = readDraft();
    expect([draft.text, draft.profileId, draft.description]).toEqual(['', null, '']);
  });
});

describe('canResetToDescription', () => {
  const pick = { Age: { value: 'elderly', description: '' } };
  it('hides the reset while the description is empty, even with picks', () => {
    expect(canResetToDescription({ description: '  ', picks: pick, profileId: 'p' })).toBe(false);
  });
  it('hides the reset when the details already follow the description', () => {
    expect(canResetToDescription({ description: 'warm', picks: {}, profileId: null })).toBe(false);
  });
  it('offers the reset when picks or a saved voice override the description', () => {
    expect(canResetToDescription({ description: 'warm', picks: pick, profileId: null })).toBe(true);
    expect(canResetToDescription({ description: 'warm', picks: {}, profileId: 'p' })).toBe(true);
  });
});
