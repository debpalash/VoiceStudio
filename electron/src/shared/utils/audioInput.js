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
  const source = context.createMediaStreamSource(stream);
  const analyser = context.createAnalyser();
  const silentGain = context.createGain();
  const samples = new Float32Array(512);
  analyser.fftSize = 1024;
  analyser.smoothingTimeConstant = 0.72;
  silentGain.gain.value = 0;
  source.connect(analyser);
  analyser.connect(silentGain);
  silentGain.connect(context.destination);
  void Promise.resolve(context.resume?.()).catch(() => {});

  let frameId;
  let stopped = false;
  const sample = () => {
    if (stopped) return;
    analyser.getFloatTimeDomainData(samples);
    let energy = 0;
    for (const value of samples) energy += value * value;
    onLevel(Math.min(1, Math.sqrt(energy / samples.length) * 4));
    frameId = requestFrame(sample);
  };
  frameId = requestFrame(sample);

  return () => {
    if (stopped) return;
    stopped = true;
    cancelFrame(frameId);
    source.disconnect();
    analyser.disconnect();
    silentGain.disconnect();
    void Promise.resolve(context.close?.()).catch(() => {});
    onLevel(0);
  };
}
