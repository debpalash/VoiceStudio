export function consumeLongformStream(
  response: Response,
  onEvent: (event: { type: string; [key: string]: unknown }) => void,
  options?: { signal?: AbortSignal; isAborted?: () => boolean },
): Promise<void>;
