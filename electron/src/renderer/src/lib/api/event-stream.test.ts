import { expect, it, vi } from 'vitest';
import { apiFetch } from './client';
import { consumeTaskStream, IncompleteTaskStreamError } from './event-stream';
vi.mock('./client', () => ({ apiFetch: vi.fn() }));
function stream(text: string) {
  const bytes = new TextEncoder().encode(text);
  return new Response(
    new ReadableStream({
      start(controller) {
        for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
        controller.close();
      },
    }),
  );
}
it('preserves named events, UTF-8 and CRLF split across arbitrary chunks', async () => {
  vi.mocked(apiFetch).mockResolvedValueOnce(
    stream('event: final\r\ndata: {"text":"caf\u00e9"}\r\n\r\n'),
  );
  const seen = vi.fn(() => true);
  await consumeTaskStream('/task', seen);
  expect(seen).toHaveBeenCalledExactlyOnceWith({ type: 'final', text: 'caf\u00e9' });
});
it('does not report success when a live task disconnects after progress', async () => {
  vi.mocked(apiFetch).mockResolvedValueOnce(stream('data: {"type":"progress","current":1}\n\n'));
  await expect(consumeTaskStream('/task', () => false)).rejects.toBeInstanceOf(
    IncompleteTaskStreamError,
  );
});
it('handles JSON data split over multiple SSE data lines and an unterminated last frame', async () => {
  vi.mocked(apiFetch).mockResolvedValueOnce(
    stream('data: {"type":"done",\ndata: "tracks":["en"]}'),
  );
  const seen = vi.fn((event) => event.type === 'done');
  await consumeTaskStream('/task', seen);
  expect(seen).toHaveBeenCalledExactlyOnceWith({ type: 'done', tracks: ['en'] });
});
it('ignores malformed events but never treats them as completion', async () => {
  vi.mocked(apiFetch).mockResolvedValueOnce(stream('data: broken\n\n'));
  const seen = vi.fn(() => true);
  await expect(consumeTaskStream('/task', seen)).rejects.toBeInstanceOf(IncompleteTaskStreamError);
  expect(seen).not.toHaveBeenCalled();
});

it('opens the licence dialog for a dub SSE error carrying model_licence_required', async () => {
  const seen = vi.fn();
  window.addEventListener('ov:model-licence-required', seen);
  vi.mocked(apiFetch).mockResolvedValueOnce(
    stream(
      'event: error\ndata: {"code":"model_licence_required","message":"m","models":[{"repo_id":"o/m","fingerprint":"fp"}]}\n\n',
    ),
  );
  const events = vi.fn(() => true);
  await consumeTaskStream('/task', events);
  window.removeEventListener('ov:model-licence-required', seen);
  expect(seen).toHaveBeenCalledTimes(1);
  expect(events).toHaveBeenCalledTimes(1);
});
