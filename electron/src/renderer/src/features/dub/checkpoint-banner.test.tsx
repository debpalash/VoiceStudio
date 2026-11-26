import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { CheckpointBanner, dubCheckpoint } from './checkpoint-banner';
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
afterEach(cleanup);

const transcribed = { phase: 'editing', jobId: 'job', segments: [{}] };
const none = new Set<string>();

it('pauses at each stage only while Settings → Review mode is on', () => {
  expect(dubCheckpoint(transcribed, 'on', none)).toEqual({ stage: 'asr', id: 'job:asr' });
  expect(dubCheckpoint(transcribed, 'off', none)).toBeNull();
  const translated = { ...transcribed, segments: [{ translations: { en: 'Hi' } }] };
  expect(dubCheckpoint(translated, 'on', none)?.stage).toBe('translate');
  expect(dubCheckpoint({ ...translated, phase: 'done' }, 'on', none)?.stage).toBe('done');
  expect(dubCheckpoint({ ...translated, phase: 'done' }, 'off', none)).toBeNull();
});

it('stays dismissed for that job and stage, and has nothing to show without segments', () => {
  expect(dubCheckpoint(transcribed, 'on', new Set(['job:asr']))).toBeNull();
  expect(dubCheckpoint({ ...transcribed, jobId: 'other' }, 'on', new Set(['job:asr']))).not.toBeNull();
  expect(dubCheckpoint({ ...transcribed, segments: [] }, 'on', none)).toBeNull();
  expect(dubCheckpoint({ ...transcribed, phase: 'generating' }, 'on', none)).toBeNull();
});

it('continues to the next stage or dismisses from the banner', () => {
  const next = vi.fn(),
    dismiss = vi.fn();
  render(<CheckpointBanner stage="asr" timingWarnings={0} onContinue={next} onDismiss={dismiss} />);
  fireEvent.click(screen.getByRole('button', { name: /checkpoint.asr_cta/ }));
  fireEvent.click(screen.getByRole('button', { name: 'checkpoint.dismiss_title' }));
  expect(next).toHaveBeenCalledOnce();
  expect(dismiss).toHaveBeenCalledOnce();
});
