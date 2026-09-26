/**
 * The mode-aware "can't reach the backend" copy.
 *
 * These messages are the only thing a user has when the backend stops
 * answering, so what they name as a likely cause matters: #1261 was told
 * "it most likely crashed or was killed mid-request" in dev mode, with the
 * backend having answered ten minutes earlier and three dub uploads in quick
 * succession. `bun run dev` runs uvicorn with --reload, where any file change
 * (including a save mid-request) restarts the process and drops the
 * connection — indistinguishable from a crash at the transport layer, and
 * fixed by simply retrying.
 */
import { describe, it, expect, vi } from 'vitest';
describe('dev-mode unreachable copy names the reloader (#1261)', () => {
  // #1261: dev mode, "Failed to fetch", backend had answered 10 min earlier,
  // three dub uploads in quick succession. The message offered only "crashed or
  // was killed" — but `bun run dev` runs uvicorn with --reload, and a reload
  // mid-request produces exactly this with the backend healthy a second later.
  it('offers auto-reload as a cause and tells the user to retry first', async () => {
    const { unreachableBackendMessage } = await import('../utils/backendContact');
    const msg = unreachableBackendMessage('dev');
    expect(msg).toMatch(/auto-reload|reload/i);
    expect(msg).toMatch(/retry/i);
  });

  it('still points at the terminal and the log as the fallback', async () => {
    const { unreachableBackendMessage } = await import('../utils/backendContact');
    const msg = unreachableBackendMessage('dev');
    expect(msg).toContain('bun run dev');
    expect(msg).toContain('omnivoice.log');
  });

  it('leaves the server-mode copy alone', async () => {
    const { unreachableBackendMessage } = await import('../utils/backendContact');
    const msg = unreachableBackendMessage('server');
    expect(msg).not.toMatch(/auto-reload/i);
    expect(msg).toMatch(/docker logs|journalctl/i);
  });
});

/**
 * Desktop got the same honesty the other modes already had (#1337).
 *
 * #1164 gave dev and server deployments a message built from what we actually
 * knew — whether the backend had answered this session, and how recently.
 * Desktop kept a fixed string: "it may still be starting up, or it stopped."
 *
 * That was often provably wrong. #1337 and #1378 both captured **Last backend
 * response: 2 s before this report**. A backend that answered two seconds ago
 * is not starting up, and saying so sent those users off to wait and retry
 * instead of at the crash notice and the backend log — the two things that
 * would have told them what happened.
 */
describe('desktop unreachable copy tells the last-contact story (#1337)', () => {
  const LS_LAST_CONTACT = 'ov_last_backend_contact';

  const withContact = async (secondsAgo: number | null) => {
    // Reset the module too, not just storage: the last-contact timestamp is
    // cached in a module variable, so a cached import would carry the
    // previous test's contact into the "never answered" case.
    sessionStorage.clear();
    sessionStorage.removeItem(LS_LAST_CONTACT);
    vi.resetModules();
    const mod = await import('../utils/backendContact');
    if (secondsAgo != null) mod.recordBackendContact(Date.now() - secondsAgo * 1000);
    return mod;
  };

  it('tells the last-contact story rather than "still starting up" when it answered seconds ago', async () => {
    // The exact shape of #1337 / #1378.
    const mod = await withContact(2);
    const msg = mod.unreachableBackendMessage('desktop');
    expect(msg).toMatch(/answering .* ago and then stopped/i);
    // The claim its own data contradicts must be gone.
    expect(msg).not.toMatch(/still be starting up/i);
    // #1802/#1805: and so must the claim in the OTHER direction. This used to
    // require "crashed or was killed" here, which overshot what #1337 had
    // actually established: that the backend answered seconds ago, not what
    // silenced it. `apiFetch` reaches this copy only after waiting out the
    // shell's death poll and finding no crash marker, so asserting a crash
    // was wrong exactly when it mattered — two Apple Silicon reporters were
    // sent to Clean & Retry, which rebuilds the Python environment, for a
    // backend that had not died at all.
    expect(msg).not.toMatch(/crashed or was killed/i);
    expect(msg).toMatch(/no crash was recorded/i);
  });

  it('says it may never have started when it never answered', async () => {
    const mod = await withContact(null);
    const msg = mod.unreachableBackendMessage('desktop');
    expect(msg).toMatch(/not answered at all this session|never have started/i);
    expect(msg).not.toMatch(/crashed or was killed/i);
  });

  it("names the recovery buttons in the user's own language", async () => {
    // The buttons are translated per locale (French: "Réessayer" /
    // "Nettoyer et réessayer"), so quoting the English labels would send a
    // non-English user hunting for a button that says something else.
    const mod = await withContact(2);
    const msg = mod.unreachableBackendMessage('desktop');
    // Interpolated, not left as raw placeholders.
    expect(msg).not.toMatch(/\{\{retry\}\}|\{\{cleanRetry\}\}/);
    expect(msg).toMatch(/Retry/);
  });

  it('keeps the desktop-only forensics, which the other modes do not have', async () => {
    // The point is to ADD the honest cause, not to lose the shell's own next
    // steps — a dev/server message here would send desktop users to a
    // terminal and a docker log they do not have.
    const mod = await withContact(2);
    const msg = mod.unreachableBackendMessage('desktop');
    expect(msg).toMatch(/Settings → Logs → Backend/);
    expect(msg).toMatch(/Clean & Retry/);
    expect(msg).not.toMatch(/bun run dev|docker logs|journalctl/i);
  });

  it('does not give desktop the dev or server copy', async () => {
    const mod = await withContact(2);
    const desktop = mod.unreachableBackendMessage('desktop');
    expect(desktop).not.toBe(mod.unreachableBackendMessage('dev'));
    expect(desktop).not.toBe(mod.unreachableBackendMessage('server'));
  });
});
