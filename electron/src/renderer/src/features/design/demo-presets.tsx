import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ChevronDownIcon, HeadphonesIcon } from 'lucide-react';
import { AudioPreviewButton } from '@/components/audio-preview-button';
import { Button } from '@/components/ui/button';
import { apiJson, apiPath } from '@/lib/api/client';

export interface DemoPreset {
  id: string;
  name: string;
  description: string;
  attrs: Record<string, string>;
  script: string;
  language: string;
  preview_url: string;
  is_demo: boolean;
}
export function DemoPresets({
  disabled,
  onUse,
  initialOpen = false,
}: {
  initialOpen?: boolean;
  disabled: boolean;
  onUse: (preset: DemoPreset) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(initialOpen);
  const query = useQuery({
    queryKey: ['personalities'],
    queryFn: ({ signal }) => apiJson<DemoPreset[]>('/personalities', { signal }),
    staleTime: Infinity,
  });
  return (
    <details
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      className="group shrink-0"
    >
      <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 rounded-md py-1 text-sm text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40 group-open:text-foreground [&::-webkit-details-marker]:hidden">
        <HeadphonesIcon className="size-4 text-primary/80" aria-hidden="true" />
        {t('demo.hear_demo')}
        <ChevronDownIcon
          className="size-3.5 transition-transform duration-150 group-open:rotate-180 motion-reduce:transition-none"
          aria-hidden="true"
        />
      </summary>
      <div className="mt-3 grid grid-cols-2 gap-3 max-lg:grid-cols-1">
        {query.data
          ?.filter((preset) => preset.is_demo)
          .map((preset) => (
            <article
              key={preset.id}
              className="flex min-w-0 flex-col gap-2 rounded-xl border border-border/40 bg-muted/15 p-3"
            >
              <h3 className="text-sm font-medium">{preset.name}</h3>
              <p className="text-xs leading-5 text-muted-foreground">{preset.description}</p>
              <div className="mt-auto flex items-center justify-between gap-2">
                <AudioPreviewButton
                  src={apiPath(preset.preview_url)}
                  source={'design-demo-' + preset.id}
                />
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={disabled}
                  onClick={() => {
                    onUse(preset);
                    setOpen(false);
                  }}
                >
                  {t('demo.preset_use')}
                </Button>
              </div>
            </article>
          ))}
      </div>
    </details>
  );
}
