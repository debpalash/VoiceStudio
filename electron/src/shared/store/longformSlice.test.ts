import { describe, it, expect } from 'vitest';
// Imported via the deprecated alias on purpose — proves the rename bridge holds.
import { createStoriesSlice, DEFAULT_CAST, SLICE_DEFAULTS, genProjectId } from './longformSlice';

function harness() {
  let state: any = {};
  const set = (fn: any) => {
    state = { ...state, ...(typeof fn === 'function' ? fn(state) : fn) };
  };
  const get = () => state;
  state = createStoriesSlice(set as any, get as any, {} as any);
  return { get };
}

function track(id: number, character = 'narrator', text = 'hi') {
  return { id, character, text, profileId: null, emotion: null, speed: null };
}

// ── Ported back-compat suite (Stories behaves exactly as before) ────────────
describe('longformSlice — stories back-compat', () => {
  it('starts with empty tracks and a Narrator cast', () => {
    const { get } = harness();
    expect(get().storyTracks).toEqual([]);
    expect(get().cast).toHaveLength(1);
    expect(get().cast[0].id).toBe('narrator');
  });

  it('setStoryTracks replaces the list', () => {
    const { get } = harness();
    get().setStoryTracks([track(1)]);
    expect(get().storyTracks).toHaveLength(1);
  });

  it('upsertCastMember adds then updates by id', () => {
    const { get } = harness();
    get().upsertCastMember({ id: 'fox', name: 'Fox', color: '#d3869b', profileId: null });
    expect(get().cast).toHaveLength(2);
    get().upsertCastMember({ id: 'fox', name: 'Fox', color: '#d3869b', profileId: 'p1' });
    expect(get().cast).toHaveLength(2);
    expect(get().cast.find((c: any) => c.id === 'fox').profileId).toBe('p1');
  });

  it('saveProject snapshots, loadProject restores, newProject clears', () => {
    const { get } = harness();
    get().setStoryTracks([track(1), track(2)]);
    get().saveProject('A');
    const id = get().currentProjectId;
    expect(get().storyProjects[0].tracks).toHaveLength(2);
    get().newProject();
    expect(get().storyTracks).toEqual([]);
    get().loadProject(id);
    expect(get().storyTracks).toHaveLength(2);
    expect(get().currentProjectId).toBe(id);
  });

  it('saved tracks are stripped of transient fields', () => {
    const { get } = harness();
    get().setStoryTracks([{ ...track(1), generating: true, audioUrl: 'blob:x' } as any]);
    get().saveProject('A');
    expect('generating' in get().storyProjects[0].tracks[0]).toBe(false);
    expect('audioUrl' in get().storyProjects[0].tracks[0]).toBe(false);
  });

  it('delete + rename', () => {
    const { get } = harness();
    get().saveProject('A');
    const id = get().currentProjectId;
    get().renameProject(id, 'Renamed');
    expect(get().storyProjects[0].name).toBe('Renamed');
    get().deleteProject(id);
    expect(get().storyProjects).toHaveLength(0);
  });
});

// ── New long-form surface (#31) ─────────────────────────────────────────────
describe('longformSlice — long-form fields', () => {
  it('inits the shared working fields to SLICE_DEFAULTS', () => {
    const { get } = harness();
    expect(get().script).toBe('');
    expect(get().meta).toEqual({});
    expect(get().lexicon).toEqual({});
    expect(get().coverRef).toBeNull();
    expect(get().outputFormat).toBe('m4b');
    expect(get().loudness).toBe('off');
    expect(get().defaultVoice).toBeNull();
    expect(get().projectMode).toBe('stories');
  });

  it('the output keeps its render-time lyrics snapshot and clears on newProject', () => {
    // The finished render's filename used to be AudiobookTab useState — the
    // Download affordance evaporated on the first tab switch.
    const { get } = harness();
    expect(get().lastOutput).toBe('');
    get().setLastOutputSnapshot('audiobook_abc123.m4b', '# Rendered', [
      { title: 'Rendered', status: 'done', duration_s: 3 },
    ]);
    expect(get().lastOutput).toBe('audiobook_abc123.m4b');
    expect(get().lastOutputScript).toBe('# Rendered');
    expect(get().lastOutputChapters).toEqual([
      { title: 'Rendered', status: 'done', duration_s: 3 },
    ]);
    get().setScript('# Edited afterwards');
    expect(get().lastOutputScript).toBe('# Rendered');
    get().newProject('audiobook');
    expect(get().lastOutput).toBe(''); // a new book doesn't show the old file
    expect(get().lastOutputScript).toBe('');
    expect(get().lastOutputChapters).toEqual([]);
  });

  it('loadProject clears lastOutput — no cross-project leak (#1139 review)', () => {
    const { get } = harness();
    get().setScript('# Book B');
    get().saveProject('B');
    const idB = get().currentProjectId;
    get().setLastOutput('audiobook_from_a.m4b'); // pretend A rendered meanwhile
    get().loadProject(idB);
    expect(get().lastOutput).toBe(''); // loading B never presents A's render
  });

  it('setProjectMeta MERGES; setLexicon REPLACES; setOutputPrefs merges', () => {
    const { get } = harness();
    get().setProjectMeta({ title: 'The Crown' });
    get().setProjectMeta({ author: 'A. Writer' });
    expect(get().meta).toEqual({ title: 'The Crown', author: 'A. Writer' });
    get().setLexicon({ gaol: 'jail' });
    get().setLexicon({ ye: 'yee' });
    expect(get().lexicon).toEqual({ ye: 'yee' }); // replace, not merge
    get().setOutputPrefs({ loudness: 'acx' });
    expect(get().outputFormat).toBe('m4b'); // untouched
    expect(get().loudness).toBe('acx');
  });

  it('saveProject snapshots the new fields; loadProject restores them', () => {
    const { get } = harness();
    get().setScript('# Chapter 1');
    get().setProjectMeta({ title: 'Bk', author: 'Au' });
    get().setOutputPrefs({ outputFormat: 'mp3', loudness: 'podcast', defaultVoice: 'p_x' });
    get().setCoverRef({ filename: 'c.png', serverPath: '/tmp/c.png' });
    get().convertMode('audiobook');
    get().saveProject('Bk');
    const id = get().currentProjectId;
    const saved = get().storyProjects[0];
    expect(saved.mode).toBe('audiobook');
    expect(saved.script).toBe('# Chapter 1');
    expect(saved.meta).toEqual({ title: 'Bk', author: 'Au' });
    // mutate working state, then reload → restored
    get().newProject();
    expect(get().script).toBe('');
    expect(get().meta).toEqual({});
    get().loadProject(id);
    expect(get().script).toBe('# Chapter 1');
    expect(get().projectMode).toBe('audiobook');
    expect(get().coverRef).toEqual({ filename: 'c.png', serverPath: '/tmp/c.png' });
  });

  it('loadProject default-fills a v4-shaped project (no mode/meta/script keys)', () => {
    const { get } = harness();
    // Inject a legacy project shape directly (what a migrated v4 record looks
    // like before the migrate fn enriches it / if a field is somehow absent).
    const legacy: any = { id: 'p_old', name: 'Old', cast: [], tracks: [track(1)], updatedAt: 1 };
    get().setStoryTracks([]);
    (get().storyProjects as any).push(legacy);
    get().loadProject('p_old');
    expect(get().projectMode).toBe('stories');
    expect(get().script).toBe(SLICE_DEFAULTS.script);
    expect(get().meta).toEqual({}); // never undefined → no controlled-input warning
    expect(get().outputFormat).toBe('m4b');
    expect(get().storyTracks).toHaveLength(1);
  });

  it('no stale carry-over: loading B after editing A leaves no A field', () => {
    const { get } = harness();
    get().setProjectMeta({ title: 'Aaa' });
    get().setScript('A body');
    get().saveProject('A');
    const a = get().currentProjectId;
    get().newProject('audiobook');
    get().setProjectMeta({ author: 'Bee' });
    get().saveProject('B');
    const b = get().currentProjectId;
    get().loadProject(a!);
    get().loadProject(b!);
    expect(get().meta).toEqual({ author: 'Bee' }); // no 'Aaa' title bleed
    expect(get().script).toBe(''); // B never set a script
  });

  it('convertMode flips mode, is idempotent, and guards invalid values', () => {
    const { get } = harness();
    expect(get().projectMode).toBe('stories');
    get().convertMode('audiobook');
    expect(get().projectMode).toBe('audiobook');
    get().convertMode('audiobook'); // idempotent
    expect(get().projectMode).toBe('audiobook');
    get().convertMode('bogus' as any); // guarded
    expect(get().projectMode).toBe('audiobook');
  });

  it('newProject(mode) sets the mode; default is stories', () => {
    const { get } = harness();
    get().newProject('audiobook');
    expect(get().projectMode).toBe('audiobook');
    get().newProject();
    expect(get().projectMode).toBe('stories');
  });

  it('genProjectId is exported (migrate fn depends on it) and unique-ish', () => {
    expect(genProjectId()).toMatch(/^p_/);
    expect(genProjectId()).not.toBe(genProjectId());
  });

  it('DEFAULT_CAST is not shared by reference between slices', () => {
    const a = harness();
    const b = harness();
    a.get().setCharacterVoice('narrator', 'x');
    expect(b.get().cast[0].profileId).toBeNull();
    expect(DEFAULT_CAST[0].profileId).toBeNull();
  });
});
