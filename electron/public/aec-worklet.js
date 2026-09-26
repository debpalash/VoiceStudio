// AudioWorklet processor for the opt-in dictate-over-playback AEC (parity
// Action 8). Accumulates Float32 input into fixed-size frames and posts
// them to the main thread, which converts them to tagged int16 PCM. The same
// processor serves both the microphone capture and the playback-reference tap
// — only the wiring on the main thread differs.
//
// Served as a static asset from /public so the AudioContext can load it via
// audioWorklet.addModule('/aec-worklet.js') in both dev and the bundled app.

class AecFrameEmitter extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const frame =
      (options && options.processorOptions && options.processorOptions.frameSize) || 320;
    this._frameSize = frame; // 320 frames = 20 ms @ 16 kHz
    this._channels = Math.max(
      1,
      Math.min(2, (options && options.processorOptions && options.processorOptions.channels) || 1),
    );
    this._buf = new Float32Array(frame * this._channels);
    this._n = 0;
  }

  process(inputs) {
    const input = inputs[0];
    // Interleave requested channels. A missing secondary channel mirrors the
    // first so the WAV layout stays valid on single-channel devices.
    if (input && input[0]) {
      for (let i = 0; i < input[0].length; i++) {
        for (let channel = 0; channel < this._channels; channel++) {
          this._buf[this._n++] = (input[channel] || input[0])[i];
        }
        if (this._n >= this._buf.length) {
          // Copy out — the buffer is reused for the next frame.
          this.port.postMessage(this._buf.slice());
          this._n = 0;
        }
      }
    }
    return true; // keep the processor alive
  }
}

registerProcessor('aec-frame-emitter', AecFrameEmitter);
