// electron/src/shared/components/UpdatesPanel.jsx
// Update management panel — lives under Settings → Updates. Shows live update
// status (with the available build's actual release notes), channel switcher,
// the data-safety line (pre-update DB backups), the app's own "What's new"
// changelog viewer, and the GitHub releases (changelog/history) list.
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Download,
  RotateCw,
  AlertTriangle,
  RefreshCw,
  X,
  ShieldCheck,
  Sparkles,
  CheckCircle2,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { useAppStore } from '../store';
import { installUpdate, checkForUpdate } from '../utils/updater';
import { prepareReleases } from '../utils/updatePresentation';
import { setChannel } from '../utils/channelControl';
import { fetchChangelog, fetchBackupState } from '../utils/updatesApi';
import { APP_VERSION } from '../utils/appVersion';
import { isAppBusy } from '../utils/appBusy';
import MarkdownLite from './MarkdownLite';
import ChangelogViewer from './ChangelogViewer';

export default function UpdatesPanel() {
  const { t } = useTranslation();
  const status = useAppStore((s) => s.updateStatus);
  const version = useAppStore((s) => s.updateVersion);
  const notes = useAppStore((s) => s.updateNotes);
  const error = useAppStore((s) => s.updateError);
  const progress = useAppStore((s) => s.updateProgress);
  const appVersion = useAppStore((s) => s.appVersion);
  const channel = useAppStore((s) => s.updateChannel);
  const releases = useAppStore((s) => s.releases);
  const releasesStatus = useAppStore((s) => s.releasesStatus);
  const loadReleases = useAppStore((s) => s.loadReleases);
  const dismissUpdate = useAppStore((s) => s.dismissUpdate);
  // Subscribed, not read via getState() — `busy` disables the install button,
  // so it has to re-render when the work starts or finishes.
  const dubStep = useAppStore((s) => s.dubStep);
  const pillStage = useAppStore((s) => s.stage);
  const ttsInflight = useAppStore((s) => s.ttsInflight);

  const [changelog, setChangelog] = useState([]);
  const [backup, setBackup] = useState(null);

  useEffect(() => {
    loadReleases(channel);
  }, [channel, loadReleases]);

  // Local-first data: the shipped changelog + the newest pre-migration DB
  // backup. Both degrade to empty on failure (the sections just hide).
  useEffect(() => {
    let alive = true;
    fetchChangelog(5).then((rel) => alive && setChangelog(rel));
    fetchBackupState().then((b) => alive && setBackup(b));
    return () => {
      alive = false;
    };
  }, []);

  // Opening the panel counts as reading the notes — retire the one-time
  // footer "What's new" pill for this version (feat/safe-updates). The pill
  // compares against the build constant, so fall back to it when the Tauri
  // version isn't available (web/dev builds).
  useEffect(() => {
    const v = appVersion || (APP_VERSION !== 'unknown' ? APP_VERSION : null);
    if (v) useAppStore.getState().setWhatsNewSeenVersion?.(v);
  }, [appVersion]);

  // Installing relaunches the process. `dubStep === 'generating'` used to be
  // the whole check, which let a relaunch through during an upload, a
  // transcription, a translation, an export or a standalone synth.
  //
  // Subscribed (not getState()) so this re-renders when work starts or stops:
  // it greys the button out and says why, rather than letting the user click
  // and get a toast back.
  const busy = isAppBusy({ dubStep, stage: pillStage, ttsInflight });
  const onInstall = () => {
    // The click-time read is the authority — work can start between the last
    // render and the click, so `busy` above cannot be the safety check.
    if (isAppBusy(useAppStore.getState())) {
      toast(t('update.busy'), { icon: '⏳' });
      return;
    }
    installUpdate(useAppStore.getState());
  };
  const rows = prepareReleases(releases, channel, appVersion);
  const latestBackup = backup?.available ? backup.latest : null;

  return (
    <div className="updates-panel">
      <div className="updates-panel__summary">
        <div className={`updates-panel__live updates-panel__live--${status}`}>
          <span className="updates-panel__status-icon" aria-hidden="true">
            {status === 'available' || status === 'downloading' ? (
              <Download size={18} />
            ) : status === 'ready' || status === 'checking' ? (
              <RefreshCw size={18} className={status === 'checking' ? 'animate-spin' : ''} />
            ) : status === 'error' ? (
              <AlertTriangle size={18} />
            ) : (
              <CheckCircle2 size={18} />
            )}
          </span>
          <div className="updates-panel__live-copy">
            <span className="updates-panel__version-label">
              {t('about.version')} {appVersion || APP_VERSION}
            </span>
            {status === 'available' && (
              <button
                className="updates-panel__cta"
                onClick={onInstall}
                disabled={busy}
                title={busy ? t('update.busy') : undefined}
              >
                {t('update.available', { version: version || '' })} · {t('update.install')}
              </button>
            )}
            {status === 'downloading' && (
              <span className="updates-panel__progress">
                {t('update.downloading', { pct: Math.round(progress) })}
                <span className="updates-panel__bar">
                  <span style={{ width: `${progress}%` }} />
                </span>
              </span>
            )}
            {status === 'ready' && (
              <button
                className="updates-panel__cta"
                onClick={onInstall}
                disabled={busy}
                title={busy ? t('update.busy') : undefined}
              >
                <RotateCw size={13} /> {t('update.restart')}
              </button>
            )}
            {status === 'error' && (
              <span className="updates-panel__err">
                {error || t('update.failed')}
                <button
                  className="updates-panel__link"
                  onClick={onInstall}
                  disabled={busy}
                  title={busy ? t('update.busy') : undefined}
                >
                  {t('update.retry')}
                </button>
                <button
                  className="updates-panel__icon"
                  onClick={dismissUpdate}
                  aria-label={t('update.dismiss')}
                >
                  <X size={13} />
                </button>
              </span>
            )}
            {(status === 'idle' || status === 'checking') && (
              <span className="updates-panel__ok">
                {t('updates.up_to_date', { version: appVersion || '' })}
                <button
                  className="updates-panel__link"
                  onClick={() => checkForUpdate(useAppStore.getState())}
                >
                  <RefreshCw size={12} /> {t('updates.check_now')}
                </button>
              </span>
            )}
          </div>
        </div>

        <div className="updates-panel__preferences">
          <div className="updates-panel__channel">
            <span>{t('about.update_channel')}</span>
            <div
              className="updates-panel__seg"
              role="radiogroup"
              aria-label={t('about.update_channel')}
            >
              {['stable', 'preview'].map((c) => (
                <button
                  key={c}
                  type="button"
                  role="radio"
                  aria-checked={channel === c}
                  className={`updates-panel__segbtn ${channel === c ? 'is-active' : ''}`}
                  onClick={() =>
                    setChannel(useAppStore.getState(), c).catch((e) =>
                      toast(t('settings.channel_set_failed', { message: e?.message || e }), {
                        icon: '⚠️',
                      }),
                    )
                  }
                >
                  {t(`about.channel_${c}`)}
                </button>
              ))}
            </div>
          </div>

          {/* Data-safety line: the backend snapshots omnivoice.db before every
              schema migration (i.e. before the first run of an updated build). */}
          <div className="updates-panel__backup" data-testid="backup-line">
            <ShieldCheck size={14} aria-hidden="true" />
            <span>
              {t('updates.backup_line')}{' '}
              {latestBackup?.created_at
                ? t('updates.backup_latest', {
                    when: new Date(latestBackup.created_at * 1000).toLocaleString(),
                  })
                : t('updates.backup_none')}
            </span>
          </div>
        </div>
      </div>

      {/* The available build's actual release notes — the updater manifest
          carries them (UpdateMeta.notes); render markdown-lite safely. */}
      {status === 'available' && notes && (
        <div className="updates-panel__notes" data-testid="update-notes">
          <div className="updates-panel__notes-head">
            {t('updates.notes_for', { version: version || '' })}
          </div>
          <MarkdownLite text={notes} className="updates-panel__notes-body" />
        </div>
      )}

      {/* "What's new" — the app's own CHANGELOG.md, newest expanded. */}
      {changelog.length > 0 && (
        <div className="updates-panel__whatsnew">
          <div className="updates-panel__rel-head">
            <Sparkles size={14} aria-hidden="true" /> {t('update.whats_new')}
          </div>
          <ChangelogViewer releases={changelog} />
        </div>
      )}

      <div className="updates-panel__releases">
        <div className="updates-panel__rel-head">{t('updates.releases')}</div>
        {releasesStatus === 'error' && (
          <div className="updates-panel__rel-empty">
            {t('updates.load_error')}
            <button className="updates-panel__link" onClick={() => loadReleases(channel)}>
              {t('updates.retry_load')}
            </button>
          </div>
        )}
        {releasesStatus === 'loading' && (
          <div className="updates-panel__rel-empty">{t('updates.loading')}</div>
        )}
        {releasesStatus === 'loaded' && rows.length === 0 && (
          <div className="updates-panel__rel-empty">{t('updates.none')}</div>
        )}
        {rows.map((r) => (
          <div
            key={r.name || r.version}
            className={`updates-panel__rel ${r.current ? 'is-current' : ''}`}
          >
            <div className="updates-panel__rel-row">
              <span className="updates-panel__rel-ver">v{r.version}</span>
              {r.current && <span className="updates-panel__rel-tag">{t('updates.current')}</span>}
              {r.prerelease && (
                <span className="updates-panel__rel-pre">{t('updates.prerelease')}</span>
              )}
              <span className="updates-panel__rel-date">{r.date}</span>
            </div>
            {r.notes && <MarkdownLite text={r.notes} className="updates-panel__rel-notes" />}
          </div>
        ))}
      </div>
    </div>
  );
}
