import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AlertTriangleIcon,
  DatabaseIcon,
  FolderIcon,
  KeyRoundIcon,
  PackageIcon,
  ScrollTextIcon,
  Trash2Icon,
  type LucideIcon,
} from 'lucide-react';
import { toast } from 'sonner';
import { getBridge } from '@/components/bridge';
import { Alert, AlertDescription } from '@/components/ui/alert';
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
import { describeError } from '@/lib/api/client';
import type { UninstallTarget } from '../../../../preload/index.d';
import { fmtBytes } from '@shared/components/settings/models/format';
import { SettingsSection } from './settings-layout';

const ICONS: Record<UninstallTarget['key'], LucideIcon> = {
  data: FolderIcon,
  env: PackageIcon,
  logs: ScrollTextIcon,
  userenv: KeyRoundIcon,
  models: DatabaseIcon,
};

export function uninstallBytes(targets: UninstallTarget[], includeModels: boolean): number {
  return targets
    .filter((target) => target.exists && (includeModels || !target.shared))
    .reduce((sum, target) => sum + target.size_bytes, 0);
}

export function UninstallSettings() {
  const { t } = useTranslation();
  const bridge = useMemo(() => getBridge(), []);
  const [targets, setTargets] = useState<UninstallTarget[]>([]);
  const [scanFailed, setScanFailed] = useState(false);
  const [includeModels, setIncludeModels] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);

  const scan = async () => {
    if (!bridge) return;
    setScanFailed(false);
    try {
      setTargets(await bridge.maintenance.scanUninstall());
    } catch {
      setScanFailed(true);
    }
  };

  useEffect(() => {
    void scan();
  }, []);

  if (!bridge) return null;

  const present = targets.filter((target) => target.exists);
  const sharedModels = present.find((target) => target.key === 'models' && target.shared);
  const selected = present.filter((target) => includeModels || !target.shared);
  const bytes = uninstallBytes(targets, includeModels);
  const confirmation = t('settings.uninstall_confirm_word');
  const confirmed = typed.trim().toUpperCase() === confirmation.toUpperCase();
  const labels = Object.fromEntries(
    (Object.keys(ICONS) as UninstallTarget['key'][]).map((key) => [
      key,
      t(`settings.uninstall_target_${key}`),
    ]),
  ) as Record<UninstallTarget['key'], string>;

  const purge = async () => {
    if (busy || !confirmed) return;
    setBusy(true);
    try {
      await bridge.maintenance.purgeUninstall(includeModels);
    } catch (error) {
      toast.error(t('settings.uninstall_failed', { message: describeError(error) }));
      setBusy(false);
    }
  };

  return (
    <>
      <SettingsSection icon={Trash2Icon} title={t('settings.uninstall')}>
        <div className="space-y-4 p-4">
          <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
            {t('settings.uninstall_body')}
          </p>
          {scanFailed && (
            <Alert variant="destructive">
              <AlertTriangleIcon />
              <AlertDescription className="flex items-center justify-between gap-3">
                <span>{t('common.error')}</span>
                <Button size="sm" variant="ghost" onClick={() => void scan()}>
                  {t('common.retry')}
                </Button>
              </AlertDescription>
            </Alert>
          )}
          {present.length > 0 && (
            <div className="overflow-hidden rounded-xl border border-border/60 bg-background/25">
              {present
                .filter((target) => !target.shared)
                .map((target) => (
                  <TargetRow
                    key={`${target.key}:${target.path}`}
                    target={target}
                    label={labels[target.key]}
                  />
                ))}
              {sharedModels && (
                <label className="flex cursor-pointer items-start gap-3 border-t border-border/50 px-3 py-3 hover:bg-muted/30">
                  <input
                    type="checkbox"
                    checked={includeModels}
                    onChange={(event) => setIncludeModels(event.target.checked)}
                    className="mt-1 accent-primary"
                  />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2 text-sm font-medium">
                      <DatabaseIcon className="size-4 text-amber-500" />
                      {labels.models}
                    </span>
                    <span className="mt-1 block text-xs leading-relaxed text-muted-foreground">
                      {t('settings.uninstall_models_caveat')}
                    </span>
                    <code className="mt-1 block truncate text-[11px] text-muted-foreground/70">
                      {sharedModels.path}
                    </code>
                  </span>
                  <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
                    {fmtBytes(sharedModels.size_bytes)}
                  </span>
                </label>
              )}
            </div>
          )}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">
              {t('settings.uninstall_total', { count: selected.length, size: fmtBytes(bytes) })}
            </p>
            <Button
              variant="destructive"
              disabled={busy || scanFailed || present.length === 0}
              onClick={() => {
                setTyped('');
                setConfirming(true);
              }}
            >
              <Trash2Icon />
              {t('settings.uninstall')}
            </Button>
          </div>
        </div>
      </SettingsSection>

      <Dialog open={confirming} onOpenChange={(open) => !busy && setConfirming(open)}>
        <DialogContent showCloseButton={!busy} className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t('settings.uninstall_confirm_title')}</DialogTitle>
            <DialogDescription>{t('settings.uninstall_confirm_body')}</DialogDescription>
          </DialogHeader>
          <ul className="space-y-1 rounded-xl bg-muted/30 p-3">
            {selected.map((target) => (
              <li
                key={`${target.key}:${target.path}`}
                className="flex justify-between gap-3 text-xs"
              >
                <span>{labels[target.key]}</span>
                <span className="font-mono tabular-nums text-muted-foreground">
                  {fmtBytes(target.size_bytes)}
                </span>
              </li>
            ))}
          </ul>
          {sharedModels && includeModels && (
            <Alert variant="destructive">
              <AlertTriangleIcon />
              <AlertDescription>
                {t('settings.uninstall_models_warning', {
                  size: fmtBytes(sharedModels.size_bytes),
                })}
              </AlertDescription>
            </Alert>
          )}
          <label className="space-y-1.5 text-sm">
            <span>{t('settings.uninstall_type_to_confirm', { word: confirmation })}</span>
            <Input
              autoFocus
              autoComplete="off"
              spellCheck={false}
              value={typed}
              disabled={busy}
              onChange={(event) => setTyped(event.target.value)}
            />
          </label>
          <DialogFooter>
            <Button variant="ghost" disabled={busy} onClick={() => setConfirming(false)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="destructive"
              disabled={busy || !confirmed}
              onClick={() => void purge()}
            >
              <Trash2Icon />
              {t('settings.uninstall_confirm', { size: fmtBytes(bytes) })}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function TargetRow({ target, label }: { target: UninstallTarget; label: string }) {
  const Icon = ICONS[target.key];
  return (
    <div className="flex items-center gap-3 border-b border-border/50 px-3 py-2.5 last:border-0">
      <Icon className="size-4 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium">{label}</span>
        <code className="mt-0.5 block truncate text-[11px] text-muted-foreground/70">
          {target.path}
        </code>
      </span>
      <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
        {fmtBytes(target.size_bytes)}
      </span>
    </div>
  );
}
