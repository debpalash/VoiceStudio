// PCM framing helpers for the opt-in dictate-over-playback AEC (parity
// Action 8). The backend's `/ws/transcribe?aec=1` mode expects raw int16 mono
// PCM frames, each prefixed with a 1-byte tag so it can tell the microphone
// from the playback reference it must cancel. These helpers are pure (no Web
// Audio) so they can be unit-tested without an AudioContext.

export const AEC_NEAR = 0x00; // microphone frame
export const AEC_FAR = 0x01; // playback-reference frame

/**
 * Convert Float32 samples in [-1, 1] to Int16Array (clamped). Mirrors the
 * encode math in utils/audioTrim.js so the wire format matches the backend's
 * stdlib-`wave` reader.
 */
export function floatToInt16(float32) {
  const out = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    let s = float32[i];
    if (s > 1) s = 1;
    else if (s < -1) s = -1;
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

/**
 * Build a tagged binary frame: ``[1-byte kind][int16 LE PCM…]`` as an
 * ArrayBuffer ready for ``ws.send()``. All VoiceStudio target platforms are
 * little-endian (x86_64 / arm64), matching numpy's native int16 read on the
 * server, so the Int16Array bytes are copied verbatim.
 */
export function tagFrame(int16, kind) {
  const pcm = int16 instanceof Int16Array ? int16 : new Int16Array(int16);
  const buf = new ArrayBuffer(1 + pcm.byteLength);
  new DataView(buf).setUint8(0, kind);
  new Uint8Array(buf, 1).set(new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength));
  return buf;
}

/** Convenience: Float32 mono frame → tagged ArrayBuffer in one step. */
export function frameFromFloat(float32, kind) {
  return tagFrame(floatToInt16(float32), kind);
}
