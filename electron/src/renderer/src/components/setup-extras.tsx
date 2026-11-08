import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { primaryUpdateAction, runUpdateAction } from '@/lib/update-actions';
import { DISCORD_URL, ISSUES_URL } from '@shared/utils/contactLinks';
import type { UpdateState } from '../../../preload/index.d';
import { appVersion, getBridge, platformFamily } from './bridge';
import { ExternalLink } from './external-link';

const REPO = 'https://github.com/debpalash/VoiceStudio';
const INSTALL_GUIDES = {
  mac: `${REPO}/blob/main/docs/install/macos.md`,
  windows: `${REPO}/blob/main/docs/install/windows.md`,
  linux: `${REPO}/blob/main/docs/install/linux.md`,
} as const;
const TROUBLESHOOTING = `${REPO}/blob/main/docs/install/troubleshooting.md`;

/** Release page for this exact build; development builds have no tag, so they get the list. */
export function releaseNotesUrl(version: string): string {
  return /^\d+\.\d+\.\d+(-beta\.\d+)?$/.test(version)
    ? `${REPO}/releases/tag/v${version}`
    : `${REPO}/releases`;
}

/** Version and channel beside the logo on the setup screen, with a link to its notes. */
export function SetupVersion() {
  const { t } = useTranslation();
  const version = appVersion();
  if (!version) return null;
  const beta = /-beta\.\d+$/.test(version);
  return (
    <span className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
      <span className="tabular-nums" data-testid="setup-version">
        {t('app.version', { version })}
      </span>
      {beta && (
        <span className="rounded-full border border-primary/30 bg-primary/10 px-1.5 py-px text-[10px] font-medium text-primary">
          {t('update.channel_beta')}
        </span>
      )}
      <a
        href={releaseNotesUrl(version)}
        target="_blank"
        rel="noreferrer"
        className="app-no-drag rounded-sm underline-offset-2 hover:text-foreground hover:underline focus-visible:outline-2 focus-visible:outline-ring"
        onClick={(event) => {
          const bridge = getBridge();
          if (!bridge) return;
          event.preventDefault();
          void bridge.files.openExternal(releaseNotesUrl(version)).catch(() => {});
        }}
      >
        {t('update.whats_new')}
      </a>
    </span>
  );
}

const OFFERED = new Set<UpdateState['status']>(['available', 'downloading', 'downloaded']);

/**
 * Offers a newer app before the runtime download: the runtime is installed for
 * this app's pins, so updating first avoids downloading several GB twice.
 */
export function SetupUpdateNotice() {
  const { t } = useTranslation();
  const [state, setState] = useState<UpdateState | null>(null);
  useEffect(() => {
    const updates = getBridge()?.updates;
    if (!updates) return;
    let active = true;
    void updates
      .getState()
      .then((next) => active && setState(next))
      .catch(() => {});
    const unsubscribe = updates.onState(setState);
    return () => {
      active = false;
      unsubscribe();
    };
  }, []);
  if (!state || !OFFERED.has(state.status) || !state.availableVersion) return null;
  const action = primaryUpdateAction(state);
  const title =
    state.status === 'downloaded'
      ? t('update.prompt_ready')
      : state.status === 'downloading'
        ? t('update.downloading', { pct: Math.round(state.progress) })
        : t('update.prompt_available', { version: state.availableVersion });
  return (
    <div
      role="status"
      data-testid="setup-update-notice"
      className="flex w-full flex-wrap items-center gap-3 rounded-xl border border-primary/30 bg-primary/[0.06] px-4 py-3 text-left"
    >
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{title}</p>
        <p className="text-xs text-muted-foreground">{t('setup.update_first')}</p>
      </div>
      {action === 'download' && (
        <Button size="sm" onClick={() => void runUpdateAction('download', state, t)}>
          {t('update.download')}
        </Button>
      )}
      {action === 'install' && (
        <Button size="sm" onClick={() => void runUpdateAction('install', state, t)}>
          {t('update.restart')}
        </Button>
      )}
    </div>
  );
}

/** Help that is there before anything goes wrong, not only after a failure. */
export function SetupHelpLinks() {
  const { t } = useTranslation();
  return (
    <nav
      aria-label={t('setup.help_label')}
      data-testid="setup-help-links"
      className="flex flex-wrap justify-center gap-1"
    >
      <ExternalLink href={INSTALL_GUIDES[platformFamily()]} variant="ghost">
        {t('setup.help_install')}
      </ExternalLink>
      <ExternalLink href={TROUBLESHOOTING} variant="ghost">
        {t('setup.help_troubleshooting')}
      </ExternalLink>
      <ExternalLink href={ISSUES_URL} variant="ghost">
        {t('setup.help_report')}
      </ExternalLink>
      <ExternalLink href={DISCORD_URL} variant="ghost">
        {t('setupHelp.discord')}
      </ExternalLink>
    </nav>
  );
}

/** Below the free-space line: voice models need room beyond the runtime. */
export const MODEL_HEADROOM_GIB = 8;
