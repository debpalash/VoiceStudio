/**
 * notifyEngineSelected — post-select feedback for a TTS/ASR/LLM engine pick.
 *
 * `/engines/select` echoes the routing verdict (routing_status /
 * effective_device / routing_reason) for the picked engine on THIS host
 * precisely so the UI can warn when the pick lands on a CPU fallback — it
 * still runs, just slower. Before this, every pick fired the same plain
 * "switched" success toast, hiding the fact that a GPU engine was silently
 * downgraded to CPU on this machine (backend note: engines.py select echo).
 *
 *   - routing_status === 'cpu_fallback' → warn-tone toast naming the reason.
 *   - accelerated BUT carrying a caveat → warn-tone toast naming the reason.
 *   - everything else → plain success.
 *
 * That middle case is #1226's under-provisioned-VRAM caveat, and dropping it
 * is what made the low-VRAM reports so bad (#1240, #1246, #1248, #1277 — four
 * on 4 GB cards, one on 6 GB). Routing computes the warning ("this engine
 * wants about 6 GB"), returns it in routing_reason, and this function used to
 * throw it away for anything that wasn't a CPU fallback: the pick returned a
 * green "switched" success, and the user only learned their card was
 * under-provisioned after waiting out the entire 300s compute budget.
 *
 * Advisory, not blocking — matching the routing layer's deliberate contract
 * (the driver can page to system RAM, and short inputs fit where long ones
 * don't). The engine still gets selected; the user just finds out now instead
 * of five minutes from now.
 *
 * Shared by Model Catalogue and the first-run WizardLibrary so both paths
 * consume the echo identically.
 */
import { toast } from 'react-hot-toast';
import { routingNotice } from './routingNotice';
import { onEngineSelected } from './generatePreflight';

export function notifyEngineSelected(r, t, family = 'tts') {
  // routingNotice() is the shared mirror of the backend's routing_notice():
  // cpu_fallback always, accelerated only when it carries a caveat. Everything
  // else — benign cpu_only (a DirectML host explains itself on a normal pick)
  // and unavailable — is information, not a warning.
  const notice = routingNotice(r);
  // Every engine pick lands here (Model Catalogue and the first-run
  // WizardLibrary both call it), which makes it the one place that reliably
  // knows the active engine just changed: drop the preflight's cached
  // /engines response, and hand it whatever caveat we are about to show so it
  // does not repeat the same sentence on the next synth.
  onEngineSelected(r?.active, notice?.reason ?? null);
  if (notice?.status === 'cpu_fallback') {
    toast(t('engines.selectCpuFallback', { engine: r.active, reason: notice.reason }), {
      icon: '⚠️',
    });
    return;
  }
  // Accelerated, but routing attached a caveat worth hearing before the first
  // generate rather than after it times out. Longer duration than a success
  // toast: it names the hardware limit and the ways around it.
  if (notice) {
    toast(t('engines.selectWithCaveat', { engine: r.active, reason: notice.reason }), {
      icon: '⚠️',
      duration: 10000,
    });
    return;
  }
  toast.success(
    t('settings.engine_switched', {
      family: (family || '').toUpperCase(),
      engine: r?.active,
    }),
  );
}
