import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { DownloadIcon } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { getBridge } from '@/components/bridge';
import { apiFetch, apiPath, ApiError } from '@/lib/api/client';
import { saveBackendFile } from '@/lib/native-save';

function exportPath(profileId: string, includeReference: boolean): string {
  return (
    '/personas/export/' +
    encodeURIComponent(profileId) +
    '?include_reference=' +
    String(includeReference)
  );
}

export async function personaBundle(profileId: string, includeReference: boolean): Promise<Blob> {
  const response = await apiFetch(exportPath(profileId, includeReference), { method: 'POST' });
  return response.blob();
}
export function PersonaExport({
  profile,
  disabled = false,
}: {
  profile: { id: string; name: string };
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const lock = useRef(false);
  const [includeReference, setIncludeReference] = useState(true);
  const [busy, setBusy] = useState(false);
  const [issue, setIssue] = useState('');
  const exportVoice = async () => {
    if (lock.current || disabled) return;
    lock.current = true;
    setBusy(true);
    setIssue('');
    try {
      const suggestedName =
        (profile.name
          .replace(/[<>:"/\\|?*\p{Cc}]/gu, '_')
          .trim()
          .slice(0, 100) || 'persona') + '.ovsvoice';
      const bridge = getBridge();
      if (bridge) {
        const saved = await saveBackendFile({
          url: apiPath(exportPath(profile.id, includeReference)),
          suggestedName,
          method: 'POST',
        });
        if (saved?.canceled) return;
        toast.success(t('voice_profile.persona_exported'));
        return;
      }
      const blob = await personaBundle(profile.id, includeReference);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = suggestedName;
      document.body.appendChild(link);
      link.click();
      link.remove();
      // Let Chromium's download consume the blob before releasing it.
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      toast.success(t('voice_profile.persona_exported'));
    } catch (error) {
      setIssue(
        error instanceof ApiError && error.status === 503
          ? 'voice_profile.persona_export_no_audio'
          : 'voice_profile.persona_export_failed',
      );
    } finally {
      lock.current = false;
      setBusy(false);
    }
  };
  return (
    <details className="space-y-3 border-t border-border/50 pt-3">
      <summary className="cursor-pointer text-sm font-medium">
        {t('voice_profile.persona_export')}
      </summary>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={includeReference}
          disabled={busy || disabled}
          onChange={(event) => setIncludeReference(event.target.checked)}
          className="accent-primary"
        />
        {t('voice_profile.persona_include_ref')}
      </label>
      <p className="text-xs leading-5 text-muted-foreground">
        {t('voice_profile.persona_include_ref_hint')}
      </p>
      {issue && (
        <p role="alert" className="text-xs text-destructive">
          {t(issue)}
        </p>
      )}
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={busy || disabled}
        onClick={() => void exportVoice()}
      >
        <DownloadIcon />
        {t(busy ? 'voice_profile.persona_exporting' : 'voice_profile.persona_export')}
      </Button>
    </details>
  );
}
