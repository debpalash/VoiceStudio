import { createInstance } from 'i18next';
import { beforeAll, expect, it } from 'vitest';
import enLocale from '@/i18n/locales/en.json';
import deLocale from '@/i18n/locales/de.json';
import { asrSetupHint, engineUnavailableReason } from './engine-guidance';

const i18n = createInstance();
beforeAll(async () => {
  await i18n.init({
    lng: 'de',
    resources: { en: { translation: enLocale }, de: { translation: deLocale } },
    interpolation: { escapeValue: false },
  });
});
const engine = (id: string, hint: string | null = 'Authored server setup hint') => ({
  id,
  display_name: id,
  available: false,
  reason: null,
  install_hint: hint,
});

it.each([
  ['whisperx', 'pip install whisperx'],
  ['faster-whisper', 'pip install faster-whisper'],
  ['moonshine', 'uv pip install moonshine-onnx'],
  ['funasr', 'pip install funasr'],
  ['sherpa-onnx-asr', 'uv add sherpa-onnx'],
  ['mlx-whisper', 'pip install mlx-whisper'],
  ['parakeet-mlx', 'uv add parakeet-mlx'],
  ['pytorch-whisper', 'transformers'],
  ['whisper-cpp', 'backend/engines/whisper_cpp/bin/'],
  ['nemo-parakeet', 'transformers>=4.57,<4.58'],
  ['faster-whisper-isolated', 'faster-whisper'],
  ['openai-compat-asr', 'OpenAI'],
])('localizes known ASR %s and retains its technical data', (id, technicalData) => {
  const english = asrSetupHint(engine(id), i18n.getFixedT('en'));
  const german = asrSetupHint(engine(id), i18n.getFixedT('de'));
  expect(german).not.toBe(english);
  expect(german).not.toBe('Authored server setup hint');
  expect(german).toContain(technicalData);
  expect(german).not.toContain('{{');
});

it('retains custom-engine guidance and absent metadata from older servers', () => {
  expect(asrSetupHint(engine('custom-asr'), i18n.t)).toBe('Authored server setup hint');
  expect(asrSetupHint(engine('constructor'), i18n.t)).toBe('Authored server setup hint');
  expect(asrSetupHint(engine('nemo-parakeet', null), i18n.t)).toBeNull();
});

it.each(['Model Catalogue', 'Settings → Models'])(
  'localizes the four owned reasons from %s',
  (destination) => {
    for (const reason of [
      `This engine's package isn't installed yet. Install it from ${destination}.`,
      `License not accepted yet. Review and accept it in ${destination} to enable this engine.`,
      `This engine needs to be configured before it can run. Open ${destination} to finish setting it up.`,
      `A file this engine needs is missing or unreadable. Reinstall it from ${destination}.`,
    ]) {
      expect(engineUnavailableReason(reason, i18n.t)).toContain('Einstellungen → Modelle');
      expect(engineUnavailableReason(reason, i18n.t)).not.toBe(reason);
    }
  },
);

it('leaves other owned reasons and custom server details unchanged', () => {
  const reason = 'This engine runs on CPU on this host.';
  expect(engineUnavailableReason(reason, i18n.t)).toBe(reason);
  expect(engineUnavailableReason('Needs setup', i18n.t)).toBe('Needs setup');
  expect(engineUnavailableReason(null, i18n.t)).toBeNull();
});

it('keeps custom server guidance compatible and does not guess a remedy for unknown failures', () => {
  const reason = 'Custom server availability reason';
  expect(asrSetupHint({ ...engine('custom-asr'), reason }, i18n.t)).toBe(
    'Authored server setup hint',
  );
  expect(asrSetupHint({ ...engine('openai-compat-asr'), reason }, i18n.t)).toBe(reason);
});
