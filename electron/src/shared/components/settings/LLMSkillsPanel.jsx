/**
 * Settings → System → LLM Skills (feat/llm-skills).
 *
 * One row per LLM-powered capability ("skill"): toggle it on/off and route it
 * to a specific provider — keep sensitive work on a local model (Ollama/
 * LM Studio) while heavier jobs use a remote key — instead of everything
 * riding the one global active provider. A disabled skill degrades exactly
 * like "no LLM configured" (Fast translation fallback, refinement
 * pass-through, heuristic direction parse, …).
 *
 * Endpoints (loopback-only):
 *   GET /api/settings/llm-skills
 *     → {skills:[{id,name_key,description_key,enabled,provider_override,
 *         provider,provider_display_name,provider_local,provider_source,
 *         ready,reason}]}
 *   PUT /api/settings/llm-skills/{id}  {enabled?, provider_override?}
 *     ('' clears the override → skill follows the active provider)
 *   GET /api/settings/llm-providers    (provider options for the Select)
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Sparkles } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { apiJson } from '../../api/client';
import { useAppStore } from '../../store';
import { SettingsSection, SettingRow, SettingsToggle } from './primitives';
import { Badge, Button, Select } from '../../ui';

export default function LLMSkillsPanel() {
  const { t } = useTranslation();
  const openSettingsTab = useAppStore((s) => s.openSettingsTab);
  const [skills, setSkills] = useState([]);
  const [providers, setProviders] = useState([]);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [skillData, providerData] = await Promise.all([
          apiJson('/api/settings/llm-skills'),
          apiJson('/api/settings/llm-providers'),
        ]);
        if (cancelled) return;
        setSkills(skillData.skills || []);
        setProviders(providerData.providers || []);
      } catch (e) {
        if (!cancelled) setError(e?.message || t('settings.llmskills_load_failed'));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [t]);

  const update = useCallback(
    async (id, patch) => {
      setBusy(id);
      setError(null);
      try {
        const data = await apiJson(`/api/settings/llm-skills/${id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(patch),
        });
        setSkills(data.skills || []);
      } catch (e) {
        setError(e?.message || t('settings.llmskills_save_failed'));
      } finally {
        setBusy('');
      }
    },
    [t],
  );

  // Routing options: configured providers only (an unconfigured one can't
  // serve a skill) — plus the skill's current override if it fell out of that
  // set, so the Select never silently misrepresents the stored routing.
  const optionsFor = (skill) => {
    const configured = providers.filter((p) => p.configured);
    if (skill.provider_override && !configured.some((p) => p.id === skill.provider_override)) {
      const current = providers.find((p) => p.id === skill.provider_override);
      if (current) configured.push(current);
    }
    return configured;
  };

  const badgeFor = (skill) => {
    if (!skill.enabled) return null; // the toggle already reads as "off"
    if (skill.ready) {
      return (
        <Badge tone="success" dot role="status" data-testid={`llm-skill-ready-${skill.id}`}>
          {t('settings.llmskills_ready')}
          {skill.provider_display_name ? ` · ${skill.provider_display_name}` : ''}
        </Badge>
      );
    }
    return (
      <span className="inline-flex items-center gap-[6px]">
        <Badge tone="warn" role="status" data-testid={`llm-skill-needs-setup-${skill.id}`}>
          {t('settings.llmskills_needs_setup')}
        </Badge>
        <Button
          variant="subtle"
          size="sm"
          onClick={() => openSettingsTab('llm-providers')}
          data-testid={`llm-skill-setup-${skill.id}`}
        >
          {t('settings.llmskills_open_providers')}
        </Button>
      </span>
    );
  };

  return (
    <SettingsSection
      icon={Sparkles}
      title={t('settings.llm_skills')}
      description={t('settings.llmskills_desc')}
    >
      {error && (
        <div className="perfpanel__error" role="alert">
          {error}
        </div>
      )}
      {skills.map((skill) => (
        <SettingRow
          key={skill.id}
          title={t(skill.name_key)}
          subtitle={t(skill.description_key)}
          align="start"
          control={
            <div className="flex flex-wrap items-center justify-end gap-[8px] min-w-0">
              {badgeFor(skill)}
              <Select
                value={skill.provider_override || ''}
                onChange={(e) => update(skill.id, { provider_override: e.target.value })}
                disabled={!skill.enabled || busy === skill.id}
                aria-label={t('settings.llmskills_route_for', {
                  defaultValue: 'Provider for {{skill}}',
                  skill: t(skill.name_key),
                })}
                data-testid={`llm-skill-provider-${skill.id}`}
              >
                <option value="">{t('settings.llmskills_use_active')}</option>
                {optionsFor(skill).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.display_name}
                    {p.local ? ` · ${t('settings.llmp_local_tag')}` : ''}
                  </option>
                ))}
              </Select>
              <SettingsToggle
                checked={skill.enabled}
                onChange={(v) => update(skill.id, { enabled: v })}
                disabled={busy === skill.id}
                aria-label={t(skill.name_key)}
                data-testid={`llm-skill-toggle-${skill.id}`}
              />
            </div>
          }
        />
      ))}
    </SettingsSection>
  );
}
