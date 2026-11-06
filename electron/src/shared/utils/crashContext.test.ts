import { describe, expect, it } from 'vitest';
import { formatCrashContext } from './crashContext';
import { lastRunCrashFacts } from './runCrashRecord';

describe('formatCrashContext', () => {
  it('states what was running and the memory left, for a report', () => {
    expect(
      formatCrashContext({
        kind: 'model_load',
        detail: 'omnivoice-tts',
        engine: 'omnivoice',
        device: 'cpu',
        ram_free_gb: 3.1,
        ram_total_gb: 7.7,
        swap_free_gb: 1,
        swap_total_gb: 2,
        seconds_before_exit: 42,
      }),
    ).toEqual([
      'Last activity: model_load omnivoice-tts (42 s before exit)',
      'Engine: omnivoice',
      'Device: cpu',
      'Free memory then: 3.1 of 7.7 GB RAM, 1 of 2 GB swap',
    ]);
  });

  it('omits what is unknown', () => {
    expect(formatCrashContext({ kind: 'task', ram_free_gb: 3 })).toEqual(['Last activity: task']);
    expect(formatCrashContext(undefined)).toEqual([]);
    expect(
      formatCrashContext({
        kind: 'task',
        ram_free_gb: 3,
        ram_total_gb: 8,
        swap_free_gb: 0,
        swap_total_gb: 0,
      }),
    ).toEqual(['Last activity: task', 'Free memory then: 3 of 8 GB RAM, no swap']);
  });
});

describe('a crash found by the next run', () => {
  it('states the memory as facts, outside the log that can be cut', () => {
    expect(
      lastRunCrashFacts({
        detected_at: 1,
        started_at: 0,
        ended_between: [0, 1],
        uptime_hint_s: 1,
        version: '1',
        last_activity: {
          ts: 0,
          kind: 'model_load',
          detail: 'omnivoice-tts',
          device: 'cpu',
          ram_free_gb: 3,
          ram_total_gb: 8,
        },
        log_tail: [],
      }),
    ).toEqual([
      'Last activity: model_load omnivoice-tts',
      'Device: cpu',
      'Free memory then: 3 of 8 GB RAM',
    ]);
  });
});
