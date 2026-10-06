/**
 * Microphone capture helpers behind `useRecording`: device enumeration, the
 * getUserMedia constraint builder, a MediaRecorder container fallback list,
 * an RMS input-level monitor, and the getUserMedia error → i18n key mapping.
 */

export type ChannelMode = 'auto' | 'mono' | 'stereo';

const CHANNEL_COUNTS: Record<ChannelMode, number | undefined> = {
  auto: undefined,
  mono: 1,
  stereo: 2,
};

export function buildAudioInputConstraints(
  deviceId: string,
  channelMode: ChannelMode,
): MediaStreamConstraints {
  const audio: MediaTrackConstraints = {};
  if (deviceId) audio.deviceId = { exact: deviceId };
  const channels = CHANNEL_COUNTS[channelMode];
  if (channels) audio.channelCount = { ideal: channels };
  return { audio: Object.keys(audio).length ? audio : true };
}

export async function listAudioInputs(): Promise<MediaDeviceInfo[]> {
  const mediaDevices = typeof navigator === 'undefined' ? undefined : navigator.mediaDevices;
  if (!mediaDevices || typeof mediaDevices.enumerateDevices !== 'function') return [];
  const devices = await mediaDevices.enumerateDevices();
  return devices.filter((device) => device.kind === 'audioinput');
}

// Chromium encodes WebM/Opus; the rest are kept so an unusual build (or a
// future engine swap) still finds a container it can produce.
const AUDIO_TYPES: ReadonlyArray<readonly [mimeType: string, extension: string]> = [
  ['audio/webm;codecs=opus', 'webm'],
  ['audio/webm', 'webm'],
  ['audio/ogg;codecs=opus', 'ogg'],
  ['audio/ogg', 'ogg'],
  ['audio/mp4', 'm4a'],
];

export function extensionForMime(mimeType: string): string {
  const mime = mimeType.toLowerCase();
  if (mime.includes('ogg')) return 'ogg';
  if (mime.includes('mp4') || mime.includes('aac')) return 'm4a';
  return 'webm';
}

export interface StartedRecorder {
  recorder: MediaRecorder;
  mimeType: string;
  extension: string;
}

export interface RecorderCallbacks {
  onData: (chunk: Blob) => void;
  onStop: () => void;
  timesliceMs?: number;
}

/**
 * Construct and start the first MediaRecorder container the engine accepts.
 * Some engines reject a mime type only from `start()`, after construction
 * succeeded, so each candidate is started before it is trusted. Returns null
 * when none work.
 */
export function startSupportedMediaRecorder(
  stream: MediaStream,
  { onData, onStop, timesliceMs = 250 }: RecorderCallbacks,
): StartedRecorder | null {
  if (typeof MediaRecorder !== 'function') return null;
  const canProbe = typeof MediaRecorder.isTypeSupported === 'function';
  const candidates: Array<{ mimeType: string | null; extension: string }> = AUDIO_TYPES.filter(
    ([mimeType]) => !canProbe || MediaRecorder.isTypeSupported(mimeType),
  ).map(([mimeType, extension]) => ({ mimeType, extension }));
  candidates.push({ mimeType: null, extension: 'webm' });

  for (const candidate of candidates) {
    let recorder: MediaRecorder | undefined;
    try {
      recorder = candidate.mimeType
        ? new MediaRecorder(stream, { mimeType: candidate.mimeType })
        : new MediaRecorder(stream);
      recorder.ondataavailable = (event: BlobEvent) => {
        if (event.data.size > 0) onData(event.data);
      };
      recorder.onstop = () => onStop();
      recorder.start(timesliceMs);
      const mimeType = recorder.mimeType || candidate.mimeType || '';
      return {
        recorder,
        mimeType,
        extension: candidate.mimeType ? candidate.extension : extensionForMime(mimeType),
      };
    } catch {
      // Detach the callbacks before cleanup so a rejected candidate can never
      // deliver an empty recording to the caller.
      if (recorder) {
        recorder.ondataavailable = null;
        recorder.onstop = null;
        try {
          if (recorder.state === 'recording') recorder.stop();
        } catch {
          // Move on to the next container.
        }
      }
    }
  }
  return null;
}

/**
 * Feed a 0..1 RMS level from `stream` to `onLevel` on every animation frame.
 * Returns a stop function that tears the graph down and reports 0.
 */
export function startInputLevelMonitor(
  stream: MediaStream,
  onLevel: (level: number) => void,
): () => void {
  if (
    typeof AudioContext === 'undefined' ||
    typeof requestAnimationFrame !== 'function' ||
    typeof cancelAnimationFrame !== 'function'
  ) {
    return () => {};
  }
  const context = new AudioContext();
  try {
    const source = context.createMediaStreamSource(stream);
    const analyser = context.createAnalyser();
    // Route through a muted gain so the graph stays "connected to a destination"
    // (some engines never pull data from a dangling analyser) without echoing
    // the microphone to the speakers.
    const silentGain = context.createGain();
    analyser.fftSize = 1024;
    analyser.smoothingTimeConstant = 0.72;
    silentGain.gain.value = 0;
    source.connect(analyser);
    analyser.connect(silentGain);
    silentGain.connect(context.destination);
    void context.resume().catch(() => {});

    const samples = new Float32Array(analyser.fftSize / 2);
    let frameId = 0;
    let stopped = false;
    const sample = (): void => {
      if (stopped) return;
      analyser.getFloatTimeDomainData(samples);
      let energy = 0;
      for (const value of samples) energy += value * value;
      onLevel(Math.min(1, Math.sqrt(energy / samples.length) * 4));
      frameId = requestAnimationFrame(sample);
    };
    frameId = requestAnimationFrame(sample);

    return () => {
      if (stopped) return;
      stopped = true;
      cancelAnimationFrame(frameId);
      source.disconnect();
      analyser.disconnect();
      silentGain.disconnect();
      void context.close().catch(() => {});
      onLevel(0);
    };
  } catch (error) {
    // The recording hook tolerates meter failures, so release its context here.
    void context.close().catch(() => {});
    throw error;
  }
}

export interface MicErrorDescription {
  key: string;
  params?: Record<string, string>;
}

/** Map a getUserMedia rejection to the `recording.*` i18n key that tells the user what to do. */
export function describeMicError(err: unknown): MicErrorDescription {
  const name = typeof err === 'object' && err !== null ? (err as { name?: unknown }).name : '';
  switch (name) {
    // Denied by the OS privacy setting, the engine, or a non-secure context.
    case 'NotAllowedError':
    case 'PermissionDeniedError':
    case 'SecurityError':
      return { key: 'recording.mic_denied' };
    // No usable input device.
    case 'NotFoundError':
    case 'DevicesNotFoundError':
    case 'OverconstrainedError':
      return { key: 'recording.mic_not_found' };
    // Device exists but cannot be started (held by another app, driver issue).
    case 'NotReadableError':
    case 'TrackStartError':
    case 'AbortError':
      return { key: 'recording.mic_busy' };
    default: {
      const message = err instanceof Error ? err.message : err == null ? 'unknown' : String(err);
      return { key: 'recording.mic_error', params: { message } };
    }
  }
}
