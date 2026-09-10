/**
 * Settings → General → Pronunciation dictionary panel (Expressive-TTS Spec 01).
 *
 * A table of user pronunciation entries (term → respelling), scoped Global or to
 * a language. Entries are applied as pure text substitution before synthesis, so
 * a saved entry changes the audio on every engine. Plus a model-free "test"
 * field that previews the substitution via POST /pronunciation/test — with a
 * language selector, since language-scoped entries only apply when the request
 * carries that language. Preview requests are debounced and sequence-guarded so
 * a slow earlier response can never overwrite a newer one, and the preview
 * re-runs after any add/toggle/delete/import so it never shows a stale result.
 * Backup & restore round-trips GET /pronunciation/export ↔ POST /pronunciation/import.
 *
 * Endpoints (loopback-only):
 *   GET    /pronunciation
 *   POST   /pronunciation              {term, replacement, type, language, enabled}
 *   PUT    /pronunciation/{id}         (partial)
 *   DELETE /pronunciation/{id}
 *   POST   /pronunciation/test         {text, language} → {substituted, changed}
 *   GET    /pronunciation/export       → {entries: [...]}
 *   POST   /pronunciation/import       {entries, replace}
 *
 * Cross-platform: identical on macOS / Windows / Linux — it's a pure form over
 * a text transform, no OS-specific behavior. All strings via i18n.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { BookA, Trash2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { apiJson, apiFetch } from '../../api/client';
import { askConfirm } from './native';
import { SettingsSection, SettingRow, SettingsInput, SettingsToggle } from './primitives';
import { Button, Badge, Select } from '../../ui';

const TYPES = ['respelling', 'ipa', 'cmu'];
const TEST_DEBOUNCE_MS = 250;

// Trigger a browser download for a Blob (same pattern as StoriesEditor).
function downloadBlob(blob, filename, doc = document, urlApi = URL) {
  const url = urlApi.createObjectURL(blob);
  const a = doc.createElement('a');
  a.href = url;
  a.download = filename;
  doc.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => urlApi.revokeObjectURL(url), 10000);
}

export default function PronunciationPanel() {
  const { t } = useTranslation();
  const [entries, setEntries] = useState([]);
  const [term, setTerm] = useState('');
  const [replacement, setReplacement] = useState('');
  const [language, setLanguage] = useState('');
  const [type, setType] = useState('respelling');
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [testText, setTestText] = useState('');
  const [testLang, setTestLang] = useState('*');
  const [testOut, setTestOut] = useState(null);
  const [testError, setTestError] = useState(false);

  // Preview sequencing: per-keystroke POSTs can resolve out of order, so each
  // request takes a ticket and only the latest one may write the result.
  const testSeq = useRef(0);
  const testTimer = useRef(null);
  const fileRef = useRef(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      setEntries(await apiJson('/pronunciation'));
    } catch (e) {
      setError(e?.message || t('pronunciation.load_error'));
    }
  }, [t]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(
    () => () => {
      if (testTimer.current) clearTimeout(testTimer.current);
    },
    [],
  );

  const runTest = useCallback(async (text, lang) => {
    // A direct run supersedes any pending debounced run (e.g. the user picked a
    // preview language while a keystroke's timer was still counting down).
    if (testTimer.current) {
      clearTimeout(testTimer.current);
      testTimer.current = null;
    }
    const seq = ++testSeq.current;
    if (!text.trim()) {
      setTestOut(null);
      setTestError(false);
      return;
    }
    try {
      const r = await apiJson('/pronunciation/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text,
          ...(lang && lang !== '*' ? { language: lang } : {}),
        }),
      });
      if (seq === testSeq.current) {
        setTestOut(r);
        setTestError(false);
      }
    } catch {
      if (seq === testSeq.current) {
        setTestOut(null);
        setTestError(true);
      }
    }
  }, []);

  const scheduleTest = useCallback(
    (text, lang) => {
      if (testTimer.current) clearTimeout(testTimer.current);
      testSeq.current += 1; // invalidate any in-flight response
      if (!text.trim()) {
        setTestOut(null);
        setTestError(false);
        return;
      }
      testTimer.current = setTimeout(() => runTest(text, lang), TEST_DEBOUNCE_MS);
    },
    [runTest],
  );

  const onTestTextChange = (value) => {
    setTestText(value);
    scheduleTest(value, testLang);
  };

  const onTestLangChange = (value) => {
    setTestLang(value);
    if (testText.trim()) runTest(testText, value);
  };

  // After a successful mutation the dictionary changed — re-run the preview so
  // it reflects the new state instead of going stale.
  const retest = useCallback(
    (lang = testLang) => {
      if (testText.trim()) runTest(testText, lang);
    },
    [runTest, testText, testLang],
  );

  const onAdd = async () => {
    if (!term.trim()) return;
    setError(null);
    try {
      await apiFetch('/pronunciation', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          term: term.trim(),
          replacement,
          type,
          language: language.trim() || '*',
          enabled: true,
        }),
      });
      setTerm('');
      setReplacement('');
      setLanguage('');
      setType('respelling');
      refresh();
      retest();
    } catch (e) {
      setError(e?.message || t('pronunciation.save_error'));
    }
  };

  const onAddKeyDown = (ev) => {
    if (ev.key === 'Enter') {
      ev.preventDefault();
      onAdd();
    }
  };

  const onToggle = async (entry) => {
    try {
      await apiFetch(`/pronunciation/${encodeURIComponent(entry.id)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !entry.enabled }),
      });
      refresh();
      retest();
    } catch (e) {
      setError(e?.message || t('pronunciation.save_error'));
    }
  };

  const onDelete = async (id) => {
    try {
      await apiFetch(`/pronunciation/${encodeURIComponent(id)}`, { method: 'DELETE' });
      refresh();
      retest();
    } catch (e) {
      setError(e?.message || t('pronunciation.save_error'));
    }
  };

  const onExport = async () => {
    setError(null);
    setNotice(null);
    try {
      const data = await apiJson('/pronunciation/export');
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      downloadBlob(blob, 'pronunciation-dictionary.json');
    } catch (e) {
      setError(e?.message || t('pronunciation.export_error'));
    }
  };

  const onImportFile = async (file) => {
    setError(null);
    setNotice(null);
    let imported;
    try {
      const parsed = JSON.parse(await file.text());
      imported = Array.isArray(parsed) ? parsed : parsed?.entries;
      if (!Array.isArray(imported)) throw new Error('not an entry list');
    } catch {
      setError(t('pronunciation.import_error'));
      return;
    }
    let replace = false;
    if (entries.length > 0) {
      replace = await askConfirm(
        t('pronunciation.import_replace_prompt', { count: entries.length }),
        t('pronunciation.backup_title'),
      );
    }
    try {
      const res = await apiJson('/pronunciation/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ entries: imported, replace }),
      });
      setNotice(t('pronunciation.import_done', { count: res?.imported ?? imported.length }));
      refresh();
      retest();
    } catch (e) {
      setError(e?.message || t('pronunciation.import_error'));
    }
  };

  const scopeLabel = (s) => (!s || s === '*' ? t('pronunciation.global') : s);
  const typeLabel = (ty) => t(`pronunciation.type_${ty}`, ty);

  // Preview-language choices: Global plus every language the dictionary
  // actually scopes entries to (keep the current pick even if its last entry
  // was just deleted, so the select never renders an unknown value).
  const testLangs = [
    ...new Set(
      entries
        .map((e) => e.scope || e.language)
        .filter((l) => l && l !== '*')
        .concat(testLang !== '*' ? [testLang] : []),
    ),
  ].sort();
  const hasScopedEnabled = entries.some((e) => e.enabled && (e.scope || e.language) !== '*');

  return (
    <SettingsSection icon={BookA} title={t('pronunciation.title')}>
      <SettingRow title={t('pronunciation.title')} hint={t('pronunciation.help')} control={null} />

      {error && (
        <div className="perfpanel__error" role="alert">
          {error}
        </div>
      )}

      {entries.length === 0 && (
        <SettingRow
          title={<span data-testid="pron-empty">{t('pronunciation.empty')}</span>}
          control={null}
        />
      )}

      {entries.map((e) => (
        <SettingRow
          key={e.id}
          title={
            <>
              <strong>{e.term}</strong> → {e.replacement || '—'}
            </>
          }
          control={
            <>
              <SettingsToggle
                checked={!!e.enabled}
                onChange={() => onToggle(e)}
                aria-label={t('pronunciation.enable_entry', { term: e.term })}
                data-testid={`pron-toggle-${e.id}`}
              />
                            <Badge tone="neutral">{typeLabel(e.type)}</Badge>
              <Badge tone="neutral">{scopeLabel(e.scope || e.language)}</Badge>
              {e.type !== 'respelling' && (
                <Badge tone="warning" data-testid={`pron-not-applied-${e.id}`}>
                  {t('pronunciation.not_applied_badge')}
                </Badge>
              )}
              <Button
                variant="danger"
                size="sm"
                onClick={() => onDelete(e.id)}
                aria-label={t('pronunciation.remove', { term: e.term })}
                data-testid={`pron-del-${e.id}`}
              >
                <Trash2 size={12} />
              </Button>
            </>
          }
        />
      ))}

      <SettingRow
        title={t('pronunciation.add')}
        hint={t('pronunciation.lang_label')}
        align="start"
        control={
          <div className="flex flex-wrap items-center gap-[6px] min-w-0 max-w-full">
            <SettingsInput
              type="text"
              value={term}
              onChange={(ev) => setTerm(ev.target.value)}
              onKeyDown={onAddKeyDown}
              placeholder={t('pronunciation.term_placeholder')}
              aria-label={t('pronunciation.term')}
              className="flex-[1_1_120px]"
              data-testid="pron-term"
            />
            <SettingsInput
              type="text"
              value={replacement}
              onChange={(ev) => setReplacement(ev.target.value)}
              onKeyDown={onAddKeyDown}
              placeholder={t('pronunciation.replacement_placeholder')}
              aria-label={t('pronunciation.replacement')}
              className="flex-[1_1_120px]"
              data-testid="pron-replacement"
            />
            <Select
              size="sm"
              value={type}
              onChange={(ev) => setType(ev.target.value)}
              aria-label={t('pronunciation.type')}
              data-testid="pron-type"
            >
              {TYPES.map((ty) => (
                <option key={ty} value={ty}>
                  {typeLabel(ty)}
                </option>
              ))}
            </Select>
            <SettingsInput
              type="text"
              value={language}
              onChange={(ev) => setLanguage(ev.target.value)}
              onKeyDown={onAddKeyDown}
              placeholder={t('pronunciation.lang_placeholder')}
              aria-label={t('pronunciation.lang_label')}
              className="w-[130px] flex-none"
              data-testid="pron-language"
            />
            <Button
              variant="subtle"
              size="sm"
              onClick={onAdd}
              disabled={!term.trim()}
              data-testid="pron-add"
            >
              {t('pronunciation.add')}
            </Button>
          </div>
        }
      />

      <SettingRow
        title={t('pronunciation.test_label')}
        subtitle={
          testLang === '*' && hasScopedEnabled ? t('pronunciation.test_global_hint') : undefined
        }
        control={
          <>
            <Select
              size="sm"
              value={testLang}
              onChange={(ev) => onTestLangChange(ev.target.value)}
              aria-label={t('pronunciation.test_language')}
              data-testid="pron-test-language"
            >
              <option value="*">{t('pronunciation.global')}</option>
              {testLangs.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </Select>
            <SettingsInput
              type="text"
              value={testText}
              onChange={(ev) => onTestTextChange(ev.target.value)}
              placeholder={t('pronunciation.test_placeholder')}
              aria-label={t('pronunciation.test_label')}
              data-testid="pron-test-input"
            />
          </>
        }
      />
            {testOut && (
        <p className="perfpanel__help" data-testid="pron-test-out">
          {testOut.changed ? (
            <>
              {t('pronunciation.test_result')} <strong>{testOut.substituted}</strong>
            </>
          ) : (
            t('pronunciation.test_nochange')
          )}
        </p>
      )}
      {testOut?.skipped_terms?.length > 0 && (
        <p className="perfpanel__help" data-testid="pron-test-skipped">
          {t('pronunciation.test_skipped', {
            terms: testOut.skipped_terms.map((s) => s.term).join(', '),
          })}
        </p>
      )}
      {testError && (
        <p className="perfpanel__help" data-testid="pron-test-error">
          {t('pronunciation.test_error')}
        </p>
      )}

      <SettingRow
        title={t('pronunciation.backup_title')}
        hint={t('pronunciation.backup_hint')}
        control={
          <>
            <Button variant="subtle" size="sm" onClick={onExport} data-testid="pron-export">
              {t('pronunciation.export')}
            </Button>
            <Button
              variant="subtle"
              size="sm"
              onClick={() => fileRef.current?.click()}
              data-testid="pron-import"
            >
              {t('pronunciation.import')}
            </Button>
            <input
              ref={fileRef}
              type="file"
              accept="application/json,.json"
              className="hidden"
              aria-label={t('pronunciation.import')}
              data-testid="pron-import-file"
              onChange={(ev) => {
                const f = ev.target.files?.[0];
                ev.target.value = '';
                if (f) onImportFile(f);
              }}
            />
          </>
        }
      />
      {notice && (
        <p className="perfpanel__help" data-testid="pron-import-done">
          {notice}
        </p>
      )}
    </SettingsSection>
  );
}
