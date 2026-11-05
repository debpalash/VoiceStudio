import { CheckIcon, TerminalIcon } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { getBridge } from '@/components/bridge';
import type { TerminalCommandStatus } from '../../../../preload/index.d';
import { SettingsRow } from './settings-layout';

/**
 * Shows whether the `voicestudio` terminal command works in new terminals for
 * DMG/AppImage users. The first launch sets it up (setUpTerminalCommand); this
 * row repairs it if the user later removed it. Windows installers, packages
 * and Snap own their own PATH setup.
 */
export function TerminalCommandSettings() {
  const { t } = useTranslation();
  const terminal = getBridge()?.terminal;
  const [status, setStatus] = useState<TerminalCommandStatus | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const bridge = getBridge()?.terminal;
    if (!bridge) return;
    let live = true;
    bridge
      .status()
      .then((next) => live && setStatus(next))
      .catch(() => live && setStatus(null));
    return () => {
      live = false;
    };
  }, []);

  if (!terminal || !status?.supported) return null;

  const add = async () => {
    setBusy(true);
    try {
      const next = await terminal.addToPath();
      setStatus(next);
      if (next.onPath) toast.success(t('settings.terminal_command_added'));
      else toast.error(t('settings.terminal_command_conflict', { path: next.command }));
    } catch {
      // A filesystem error ("EACCES: ...") tells the user nothing they can act on.
      toast.error(t('settings.terminal_command_failed'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <SettingsRow
      id="terminal-command-label"
      title={t('settings.terminal_command')}
      description={
        status.linked
          ? t('settings.terminal_command_desc')
          : t('settings.terminal_command_conflict', { path: status.command })
      }
    >
      {status.linked && status.onPath ? (
        <span className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
          <CheckIcon className="size-4 text-emerald-500" />
          {t('settings.terminal_command_ready')}
        </span>
      ) : (
        <Button
          size="sm"
          variant="outline"
          disabled={busy || !status.linked}
          aria-labelledby="terminal-command-label"
          onClick={() => void add()}
        >
          <TerminalIcon />
          {t('settings.terminal_command_add')}
        </Button>
      )}
    </SettingsRow>
  );
}
