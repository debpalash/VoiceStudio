import { useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { FilmIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { apiJson } from '@/lib/api/client';
import { SettingsRow, SettingsSection } from './settings-layout';
import { useSettingsAction } from './use-settings-action';

const PATH = '/api/settings/video-export';
const KEY = ['video-export'] as const;

interface VideoExportState {
  encoder: 'software' | 'hardware';
  max_resolution: string;
  hardware_encoders: string[];
  resolutions: string[];
}

type Patch = Partial<Pick<VideoExportState, 'encoder' | 'max_resolution'>>;

/** #3732: opt-in hardware video encoding and a maximum resolution for dub exports. */
export function VideoExportSettings() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const action = useSettingsAction();
  const query = useQuery({
    queryKey: KEY,
    queryFn: ({ signal }) => apiJson<VideoExportState>(PATH, { signal }),
  });
  const state = query.data;
  // The change the last save tried, so Retry repeats a failed save.
  const attempted = useRef<Patch | null>(null);
  const save = (patch: Patch) => {
    attempted.current = patch;
    void action.run(async () => {
      const saved = await apiJson<VideoExportState>(PATH, {
        method: 'PUT',
        body: JSON.stringify(patch),
      });
      client.setQueryData(KEY, saved);
    });
  };
  const retry = () => {
    if (action.error && attempted.current) save(attempted.current);
    else void query.refetch();
  };
  const encoderItems = [
    { value: 'software', label: t('videoExport.encoder_software') },
    { value: 'hardware', label: t('videoExport.encoder_hardware') },
  ];
  const resolutionItems = (state?.resolutions ?? ['original']).map((value) => ({
    value,
    label: value === 'original' ? t('videoExport.res_original') : value,
  }));
  return (
    <SettingsSection icon={FilmIcon} title={t('videoExport.title')}>
      <SettingsRow
        id="video-encoder-label"
        title={t('videoExport.encoder')}
        description={
          state?.hardware_encoders.length
            ? t('videoExport.encoder_detected', { names: state.hardware_encoders.join(', ') })
            : t('videoExport.encoder_note')
        }
      >
        <Select
          items={encoderItems}
          value={state?.encoder ?? 'software'}
          onValueChange={(value) => save({ encoder: value as VideoExportState['encoder'] })}
          disabled={!state || action.busy}
        >
          <SelectTrigger aria-labelledby="video-encoder-label" className="min-w-52">
            <SelectValue />
          </SelectTrigger>
          <SelectContent align="end">
            {encoderItems.map(({ value, label }) => (
              <SelectItem key={value} value={value}>
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </SettingsRow>
      <SettingsRow
        id="video-max-resolution-label"
        title={t('videoExport.max_resolution')}
        description={t('videoExport.max_resolution_note')}
      >
        <Select
          items={resolutionItems}
          value={state?.max_resolution ?? 'original'}
          onValueChange={(value) => save({ max_resolution: value as string })}
          disabled={!state || action.busy}
        >
          <SelectTrigger aria-labelledby="video-max-resolution-label" className="min-w-36">
            <SelectValue />
          </SelectTrigger>
          <SelectContent align="end">
            {resolutionItems.map(({ value, label }) => (
              <SelectItem key={value} value={value}>
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </SettingsRow>
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
