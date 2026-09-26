import { describe, it, expect } from 'vitest';
import { reduceWizardDownloadEvent, mirrorBlockedRepos } from '../components/WizardLibrary.jsx';

const REPO = 'org/model';
const KEY = `local\u0000${REPO}`;

// P1-A: the first-run wizard used to DELETE a row on install_error (never even
// reading ev.error), so a failed download vanished with no reason. It must now
// keep the row and its mirror-aware message so the row can render a Retry.
describe('reduceWizardDownloadEvent — install_error persists (P1-A)', () => {
  it('KEEPS the row + its error message on install_error (does not delete)', () => {
    let s = reduceWizardDownloadEvent({}, { repo_id: REPO, phase: 'install_start' });
    s = reduceWizardDownloadEvent(s, {
      repo_id: REPO,
      phase: 'install_error',
      error: 'We couldn’t connect to hf-mirror.com',
    });
    expect(s[KEY]).toBeDefined();
    expect(s[KEY].phase).toBe('install_error');
    expect(s[KEY].error).toBe('We couldn’t connect to hf-mirror.com');
  });

  it('install_done still drops the transient row (reverts to installed flag)', () => {
    let s = reduceWizardDownloadEvent({}, { repo_id: REPO, phase: 'install_start' });
    s = reduceWizardDownloadEvent(s, { repo_id: REPO, phase: 'install_done' });
    expect(s[KEY]).toBeUndefined();
  });

  it('aggregate + per-file events accumulate without clearing the row', () => {
    let s = reduceWizardDownloadEvent(
      {},
      { repo_id: REPO, phase: 'aggregate', bytes_done: 10, total_bytes: 100, rate: 5 },
    );
    s = reduceWizardDownloadEvent(s, {
      repo_id: REPO,
      phase: 'progress',
      filename: 'a.bin',
      downloaded: 10,
      total: 100,
    });
    expect(s[KEY].agg.totalBytes).toBe(100);
    expect(s[KEY].files['a.bin'].total).toBe(100);
  });

  it('ignores keepalive events without a repo_id', () => {
    const prev = { [REPO]: { phase: 'install_error', error: 'x' } };
    expect(reduceWizardDownloadEvent(prev, {})).toBe(prev);
  });

  it('stores the docs_topic failure class on install_error (mirror rescue trigger)', () => {
    let s = reduceWizardDownloadEvent({}, { repo_id: REPO, phase: 'install_start' });
    s = reduceWizardDownloadEvent(s, {
      repo_id: REPO,
      phase: 'install_error',
      error: 'mirror down',
      docs_topic: 'HF_MIRROR_UNREACHABLE',
    });
    expect(s[KEY].docsTopic).toBe('HF_MIRROR_UNREACHABLE');
    // Absent docs_topic (older backend / other failures) degrades to ''.
    const s2 = reduceWizardDownloadEvent({}, { repo_id: REPO, phase: 'install_error', error: 'x' });
    expect(s2[KEY].docsTopic).toBe('');
  });
});

// The wizard renders the inline mirror picker (MirrorRescue) ONLY for the
// mirror-unreachable class — the error hint points at Settings, which the
// first-run wizard gates, so switch-and-retry must be possible in place.
describe('mirrorBlockedRepos', () => {
  it('lists only install_error rows with the HF_MIRROR_UNREACHABLE class', () => {
    const progress = {
      'a/mirror-blocked': { phase: 'install_error', docsTopic: 'HF_MIRROR_UNREACHABLE' },
      'b/other-error': { phase: 'install_error', docsTopic: '' },
      'c/downloading': { phase: 'active', docsTopic: 'HF_MIRROR_UNREACHABLE' },
    };
    expect(mirrorBlockedRepos(progress)).toEqual(['a/mirror-blocked']);
  });

  it('is empty for empty/undefined progress', () => {
    expect(mirrorBlockedRepos({})).toEqual([]);
    expect(mirrorBlockedRepos(undefined)).toEqual([]);
  });
});
