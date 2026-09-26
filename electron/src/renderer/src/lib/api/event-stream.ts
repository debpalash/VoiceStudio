import { apiFetch } from './client';
import { splitSSEBuffer, parseSSELine } from '@shared/utils/sseParse';
export class IncompleteTaskStreamError extends Error {
  constructor() {
    super('Task stream ended without a terminal event');
    this.name = 'IncompleteTaskStreamError';
  }
}
export interface TaskEvent {
  type: string;
  [key: string]: unknown;
}
/** Consume the backend's named/JSON task events. Only the caller's terminal event completes a task. */
export async function consumeTaskStream(
  path: string,
  onEvent: (event: TaskEvent) => boolean,
  signal?: AbortSignal,
): Promise<void> {
  const response = await apiFetch(path, { signal });
  if (!response.body) throw new IncompleteTaskStreamError();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let name = '';
  let payload: string[] = [];
  let terminal = false;
  const dispatch = () => {
    if (!payload.length) {
      name = '';
      return false;
    }
    const parsed = parseSSELine('data:' + payload.join('\n'));
    payload = [];
    const eventName = name;
    name = '';
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return false;
    const object = parsed as Record<string, unknown>;
    return onEvent({
      ...object,
      type: eventName || (typeof object.type === 'string' ? object.type : 'message'),
    });
  };
  try {
    while (!terminal) {
      const chunk = await reader.read();
      buffer += decoder.decode(chunk.value, { stream: !chunk.done });
      if (chunk.done && buffer) buffer += '\n';
      const split = splitSSEBuffer(buffer);
      buffer = split.rest;
      for (const raw of split.lines) {
        const line = raw.endsWith('\r') ? raw.slice(0, -1) : raw;
        if (!line) {
          if (dispatch()) {
            terminal = true;
            break;
          }
        } else if (line.startsWith('event:')) name = line.slice(6).trim();
        else if (line.startsWith('data:')) payload.push(line.slice(5).trimStart());
      }
      if (chunk.done) {
        if (!terminal) terminal = dispatch();
        break;
      }
    }
    if (!terminal) throw new IncompleteTaskStreamError();
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
