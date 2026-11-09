import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ImageIcon,
  ImagePlusIcon,
  PlusIcon,
  SpellCheckIcon,
  TrashIcon,
  Volume2Icon,
} from 'lucide-react';
import { SettingsSection } from './settings-section';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { PipelineFailure } from '@/components/pipeline-failure';
import { apiJson, describeError } from '@/lib/api/client';
import { metadataFields, duplicateWords } from './book-options';
import type { Draft } from './longform-session';
export function BookSettings({
  draft,
  disabled,
  pronunciation,
  onChange,
  onBusy,
}: {
  draft: Draft;
  disabled: boolean;
  pronunciation: boolean;
  onChange: (value: Partial<Draft>) => void;
  onBusy: (busy: boolean) => void;
}) {
  const { t } = useTranslation();
  const input = useRef<HTMLInputElement>(null);
  const controller = useRef<AbortController | null>(null);
  const [preview, setPreview] = useState('');
  const [error, setError] = useState<string | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(
    () => () => {
      if (preview) URL.revokeObjectURL(preview);
    },
    [preview],
  );
  const upload = async (file: File) => {
    if (disabled || controller.current) return;
    const current = new AbortController();
    controller.current = current;
    onBusy(true);
    setError(null);
    try {
      const body = new FormData();
      body.set('cover', file);
      const result = await apiJson<{ path: string }>('/audiobook/cover', {
        method: 'POST',
        body,
        signal: current.signal,
      });
      if (!current.signal.aborted) {
        onChange({ cover: { path: result.path, name: file.name } });
        setPreview(URL.createObjectURL(file));
      }
    } catch (cause) {
      if (!current.signal.aborted) setError(describeError(cause));
    } finally {
      if (controller.current === current) {
        controller.current = null;
        onBusy(false);
      }
    }
  };
  return (
    <div className="space-y-3">
      <SettingsSection title={t('audiobook.details')} icon={ImageIcon}>
        {metadataFields.map((field) => (
          <label key={field} className="block space-y-1 text-xs text-muted-foreground">
            {t('audiobook.meta_' + field)}
            <Input
              disabled={disabled}
              value={draft.metadata[field] || ''}
              onChange={(e) =>
                onChange({
                  metadata: { ...draft.metadata, [field]: e.target.value },
                })
              }
            />
          </label>
        ))}
        <input
          ref={input}
          type="file"
          className="hidden"
          accept="image/png,image/jpeg"
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = '';
            if (file) void upload(file);
          }}
        />
        {preview && draft.cover && (
          <img
            src={preview}
            alt={t('audiobook.cover')}
            className="h-32 w-24 rounded-md object-cover"
          />
        )}
        {draft.cover && (
          <p className="break-all text-xs text-muted-foreground">{draft.cover.name}</p>
        )}
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={disabled}
            onClick={() => input.current?.click()}
          >
            <ImagePlusIcon />
            {t('audiobook.cover_add')}
          </Button>
          {draft.cover && (
            <Button
              variant="ghost"
              size="sm"
              disabled={disabled}
              onClick={() => {
                onChange({ cover: null });
                setPreview('');
              }}
            >
              {t('audiobook.cover_remove')}
            </Button>
          )}
        </div>
        {error && <PipelineFailure fallback={error} onDismiss={() => setError(null)} />}
      </SettingsSection>
      <SettingsSection
        title={t('audiobook.loudness')}
        icon={Volume2Icon}
        meta={t('audiobook.loudness_' + draft.loudness)}
      >
        <div className="space-y-1">
          {(['off', 'acx', 'podcast'] as const).map((value) => (
            <Button
              key={value}
              size="sm"
              className="w-full justify-start"
              disabled={disabled}
              variant={draft.loudness === value ? 'secondary' : 'ghost'}
              onClick={() => onChange({ loudness: value })}
            >
              {t('audiobook.loudness_' + value)}
            </Button>
          ))}
        </div>
      </SettingsSection>
      {pronunciation && (
        <SettingsSection
          title={t('audiobook.lexicon')}
          icon={SpellCheckIcon}
          meta={draft.lexicon.length || undefined}
        >
          {draft.lexicon.map((row, index) => (
            <div key={index} className="space-y-1">
              <Input
                aria-label={t('audiobook.lex_word')}
                placeholder={t('audiobook.lex_word')}
                value={row.word}
                disabled={disabled}
                onChange={(e) =>
                  onChange({
                    lexicon: draft.lexicon.map((item, i) =>
                      i === index ? { ...item, word: e.target.value } : item,
                    ),
                  })
                }
              />
              <div className="flex gap-1">
                <Input
                  aria-label={t('audiobook.lex_say')}
                  placeholder={t('audiobook.lex_say')}
                  value={row.pronunciation}
                  disabled={disabled}
                  onChange={(e) =>
                    onChange({
                      lexicon: draft.lexicon.map((item, i) =>
                        i === index ? { ...item, pronunciation: e.target.value } : item,
                      ),
                    })
                  }
                />
                <Button
                  size="icon-sm"
                  variant="ghost"
                  aria-label={t('audiobook.lex_remove')}
                  disabled={disabled}
                  onClick={() =>
                    onChange({
                      lexicon: draft.lexicon.filter((_, i) => i !== index),
                    })
                  }
                >
                  <TrashIcon />
                </Button>
              </div>
            </div>
          ))}
          <Button
            size="sm"
            variant="ghost"
            disabled={disabled}
            onClick={() =>
              onChange({
                lexicon: [...draft.lexicon, { word: '', pronunciation: '' }],
              })
            }
          >
            <PlusIcon />
            {t('audiobook.lex_add')}
          </Button>
          {duplicateWords(draft.lexicon) && (
            <p role="alert" className="text-xs text-destructive">
              {t('audiobook.lex_duplicate')}
            </p>
          )}
        </SettingsSection>
      )}
    </div>
  );
}
