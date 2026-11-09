/**
 * The desktop app has to say when a dialect was not applied (#2752).
 *
 * Reported against v0.5.6: picking Portuguese (Portugal) produced Brazilian
 * Portuguese. Nothing was lying — NLLB has a single `por_Latn` with no European
 * variant to select, so it cannot honour pt-PT, and the backend returns
 * `dialect_applied: false` to say exactly that.
 *
 * The browser app raised a notice on that flag. Electron typed the field in its
 * response shape and never read it, so the desktop app accepted the answer and
 * discarded it: the picker still showed Portugal, the output was Brazilian, and
 * the one sentence explaining why was dropped on the floor. That reads as a
 * broken feature rather than an engine limit, which is how the issue was filed.
 */
import { beforeEach, expect, it, vi } from 'vitest';
import { apiJson } from '@/lib/api/client';
import { clearDubEditHistory, dubSession, translateDub, type DubSegment } from './dub-session';

vi.mock('@/lib/api/client', () => ({ apiJson: vi.fn(), ApiError: class extends Error {} }));

const segment = (id: string): DubSegment => ({
  id,
  start: 0,
  end: 1,
  text: id,
  text_original: id,
  speaker_id: 'Speaker 1',
});

/** A dub mid-edit with Portuguese (Portugal) chosen, as the reporter had it. */
function editingWithDialect(dialect: string | null = 'pt-PT') {
  dubSession.setState((current) => ({
    ...current,
    jobId: 'job',
    phase: 'editing',
    recovery: null,
    error: null,
    tracks: [],
    dialect: dialect ?? undefined,
    dialectIgnored: false,
    segments: [segment('a')],
  }));
}

/** `/dub/translate` answering with the given dialect verdict. */
function backendAnswers(reply: Record<string, unknown>) {
  vi.mocked(apiJson).mockImplementation((path: string) =>
    Promise.resolve(
      path === '/dub/translate' ? { translated: [{ id: 'a', text: 'olá' }], ...reply } : [],
    ),
  );
}

beforeEach(() => {
  vi.mocked(apiJson).mockReset();
  clearDubEditHistory();
});

it('records that the engine could not honour the chosen dialect', async () => {
  editingWithDialect('pt-PT');
  backendAnswers({ dialect: 'pt-PT', dialect_applied: false });

  await translateDub('pt', 'nllb');

  expect(dubSession.state.dialectIgnored).toBe(true);
});

it('stays quiet when the engine did honour it', async () => {
  editingWithDialect('pt-PT');
  backendAnswers({ dialect: 'pt-PT', dialect_applied: true });

  await translateDub('pt', 'openai');

  expect(dubSession.state.dialectIgnored).toBe(false);
});

it('stays quiet when no dialect was chosen', async () => {
  editingWithDialect(null);
  backendAnswers({});

  await translateDub('pt', 'nllb');

  expect(dubSession.state.dialectIgnored).toBe(false);
});

it('defers to the quality notice rather than showing two at once', async () => {
  // #372: "pick Cinematic" followed by "Cinematic needs an LLM" is a loop.
  editingWithDialect('pt-PT');
  backendAnswers({
    dialect: 'pt-PT',
    dialect_applied: false,
    cinematic_skipped: 'no-llm-configured',
  });

  await translateDub('pt', 'nllb');

  expect(dubSession.state.translationFallback).toBe(true);
  expect(dubSession.state.dialectIgnored).toBe(false);
});

it('clears the notice once a later translation applies the dialect', async () => {
  // The flag describes the last translation, not the draft forever — otherwise
  // switching to an LLM engine fixes the output and still reads as broken.
  editingWithDialect('pt-PT');
  backendAnswers({ dialect: 'pt-PT', dialect_applied: false });
  await translateDub('pt', 'nllb');
  expect(dubSession.state.dialectIgnored).toBe(true);

  backendAnswers({ dialect: 'pt-PT', dialect_applied: true });
  await translateDub('pt', 'openai');

  expect(dubSession.state.dialectIgnored).toBe(false);
});

it('keeps the earlier verdict when no translated text landed', async () => {
  editingWithDialect('pt-PT');
  dubSession.setState((current) => ({ ...current, dialectIgnored: true }));
  vi.mocked(apiJson).mockImplementation((path: string) =>
    Promise.resolve(
      path === '/dub/translate'
        ? { translated: [{ id: 'a', error: 'provider down' }], dialect_applied: true }
        : [],
    ),
  );

  await translateDub('pt', 'nllb');

  expect(dubSession.state.dialectIgnored).toBe(true);
});

it('remembers which variant a single-variant engine wrote instead (#3143)', async () => {
  editingWithDialect('pt-PT');
  backendAnswers({ dialect: 'pt-PT', dialect_applied: false, dialect_used: 'pt-BR' });

  await translateDub('pt', 'nllb');

  expect(dubSession.state.dialectIgnored).toBe(true);
  expect(dubSession.state.dialectUsed).toBe('pt-BR');

  backendAnswers({ dialect: 'pt-PT', dialect_applied: true });
  await translateDub('pt', 'openai');

  expect(dubSession.state.dialectUsed).toBeUndefined();
});
