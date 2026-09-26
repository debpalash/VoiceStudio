/**
 * Settings → Sharing → MCP voice bindings panel (parity program Wave 2.2).
 *
 * Bind an MCP client id (the X-OmniVoice-Client-Id an agent sends) to a voice
 * profile, so "Claude Code speaks in Morgan, Cursor in Scarlett". The MCP
 * server is mounted at /mcp on the backend; see docs/mcp.md.
 *
 * Endpoints (loopback-only):
 *   GET    /api/mcp/bindings
 *   PUT    /api/mcp/bindings   {client_id, label?, profile_id?, default_engine?}
 *   DELETE /api/mcp/bindings/{client_id}
 *
 * The API's `default_engine` field is intentionally NOT editable here — it is
 * an MCP-side capability (agents can request an engine per docs/mcp.md); the
 * panel only manages the voice routing a user actually reasons about.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Bot, Trash2 } from 'lucide-react';
import { apiJson, apiFetch } from '../../api/client';
import { listProfiles } from '../../api/profiles';
import { askConfirm } from './native';
import { SettingsSection, SettingRow, SettingsInput, InfoHint } from './primitives';
import { Button, Badge, Select } from '../../ui';

const MCP_DOCS_URL = 'https://github.com/debpalash/VoiceStudio/blob/main/docs/mcp.md';

export default function MCPBindingsPanel() {
  const { t } = useTranslation();
  const [bindings, setBindings] = useState([]);
  const [profiles, setProfiles] = useState([]);
  const [clientId, setClientId] = useState('');
  const [label, setLabel] = useState('');
  const [profileId, setProfileId] = useState('');
  const [adding, setAdding] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const [b, p] = await Promise.all([apiJson('/api/mcp/bindings'), listProfiles()]);
      setBindings(b);
      setProfiles(p);
    } catch (e) {
      setError(
        e?.message ||
          t('settings.mcp_load_failed', { defaultValue: 'Failed to load MCP bindings' }),
      );
    }
  }, [t]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const profileName = (id) =>
    profiles.find((p) => p.id === id)?.name ||
    id ||
    t('settings.mcp_default_voice', { defaultValue: 'Default voice' });

  const onAdd = async () => {
    if (!clientId.trim() || adding) return;
    setAdding(true);
    setError(null);
    try {
      await apiFetch('/api/mcp/bindings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          client_id: clientId.trim(),
          label: label.trim() || null,
          profile_id: profileId || null,
        }),
      });
      setClientId('');
      setLabel('');
      setProfileId('');
      await refresh();
    } catch (e) {
      setError(
        e?.message || t('settings.mcp_save_failed', { defaultValue: 'Failed to save binding' }),
      );
    } finally {
      setAdding(false);
    }
  };

  const onDelete = async (cid) => {
    if (deletingId) return;
    const confirmed = await askConfirm(
      t('settings.mcp_delete_confirm', {
        defaultValue: 'Remove the voice binding for “{{clientId}}”?',
        clientId: cid,
      }),
      t('settings.mcp_delete_confirm_title', { defaultValue: 'Remove binding' }),
    );
    if (!confirmed) return;
    setDeletingId(cid);
    setError(null);
    let failure = null;
    try {
      await apiFetch(`/api/mcp/bindings/${encodeURIComponent(cid)}`, { method: 'DELETE' });
    } catch (e) {
      failure =
        e?.message || t('settings.mcp_delete_failed', { defaultValue: 'Failed to delete binding' });
    }
    // Re-sync even on failure: a 404 means the row was already gone — the list
    // must not keep showing it. refresh() clears error state, so re-apply the
    // delete failure afterwards.
    await refresh();
    if (failure) setError(failure);
    setDeletingId(null);
  };

  return (
    <SettingsSection
      icon={Bot}
      title={t('settings.mcp_title', { defaultValue: 'MCP voice bindings' })}
      description={t('settings.mcp_desc', {
        defaultValue:
          'Give each MCP agent its own voice — bind the client id an agent sends to a voice profile.',
      })}
      actions={
        <InfoHint learnMoreHref={MCP_DOCS_URL}>
          {t('settings.mcp_hint', {
            defaultValue:
              'Agents reach VoiceStudio at /mcp and identify themselves with a client id (e.g. claude-code). Bind that id to a voice so the agent always speaks in that profile.',
          })}
        </InfoHint>
      }
    >
      {error && (
        <div className="perfpanel__error" role="alert">
          {error}
        </div>
      )}

      {bindings.length === 0 && !error && (
        <p
          className="m-0 py-[var(--space-3)] text-[length:var(--text-xs)] text-[color:var(--chrome-fg-dim)] leading-[1.5]"
          data-testid="mcp-empty"
        >
          {t('settings.mcp_empty', {
            defaultValue: "No bindings yet — add an agent's client id below.",
          })}
        </p>
      )}

      {bindings.map((b) => (
        <SettingRow
          key={b.client_id}
          title={b.label || b.client_id}
          subtitle={b.label ? b.client_id : undefined}
          control={
            <>
              <Badge tone="neutral">{profileName(b.profile_id)}</Badge>
              <Button
                variant="danger"
                size="sm"
                onClick={() => onDelete(b.client_id)}
                disabled={deletingId === b.client_id}
                aria-label={t('settings.mcp_remove', {
                  defaultValue: 'Remove {{clientId}}',
                  clientId: b.client_id,
                })}
                data-testid={`mcp-del-${b.client_id}`}
              >
                <Trash2 size={12} />
              </Button>
            </>
          }
        />
      ))}

      <SettingRow
        title={t('settings.mcp_add_title', { defaultValue: 'Add binding' })}
        stack
        control={
          <>
            <SettingsInput
              type="text"
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              placeholder={t('settings.mcp_client_id_placeholder', {
                defaultValue: 'Client ID (e.g. claude-code)',
              })}
              aria-label={t('settings.mcp_client_id', { defaultValue: 'Client ID' })}
              data-testid="mcp-client-id"
            />
            <SettingsInput
              type="text"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder={t('settings.mcp_label_placeholder', {
                defaultValue: 'Label (optional)',
              })}
              aria-label={t('settings.mcp_label', { defaultValue: 'Label' })}
              data-testid="mcp-label"
            />
            <Select
              size="sm"
              value={profileId}
              onChange={(e) => setProfileId(e.target.value)}
              aria-label={t('settings.mcp_voice_profile', { defaultValue: 'Voice profile' })}
              data-testid="mcp-profile"
            >
              <option value="">
                {t('settings.mcp_default_voice', { defaultValue: 'Default voice' })}
              </option>
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </Select>
            <Button
              variant="subtle"
              size="sm"
              onClick={onAdd}
              disabled={!clientId.trim() || adding}
              data-testid="mcp-add"
            >
              {t('settings.mcp_add', { defaultValue: 'Add binding' })}
            </Button>
          </>
        }
      />
    </SettingsSection>
  );
}
