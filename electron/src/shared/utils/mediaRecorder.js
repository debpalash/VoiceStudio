/**
 * Pick a microphone container the current WebView can actually encode.
 *
 * Chromium usually provides WebM/Opus; Safari and WebKitGTK commonly expose
 * MP4 or Ogg instead, and some WebKitGTK builds expose MediaRecorder while
 * rejecting every constructor. Callers must treat `null` as "use PCM".
 */
const AUDIO_TYPES = [
  ['audio/webm;codecs=opus', 'webm'],
  ['audio/webm', 'webm'],
  ['audio/ogg;codecs=opus', 'ogg'],
  ['audio/ogg', 'ogg'],
  ['audio/mp4', 'm4a'],
];

function extensionFor(mimeType) {
  const mime = String(mimeType || '').toLowerCase();
  if (mime.includes('ogg')) return 'ogg';
  if (mime.includes('mp4') || mime.includes('aac')) return 'm4a';
  return 'webm';
}

export function audioFormatForMimeType(mimeType) {
  return { mimeType: String(mimeType || ''), extension: extensionFor(mimeType) };
}

function recorderCandidates(Recorder) {
  const canProbe = typeof Recorder.isTypeSupported === 'function';
  const candidates = AUDIO_TYPES.filter(
    ([mimeType]) => !canProbe || Recorder.isTypeSupported(mimeType),
  ).map(([mimeType, extension]) => ({ options: { mimeType }, mimeType, extension }));
  candidates.push({ options: undefined, mimeType: '', extension: 'webm' });
  return candidates;
}

function tryRecorders(stream, Recorder, prepare) {
  if (typeof Recorder !== 'function') return null;

  for (const candidate of recorderCandidates(Recorder)) {
    let recorder;
    try {
      recorder = candidate.options ? new Recorder(stream, candidate.options) : new Recorder(stream);
      prepare(recorder);
      const mimeType = recorder.mimeType || candidate.mimeType;
      return {
        recorder,
        mimeType,
        extension: candidate.options ? candidate.extension : extensionFor(mimeType),
      };
    } catch {
      // WebKitGTK can accept construction and fail only when start() runs.
      // Detach callbacks before cleanup so a rejected candidate cannot ingest
      // an empty recording.
      if (recorder) {
        recorder.ondataavailable = null;
        recorder.onstop = null;
        try {
          if (recorder.state === 'recording') recorder.stop();
        } catch {
          // Continue to the next format.
        }
      }
    }
  }
  return null;
}

/**
 * @returns {{recorder: MediaRecorder, mimeType: string, extension: string}|null}
 */
export function createSupportedMediaRecorder(
  stream,
  Recorder = typeof MediaRecorder === 'undefined' ? undefined : MediaRecorder,
) {
  return tryRecorders(stream, Recorder, () => {});
}

/**
 * Construct and start the first recorder that works. Some WebKitGTK builds
 * throw NotSupportedError only from start(), after construction succeeds.
 *
 * @returns {{recorder: MediaRecorder, mimeType: string, extension: string}|null}
 */
export function startSupportedMediaRecorder(
  stream,
  { onData, onStop, timeslice = 250 },
  Recorder = typeof MediaRecorder === 'undefined' ? undefined : MediaRecorder,
) {
  return tryRecorders(stream, Recorder, (recorder) => {
    recorder.ondataavailable = onData;
    recorder.onstop = onStop;
    recorder.start(timeslice);
  });
}
