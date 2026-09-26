/**
 * Warn about an under-provisioned engine BEFORE the synth runs, not after.
 *
 * The routing layer has always known that a 4 GB card running an engine that
 * wants 6 GB will be slow enough to hit the 300s compute budget. Until now the
 * only place that knowledge surfaced was the engine-*pick* toast — so it fired
 * for people who changed engines, and never for the much larger group whose
 * engine was already selected (persisted from a previous session, or the
 * default). Those users' first contact with the fact was a 300s wait ending in
 * "this job was too heavy": #1240, #1246, #1248, #1277, #1283, #1284.
 *
 * The generate response already carries `X-OmniVoice-Routing`, but a response
 * header arrives when the job ends — five minutes too late to be a warning.
 * So the check runs at the chokepoint every synth path shares, before the POST.
 *
 * Deliberately NOT blocking, matching the routing layer's contract: the driver
 * can page to system RAM and short inputs fit where long ones don't. It is also
 * deliberately NOT awaited by the caller — a warning must never add latency to
 * the request it is warning about.
 */
import { toast } from 'react-hot-toast';
import i18next from 'i18next';
import { listEngines } from '../api/engines';
import { routingNotice } from './routingNotice';

// Once per (engine, reason) per session. The caveat is a property of the
// hardware, so repeating it on every synth would be nagging, but keying on the
// reason too means a genuinely different problem still gets through.
const warned = new Set();

// One /engines round-trip is cheap, but one per synth is not. Engine picks are
// rare and hardware does not change mid-session; a short TTL keeps a fresh
// install's just-installed engine from being judged on stale data.
const TTL_MS = 60_000;
let cache = null; // { at: number, promise: Promise }

function enginesCached() {
  const now = Date.now();
  if (!cache || now - cache.at > TTL_MS) {
    const entry = { at: now, promise: null };
    // A rejected promise must NOT sit in the cache for the full TTL, or a
    // single blip while the backend restarts silences the caveat for the next
    // minute of synths. Drop it — but only if this entry is still the current
    // one, so a newer fetch that raced past us isn't evicted.
    entry.promise = listEngines().catch((e) => {
      if (cache === entry) cache = null;
      throw e;
    });
    cache = entry;
  }
  return cache.promise;
}

/**
 * Called after an engine pick. The cached /engines response now describes the
 * PREVIOUS engine, and with a 60s TTL a user who switches engines and
 * immediately generates would be warned about the engine they just left — or
 * not warned about the one they just chose (Greptile P1, #1288).
 *
 * `alreadyWarnedFor` is the notice the select toast just showed, if any:
 * recording it here stops the preflight repeating the same sentence seconds
 * later. Different engine or different reason still gets through.
 */
export function onEngineSelected(engineId, alreadyWarnedFor = null) {
  cache = null;
  if (engineId && alreadyWarnedFor) warned.add(`${engineId}|${alreadyWarnedFor}`);
}

/** Test seam — drop the memoized /engines response and the warned-once set. */
export function _resetPreflight() {
  cache = null;
  warned.clear();
}

// Mirrors the backend's own definition of "past short": generate_timeout_for()
// in services/model_manager.py gives the first 1200 characters the flat budget
// and only then starts extending it. Below this the job is inside the budget
// the backend considers generous; above it, on a CPU-class host, it is the
// shape that times out. Keep the two in sync.
const LONG_TEXT_CHARS = 1200;

// The engines the backend's own timeout message names as CPU-tuned
// (model_manager.py: "OmniVoice GGUF and Supertonic-3 are CPU-tuned"). Telling
// someone already running one of these to "try a CPU-tuned engine" is advice
// to switch to what they are using (Greptile P1) — they get the same warning
// without the self-referential suggestion.
const CPU_TUNED_ENGINES = new Set(['omnivoice-gguf', 'supertonic3']);

/**
 * A CPU-only host is a BENIGN routing verdict — nothing is misconfigured, so
 * routingNotice() correctly stays silent. But "nothing is wrong" and "this will
 * finish in time" are different claims: #1299 and #1260 are CPU hosts that hit
 * the 300s budget on long text and got no warning at all, because the
 * under-provisioned-GPU check does not apply to them.
 *
 * MPS (Apple Silicon) gets its own branch for the same reason (#1787/#1778):
 * `generate_timeout_s()` in model_manager.py gives MPS the GPU-family budget,
 * not the CPU one, so a long render there can legitimately exceed it too —
 * the class of report is identical to the CPU case, just a different device.
 *
 * Only fires past the backend's own long-text threshold, so ordinary sentences
 * on a slower device stay quiet.
 */
function warnIfLongTextOnSlowDevice(active, entry, text) {
  const onCpu = entry?.routing_status === 'cpu_only' || entry?.effective_device === 'cpu';
  const onMps = !onCpu && entry?.effective_device === 'mps';
  if ((!onCpu && !onMps) || (text || '').length <= LONG_TEXT_CHARS) return;

  const key = `${active}|${onCpu ? 'cpu' : 'mps'}-long-text`;
  if (warned.has(key)) return;
  warned.add(key);

  const i18nKey = onMps
    ? 'engines.mpsLongText'
    : CPU_TUNED_ENGINES.has(active)
      ? 'engines.cpuLongTextTuned'
      : 'engines.cpuLongText';
  toast(i18next.t(i18nKey, { engine: active }), {
    id: `${onCpu ? 'cpu' : 'mps'}-long-text-${active}`,
    icon: '⏳',
    duration: 12000,
  });
}

/**
 * Fire-and-forget. Never throws, never blocks the synth: an engine list we
 * could not fetch is a reason to stay quiet, not a reason to fail a generate.
 */
export async function warnIfEngineUnderProvisioned(text = '') {
  try {
    const data = await enginesCached();
    const tts = data?.tts;
    const active = tts?.active;
    if (!active) return;
    const entry = (tts.backends || []).find((b) => b.id === active);
    const notice = routingNotice(entry);
    if (!notice) {
      warnIfLongTextOnSlowDevice(active, entry, text);
      return;
    }

    const key = `${active}|${notice.reason}`;
    if (warned.has(key)) return;
    warned.add(key);

    toast(i18next.t('engines.generateCaveat', { engine: active, reason: notice.reason }), {
      id: `generate-caveat-${active}`,
      icon: '⚠️',
      duration: 12000,
    });
  } catch {
    // Offline, backend restarting, shape changed — all fine. This is advice.
  }
}
