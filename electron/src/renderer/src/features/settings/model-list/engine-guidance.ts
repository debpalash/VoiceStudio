import type { TFunction } from 'i18next';
import type { EngineBackend } from '@/lib/api/types';

const packageCommands = new Map([
  ['whisperx', 'pip install whisperx'],
  ['faster-whisper', 'pip install faster-whisper'],
  ['moonshine', 'uv pip install moonshine-onnx'],
  ['funasr', 'pip install funasr'],
  ['sherpa-onnx-asr', 'uv add sherpa-onnx'],
]);
const appleCommands = new Map([
  ['mlx-whisper', 'pip install mlx-whisper'],
  ['parakeet-mlx', 'uv add parakeet-mlx'],
]);

const bundledEngines = new Set(['openai-compat-asr', 'pytorch-whisper', 'faster-whisper-isolated']);
const otherKnownEngines = new Set(['nemo-parakeet', 'whisper-cpp']);

/** Localize known authored ASR guidance; older/custom engines keep their hint. */
export function asrSetupHint(engine: EngineBackend, t: TFunction) {
  if (!engine.install_hint) return engine.install_hint;
  const category = ownedUnavailableCategory(engine.reason);
  // Setup advice must address the actual availability failure, not hide it.
  if (category === 'platform' || category === 'noMps' || category === 'license')
    return engineUnavailableReason(engine.reason, t);
  const known =
    packageCommands.has(engine.id) ||
    appleCommands.has(engine.id) ||
    bundledEngines.has(engine.id) ||
    otherKnownEngines.has(engine.id);
  if (!known) return engine.install_hint;
  if (engine.reason && !category) return engine.reason;
  if (bundledEngines.has(engine.id) && (category === 'notInstalled' || category === 'fileMissing'))
    return t('engines.asrSetupRepairBundled');
  if (category === 'fileMissing' && !['nemo-parakeet', 'whisper-cpp'].includes(engine.id))
    return engineUnavailableReason(engine.reason, t);
  if (category === 'needsConfig' && engine.id !== 'openai-compat-asr')
    return engineUnavailableReason(engine.reason, t);
  const command = packageCommands.get(engine.id);
  if (command) return t('engines.asrSetupPackageInstall', { command });
  const appleCommand = appleCommands.get(engine.id);
  if (appleCommand) return t('engines.asrSetupApplePackageInstall', { command: appleCommand });
  switch (engine.id) {
    case 'pytorch-whisper':
      return t('engines.asrSetupBundled');
    case 'whisper-cpp':
      return t('engines.asrSetupWhisperCpp', {
        binary: 'whisper-cli',
        folder: 'backend/engines/whisper_cpp/bin/',
      });
    case 'nemo-parakeet':
      return t('engines.asrSetupNemoParakeet', {
        nemoRequirement: 'transformers>=4.57,<4.58',
        appRequirement: 'transformers>=5.3',
      });
    case 'faster-whisper-isolated':
      return t('engines.asrSetupIsolated');
    case 'openai-compat-asr':
      return t('engines.asrSetupRemote');
    default:
      return engine.install_hint;
  }
}

const platformReason =
  "This engine doesn't run on this computer's platform. Its guide lists the platforms it supports.";
const noMpsReason =
  "This engine needs Apple's GPU (MPS), and this installation's PyTorch can't use it. Updating macOS or reinstalling VoiceStudio usually restores it.";

/** Classify only exact owned API text; custom server metadata stays compatible. */
function ownedUnavailableCategory(reason: string | null | undefined) {
  if (reason === platformReason) return 'platform';
  if (reason === noMpsReason) return 'noMps';
  for (const destination of ['Model Catalogue', 'Settings → Models']) {
    if (reason === `This engine's package isn't installed yet. Install it from ${destination}.`)
      return 'notInstalled';
    if (
      reason ===
      `License not accepted yet. Review and accept it in ${destination} to enable this engine.`
    )
      return 'license';
    if (
      reason ===
      `This engine needs to be configured before it can run. Open ${destination} to finish setting it up.`
    )
      return 'needsConfig';
    if (
      reason ===
      `A file this engine needs is missing or unreadable. Reinstall it from ${destination}.`
    )
      return 'fileMissing';
  }
  return undefined;
}

/** Match only the API's owned sentences, never arbitrary probe diagnostics. */
export function engineUnavailableReason(reason: string | null | undefined, t: TFunction) {
  switch (ownedUnavailableCategory(reason)) {
    case 'platform':
      return t('engines.setupStatusPlatform');
    case 'noMps':
      return t('engines.setupStatusNoMps');
    case 'notInstalled':
      return t('engines.setupStatusNotInstalled');
    case 'license':
      return t('engines.setupStatusLicense');
    case 'needsConfig':
      return t('engines.setupStatusNeedsConfig');
    case 'fileMissing':
      return t('engines.setupStatusFileMissing');
    default:
      return reason;
  }
}
