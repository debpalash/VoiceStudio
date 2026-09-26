/**
 * MirrorRescue — the restricted-network escape hatch of the first-run wizard.
 *
 * Users behind restricted networks (e.g. China, where huggingface.co is
 * blocked) can't reach Settings yet — the wizard gates the studio — so the
 * mirror quick-pick has to live right in the wizard. It renders in two spots:
 *   - step 0 (System) when the network preflight can't reach the HF endpoint,
 *   - step 1 (Models) when a model install failed because the CONFIGURED
 *     mirror is unreachable (docs_topic HF_MIRROR_UNREACHABLE) — switching
 *     mirrors and retrying right here beats a hint pointing at Settings the
 *     user can't open yet.
 * PUT /hf-mirror takes effect immediately for downloads (no restart during
 * setup) and clears the install cooldown, so onApplied can retry at once.
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { apiJson, apiFetch } from '../api/client';
import { Button } from '../ui';

export default function MirrorRescue({ onApplied }) {
  const { t } = useTranslation();
  const [presets, setPresets] = useState([]);
  const [url, setUrl] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    let alive = true;
    apiJson('/api/settings/hf-mirror')
      .then((d) => {
        if (!alive) return;
        // Keep the official preset (empty url) too: when the CONFIGURED
        // mirror is the thing that's unreachable, official IS the rescue.
        setPresets(d?.presets || []);
        setUrl(d?.configured || '');
      })
      .catch(() => {
        /* endpoint unavailable — keep the free-text input usable */
      });
    return () => {
      alive = false;
    };
  }, []);

  const apply = async (value) => {
    setSaving(true);
    setError(null);
    try {
      await apiFetch('/api/settings/hf-mirror', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: value }),
      });
      setUrl(value);
      onApplied();
    } catch (e) {
      setError(e?.message || t('setup.mirror_apply_error'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mt-3 flex flex-col gap-1.5 rounded-md border border-border px-3 py-2.5">
      <span className="text-sm font-semibold">{t('setup.mirror_rescue_title')}</span>
      <span className="text-xs leading-snug text-fg-muted">{t('setup.mirror_rescue_hint')}</span>
      {error && (
        <span className="text-xs text-danger" role="alert">
          {error}
        </span>
      )}
      <div className="mt-1 flex flex-wrap items-center gap-2">
        {presets.map((p) => (
          <Button
            key={p.url || 'official'}
            variant="preset"
            size="sm"
            disabled={saving}
            onClick={() => apply(p.url)}
            data-testid={`wizard-mirror-${p.url || 'official'}`}
          >
            {p.label}
          </Button>
        ))}
        <input
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://hf-mirror.com"
          className="min-w-[220px] flex-1 rounded border border-border bg-transparent px-2 py-1 font-mono text-xs text-fg"
          data-testid="wizard-mirror-url"
        />
        <Button
          variant="subtle"
          size="sm"
          loading={saving}
          disabled={saving || !url.trim()}
          onClick={() => apply(url.trim())}
          data-testid="wizard-mirror-apply"
        >
          {t('setup.mirror_apply')}
        </Button>
      </div>
    </div>
  );
}
