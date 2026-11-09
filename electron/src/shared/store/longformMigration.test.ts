import { describe, expect, it } from 'vitest';

import { migrateAppStore } from './index';

describe('long-form v5 migration', () => {
  it('normalizes malformed nested project fields after applying defaults', () => {
    const migrated = migrateAppStore(
      {
        storyProjects: [
          null,
          'bad',
          { id: null, name: null, tracks: 'bad', cast: {}, updatedAt: 'yesterday' },
        ],
      },
      4,
    );
    const projects = migrated.storyProjects ?? [];

    expect(projects).toHaveLength(1);
    expect(projects[0]).toMatchObject({
      id: expect.stringMatching(/^p_/),
      name: 'Untitled',
      tracks: [],
      cast: [],
      mode: 'stories',
      updatedAt: 0,
    });
  });

  it('keeps valid legacy identity and arrays while normalizing corrupt optional objects', () => {
    const migrated = migrateAppStore(
      {
        storyProjects: [
          {
            id: 'p_book',
            name: 'Book',
            tracks: [{ id: 1 }],
            cast: [{ id: 'narrator' }],
            meta: null,
            lexicon: [],
            voiceCast: 'bad',
            updatedAt: Number.NaN,
          },
        ],
      },
      4,
    );
    const projects = migrated.storyProjects ?? [];

    expect(projects[0]).toMatchObject({
      id: 'p_book',
      name: 'Book',
      tracks: [{ id: 1 }],
      cast: [{ id: 'narrator' }],
      meta: {},
      lexicon: {},
      voiceCast: {},
      updatedAt: 0,
    });
  });

  it('keeps only well-typed legacy override fields', () => {
    const migrated = migrateAppStore(
      {
        storyProjects: [
          {
            overrides: {
              numStep: {},
              guidanceScale: 2.5,
              postprocess: false,
              seed: Number.POSITIVE_INFINITY,
              varyRepeats: 'yes',
              emoText: 'calm',
              unknown: true,
            },
          },
        ],
      },
      4,
    );
    const projects = migrated.storyProjects ?? [];

    expect(projects[0].overrides).toEqual({
      numStep: null,
      guidanceScale: 2.5,
      posTemp: null,
      classTemp: null,
      postprocess: false,
      seed: null,
      varyRepeats: false,
      emoText: 'calm',
      emoAlpha: null,
      lineGapMs: null,
      paragraphGapMs: null,
      trimEdges: null,
    });
  });

  it('keeps explicit zero gaps and an off trim switch, and drops malformed join values', () => {
    const run = (overrides: Record<string, unknown>) =>
      migrateAppStore({ storyProjects: [{ overrides }] }, 4).storyProjects?.[0].overrides;
    expect(run({ lineGapMs: 0, paragraphGapMs: 350, trimEdges: false })).toMatchObject({
      lineGapMs: 0,
      paragraphGapMs: 350,
      trimEdges: false,
    });
    expect(run({ lineGapMs: '250', paragraphGapMs: Number.NaN, trimEdges: 'yes' })).toMatchObject({
      lineGapMs: null,
      paragraphGapMs: null,
      trimEdges: null,
    });
    expect(run({ lineGapMs: Number.POSITIVE_INFINITY })).toMatchObject({ lineGapMs: null });
  });
});
