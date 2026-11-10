import { CpuIcon } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { primaryUpdateAction, runUpdateAction } from '@/lib/update-actions';
import { updateErrorTitleKey } from '@/lib/update-format';
import { DISCORD_URL, ISSUES_URL } from '@shared/utils/contactLinks';
import type { BackendStatus, UpdateState } from '../../../preload/index.d';
import { appVersion, getBridge, platformFamily } from './bridge';
import { ExternalLink } from './external-link';

const REPO = 'https://github.com/debpalash/VoiceStudio';
const INSTALL_GUIDES = {
  mac: `${REPO}/blob/main/docs/install/macos.md`,
  windows: `${REPO}/blob/main/docs/install/windows.md`,
  linux: `${REPO}/blob/main/docs/install/linux.md`,
} as const;
const TROUBLESHOOTING = `${REPO}/blob/main/docs/install/troubleshooting.md`;

/**
 * Release page for this exact build. Development and preview builds carry the
 * same version as the last release but different code, so they get the list.
 */
export function releaseNotesUrl(version: string, isDev: boolean): string {
  return !isDev && /^\d+\.\d+\.\d+(-beta\.\d+)?$/.test(version)
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
      <ExternalLink
        href={releaseNotesUrl(version, getBridge()?.app?.isDev ?? false)}
        variant="link"
        showIcon={false}
        className="app-no-drag h-auto px-0 text-xs text-muted-foreground hover:text-foreground"
      >
        {t('update.whats_new')}
      </ExternalLink>
    </span>
  );
}

// A failed download keeps availableVersion, so it stays here with Retry.
const OFFERED = new Set<UpdateState['status']>(['available', 'downloading', 'downloaded', 'error']);

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
    // An event can arrive before the initial snapshot resolves; the event is newer.
    let heard = false;
    const unsubscribe = updates.onState((next) => {
      heard = true;
      setState(next);
    });
    void updates
      .getState()
      .then((next) => active && !heard && setState(next))
      .catch(() => {});
    return () => {
      active = false;
      unsubscribe();
    };
  }, []);
  if (!state || !OFFERED.has(state.status) || !state.availableVersion) return null;
  const action = primaryUpdateAction(state);
  const title =
    state.status === 'error'
      ? t(updateErrorTitleKey(state), { version: state.availableVersion })
      : state.status === 'downloaded'
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
      {action === 'retry' && (
        <Button size="sm" onClick={() => void runUpdateAction('retry', state, t)}>
          {t('update.retry')}
        </Button>
      )}
    </div>
  );
}

/**
 * The one help row on the setup screen, there before anything goes wrong. A
 * failure swaps the plain issue link for its prefilled report (`report`), so
 * no destination is offered twice.
 */
export function SetupHelpLinks({ leading, report }: { leading?: ReactNode; report?: ReactNode }) {
  const { t } = useTranslation();
  return (
    <nav
      aria-label={t('setup.help_label')}
      data-testid="setup-help-links"
      className="-mx-2 flex flex-wrap items-start gap-x-0.5 gap-y-1 text-muted-foreground [&_:is(a,button)]:h-7 [&_:is(a,button)]:px-2 [&_:is(a,button)]:text-xs [&_:is(a,button)]:font-normal [&_:is(a,button)]:text-muted-foreground [&_:is(a,button):hover]:text-foreground"
    >
      {leading}
      <ExternalLink href={INSTALL_GUIDES[platformFamily()]} variant="ghost" showIcon={false}>
        {t('setup.help_install')}
      </ExternalLink>
      <ExternalLink href={TROUBLESHOOTING} variant="ghost" showIcon={false}>
        {t('setup.help_troubleshooting')}
      </ExternalLink>
      {report ?? (
        <ExternalLink href={ISSUES_URL} variant="ghost" showIcon={false}>
          {t('setup.help_report')}
        </ExternalLink>
      )}
      <ExternalLink href={DISCORD_URL} variant="ghost" showIcon={false}>
        {t('setupHelp.discord')}
      </ExternalLink>
    </nav>
  );
}

/** Below the free-space line: voice models need room beyond the runtime. */
export const MODEL_HEADROOM_GIB = 8;

/** Which PyTorch build the install will download, shown before the multi-GB download starts. */
export function SetupHardware({ hardware }: { hardware: BackendStatus['setupHardware'] }) {
  const { t } = useTranslation();
  if (!hardware) return null;
  const { build, gpu, reason } = hardware;
  const text =
    build === 'metal'
      ? gpu
        ? t('setup.hardware_metal', { gpu })
        : t('setup.hardware_metal_unnamed')
      : build === 'cuda'
        ? gpu
          ? t('setup.hardware_cuda', { gpu })
          : t('setup.hardware_cuda_unnamed')
        : build === 'rocm'
          ? t('setup.hardware_rocm')
          : reason === 'windows_arm'
            ? t('setup.hardware_cpu_windows_arm')
            : reason === 'explicit'
              ? t('setup.hardware_cpu_explicit')
              : t('setup.hardware_cpu_no_nvidia');
  return (
    <p
      data-testid="setup-hardware"
      className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground"
    >
      <CpuIcon className="size-3.5 shrink-0" aria-hidden="true" />
      <span className="min-w-0 truncate">{text}</span>
    </p>
  );
}
