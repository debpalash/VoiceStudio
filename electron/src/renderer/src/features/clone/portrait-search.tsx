import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { SearchIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { apiJson } from '@/lib/api/client';

export function PortraitSearch({
  name,
  onSelect,
  disabled = false,
}: {
  name: string;
  onSelect: (file: File) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{
    name: string;
    images: { title: string; data: string }[];
  }>();
  const [error, setError] = useState('');
  const latestName = useRef(name);
  latestName.current = name;
  return (
    <div className="space-y-2">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        disabled={disabled || !name.trim() || busy}
        onClick={async () => {
          setBusy(true);
          setError('');
          setResult(undefined);
          const query = name;
          try {
            const response = await apiJson<{ images: { title: string; data: string }[] }>(
              `/profile-images/search?name=${encodeURIComponent(query)}`,
            );
            if (latestName.current === query) setResult({ name: query, images: response.images });
          } catch {
            setError(t('profileIdentity.search_failed'));
          } finally {
            setBusy(false);
          }
        }}
      >
        <SearchIcon />
        {busy ? t('preferences.loading') : t('profileIdentity.search')}
      </Button>
      {error && (
        <p role="alert" className="text-xs text-muted-foreground">
          {error}
        </p>
      )}
      {result?.name === name && (
        <div className="grid grid-cols-5 gap-2">
          {result.images.length === 0 ? (
            <p role="status" className="col-span-5 text-xs text-muted-foreground">
              {t('profileIdentity.search_empty')}
            </p>
          ) : (
            result.images.map((image, index) => (
              <button
                key={index}
                type="button"
                disabled={disabled}
                aria-label={image.title || name}
                className="aspect-square overflow-hidden rounded-lg outline-none ring-offset-background hover:ring-2 hover:ring-primary focus-visible:ring-2 focus-visible:ring-ring"
                onClick={() => {
                  const bytes = Uint8Array.from(atob(image.data), (char) => char.charCodeAt(0));
                  onSelect(new File([bytes], 'portrait.jpg', { type: 'image/jpeg' }));
                  setResult(undefined);
                }}
              >
                <img
                  src={`data:image/jpeg;base64,${image.data}`}
                  alt={image.title}
                  className="size-full object-cover"
                />
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
