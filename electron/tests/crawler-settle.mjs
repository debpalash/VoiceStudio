/** Wait for a continuous API-quiet window, including traffic between samples. */
export async function waitForQuietRequests(
  traffic,
  { wait, graceMs, budgetMs, now = () => performance.now(), snapshot = async () => undefined },
) {
  const deadline = now() + budgetMs;
  let revision = traffic.activity;
  let quietSince = traffic.inflight.size ? null : now();
  while (true) {
    const time = now();
    if (revision !== traffic.activity || traffic.inflight.size) {
      revision = traffic.activity;
      quietSince = traffic.inflight.size ? null : time;
    }
    if (time >= deadline) return { settled: false, pending: traffic.inflight.size };
    if (quietSince !== null && time - quietSince >= graceMs) {
      let timer;
      let observation;
      try {
        observation = await Promise.race([
          Promise.resolve().then(snapshot).then((value) => ({ value })),
          new Promise((resolve) => {
            timer = setTimeout(() => resolve({ unresponsive: true }), Math.max(0, deadline - now()));
          }),
        ]);
      } finally {
        clearTimeout(timer);
      }
      if (observation.unresponsive || now() >= deadline)
        return { settled: false, pending: traffic.inflight.size, unresponsive: true };
      const value = observation.value;
      // A new poll can start while Playwright reads the DOM. Observe it only
      // after its own quiet window, under the original total deadline.
      if (revision === traffic.activity && !traffic.inflight.size)
        return { settled: true, pending: 0, snapshot: value };
      continue;
    }
    await wait(Math.min(50, deadline - time, quietSince === null ? 50 : graceMs - (time - quietSince)));
  }
}

/**
 * The page's same-origin requests still in flight, as `waitForQuietRequests` reads them.
 *
 * Playwright emits neither `requestfinished` nor `requestfailed` for a request that a reload of
 * the page cancels (Playwright 1.64: a page polling two endpoints leaked about two requests per
 * reload). Left in `inflight`, those never settle, so every later click waits out the whole
 * request budget: "Reset and reload" on /settings/storage turned one shard into a 33-minute
 * failure. A request belongs to the document committed when it started; once a new main-frame
 * document commits, requests of the old one are over even though no event says so.
 */
export function createTraffic({ now = () => performance.now() } = {}) {
  const started = new Map(); // request -> { document, at, cause }
  let document = 0;
  let navigating = false;
  const traffic = {
    inflight: new Set(),
    activity: 0,
    /** What the crawler is doing now; a request that never finishes names it. */
    cause: '',
    start(request, { navigation = false } = {}) {
      if (navigation) navigating = true;
      traffic.inflight.add(request);
      started.set(request, { document, at: now(), cause: traffic.cause });
      traffic.activity += 1;
    },
    finish(request) {
      started.delete(request);
      if (traffic.inflight.delete(request)) traffic.activity += 1;
    },
    /** A navigation request failed, so the current document stays. */
    navigationFailed() {
      navigating = false;
    },
    /** The main frame navigated; only a navigation that loaded a new document drops requests. */
    committed() {
      if (!navigating) return; // same-document (hash or history) navigation
      navigating = false;
      document += 1;
      for (const request of traffic.inflight)
        if (started.get(request).document < document) traffic.finish(request);
    },
    /** The longest-pending request, if it has been in flight for at least `ms`. */
    stalled(ms) {
      let oldest = null;
      for (const request of traffic.inflight) {
        const entry = started.get(request);
        if (!oldest || entry.at < oldest.at) oldest = { request, ...entry };
      }
      return oldest && now() - oldest.at >= ms ? { ...oldest, ageMs: now() - oldest.at } : null;
    },
  };
  return traffic;
}

/**
 * The request that stalled a click, if any. Judged only from the click's own wait, and only when
 * that wait ended unsettled (already a failed run): a request held through the waits before a
 * click may still finish in time, so those waits never stop a mode that could pass.
 */
export function stalledClick(observation, traffic, ms) {
  return observation.settled ? null : traffic.stalled(ms);
}

/**
 * The request still open after a route's last click, if it has been pending for `ms`. Catches a
 * hang that no click judged because every click missed or the screen had none; a request that
 * settled during any click is no longer in flight here.
 */
export function stalledRoute(traffic, ms) {
  return traffic.stalled(ms);
}

/**
 * Whether a request counts toward the quiet window: every same-origin load except event
 * streams, which stay open by design. Not only `/api/`: lazily loaded code shares the browser's
 * six connections per host with the API, so a screen still downloading modules after the crawler
 * leaves it queues the next screen's API reads behind them and fails that screen's clicks.
 */
export function tracksRequest(url, resourceType, origin) {
  return resourceType !== 'eventsource' && new URL(url).origin === origin;
}
