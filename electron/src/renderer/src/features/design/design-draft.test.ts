import { beforeEach, describe, expect, it } from 'vitest';
import type { HistoryItem, Profile } from '@/lib/api/types';
import {
  applyDescription,
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

describe('designInstruct', () => {
  const attrs = { Gender: 'female', Age: 'elderly' };
  const description = ' raspy old female, scottish accent ';
  const draft = { attrs, description, picks: {} };
  const pick = (value: string) => ({ value, description: '' });

  it('sends OmniVoice its effective details', () => {
    expect(designInstruct(draft, 'tags')).toBe('female, elderly');
  });

  it('sends a free-form engine the description as written plus its picks (#2389)', () => {
    expect(designInstruct(draft, 'freeform')).toBe('raspy old female, scottish accent');
    expect(designInstruct({ ...draft, picks: { Age: pick('elderly') } }, 'freeform')).toBe(
      'raspy old female, scottish accent, elderly',
    );
    expect(
      designInstruct(
        { ...draft, description: '  ', picks: { Gender: pick('female'), Age: pick('elderly') } },
        'freeform',
      ),
    ).toBe('female, elderly');
  });
});

describe('free-form design drafts (#2389)', () => {
  // Sichuanese, written as an escape to keep the source ASCII.
  const DIALECT = '\u56DB\u5DDD\u8BDD';
  const blank = () => ({ ...readDraft(), description: '', picks: {}, mapped: {} });
  const typed = (draft: ReturnType<typeof blank>, description: string) => ({
    ...draft,
    description,
  });
  const take = {
    id: 'take',
    text: 'Hello',
    mode: 'design',
    language: null,
    instruct: 'raspy, female',
    profile_id: null,
    audio_path: 'take.wav',
    duration_seconds: 1,
    generation_time: 1,
    seed: 3,
    starred: null,
    created_at: 1,
  } satisfies HistoryItem;

  beforeEach(() => localStorage.clear());

  it('keeps a pick when an edit says nothing new about it', () => {
    const mapped = applyDescription(
      typed(blank(), 'old man'),
      { Gender: 'male', Age: 'elderly' },
      'old man',
    );
    const picked = pickDetail(mapped, 'Age', 'middle-aged').draft;
    const edited = typed(picked, 'old man, raspy');
    const remapped = applyDescription(edited, { Gender: 'male', Age: 'elderly' }, 'old man, raspy');
    expect(remapped.attrs).toMatchObject({ Gender: 'male', Age: 'middle-aged' });
    expect(designInstruct(remapped, 'freeform')).toBe('old man, raspy, middle-aged');
    expect(designInstruct(remapped, 'tags')).toBe('male, middle-aged');
  });

  it('keeps a pick that matches the mapped value', () => {
    const mapped = applyDescription(typed(blank(), 'raspy'), { Age: 'elderly' }, 'raspy');
    const picked = pickDetail(mapped, 'Age', 'elderly').draft;
    expect(designInstruct(picked, 'freeform')).toBe('raspy, elderly');
  });

  it('lets a newer description override an older pick of the same detail', () => {
    const recipe = replaceRecipe(blank(), {
      attrs: { ...blank().attrs, Gender: 'male', Pitch: 'low pitch' },
    });
    const edited = typed(recipe, 'young woman');
    const remapped = applyDescription(
      edited,
      { Gender: 'female', Age: 'young adult' },
      'young woman',
    );
    expect(remapped.attrs).toMatchObject({
      Gender: 'female',
      Age: 'young adult',
      Pitch: 'low pitch',
    });
    expect(designInstruct(remapped, 'freeform')).toBe('young woman, low pitch');
  });

  it('does not let a late mapping undo a pick made after the text was typed', () => {
    const picked = pickDetail(typed(blank(), 'a child'), 'Age', 'elderly').draft;
    const landed = applyDescription(picked, { Age: 'child' }, 'a child');
    expect(landed.attrs.Age).toBe('elderly');
    const edited = typed(landed, 'a child, raspy');
    expect(applyDescription(edited, { Age: 'child' }, 'a child, raspy').attrs.Age).toBe('elderly');
  });

  it('lets a newer description override an older exclusive pick', () => {
    const accent = pickDetail(blank(), 'EnglishAccent', 'british accent').draft;
    const edited = typed(accent, 'from Sichuan');
    const remapped = applyDescription(edited, { ChineseDialect: DIALECT }, 'from Sichuan');
    expect(remapped.attrs).toMatchObject({ ChineseDialect: DIALECT, EnglishAccent: 'Auto' });
    expect(remapped.picks).not.toHaveProperty('EnglishAccent');
  });

  it('drops the pick an exclusive pick clears', () => {
    const accent = pickDetail(blank(), 'EnglishAccent', 'british accent').draft;
    const dialect = pickDetail(accent, 'ChineseDialect', DIALECT);
    expect(dialect.clearedCategory).toBe('EnglishAccent');
    expect(dialect.draft.picks).not.toHaveProperty('EnglishAccent');
  });

  it('restores a take so both engine kinds receive the same instruct', () => {
    const draft = designDraftFromTake(take);
    expect(draft).toMatchObject({
      description: 'raspy, female',
      attrs: { Gender: 'female', Age: 'Auto' },
      picks: {},
    });
    expect(designInstruct(draft, 'freeform')).toBe('raspy, female');
    expect(designInstruct(draft, 'tags')).toBe('female');
    const tagsOnly = designDraftFromTake({ ...take, instruct: 'female, elderly' });
    expect(tagsOnly.description).toBe('female, elderly');
    expect(designInstruct(tagsOnly, 'freeform')).toBe('female, elderly');
    expect(designInstruct(tagsOnly, 'tags')).toBe('female, elderly');
  });

  it('reopens a take with a stored recipe exactly as it was drafted', () => {
    const mapped = applyDescription(
      typed(blank(), 'raspy old woman'),
      { Gender: 'female', Age: 'elderly' },
      'raspy old woman',
    );
    const drafted = pickDetail(mapped, 'Pitch', 'low pitch').draft;
    const sent = designRecipe(drafted);
    expect(sent).toEqual({ description: 'raspy old woman', picks: { Pitch: 'low pitch' } });
    // The backend stores the recipe with the mapping it derives from the description.
    const reopened = designDraftFromTake({
      ...take,
      instruct: designInstruct(drafted, 'freeform'),
      design_recipe: JSON.stringify({ ...sent, mapped: drafted.mapped }),
    });
    expect(reopened).toMatchObject({
      description: 'raspy old woman',
      attrs: { Gender: 'female', Age: 'elderly', Pitch: 'low pitch' },
      picks: { Pitch: { value: 'low pitch', description: 'raspy old woman' } },
      mapped: { Gender: 'female', Age: 'elderly', Pitch: 'Auto' },
    });
    for (const vocabulary of ['freeform', 'tags'] as const) {
      expect(designInstruct(reopened, vocabulary)).toBe(designInstruct(drafted, vocabulary));
    }
    // Editing the reopened description keeps the pick it was made with.
    const edited = typed(reopened, 'raspy old woman, tired');
    expect(
      applyDescription(edited, { Gender: 'female', Age: 'elderly' }, 'raspy old woman, tired').attrs
        .Pitch,
    ).toBe('low pitch');
  });

  it('falls back to the instruct when a stored recipe is unreadable', () => {
    for (const design_recipe of ['{broken', '[]', JSON.stringify({ picks: {} }), null]) {
      expect(designDraftFromTake({ ...take, design_recipe })).toMatchObject({
        description: 'raspy, female',
        attrs: { Gender: 'female' },
        picks: {},
      });
    }
  });

  it('replaces the description and makes the recipe the picks', () => {
    const current = {
      ...blank(),
      description: 'raspy',
      picks: { Pitch: { value: 'low pitch', description: 'raspy' } },
    };
    const replaced = replaceRecipe(current, { attrs: { ...current.attrs, Gender: 'male' } });
    expect(replaced).toMatchObject({
      description: '',
      picks: { Gender: { value: 'male', description: '' } },
      mapped: {},
    });
    expect(designInstruct(replaced, 'freeform')).toBe('male');
  });

  it('treats details in drafts saved before descriptions as picks', () => {
    localStorage.setItem(STORAGE, JSON.stringify({ text: 'Hi', attrs: { Gender: 'female' } }));
    const legacy = readDraft();
    expect(legacy).toMatchObject({
      description: '',
      picks: { Gender: { value: 'female', description: '' } },
    });
    expect(designInstruct(legacy, 'freeform')).toBe('female');
    localStorage.setItem(STORAGE, JSON.stringify({ picks: { Age: 'elderly' }, mapped: [1] }));
    expect(readDraft()).toMatchObject({ picks: {}, mapped: {} });
  });
});
