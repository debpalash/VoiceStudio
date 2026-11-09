import { expect, it } from 'vitest';
import {
  STALL_COLD_S,
  STALL_FLOOR_S,
  formatDuration,
  parseProgressEvent,
  renderStatus,
  stallThresholdS,
} from './render-eta';

const T0 = 1_000_000;
const chapters = (...statuses: string[]) => statuses.map((status) => ({ title: '', status }));

it('counts the backend ETA down between progress events', () => {
  const progress = parseProgressEvent({ fraction: 0.4, eta_s: 600, rtf: 0.5, chunk_s: 8 }, T0);
  const status = renderStatus({
    stage: 'rendering',
    chapters: chapters('done', 'rendering', 'pending'),
    progress,
    startedAt: T0 - 400_000,
    lastActivityAt: T0,
    now: T0 + 30_000,
  });
  expect(status.percent).toBeCloseTo(40);
  expect(status.etaS).toBe(570);
  expect(status.rtf).toBe(0.5);
  expect(status.elapsedS).toBe(430);
  expect(status.stalledForS).toBeNull();
});

it('moves within a chapter instead of waiting for it to finish', () => {
  // The #3417 freeze: chapter 1 of 7 rendering for an hour read as 0%.
  const status = renderStatus({
    stage: 'rendering',
    chapters: chapters('rendering', ...Array(6).fill('pending')),
    progress: parseProgressEvent({ fraction: 0.09, eta_s: null }, T0),
    startedAt: T0 - 3_600_000,
    lastActivityAt: T0,
    now: T0,
  });
  expect(status.percent).toBeCloseTo(9);
  expect(status.etaS).toBeNull(); // nothing freshly timed yet: no invented number
});

it('flags a stall after several typical chunks of silence, never sooner than the floor', () => {
  const progress = parseProgressEvent({ fraction: 0.5, eta_s: 100, chunk_s: 30 }, T0);
  const base = { stage: 'rendering', chapters: chapters('rendering'), progress, startedAt: T0 };
  expect(stallThresholdS(30)).toBe(180);
  expect(stallThresholdS(2)).toBe(STALL_FLOOR_S);
  expect(stallThresholdS(null)).toBe(STALL_COLD_S);
  expect(renderStatus({ ...base, lastActivityAt: T0, now: T0 + 179_000 }).stalledForS).toBeNull();
  expect(renderStatus({ ...base, lastActivityAt: T0, now: T0 + 181_000 }).stalledForS).toBe(181);
  // Assembly runs ffmpeg with no chunk events; that is not a stall.
  expect(
    renderStatus({ ...base, stage: 'assembling', lastActivityAt: T0, now: T0 + 3_600_000 })
      .stalledForS,
  ).toBeNull();
});

it('falls back to chapter counts on an older backend, ignoring instant cached chapters', () => {
  const status = renderStatus({
    stage: 'rendering',
    chapters: chapters('cached', 'cached', 'done', 'rendering'),
    progress: null,
    startedAt: T0,
    lastActivityAt: T0,
    now: T0 + 600_000,
  });
  expect(status.percent).toBe(75);
  // 600 s for the one chapter actually rendered, one left — not 200 s.
  expect(status.etaS).toBe(600);
  // No chunk events from this backend: a long chapter is not a stall.
  expect(status.stalledForS).toBeNull();
  expect(
    renderStatus({
      stage: 'rendering',
      chapters: chapters('rendering'),
      progress: null,
      startedAt: T0,
      lastActivityAt: T0,
      now: T0 + 3_600_000,
    }).stalledForS,
  ).toBeNull();
});

it('rejects malformed progress events and clamps values', () => {
  expect(parseProgressEvent({ type: 'progress' }, T0)).toBeNull();
  expect(parseProgressEvent({ fraction: 'x' }, T0)).toBeNull();
  expect(parseProgressEvent({ fraction: 2, eta_s: -1, rtf: 0, chunk_s: null }, T0)).toEqual({
    fraction: 1,
    etaS: null,
    rtf: null,
    chunkS: null,
    receivedAt: T0,
  });
});

it('formats long renders with hours', () => {
  expect(formatDuration(75)).toBe('1:15');
  expect(formatDuration(8 * 3600 + 5 * 60 + 7)).toBe('8:05:07');
});
