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
 * Whether a request counts toward the quiet window: every same-origin load except event
 * streams, which stay open by design. Not only `/api/`: lazily loaded code shares the browser's
 * six connections per host with the API, so a screen still downloading modules after the crawler
 * leaves it queues the next screen's API reads behind them and fails that screen's clicks.
 */
export function tracksRequest(url, resourceType, origin) {
  return resourceType !== 'eventsource' && new URL(url).origin === origin;
}
