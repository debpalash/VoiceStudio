import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { BookAIcon, DownloadIcon, PlusIcon, Trash2Icon, UploadIcon } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { PipelineFailure } from '@/components/pipeline-failure';
import { apiFetch, apiJson, describeError } from '@/lib/api/client';
import { isImeComposing } from '@/lib/ime';
import { saveLocalFile } from '@/lib/local-export';
import { listProfiles } from '@/lib/api/profiles';
import { SettingsRow, SettingsSection } from './settings-layout';

type PronunciationType = 'respelling' | 'ipa' | 'cmu';

interface PronunciationEntry {
  id: string;
  term: string;
  replacement: string;
  type: PronunciationType;
  language: string;
  scope?: string;
  enabled: boolean;
}

interface TestResult {
  substituted: string;
  changed: boolean;
}

interface ImportPayload {
  entries: Omit<PronunciationEntry, 'id' | 'scope'>[];
}

// An import belongs to the dictionary it was started for (#3873 review).
interface PendingImport extends ImportPayload {
  profileId: string;
  count: number;
}

const TYPES: PronunciationType[] = ['respelling', 'ipa', 'cmu'];

export function PronunciationSettings() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [term, setTerm] = useState('');
  const [replacement, setReplacement] = useState('');
  const [language, setLanguage] = useState('');
  const [type, setType] = useState<PronunciationType>('respelling');
  const [testText, setTestText] = useState('');
  const [testLanguage, setTestLanguage] = useState('*');
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [testError, setTestError] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  // #3873: the dictionary being edited is the language one, or one voice's own.
  const [scopeKind, setScopeKind] = useState<'language' | 'voice'>('language');
  const [voiceId, setVoiceId] = useState('');
  const profileId = scopeKind === 'voice' ? voiceId : '';
  const profileQuery = profileId ? '?profile_id=' + encodeURIComponent(profileId) : '';
  const voices = useQuery({ queryKey: ['profiles'], queryFn: listProfiles });
  const voiceScopeNeedsVoice = scopeKind === 'voice' && !voiceId;
  const [previewRevision, setPreviewRevision] = useState(0);
  const [pendingImport, setPendingImport] = useState<PendingImport | null>(null);
  const profileIdRef = useRef('');
  const entries = useQuery({
    queryKey: ['pronunciation', profileId],
    queryFn: ({ signal }) =>
      apiJson<PronunciationEntry[]>('/pronunciation' + profileQuery, { signal }),
    enabled: !voiceScopeNeedsVoice,
  });
  useEffect(() => {
    profileIdRef.current = profileId;
  }, [profileId]);
  const rows = voiceScopeNeedsVoice ? [] : (entries.data ?? []);

  // Switching dictionary drops a pending replace prompt rather than retargeting it.
  useEffect(() => setPendingImport(null), [profileId]);

  useEffect(() => {
    if (!testText.trim()) {
      setTestResult(null);
      setTestError('');
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      void apiJson<TestResult>('/pronunciation/test', {
        method: 'POST',
        body: JSON.stringify({
          text: testText,
          ...(testLanguage === '*' ? {} : { language: testLanguage }),
          ...(profileId ? { profile_id: profileId } : {}),
        }),
        signal: controller.signal,
      })
        .then((result) => {
          setTestResult(result);
          setTestError('');
        })
        .catch((error) => {
          if (!controller.signal.aborted) {
            setTestResult(null);
            setTestError(describeError(error));
          }
        });
    }, 250);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [previewRevision, profileId, testLanguage, testText]);

  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ['pronunciation'] });
    setPreviewRevision((value) => value + 1);
  };
  const act = async (work: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await work();
    } catch (error) {
      setError(describeError(error));
    } finally {
      setBusy(false);
    }
  };
  const add = () =>
    act(async () => {
      if (voiceScopeNeedsVoice) return;
      await apiJson('/pronunciation', {
        method: 'POST',
        body: JSON.stringify({
          term: term.trim(),
          replacement,
          type,
          language: language.trim() || '*',
          enabled: true,
          ...(profileId ? { profile_id: profileId } : {}),
        }),
      });
      setTerm('');
      setReplacement('');
      setLanguage('');
      setType('respelling');
      await refresh();
    });
  const update = (entry: PronunciationEntry, enabled: boolean) =>
    act(async () => {
      await apiJson('/pronunciation/' + encodeURIComponent(entry.id), {
        method: 'PUT',
        body: JSON.stringify({ enabled }),
      });
      await refresh();
    });
  const remove = (entry: PronunciationEntry) =>
    act(async () => {
      await apiFetch('/pronunciation/' + encodeURIComponent(entry.id), { method: 'DELETE' });
      await refresh();
    });
  const importEntries = (payload: ImportPayload, target: string, replace: boolean) =>
    act(async () => {
      if (scopeKind === 'voice' && !target) return;
      const result = await apiJson<{ imported: number }>('/pronunciation/import', {
        method: 'POST',
        body: JSON.stringify({
          entries: payload.entries,
          replace,
          ...(target ? { profile_id: target } : {}),
        }),
      });
      setPendingImport(null);
      setNotice(t('pronunciation.import_done', { count: result.imported }));
      await refresh();
    });
  const loadImport = async (file: File) => {
    if (voiceScopeNeedsVoice) return;
    const target = profileId;
    const existing = rows.length;
    setError('');
    setNotice('');
    try {
      const parsed = JSON.parse(await file.text()) as unknown;
      const imported = Array.isArray(parsed)
        ? parsed
        : parsed && typeof parsed === 'object' && 'entries' in parsed
          ? (parsed as { entries?: unknown }).entries
          : null;
      if (!Array.isArray(imported)) throw new Error('Invalid pronunciation export');
      if (profileIdRef.current !== target) return;
      const payload = { entries: imported } as ImportPayload;
      if (existing) setPendingImport({ ...payload, profileId: target, count: existing });
      else void importEntries(payload, target, false);
    } catch {
      setError(t('pronunciation.import_error'));
    }
  };
  const exportEntries = () =>
    act(async () => {
      if (voiceScopeNeedsVoice) return;
      const data = await apiJson<ImportPayload>('/pronunciation/export' + profileQuery);
      await saveLocalFile(
        new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
        profileId ? 'pronunciation-voice-dictionary.json' : 'pronunciation-dictionary.json',
      );
    });

  const scopedLanguages = Array.from(
    new Set(
      rows
        .map((entry) => entry.scope || entry.language)
        .filter((scope) => scope && scope !== '*')
        .concat(testLanguage === '*' ? [] : [testLanguage]),
    ),
  ).sort();
  const globalPreviewHidesScoped =
    testLanguage === '*' &&
    rows.some((entry) => entry.enabled && (entry.scope || entry.language) !== '*');

  return (
    <SettingsSection icon={BookAIcon} title={t('pronunciation.title')}>
      <SettingsRow
        id="pronunciation-overview"
        title={t('pronunciation.title')}
        description={t('pronunciation.help')}
      >
        <Badge variant="secondary">
          {t('pronunciation.entries_count', { count: rows.length })}
        </Badge>
      </SettingsRow>
      <SettingsRow
        id="pronunciation-scope"
        title={t('pronunciation.scope')}
        description={t('pronunciation.voice_note')}
      >
        <div
          role="group"
          aria-label={t('pronunciation.scope')}
          className="inline-flex rounded-lg border border-border/60 bg-muted/30 p-0.5"
        >
          {(['language', 'voice'] as const).map((kind) => (
            <Button
              key={kind}
              size="sm"
              variant={scopeKind === kind ? 'secondary' : 'ghost'}
              aria-pressed={scopeKind === kind}
              onClick={() => setScopeKind(kind)}
            >
              {t('pronunciation.scope_' + kind)}
            </Button>
          ))}
        </div>
        {scopeKind === 'voice' && (
          <Select
            items={(voices.data ?? []).map((voice) => ({ value: voice.id, label: voice.name }))}
            value={voiceId}
            onValueChange={(value) => setVoiceId(value ?? '')}
          >
            <SelectTrigger aria-label={t('pronunciation.voice_pick')} className="min-w-44">
              <SelectValue placeholder={t('pronunciation.voice_pick')} />
            </SelectTrigger>
            <SelectContent align="end">
              {(voices.data ?? []).map((voice) => (
                <SelectItem key={voice.id} value={voice.id}>
                  {voice.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </SettingsRow>
      {(error || entries.isError) && (
        <div className="p-4">
          <PipelineFailure
            fallback={error || describeError(entries.error)}
            onDismiss={error ? () => setError('') : undefined}
            action={
              entries.isError ? (
                <Button size="sm" variant="ghost" onClick={() => void entries.refetch()}>
                  {t('common.retry')}
                </Button>
              ) : undefined
            }
          />
        </div>
      )}
      {scopeKind === 'voice' && voices.isError && (
        <div className="p-4">
          <PipelineFailure
            fallback={t('pronunciation.voices_error')}
            action={
              <Button size="sm" variant="ghost" onClick={() => void voices.refetch()}>
                {t('common.retry')}
              </Button>
            }
          />
        </div>
      )}
      {!voiceScopeNeedsVoice && entries.isPending && (
        <p role="status" className="px-4 py-3 text-sm text-muted-foreground">
          {t('common.loading')}
        </p>
      )}
      {!voiceScopeNeedsVoice && !entries.isPending && rows.length === 0 && (
        <p className="px-4 py-3 text-sm text-muted-foreground">{t('pronunciation.empty')}</p>
      )}
      {rows.map((entry) => (
        <SettingsRow
          key={entry.id}
          id={'pronunciation-' + entry.id}
          title={entry.term + ' → ' + (entry.replacement || '—')}
          description={
            t('pronunciation.type_' + entry.type) +
            ' · ' +
            ((entry.scope || entry.language) === '*'
              ? t('pronunciation.global')
              : entry.scope || entry.language)
          }
        >
          <Switch
            checked={entry.enabled}
            disabled={busy}
            aria-label={t('pronunciation.enable_entry', { term: entry.term })}
            onCheckedChange={(enabled) => void update(entry, enabled)}
          />
          <Button
            variant="ghost"
            size="icon-sm"
            disabled={busy}
            aria-label={t('pronunciation.remove', { term: entry.term })}
            onClick={() => void remove(entry)}
          >
            <Trash2Icon />
          </Button>
        </SettingsRow>
      ))}
      <SettingsRow
        id="pronunciation-add"
        title={t('pronunciation.add')}
        description={t('pronunciation.lang_label')}
      >
        <Input
          value={term}
          disabled={busy}
          placeholder={t('pronunciation.term_placeholder')}
          aria-label={t('pronunciation.term')}
          className="min-w-36 flex-1 @2xl:max-w-52"
          onChange={(event) => setTerm(event.target.value)}
        />
        <Input
          value={replacement}
          disabled={busy}
          placeholder={t('pronunciation.replacement_placeholder')}
          aria-label={t('pronunciation.replacement')}
          className="min-w-36 flex-1 @2xl:max-w-52"
          onChange={(event) => setReplacement(event.target.value)}
        />
        <Select
          items={TYPES.map((value) => ({ value, label: t('pronunciation.type_' + value) }))}
          value={type}
          disabled={busy}
          onValueChange={(value) => {
            if (value === 'respelling' || value === 'ipa' || value === 'cmu') setType(value);
          }}
        >
          <SelectTrigger aria-label={t('pronunciation.type')}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {TYPES.map((value) => (
              <SelectItem key={value} value={value}>
                {t('pronunciation.type_' + value)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          value={language}
          disabled={busy}
          placeholder={t('pronunciation.lang_placeholder')}
          aria-label={t('pronunciation.lang_label')}
          className="w-40"
          onChange={(event) => setLanguage(event.target.value)}
          onKeyDown={(event) => {
            if (isImeComposing(event)) return;
            if (event.key === 'Enter' && term.trim() && !voiceScopeNeedsVoice) {
              event.preventDefault();
              void add();
            }
          }}
        />
        <Button disabled={busy || !term.trim() || voiceScopeNeedsVoice} onClick={() => void add()}>
          <PlusIcon />
          {t('pronunciation.add')}
        </Button>
      </SettingsRow>
      <SettingsRow
        id="pronunciation-test"
        title={t('pronunciation.test_label')}
        description={globalPreviewHidesScoped ? t('pronunciation.test_global_hint') : undefined}
      >
        <Select
          items={[
            { value: '*', label: t('pronunciation.global') },
            ...scopedLanguages.map((value) => ({ value, label: value })),
          ]}
          value={testLanguage}
          onValueChange={(value) => value && setTestLanguage(value)}
        >
          <SelectTrigger aria-label={t('pronunciation.test_language')}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="*">{t('pronunciation.global')}</SelectItem>
            {scopedLanguages.map((value) => (
              <SelectItem key={value} value={value}>
                {value}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          value={testText}
          placeholder={t('pronunciation.test_placeholder')}
          aria-label={t('pronunciation.test_label')}
          className="min-w-64 flex-1 @2xl:max-w-xl"
          onChange={(event) => setTestText(event.target.value)}
        />
      </SettingsRow>
      {testResult && (
        <p className="px-4 py-3 text-sm text-muted-foreground">
          {testResult.changed ? (
            <>
              {t('pronunciation.test_result')}{' '}
              <strong className="text-foreground">{testResult.substituted}</strong>
            </>
          ) : (
            t('pronunciation.test_nochange')
          )}
        </p>
      )}
      {testError && (
        <div className="p-4">
          <PipelineFailure
            fallback={t('pronunciation.test_error') + ' ' + testError}
            onDismiss={() => setTestError('')}
          />
        </div>
      )}
      <SettingsRow
        id="pronunciation-backup"
        title={t('pronunciation.backup_title')}
        description={t('pronunciation.backup_hint')}
      >
        <Button
          variant="outline"
          disabled={busy || voiceScopeNeedsVoice}
          onClick={() => void exportEntries()}
        >
          <DownloadIcon />
          {t('pronunciation.export')}
        </Button>
        <Button
          variant="outline"
          disabled={busy || voiceScopeNeedsVoice}
          onClick={() => fileInput.current?.click()}
        >
          <UploadIcon />
          {t('pronunciation.import')}
        </Button>
        <input
          ref={fileInput}
          hidden
          type="file"
          accept="application/json,.json"
          aria-label={t('pronunciation.import')}
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = '';
            if (file) void loadImport(file);
          }}
        />
      </SettingsRow>
      {pendingImport && (
        <div className="flex flex-wrap items-center gap-2 px-4 py-3 text-sm">
          <p className="min-w-0 flex-1">
            {t('pronunciation.import_replace_prompt', { count: pendingImport.count })}
          </p>
          <Button
            disabled={busy}
            onClick={() => void importEntries(pendingImport, pendingImport.profileId, true)}
          >
            {t('common.yes')}
          </Button>
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => void importEntries(pendingImport, pendingImport.profileId, false)}
          >
            {t('common.no')}
          </Button>
          <Button variant="ghost" disabled={busy} onClick={() => setPendingImport(null)}>
            {t('common.cancel')}
          </Button>
        </div>
      )}
      {notice && (
        <p role="status" className="px-4 py-3 text-sm text-muted-foreground">
          {notice}
        </p>
      )}
    </SettingsSection>
  );
}
