import { expect, it } from 'vitest';
import type { CatalogueModel } from '../model-catalogue-query';
import { familyModels, modelsForEngine, sortByRank, unclaimedModels } from './linking';

const model = (repo_id: string, extra: Partial<CatalogueModel> = {}): CatalogueModel => ({
  repo_id,
  label: repo_id,
  role: 'TTS',
  size_gb: 1,
  installed: false,
  supported: true,
  ...extra,
});

it('links models to engines by catalogue engines, curated picks and registry aliases', () => {
  const models = [
    model('a/omnivoice', { engines: ['omnivoice', 'omnivoice-subprocess'] }),
    model('mlx/kokoro', { engines: ['mlx-audio'] }),
    model('mlx/csm', { engines: ['mlx-audio'] }),
    model('facebook/nllb-200-distilled-600M', { role: 'Translation', engines: [] }),
  ];
  // Curated order wins over catalogue order.
  expect(
    modelsForEngine('mlx-audio', models, [{ repo_id: 'mlx/csm' }, { repo_id: 'mlx/kokoro' }]).map(
      (item) => item.repo_id,
    ),
  ).toEqual(['mlx/csm', 'mlx/kokoro']);
  expect(modelsForEngine('omnivoice-subprocess', models)).toHaveLength(1);
  expect(modelsForEngine('nllb', models).map((item) => item.repo_id)).toEqual([
    'facebook/nllb-200-distilled-600M',
  ]);
  const claimed = modelsForEngine('mlx-audio', models);
  expect(unclaimedModels(models, claimed).map((item) => item.repo_id)).toEqual([
    'a/omnivoice',
    'facebook/nllb-200-distilled-600M',
  ]);
});

it('keeps dictation checkpoints off the ASR page and diarisation bundles on theirs', () => {
  const models = [
    model('asr/whisper', { role: 'ASR' }),
    model('asr/sherpa', { role: 'ASR', dictation_id: 'sherpa' }),
    model('audio-cpp/bundle', { families: ['tts', 'diarisation'] }),
  ];
  expect(familyModels(models, 'asr').map((item) => item.repo_id)).toEqual(['asr/whisper']);
  expect(familyModels(models, 'dictation').map((item) => item.repo_id)).toEqual(['asr/sherpa']);
  expect(familyModels(models, 'diarisation').map((item) => item.repo_id)).toEqual([
    'audio-cpp/bundle',
  ]);
});

it('orders active, then ready, then recommended, keeping backend order for ties', () => {
  const rank = (active: boolean, ready: boolean, recommended: boolean) => ({
    active,
    ready,
    recommended,
  });
  const items = [
    { key: 'rest', rank: rank(false, false, false) },
    { key: 'recommended', rank: rank(false, false, true) },
    { key: 'ready-1', rank: rank(false, true, false) },
    { key: 'active', rank: rank(true, true, false) },
    { key: 'ready-2', rank: rank(false, true, false) },
  ];
  expect(sortByRank(items).map((item) => item.key)).toEqual([
    'active',
    'ready-1',
    'ready-2',
    'recommended',
    'rest',
  ]);
});
