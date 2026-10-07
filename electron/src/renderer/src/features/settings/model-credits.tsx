import { useTranslation } from 'react-i18next';
import { InfoIcon } from 'lucide-react';
import { SettingsSection } from './settings-layout';

// Required literal notice: OmniVoice audio_tokenizer/LICENSE, clause 1.b.i.
// Keep legal notices in their original language; localize the surrounding UI.
export const HIGGS_ATTRIBUTION =
  'Built with Higgs Materials licensed from Boson AI USA, Inc., Copyright Boson AI USA, Inc., All Rights Reserved and Meta Llama 3 licensed under the Meta Llama 3 Community License, Copyright Meta Platforms, Inc., All Right Reserved';

const credits = [
  {
    name: 'Parakeet TDT 0.6B v2',
    authors: 'NVIDIA',
    source: 'https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2',
    terms: 'https://creativecommons.org/licenses/by/4.0/',
    license: 'CC BY 4.0',
    adaptations: [
      {
        name: 'sherpa-onnx INT8 · csukuangfj',
        url: 'https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8',
      },
    ],
  },
  {
    name: 'Parakeet TDT 0.6B v3',
    authors: 'NVIDIA',
    source: 'https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3',
    terms: 'https://creativecommons.org/licenses/by/4.0/',
    license: 'CC BY 4.0',
    adaptations: [
      {
        name: 'MLX · mlx-community',
        url: 'https://huggingface.co/mlx-community/parakeet-tdt-0.6b-v3',
      },
      {
        name: 'sherpa-onnx INT8 · csukuangfj',
        url: 'https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8',
      },
    ],
  },
  {
    name: 'WeSpeaker VoxCeleb ResNet34-LM',
    authors: 'WeSpeaker · pyannote',
    source: 'https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM',
    terms: 'https://creativecommons.org/licenses/by/4.0/',
    license: 'CC BY 4.0',
    adaptations: [{ name: 'WeSpeaker', url: 'https://github.com/wenet-e2e/wespeaker' }],
  },
  {
    name: 'Pocket TTS',
    authors: 'Kyutai',
    source: 'https://github.com/kyutai-labs/pocket-tts',
    // The gated weight card has not been retrieved. Do not turn its API
    // license label into a verified model-license or clearance claim.
    terms: 'https://huggingface.co/kyutai/pocket-tts',
  },
  {
    name: 'OmniVoice',
    authors:
      'Han Zhu · Lingxuan Ye · Wei Kang · Zengwei Yao · Liyong Guo · Fangjun Kuang · Zhifeng Han · Weiji Zhuang · Long Lin · Daniel Povey',
    source: 'https://huggingface.co/k2-fsa/OmniVoice',
    // Upstream names CC-BY-NC without specifying a version in its card.
    terms: 'https://huggingface.co/k2-fsa/OmniVoice#license',
    license: 'CC BY-NC',
    adaptations: [
      { name: 'GGUF · Serveurperso', url: 'https://huggingface.co/Serveurperso/OmniVoice-GGUF' },
    ],
  },
  {
    name: 'OmniVoice-GGUF',
    authors: 'Serveurperso · OmniVoice',
    source: 'https://huggingface.co/Serveurperso/OmniVoice-GGUF',
    terms: 'https://creativecommons.org/licenses/by-nc/4.0/',
    license: 'CC BY-NC 4.0',
  },
  {
    name: 'NLLB-200 distilled 600M',
    authors: 'Meta AI',
    source: 'https://huggingface.co/facebook/nllb-200-distilled-600M',
    terms: 'https://creativecommons.org/licenses/by-nc/4.0/',
    license: 'CC BY-NC 4.0',
  },
  {
    name: 'Llama-OuteTTS-1.0-1B · MLX 4-bit',
    authors: 'OuteAI · mlx-community · Meta',
    notice: 'Built with Llama',
    source: 'https://huggingface.co/mlx-community/Llama-OuteTTS-1.0-1B-4bit',
    terms: 'https://huggingface.co/OuteAI/Llama-OuteTTS-1.0-1B',
    license: 'CC BY-NC-SA 4.0',
    adaptations: [
      {
        name: 'OuteAI/Llama-OuteTTS-1.0-1B',
        url: 'https://huggingface.co/OuteAI/Llama-OuteTTS-1.0-1B',
      },
      {
        name: 'Meta Llama 3.2 Community License',
        url: 'https://huggingface.co/meta-llama/Llama-3.2-1B/blob/main/LICENSE.txt',
      },
    ],
  },
];

const linkClass = 'underline underline-offset-4 hover:text-foreground';

export function ModelCredits() {
  const { t } = useTranslation();
  return (
    <SettingsSection icon={InfoIcon} title={t('about.model_credits')}>
      <div className="space-y-3 p-4 text-sm leading-6">
        <p className="text-muted-foreground">{t('about.model_credits_hint')}</p>
        <p lang="en" dir="ltr">
          {HIGGS_ATTRIBUTION}
        </p>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
          <a
            className={linkClass}
            href="https://huggingface.co/k2-fsa/OmniVoice/blob/c5fdb5ccb189668d56333f77ba2629f4cd7535f4/audio_tokenizer/LICENSE"
            target="_blank"
            rel="noopener noreferrer"
          >
            Boson Higgs Audio 2 Community License
          </a>
          <a
            className={linkClass}
            href="https://github.com/meta-llama/llama3/blob/main/LICENSE"
            target="_blank"
            rel="noopener noreferrer"
          >
            Meta Llama 3 Community License
          </a>
        </div>
      </div>
      <ul className="divide-y divide-border/50">
        {credits.map((credit) => (
          <li key={credit.name} className="space-y-1 p-4 text-sm leading-6">
            <a className={linkClass} href={credit.source} target="_blank" rel="noopener noreferrer">
              {credit.name}
            </a>
            <p className="text-xs text-muted-foreground">{credit.authors}</p>
            {credit.notice && (
              <p lang="en" dir="ltr">
                {credit.notice}
              </p>
            )}
            <a
              className={`${linkClass} text-xs`}
              href={credit.terms}
              target="_blank"
              rel="noopener noreferrer"
            >
              {t('about.model_terms')}
              {credit.license ? ` · ${credit.license}` : ''}
            </a>
            {credit.adaptations && (
              <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
                <span className="text-muted-foreground">{t('about.model_related_sources')}</span>
                {credit.adaptations.map((adaptation) => (
                  <a
                    key={adaptation.url}
                    className={linkClass}
                    href={adaptation.url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {adaptation.name}
                  </a>
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>
    </SettingsSection>
  );
}
