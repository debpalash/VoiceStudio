import { BotIcon, LoaderCircleIcon, Trash2Icon } from 'lucide-react';
import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ExternalLink } from '@/components/external-link';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { ConfirmDialog } from '@/features/clone/confirm-dialog';
import { useProfiles } from '@/hooks/use-profiles';
import { apiFetch, apiJson, describeError } from '@/lib/api/client';
import { isImeComposing } from '@/lib/ime';
import { SettingsActionError } from './settings-action-error';
import { SettingsRow, SettingsSection } from './settings-layout';

interface McpBinding {
  client_id: string;
  label: string | null;
  profile_id: string | null;
  default_engine: string | null;
}

const DEFAULT_PROFILE = '__default__';
const MCP_DOCS_URL = 'https://github.com/debpalash/VoiceStudio/blob/main/docs/mcp.md';

export function McpBindingsSettings() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const profiles = useProfiles();
  const bindings = useQuery({
    queryKey: ['mcp-bindings'],
    queryFn: ({ signal }) => apiJson<McpBinding[]>('/api/mcp/bindings', { signal }),
  });
  const [clientId, setClientId] = useState('');
  const [label, setLabel] = useState('');
  const [profileId, setProfileId] = useState(DEFAULT_PROFILE);
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [error, setError] = useState('');
  const availableProfiles = Array.isArray(profiles.data) ? profiles.data : [];

  const profileName = (id: string | null) =>
    availableProfiles.find((profile) => profile.id === id)?.name ||
    id ||
    t('settings.mcp_default_voice');

  const save = async () => {
    const normalizedId = clientId.trim();
    if (!normalizedId || saving) return;
    setSaving(true);
    setError('');
    try {
      await apiJson<McpBinding>('/api/mcp/bindings', {
        method: 'PUT',
        body: JSON.stringify({
          client_id: normalizedId,
          label: label.trim() || null,
          profile_id: profileId === DEFAULT_PROFILE ? null : profileId,
        }),
      });
      setClientId('');
      setLabel('');
      setProfileId(DEFAULT_PROFILE);
      await queryClient.invalidateQueries({ queryKey: ['mcp-bindings'] });
    } catch (reason) {
      setError(describeError(reason) || t('settings.mcp_save_failed'));
    } finally {
      setSaving(false);
    }
  };

  const remove = async (id: string) => {
    if (deletingId) return;
    setDeletingId(id);
    setError('');
    try {
      await apiFetch(`/api/mcp/bindings/${encodeURIComponent(id)}`, { method: 'DELETE' });
    } catch (reason) {
      setError(describeError(reason) || t('settings.mcp_delete_failed'));
    } finally {
      await queryClient.invalidateQueries({ queryKey: ['mcp-bindings'] });
      setDeletingId(null);
    }
  };

  const profileItems = [
    { value: DEFAULT_PROFILE, label: t('settings.mcp_default_voice') },
    ...availableProfiles.map((profile) => ({ value: profile.id, label: profile.name })),
  ];

  return (
    <>
      <SettingsSection icon={BotIcon} title={t('settings.mcp_title')}>
        <SettingsRow
          id="sharing-mcp-overview"
          title={t('settings.mcp_desc')}
          description={t('settings.mcp_hint')}
        >
          <ExternalLink href={MCP_DOCS_URL}>{t('common.learn_more')}</ExternalLink>
        </SettingsRow>

        {(bindings.isError || error) && (
          <div className="p-4">
            <SettingsActionError
              title={bindings.isError ? t('settings.mcp_load_failed') : error}
              detail={bindings.isError ? describeError(bindings.error) : undefined}
              onDismiss={error ? () => setError('') : undefined}
            />
          </div>
        )}

        {bindings.isPending ? (
          <div className="flex min-h-16 items-center justify-center p-4 text-muted-foreground">
            <LoaderCircleIcon aria-label={t('common.loading')} className="size-4 animate-spin" />
          </div>
        ) : bindings.data?.length ? (
          bindings.data.map((binding) => (
            <SettingsRow
              key={binding.client_id}
              id={`mcp-binding-${binding.client_id}`}
              title={binding.label || binding.client_id}
              description={binding.label ? binding.client_id : undefined}
            >
              <Badge variant="outline">{profileName(binding.profile_id)}</Badge>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                disabled={deletingId === binding.client_id}
                aria-label={t('settings.mcp_remove', { clientId: binding.client_id })}
                onClick={() => setConfirmDeleteId(binding.client_id)}
              >
                {deletingId === binding.client_id ? (
                  <LoaderCircleIcon className="animate-spin" />
                ) : (
                  <Trash2Icon />
                )}
              </Button>
            </SettingsRow>
          ))
        ) : (
          <p className="px-4 py-3 text-sm text-muted-foreground">{t('settings.mcp_empty')}</p>
        )}

        <SettingsRow id="sharing-mcp-add" title={t('settings.mcp_add_title')}>
          <div className="grid w-full min-w-0 gap-2 @xl:grid-cols-2 @3xl:grid-cols-[minmax(10rem,1fr)_minmax(10rem,1fr)_minmax(11rem,1fr)_auto]">
            <Input
              value={clientId}
              maxLength={128}
              placeholder={t('settings.mcp_client_id_placeholder')}
              aria-label={t('settings.mcp_client_id')}
              disabled={saving}
              onChange={(event) => setClientId(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !isImeComposing(event)) void save();
              }}
            />
            <Input
              value={label}
              placeholder={t('settings.mcp_label_placeholder')}
              aria-label={t('settings.mcp_label')}
              disabled={saving}
              onChange={(event) => setLabel(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !isImeComposing(event)) void save();
              }}
            />
            <Select
              items={profileItems}
              value={profileId}
              disabled={saving || profiles.isPending}
              onValueChange={(value) => {
                if (typeof value === 'string') setProfileId(value);
              }}
            >
              <SelectTrigger className="w-full" aria-label={t('settings.mcp_voice_profile')}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {profileItems.map((item) => (
                  <SelectItem key={item.value} value={item.value}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button disabled={!clientId.trim() || saving} onClick={() => void save()}>
              {saving && <LoaderCircleIcon className="animate-spin" />}
              {t('settings.mcp_add')}
            </Button>
          </div>
        </SettingsRow>
      </SettingsSection>

      <ConfirmDialog
        open={confirmDeleteId !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmDeleteId(null);
        }}
        title={t('settings.mcp_delete_confirm_title')}
        description={t('settings.mcp_delete_confirm', { clientId: confirmDeleteId ?? '' })}
        confirmLabel={t('settings.mcp_remove', { clientId: confirmDeleteId ?? '' })}
        onConfirm={async () => {
          const id = confirmDeleteId;
          if (id) await remove(id);
        }}
      />
    </>
  );
}
