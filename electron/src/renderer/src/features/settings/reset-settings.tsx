import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AlertTriangleIcon,
  ArchiveIcon,
  BoxesIcon,
  ChevronRightIcon,
  DatabaseIcon,
  FolderIcon,
  HistoryIcon,
  PaletteIcon,
  RotateCcwIcon,
  ScrollTextIcon,
  SlidersHorizontalIcon,
  WrenchIcon,
  type LucideIcon,
} from 'lucide-react';
import { toast } from 'sonner';
import { getBridge } from '@/components/bridge';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { apiFetch, describeError } from '@/lib/api/client';
import { projectLibrary } from '@/features/longform/project-library';
import { clearLongformDraftForReset } from '@/features/longform/longform-session';
import { clearDubDraftForReset } from '@/features/dub/dub-session';
import { fmtBytes } from '@shared/components/settings/models/format';
import { SettingsSection } from './settings-layout';
import { forgetSetupProgress } from '@/lib/setup-progress';

type ScopeKey =
  | 'ui_prefs'
  | 'settings'
  | 'history'
  | 'content'
  | 'engines'
  | 'tools'
  | 'models'
  | 'caches'
  | 'logs';

interface ResetScope {
  key: string;
  size_bytes: number;
  exists: boolean;
  shared: boolean;
  needs_restart: boolean;
}

const FRONTEND_SCOPES = new Set<ScopeKey>(['ui_prefs', 'history']);
const SCOPE_ORDER: ScopeKey[] = [
  'ui_prefs',
  'settings',
  'history',
  'content',
  'engines',
  'tools',
  'models',
  'caches',
  'logs',
];
const PRESETS = {
  ui: ['ui_prefs'],
  settings: ['ui_prefs', 'settings'],
  assets: ['models', 'engines', 'tools', 'caches'],
  everything: ['ui_prefs', 'settings', 'content', 'engines', 'tools', 'models', 'caches', 'logs'],
} satisfies Record<string, ScopeKey[]>;

const SCOPE_ICONS: Record<ScopeKey, LucideIcon> = {
  ui_prefs: PaletteIcon,
  settings: SlidersHorizontalIcon,
  history: HistoryIcon,
  content: FolderIcon,
  engines: BoxesIcon,
  tools: WrenchIcon,
  models: DatabaseIcon,
  caches: ArchiveIcon,
  logs: ScrollTextIcon,
};

export const UI_PREFERENCE_KEYS = [
  'voicestudio.theme.v2',
  'voicestudio.theme',
  'voicestudio.appearance',
  'voicestudio.locale',
  'voicestudio.clone.settings.v1',
  'voicestudio.take-settings.v1',
  'voicestudio.review-mode.v1',
  'voicestudio.settings.last-route',
  'voicestudio.transcription.mode',
  'voicestudio.workspace-layout',
  'voicestudio.library-width',
  'voicestudio.editor-width',
  'voicestudio.inspector-width',
  'voicestudio.gallery.favorites.v1',
  'voicestudio.dismissed-system-notifications',
  'omnivoice.demoClonePrompted',
  'omnivoice.dubbingDemoDismissed',
] as const;

export const LOCAL_SETTING_KEYS = ['voicestudio.dictation-settings.v1'] as const;

export function selectedBytes(scopes: ResetScope[], selected: ScopeKey[]): number {
  return scopes
    .filter((scope) => selected.includes(scope.key as ScopeKey) && scope.exists)
    .reduce((sum, scope) => sum + scope.size_bytes, 0);
}

export function resetPlan(selected: ScopeKey[]) {
  return {
    disk: selected.filter((scope) => !FRONTEND_SCOPES.has(scope)),
    prefs: selected.includes('ui_prefs'),
    settings: selected.includes('settings'),
    history: selected.includes('history') && !selected.includes('content'),
    content: selected.includes('content'),
    setup:
      selected.includes('ui_prefs') &&
      selected.includes('settings') &&
      selected.includes('content'),
  };
}

function sameScopes(left: ScopeKey[], right: ScopeKey[]): boolean {
  return left.length === right.length && left.every((scope) => right.includes(scope));
}

async function clearFrontendState(plan: ReturnType<typeof resetPlan>): Promise<void> {
  if (plan.history) {
    await Promise.all([
      apiFetch('/history', { method: 'DELETE' }),
      apiFetch('/dub/history', { method: 'DELETE' }),
    ]);
    localStorage.removeItem('omni_transcriptions');
  }
  if (plan.content) {
    await projectLibrary.clear();
    clearLongformDraftForReset();
    clearDubDraftForReset();
    localStorage.removeItem('voicestudio.design.v1');
    localStorage.removeItem('omni_transcriptions');
  }
  if (plan.setup) forgetSetupProgress();
  if (plan.prefs) for (const key of UI_PREFERENCE_KEYS) localStorage.removeItem(key);
  if (plan.settings) for (const key of LOCAL_SETTING_KEYS) localStorage.removeItem(key);
}

export function ResetSettings({
  reload = () => window.location.reload(),
}: {
  reload?: () => void;
}) {
  const { t } = useTranslation();
  const bridge = useMemo(() => getBridge(), []);
  const [scopes, setScopes] = useState<ResetScope[]>([]);
  const [selected, setSelected] = useState<ScopeKey[]>(PRESETS.ui);
  const [advanced, setAdvanced] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  const [scanFailed, setScanFailed] = useState(false);

  useEffect(() => {
    let current = true;
    if (!bridge) return;
    Promise.all([bridge.backend.getConnection(), bridge.maintenance.scanReset()])
      .then(([connection, value]) => {
        if (!current) return;
        if (connection.remote) setScanFailed(true);
        else setScopes(value);
      })
      .catch(() => {
        if (current) setScanFailed(true);
      });
    return () => {
      current = false;
    };
  }, [bridge]);

  const byKey = useMemo(
    () =>
      Object.fromEntries(scopes.map((scope) => [scope.key, scope])) as Record<string, ResetScope>,
    [scopes],
  );
  const nativeAvailable = Boolean(bridge && scopes.length);
  const bytes = selectedBytes(scopes, selected);
  const activePreset = Object.entries(PRESETS).find(([, value]) =>
    sameScopes(value, selected),
  )?.[0];
  const needsTyped = selected.includes('content');
  const typedOk = !needsTyped || typed.trim().toUpperCase() === t('settings.reset_confirm_word');

  const labels = Object.fromEntries(
    SCOPE_ORDER.map((scope) => [scope, t(`settings.reset_scope_${scope}`)]),
  ) as Record<ScopeKey, string>;
  const tiers = [
    ['ui', 'settings.reset_tier_ui', 'settings.reset_tier_ui_hint'],
    ['settings', 'settings.reset_tier_settings', 'settings.reset_tier_settings_hint'],
    ['assets', 'settings.reset_tier_assets', 'settings.reset_tier_assets_hint'],
    ['everything', 'settings.reset_tier_everything', 'settings.reset_tier_everything_hint'],
  ] as const;

  const run = async () => {
    if (busy || selected.length === 0 || !typedOk) return;
    setBusy(true);
    try {
      const plan = resetPlan(selected);
      if (plan.history) await clearFrontendState({ ...plan, prefs: false, content: false });
      let partial: string[] = [];
      if (plan.disk.length) {
        if (!bridge || !nativeAvailable) throw new Error(t('settings.reset_body_web'));
        const report = await bridge.maintenance.purgeReset(plan.disk);
        partial = [...report.failed, ...report.refused];
      }
      await clearFrontendState({ ...plan, history: false });
      if (partial.length) {
        toast.error(t('settings.reset_partial', { paths: partial.join(', ') }), {
          duration: 10_000,
        });
      } else {
        toast.success(t(plan.disk.length ? 'settings.reset_done_restart' : 'settings.reset_done'));
      }
      setConfirming(false);
      window.setTimeout(reload, 400);
    } catch (error) {
      toast.error(t('settings.reset_failed', { message: describeError(error) }));
      setBusy(false);
    }
  };

  return (
    <>
      <SettingsSection icon={RotateCcwIcon} title={t('settings.reset')}>
        <div className="space-y-4 p-4">
          <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
            {t('settings.reset_desc')}
          </p>
          {(!bridge || scanFailed) && (
            <Alert>
              <AlertTriangleIcon />
              <AlertTitle>{t('settings.reset_tier_ui')}</AlertTitle>
              <AlertDescription>{t('settings.reset_body_web')}</AlertDescription>
            </Alert>
          )}
          <div className="grid gap-2 @2xl:grid-cols-2">
            {tiers.map(([id, label, hint]) => {
              const scopesForTier = PRESETS[id];
              const disabled = id !== 'ui' && !nativeAvailable;
              const active = activePreset === id;
              return (
                <label
                  key={id}
                  className={`flex min-h-24 gap-3 rounded-xl border p-3.5 transition-colors ${
                    disabled
                      ? 'cursor-not-allowed opacity-45'
                      : active
                        ? 'border-primary/35 bg-primary/8 shadow-[inset_2px_0_0_var(--primary)]'
                        : 'cursor-pointer border-border/60 bg-background/30 hover:bg-muted/40'
                  }`}
                >
                  <input
                    type="radio"
                    name="reset-tier"
                    checked={active}
                    disabled={disabled}
                    onChange={() => setSelected(scopesForTier)}
                    className="mt-1 accent-primary"
                  />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-baseline justify-between gap-3 text-sm font-medium">
                      <span>{t(label)}</span>
                      {id !== 'ui' && (
                        <span className="shrink-0 font-mono text-xs font-normal tabular-nums text-muted-foreground">
                          {nativeAvailable ? fmtBytes(selectedBytes(scopes, scopesForTier)) : '—'}
                        </span>
                      )}
                    </span>
                    <span className="mt-1 block text-xs leading-relaxed text-muted-foreground">
                      {t(hint)}
                    </span>
                  </span>
                </label>
              );
            })}
          </div>
          {nativeAvailable && (
            <>
              <Button
                variant="ghost"
                size="sm"
                aria-expanded={advanced}
                onClick={() => setAdvanced((value) => !value)}
              >
                <ChevronRightIcon
                  className={advanced ? 'rotate-90 transition-transform' : 'transition-transform'}
                />
                {t('settings.reset_advanced')}
              </Button>
              {advanced && (
                <div className="overflow-hidden rounded-xl border border-border/60 bg-background/25">
                  {SCOPE_ORDER.map((scope) => {
                    const value = byKey[scope];
                    const frontend = FRONTEND_SCOPES.has(scope);
                    const disabled = !frontend && value?.exists === false;
                    const Icon = SCOPE_ICONS[scope];
                    return (
                      <label
                        key={scope}
                        className={`flex items-center gap-3 border-b border-border/45 px-3 py-2.5 last:border-0 ${
                          disabled ? 'opacity-45' : 'cursor-pointer hover:bg-muted/35'
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={selected.includes(scope)}
                          disabled={disabled}
                          onChange={() =>
                            setSelected((current) =>
                              current.includes(scope)
                                ? current.filter((item) => item !== scope)
                                : [...current, scope],
                            )
                          }
                          className="accent-primary"
                        />
                        <Icon className="size-4 shrink-0 text-muted-foreground" />
                        <span className="min-w-0 flex-1 text-sm">
                          {labels[scope]}
                          {value?.shared && (
                            <span className="mt-0.5 block text-xs text-amber-500">
                              {t('settings.reset_models_shared')}
                            </span>
                          )}
                        </span>
                        {!frontend && (
                          <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
                            {fmtBytes(value?.size_bytes ?? 0)}
                          </span>
                        )}
                      </label>
                    );
                  })}
                </div>
              )}
            </>
          )}
          <Button
            variant="destructive"
            disabled={selected.length === 0}
            onClick={() => {
              setTyped('');
              setConfirming(true);
            }}
          >
            <RotateCcwIcon />
            {t('settings.reset')}
          </Button>
        </div>
      </SettingsSection>

      <Dialog open={confirming} onOpenChange={(open) => !busy && setConfirming(open)}>
        <DialogContent showCloseButton={!busy}>
          <DialogHeader>
            <DialogTitle>{t('settings.reset_confirm_title')}</DialogTitle>
            <DialogDescription>
              {needsTyped ? t('settings.reset_irreversible') : t('settings.reset_restart_note')}
            </DialogDescription>
          </DialogHeader>
          <ul className="space-y-1 rounded-lg bg-muted/35 p-3 text-xs text-muted-foreground">
            {selected.map((scope) => (
              <li key={scope} className="flex justify-between gap-3">
                <span>{labels[scope]}</span>
                {!FRONTEND_SCOPES.has(scope) && (
                  <span className="shrink-0 font-mono tabular-nums">
                    {fmtBytes(byKey[scope]?.size_bytes ?? 0)}
                  </span>
                )}
              </li>
            ))}
          </ul>
          {byKey.models?.shared && selected.includes('models') && (
            <Alert variant="destructive">
              <AlertTriangleIcon />
              <AlertDescription>{t('settings.reset_models_shared_warning')}</AlertDescription>
            </Alert>
          )}
          {needsTyped && (
            <label className="space-y-1.5 text-sm">
              <span>
                {t('settings.reset_type_to_confirm', { word: t('settings.reset_confirm_word') })}
              </span>
              <Input
                autoFocus
                value={typed}
                disabled={busy}
                onChange={(event) => setTyped(event.target.value)}
              />
            </label>
          )}
          <DialogFooter>
            <Button variant="ghost" disabled={busy} onClick={() => setConfirming(false)}>
              {t('common.cancel')}
            </Button>
            <Button variant="destructive" disabled={busy || !typedOk} onClick={() => void run()}>
              {planHasDisk(selected)
                ? t('settings.reset_confirm_restart', { size: fmtBytes(bytes) })
                : t('settings.reset_confirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function planHasDisk(selected: ScopeKey[]): boolean {
  return resetPlan(selected).disk.length > 0;
}
