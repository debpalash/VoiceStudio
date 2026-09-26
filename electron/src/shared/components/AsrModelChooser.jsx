/**
 * AsrModelChooser — the "no speech-to-text model installed" empty state's
 * model picker.
 *
 * The page used to offer exactly one button: download the backend's
 * recommended model. That hid the six other catalogue models — the English
 * Parakeet that is markedly more accurate, the 25–44 MB streaming models that
 * show text while you speak, the bilingual zh/en ones — behind Settings, and
 * a user who already had one of them on disk still saw "download Whisper
 * Tiny". This lists the whole sherpa-onnx catalogue grouped by what the user
 * is choosing between (accuracy vs latency), with languages and size on every
 * row, and lets them install any of them, or switch to one already installed.
 *
 * Falls back to the single recommended-download button when the catalogue
 * can't be read (backend restarting, older backend), so the page is never
 * left without a way forward.
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Download } from 'lucide-react';
import { apiJson } from '../api/client';
import { Badge, Button } from '../ui';

function fmtSize(sizeGb) {
  if (sizeGb == null) return '';
  if (sizeGb < 1) return `${Math.round(sizeGb * 1000)} MB`;
  return `${sizeGb.toFixed(sizeGb < 10 ? 1 : 0)} GB`;
}

// Offline models transcribe once you stop (best accuracy); streaming ones emit
// partial text as you speak (lowest latency). That is the real trade-off, so
// it is the grouping — not the engine's internal `kind`.
const GROUPS = [
  { tag: 'offline', title: 'asr_missing.group_offline' },
  { tag: 'streaming', title: 'asr_missing.group_streaming' },
];

export default function AsrModelChooser({ fallback, onInstall, onSelect, disabled = false }) {
  const { t } = useTranslation();
  // null = loading, [] = catalogue unavailable.
  const [models, setModels] = useState(null);

  useEffect(() => {
    let live = true;
    apiJson('/dictation/models')
      .then((data) => {
        if (live) setModels(Array.isArray(data?.models) ? data.models : []);
      })
      .catch(() => {
        if (live) setModels([]);
      });
    return () => {
      live = false;
    };
  }, []);

  if (models === null) return null;

  if (models.length === 0) {
    if (!fallback?.repo_id) return null;
    return (
      <Button
        size="sm"
        variant="primary"
        leading={<Download size={13} />}
        disabled={disabled}
        onClick={() => onInstall(fallback)}
      >
        {t('asr_missing.download', { label: fallback.label, size: fallback.size_gb })}
      </Button>
    );
  }

  const desc = (m) => t(`voicePanel.model_desc.${m.id}`, { defaultValue: m.languages || '' });

  return (
    <div className="flex flex-col gap-3" data-testid="asr-model-chooser">
      <p className="m-0 text-xs text-fg-muted">{t('asr_missing.choose')}</p>
      {GROUPS.map(({ tag, title }) => {
        const rows = models
          .filter((m) => m.tag === tag)
          .sort((a, b) => Number(b.recommended) - Number(a.recommended));
        if (rows.length === 0) return null;
        return (
          <section key={tag} aria-labelledby={`asr-group-${tag}`} className="flex flex-col gap-1">
            <h3
              id={`asr-group-${tag}`}
              className="m-0 text-[11px] font-semibold uppercase tracking-[0.06em] text-fg-muted"
            >
              {t(title)}
            </h3>
            <ul className="m-0 flex list-none flex-col gap-1 p-0">
              {rows.map((m) => (
                <li
                  key={m.id}
                  data-testid={`asr-choice-${m.id}`}
                  className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-bg px-3 py-2"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="text-sm font-semibold text-fg">{m.label}</span>
                      {m.recommended && (
                        <Badge tone="success" size="xs">
                          {t('voicePanel.badge_recommended')}
                        </Badge>
                      )}
                      {m.installed && (
                        <Badge tone="neutral" size="xs">
                          {t('asr_missing.installed')}
                        </Badge>
                      )}
                      <span className="text-xs tabular-nums text-fg-muted">
                        {fmtSize(m.size_gb)}
                      </span>
                    </div>
                    <p className="m-0 text-xs text-fg-muted">
                      {desc(m)}
                      {m.languages && (
                        <>
                          {' · '}
                          <span className="whitespace-nowrap">{m.languages}</span>
                        </>
                      )}
                    </p>
                  </div>
                  {m.installed ? (
                    <Button
                      size="sm"
                      variant="subtle"
                      leading={<Check size={13} />}
                      disabled={disabled}
                      onClick={() => onSelect(m)}
                    >
                      {t('asr_missing.use', { label: m.label })}
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      variant={m.recommended ? 'primary' : 'subtle'}
                      leading={<Download size={13} />}
                      disabled={disabled}
                      onClick={() => onInstall(m)}
                    >
                      {t('asr_missing.download', { label: m.label, size: m.size_gb })}
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
