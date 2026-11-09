import { afterEach, describe, expect, it, vi } from "vitest";
import { updateRestartBlocked } from "./update-actions";

// Through the real API client: its fetch wrapper reports any fetch failure,
// a timeout included, as an unreachable backend (status 0).
afterEach(() => vi.unstubAllGlobals());

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

describe("updateRestartBlocked", () => {
  it("defers when the work-status request times out on a busy backend", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: string, init?: RequestInit) =>
          new Promise((_resolve, reject) =>
            init?.signal?.addEventListener("abort", () =>
              reject(init.signal!.reason),
            ),
          ),
      ),
    );
    await expect(updateRestartBlocked(20)).resolves.toBe(true);
  });

  it("allows the restart when the backend is stopped", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    await expect(updateRestartBlocked(1_000)).resolves.toBe(false);
  });

  it("allows the restart when the backend reports no work", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        String(url).includes("/batch/") ? json([]) : json({ jobs: [] }),
      ),
    );
    await expect(updateRestartBlocked(1_000)).resolves.toBe(false);
  });

  it("defers while the backend answers 503", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ detail: "busy" }, 503)));
    await expect(updateRestartBlocked(1_000)).resolves.toBe(true);
  });
});
