const CHANNEL_COUNTS = {
  mono: 1,
  stereo: 2,
};

export function createInputLevelStore(initialLevel = 0) {
  let level = initialLevel;
  const listeners = new Set();
  return {
    getSnapshot: () => level,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    set(nextLevel) {
      if (Math.abs(nextLevel - level) < 0.005) return;
      level = nextLevel;
      listeners.forEach((listener) => listener());
    },
  };
}

export function buildAudioInputConstraints(deviceId = '', channelMode = 'auto') {
  const audio = {};
  if (deviceId) audio.deviceId = { exact: deviceId };
  if (CHANNEL_COUNTS[channelMode]) audio.channelCount = { ideal: CHANNEL_COUNTS[channelMode] };
  return { audio: Object.keys(audio).length ? audio : true };
}

export async function listAudioInputs(mediaDevices = navigator.mediaDevices) {
  if (!mediaDevices?.enumerateDevices) return [];
  const devices = await mediaDevices.enumerateDevices();
  return devices.filter((device) => device.kind === 'audioinput');
}

export function startInputLevelMonitor(
  stream,
  onLevel,
  {
    AudioContextClass = globalThis.AudioContext || globalThis.webkitAudioContext,
    requestFrame = globalThis.requestAnimationFrame,
    cancelFrame = globalThis.cancelAnimationFrame,
  } = {},
) {
  if (!AudioContextClass || !requestFrame || !cancelFrame) return () => {};

  const context = new AudioContextClass();
  let source;
  let analyser;
  let silentGain;
  let frameId;
  let stopped = false;
  const closeContext = () => {
    try {
      void Promise.resolve(context.close?.()).catch(() => {});
    } catch {
      // Already closed or unsupported; nothing left to release.
    }
  };
  const disconnectGraph = () => {
    for (const node of [source, analyser, silentGain]) {
      try {
        node?.disconnect();
      } catch {
        // Node never connected or already torn down.
      }
    }
  };
  try {
    source = context.createMediaStreamSource(stream);
    analyser = context.createAnalyser();
    silentGain = context.createGain();
    const samples = new Float32Array(512);
    analyser.fftSize = 1024;
    analyser.smoothingTimeConstant = 0.72;
    silentGain.gain.value = 0;
    source.connect(analyser);
    analyser.connect(silentGain);
    silentGain.connect(context.destination);
    void Promise.resolve(context.resume?.()).catch(() => {});

    const sample = () => {
      if (stopped) return;
      analyser.getFloatTimeDomainData(samples);
      let energy = 0;
      for (const value of samples) energy += value * value;
      onLevel(Math.min(1, Math.sqrt(energy / samples.length) * 4));
      frameId = requestFrame(sample);
    };
    frameId = requestFrame(sample);
  } catch (error) {
    // Setup failed after the context exists: an unclosed AudioContext keeps
    // the audio device open, and nobody holds a stop handle to release it.
    stopped = true;
    try {
      if (frameId !== undefined) cancelFrame(frameId);
    } catch {
      // Best-effort.
    }
    disconnectGraph();
    closeContext();
    throw error;
  }

  return () => {
    if (stopped) return;
    stopped = true;
    try {
      cancelFrame(frameId);
    } finally {
      disconnectGraph();
      closeContext();
      onLevel(0);
    }
  };
}
