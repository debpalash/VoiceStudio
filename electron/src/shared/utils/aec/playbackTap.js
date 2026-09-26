// Playback-reference tap for the opt-in AEC path (parity Action 8). Routes an
// <audio>/<video> element's decoded output through a Web Audio graph so the
// dictation AEC can use it as the far-end echo reference — WITHOUT changing
// what the user hears. Only ever called when AEC is enabled; the default
// playback path never constructs an AudioContext.
//
// Constraint: createMediaElementSource() may be called at most once per
// element, and once called the element's audio routes ONLY through the graph.
// So we (a) memoise the source per element, (b) always reconnect it to
// destination to keep it audible, and (c) on detach disconnect only the tap
// node — never the element→destination edge — so toggling AEC or remounting
// the player never silences playback.

import { publishFarEnd } from './farEndBus';
import { buildAntiAliasChain, resampleInterleavedFrame } from './micCapture';

const WORKLET_URL = '/aec-worklet.js';

// element → { ctx, src } so we never double-tap or double-source an element.
const tapped = new WeakMap();

/**
 * Attach (or re-attach) a far-end tap to ``mediaEl``. Returns an async
 * detach() that stops publishing but keeps the element audible.
 */
export async function attachPlaybackTap(mediaEl, { sampleRate = 16000, frameSize = 320 } = {}) {
  if (!mediaEl) return async () => {};

  let entry = tapped.get(mediaEl);
  if (!entry) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    const ctx = new Ctx({ sampleRate });
    await ctx.audioWorklet.addModule(WORKLET_URL);
    const src = ctx.createMediaElementSource(mediaEl);
    src.connect(ctx.destination); // keep playback audible — set up once, forever
    entry = { ctx, src };
    tapped.set(mediaEl, entry);
  }

  const { ctx, src } = entry;
  if (ctx.state === 'suspended') {
    try {
      await ctx.resume();
    } catch {
      /* gesture may be required; harmless */
    }
  }
  const channels = 1;
  const sourceFrameSize = Math.max(1, Math.round((frameSize * ctx.sampleRate) / sampleRate));
  const node = new AudioWorkletNode(ctx, 'aec-frame-emitter', {
    processorOptions: { frameSize: sourceFrameSize, channels },
  });
  node.port.onmessage = (e) =>
    publishFarEnd(resampleInterleavedFrame(e.data, ctx.sampleRate, sampleRate, channels));
  // The far-end reference decimates exactly like the mic path, so it needs the
  // same anti-alias low-pass before resampling — an aliased reference makes
  // the AEC subtract tones the speaker never played. Filters sit only on the
  // tap branch: the src → destination edge stays untouched, so what the user
  // hears is unchanged.
  const antiAlias = ctx.sampleRate > sampleRate ? buildAntiAliasChain(ctx, sampleRate) : [];
  const chain = [src, ...antiAlias, node];
  for (let i = 0; i < chain.length - 1; i += 1) chain[i].connect(chain[i + 1]);

  return async function detach() {
    try {
      node.port.onmessage = null;
    } catch {
      /* ignore */
    }
    try {
      node.disconnect();
    } catch {
      /* ignore */
    }
    for (const filter of antiAlias) {
      try {
        filter.disconnect();
      } catch {
        /* ignore */
      }
    }
    // Detach the tap edge too: the ctx and src are memoised per element, so a
    // filter left hanging off src would accumulate one dead chain per AEC
    // toggle. The audible src→destination edge stays (see header note).
    try {
      src.disconnect(antiAlias[0] ?? node);
    } catch {
      /* ignore */
    }
  };
}
