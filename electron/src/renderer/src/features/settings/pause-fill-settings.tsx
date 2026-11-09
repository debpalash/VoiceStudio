import { useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { AudioLinesIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { apiJson } from '@/lib/api/client';
import { SettingsRow, SettingsSection } from './settings-layout';
import { useSettingsAction } from './use-settings-action';

const PATH = '/api/settings/pause-fill';
const KEY = ['pause-fill'] as const;

interface PauseFillState {
  mode: 'silence' | 'room_tone';
  env_pinned: boolean;
}

/** #3611: fill [pause] markers and audiobook gaps with room tone instead of digital silence. */
export function PauseFillSettings() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const action = useSettingsAction();
  const query = useQuery({
    queryKey: KEY,
    queryFn: ({ signal }) => apiJson<PauseFillState>(PATH, { signal }),
  });
  const state = query.data;
  // The mode the last save tried, so Retry repeats a failed save instead of
  // only re-reading the setting.
  const attempted = useRef<PauseFillState['mode'] | null>(null);
  const save = (mode: PauseFillState['mode']) => {
    attempted.current = mode;
    void action.run(async () => {
      const saved = await apiJson<PauseFillState>(PATH, {
        method: 'PUT',
        body: JSON.stringify({ mode }),
      });
      client.setQueryData(KEY, saved);
    });
  };
  const retry = () => {
    if (action.error && attempted.current) save(attempted.current);
    else void query.refetch();
  };
  return (
    <SettingsSection icon={AudioLinesIcon} title={t('pauseFill.title')}>
      <SettingsRow
        id="pause-fill"
        title={t('pauseFill.room_tone')}
        description={t('pauseFill.room_tone_note')}
      >
        <Switch
          aria-label={t('pauseFill.room_tone')}
          checked={state?.mode === 'room_tone'}
          disabled={!state || action.busy || state.env_pinned}
          onCheckedChange={(on) => save(on ? 'room_tone' : 'silence')}
        />
      </SettingsRow>
      {state?.env_pinned && (
        <p className="p-4 text-sm text-muted-foreground">
          {t('pauseFill.env_pinned', { env: 'OMNIVOICE_PAUSE_FILL' })}
        </p>
      )}
      {(action.error || query.isError) && (
        <p role="alert" className="p-4 text-sm text-destructive">
          {t('common.error')}{' '}
          <Button size="sm" variant="ghost" onClick={retry}>
            {t('common.retry')}
          </Button>
        </p>
      )}
    </SettingsSection>
  );
}
