/**
 * retryInitialLoad — run an initial data load until first success.
 *
 * The INITIAL app load differs from later (WS-triggered) reloads: on reload a
 * failure keeps the previous list, but on first load there IS no previous
 * list, so a single transient failure left the panel empty forever and read
 * to users as "my voices are gone" (#1158 class). Retry with backoff until
 * the first success; afterwards the caller's normal keep-previous-list
 * behavior takes over.
 *
 * `opts.cancelled` is the caller's mount-cancellation flag — set it to true
 * and any pending wait resolves without another attempt.
 */
export async function retryInitialLoad(loader, opts = {}) {
  const baseDelayMs = opts.baseDelayMs ?? 500;
  const maxDelayMs = opts.maxDelayMs ?? 4000;
  let delay = 0; // first attempt is immediate; only failures pay the backoff
  for (;;) {
    if (opts.cancelled) return;
    try {
      const applied = await loader();
      if (applied !== false) return;
    } catch {
      // transient — retry
    }
    await new Promise((r) => setTimeout(r, delay || baseDelayMs));
    delay = Math.min((delay || baseDelayMs) * 2, maxDelayMs);
  }
}

/** Apply only the newest invocation's response for a named list. */
export async function loadLatest({ generations, key, fetch, apply, label, rethrow = false }) {
  const generation = (generations[key] ?? 0) + 1;
  generations[key] = generation;
  try {
    const data = await fetch();
    if (generation !== generations[key]) return false;
    apply(data);
    return true;
  } catch (error) {
    console.warn(`Failed to load ${label}:`, error);
    if (rethrow) throw error;
    return false;
  }
}
