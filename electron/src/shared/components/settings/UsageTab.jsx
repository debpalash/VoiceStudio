/**
 * Settings → Usage — local-only insights.
 *
 * The privacy-preserving answer to "how am I using this?" — and the deliberate
 * alternative to cloud analytics (a PostHog integration was proposed and
 * rejected, PR #1110, because a third-party telemetry endpoint would break the
 * product's headline promise that nothing leaves your machine).
 *
 * Everything here is computed from the history the app has ALREADY written to
 * your own database in the course of doing its job. It collects nothing new,
 * stores nothing new, and sends nothing anywhere: the numbers are aggregates
 * (counts and totals — never the text of a take, never a file path), fetched
 * over loopback from your own backend. The panel says so plainly, because a
 * privacy guarantee the user can't see isn't worth much.
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { BarChart3, ShieldCheck } from 'lucide-react';
import { apiJson } from '../../api/client';
import { SettingsSection } from './primitives';

/** "2 h 14 m" / "3 m 20 s" / "45 s". Pure + exported for tests. */
export function fmtDuration(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} m ${s % 60} s`;
  const h = Math.floor(m / 60);
  return `${h} h ${m % 60} m`;
}

/** Local date of a unix timestamp, or null. Pure + exported for tests. */
export function fmtDate(ts) {
  if (!ts) return null;
  try {
    return new Date(ts * 1000).toLocaleDateString();
  } catch {
    return null;
  }
}

function Stat({ label, value, sub }) {
  return (
    <div className="flex flex-col gap-[var(--space-1)] rounded-[var(--radius-md)] bg-[var(--chrome-hover-bg)] px-[var(--space-4)] py-[var(--space-3)]">
      <span className="[font-family:var(--font-mono)] text-[length:var(--text-lg)] tabular-nums text-[var(--chrome-fg)]">
        {value}
      </span>
      <span className="[font-family:var(--font-sans)] text-[length:var(--text-sm)] text-[var(--chrome-fg-muted)]">
        {label}
      </span>
      {sub && (
        <span className="[font-family:var(--font-sans)] text-[length:var(--text-xs)] text-[var(--chrome-fg-dim)]">
          {sub}
        </span>
      )}
    </div>
  );
}

function Bars({ title, rows }) {
  if (!rows?.length) return null;
  const max = Math.max(...rows.map((r) => r.count), 1);
  return (
    <div className="flex flex-col gap-[var(--space-2)]">
      <span className="[font-family:var(--font-sans)] text-[length:var(--text-sm)] text-[var(--chrome-fg-muted)]">
        {title}
      </span>
      {rows.map((r) => (
        <div key={r.name} className="flex items-center gap-[var(--space-3)]">
          <span className="w-[92px] shrink-0 truncate [font-family:var(--font-sans)] text-[length:var(--text-sm)] text-[var(--chrome-fg)]">
            {r.name}
          </span>
          <span className="h-[6px] flex-1 overflow-hidden rounded-full bg-[var(--chrome-hover-bg)]">
            <span
              className="block h-full rounded-full bg-[var(--chrome-accent)]"
              style={{ width: `${Math.round((r.count / max) * 100)}%` }}
            />
          </span>
          <span className="w-[36px] shrink-0 text-right [font-family:var(--font-mono)] text-[length:var(--text-sm)] tabular-nums text-[var(--chrome-fg-muted)]">
            {r.count}
          </span>
        </div>
      ))}
    </div>
  );
}

export default function UsageTab() {
  const { t } = useTranslation();
  const [s, setS] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let alive = true;
    apiJson('/stats/usage')
      .then((d) => alive && setS(d))
      .catch((e) => alive && setErr(e?.message || String(e)));
    return () => {
      alive = false;
    };
  }, []);

  const since = fmtDate(s?.first_at);

  return (
    <SettingsSection
      icon={BarChart3}
      title={t('settings.usage', { defaultValue: 'Usage' })}
      description={t('settings.usage_desc', {
        defaultValue: 'What you have made with VoiceStudio — counted on your own machine.',
      })}
    >
      {/* The guarantee, stated where the user can actually see it. */}
      <p className="m-0 mb-[var(--space-4)] flex items-start gap-[var(--space-2)] [font-family:var(--font-sans)] text-[length:var(--text-sm)] leading-[1.6] text-[var(--chrome-fg-muted)]">
        <ShieldCheck size={14} className="mt-[3px] shrink-0 text-[var(--chrome-accent)]" />
        <span>
          {t('settings.usage_privacy', {
            defaultValue:
              'These numbers are counted from your own history, on this machine, and are never sent anywhere. VoiceStudio has no analytics service — nothing here leaves your computer.',
          })}
        </span>
      </p>

      {err && (
        <p className="m-0 [font-family:var(--font-sans)] text-[length:var(--text-sm)] text-[var(--chrome-severity-err)]">
          {err}
        </p>
      )}

      {s && s.takes === 0 && (
        <p className="m-0 [font-family:var(--font-sans)] text-[length:var(--text-md)] text-[var(--chrome-fg-muted)]">
          {t('settings.usage_empty', {
            defaultValue:
              "You haven't generated anything yet — make something and it'll show up here.",
          })}
        </p>
      )}

      {s && s.takes > 0 && (
        <div className="flex flex-col gap-[var(--space-5)]">
          <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-[var(--space-3)]">
            <Stat
              label={t('settings.usage_takes', { defaultValue: 'Takes generated' })}
              value={s.takes}
              sub={
                since
                  ? t('settings.usage_since', { defaultValue: 'since {{date}}', date: since })
                  : null
              }
            />
            <Stat
              label={t('settings.usage_audio', { defaultValue: 'Audio produced' })}
              value={fmtDuration(s.audio_seconds)}
            />
            <Stat label={t('settings.usage_voices', { defaultValue: 'Voices' })} value={s.voices} />
            <Stat
              label={t('settings.usage_active_days', { defaultValue: 'Days used' })}
              value={s.active_days}
            />
            {s.dubs > 0 && (
              <Stat label={t('settings.usage_dubs', { defaultValue: 'Dubs' })} value={s.dubs} />
            )}
            {s.starred > 0 && (
              <Stat
                label={t('settings.usage_starred', { defaultValue: 'Starred takes' })}
                value={s.starred}
              />
            )}
          </div>

          <Bars title={t('settings.usage_by_mode', { defaultValue: 'By mode' })} rows={s.by_mode} />
          <Bars
            title={t('settings.usage_by_language', { defaultValue: 'By language' })}
            rows={s.by_language}
          />
        </div>
      )}
    </SettingsSection>
  );
}
