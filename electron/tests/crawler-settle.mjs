/**
 * Wait for a continuous API-quiet window, including traffic between samples.
 *
 * `quietFrom` is when the page last saw activity. Omitted, the window starts now, so it measures
 * time after a click. Given, quiet time the page already had counts toward the window.
 */
export async function waitForQuietRequests(
  traffic,
  {
    wait,
    graceMs,
    budgetMs,
    now = () => performance.now(),
    snapshot = async () => undefined,
    quietFrom,
  },
) {
  const start = now();
  const deadline = start + budgetMs;
  let revision = traffic.activity;
  let quietSince = traffic.inflight.size ? null : Math.min(start, quietFrom ?? start);
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
