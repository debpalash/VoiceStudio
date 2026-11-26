import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ApiError, apiJson } from '@/lib/api/client';
import { DubGenerationReview, type DubGenerationPlan } from './dub-generation-review';
import type { DubSession } from './dub-session';
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/lib/api/client', async (load) => ({
  ...(await load<typeof import('@/lib/api/client')>()),
  apiJson: vi.fn(),
}));
afterEach(() => {
  cleanup();
  vi.mocked(apiJson).mockReset();
});
const session = {
  jobId: 'job',
  phase: 'editing',
  quality: 'fast',
  target: 'English',
  segments: [
    { id: 'a', start: 0, end: 1, text: 'Hello', text_original: 'Hola', profile_id: 'voice' },
  ],
} as DubSession;
const props = {
  open: true,
  session,
  targets: [{ lang: 'English', code: 'en' }],
  options: {},
  onClose: vi.fn(),
  onConfirm: vi.fn(),
};

it('inspects saved takes and waits for a deliberate start before generating anything', async () => {
  let resolve!: (value: DubGenerationPlan) => void;
  vi.mocked(apiJson).mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done as typeof resolve;
      }),
  );
  const start = vi.fn();
  render(<DubGenerationReview {...props} onConfirm={start} />);
  expect(screen.getByRole('button', { name: 'dub.plan_checking' }).hasAttribute('disabled')).toBe(
    true,
  );
  resolve({ total: 12, reuse: 9, synthesize: 3, silence: 0, forced: 0 });
  await screen.findByText('9');
  await screen.findByText('3');
  expect(apiJson).toHaveBeenCalledOnce();
  expect(vi.mocked(apiJson).mock.calls[0][0]).toBe('/dub/plan/job');
  expect(start).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: /dub.flow_(generate|continue)_count/ }));
  expect(start).toHaveBeenCalledOnce();
});
it('describes a mix-only run when all takes can be reused', async () => {
  vi.mocked(apiJson).mockResolvedValueOnce({
    total: 1,
    reuse: 1,
    synthesize: 0,
    silence: 0,
    forced: 0,
  });
  render(<DubGenerationReview {...props} />);
  expect(await screen.findByText('dub.plan_mix_only')).toBeTruthy();
});
it('blocks start when inspection fails and allows a fresh check', async () => {
  vi.mocked(apiJson)
    .mockRejectedValueOnce(new Error('unavailable'))
    .mockResolvedValueOnce({ total: 1, reuse: 0, synthesize: 1, silence: 0, forced: 0 });
  render(<DubGenerationReview {...props} />);
  await screen.findByRole('alert');
  expect(screen.getByRole('button', { name: /dub.flow_(generate|continue)_count/ }).hasAttribute('disabled')).toBe(
    true,
  );
  fireEvent.click(screen.getByRole('button', { name: 'common.retry' }));
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: /dub.flow_(generate|continue)_count/ }).hasAttribute('disabled'),
    ).toBe(false),
  );
});
it('makes forced quick regeneration explicit and passes the same scope to inspection', async () => {
  vi.mocked(apiJson).mockResolvedValueOnce({
    total: 1,
    reuse: 0,
    synthesize: 1,
    silence: 0,
    forced: 1,
  });
  render(<DubGenerationReview {...props} options={{ regenOnly: ['a'], preview: true }} />);
  await screen.findByText('1');
  expect(screen.getByText('dub.plan_forced')).toBeTruthy();
  expect(screen.getByText('dub.plan_preview_hint')).toBeTruthy();
  const body = JSON.parse(vi.mocked(apiJson).mock.calls[0][1]!.body as string);
  expect(body.regen_only).toEqual(['a']);
  expect(body.preview).toBe(true);
});

it('invalidates the old counts when generation inputs change before confirmation', async () => {
  vi.mocked(apiJson).mockResolvedValueOnce({
    total: 1,
    reuse: 1,
    synthesize: 0,
    silence: 0,
    forced: 0,
  });
  const { rerender } = render(<DubGenerationReview {...props} />);
  await screen.findByText('dub.plan_mix_only');
  vi.mocked(apiJson).mockImplementationOnce(() => new Promise(() => {}));
  rerender(<DubGenerationReview {...props} session={{ ...session, guidance: 3 }} />);
  expect(screen.getByRole('button', { name: 'dub.plan_checking' }).hasAttribute('disabled')).toBe(
    true,
  );
  expect(screen.queryByText('dub.plan_mix_only')).toBeNull();
});

it('does not invent reuse counts for batch languages that will first be translated', async () => {
  const s = {
    ...session,
    quality: 'agent' as const,
    agentCli: undefined,
    segments: session.segments.map((segment) => ({
      ...segment,
      translations: { en: 'Hello', fr: 'Bonjour' },
    })),
  };
  render(
    <DubGenerationReview
      {...props}
      session={s}
      targets={[
        { lang: 'English', code: 'en' },
        { lang: 'French', code: 'fr' },
      ]}
    />,
  );
  await waitFor(() => expect(screen.getAllByText('dub.plan_translation_first')).toHaveLength(2));
  expect(apiJson).not.toHaveBeenCalled();
  expect(screen.queryByText('dub.plan_mix_only')).toBeNull();
});

it('blocks a known short cue before a long run and opens the affected row', async () => {
  const s = {
    ...session,
    segments: [{ ...session.segments[0], id: '992', start: 1524.95, end: 1525, text: 'Right?' }],
  };
  vi.mocked(apiJson).mockResolvedValueOnce({
    total: 1,
    reuse: 0,
    synthesize: 0,
    silence: 1,
    forced: 0,
    issues: [{ segment_id: '992', code: 'dub_cue_too_short' }],
  });
  const fix = vi.fn();
  render(<DubGenerationReview {...props} session={s} onFix={fix} />);
  await screen.findByText('dub.plan_silence');
  expect(screen.getByRole('button', { name: /dub.flow_(generate|continue)_count/ }).hasAttribute('disabled')).toBe(
    true,
  );
  fireEvent.click(screen.getByRole('button', { name: 'dub.issue_locate' }));
  expect(fix).toHaveBeenCalledWith('992');
  expect(screen.queryByText('dub.plan_mix_only')).toBeNull();
});

it('still lets an older backend without the plan route generate, without inventing counts', async () => {
  vi.mocked(apiJson).mockRejectedValueOnce(
    new ApiError(404, 'route missing', { detail: 'Not Found' }),
  );
  const start = vi.fn();
  render(<DubGenerationReview {...props} onConfirm={start} />);
  const action = await screen.findByRole('button', { name: 'dub.generate_needed' });
  expect(action.hasAttribute('disabled')).toBe(false);
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.queryByText('dub.plan_mix_only')).toBeNull();
  fireEvent.click(action);
  expect(start).toHaveBeenCalledOnce();
});

it('keeps blocking when the plan route exists but the check fails', async () => {
  vi.mocked(apiJson).mockRejectedValueOnce(
    new ApiError(404, 'Job not found', { detail: 'Job not found' }),
  );
  render(<DubGenerationReview {...props} />);
  await screen.findByRole('alert');
  expect(screen.getByRole('button', { name: /dub\.(flow_|generate_)/ }).hasAttribute('disabled')).toBe(
    true,
  );
});
