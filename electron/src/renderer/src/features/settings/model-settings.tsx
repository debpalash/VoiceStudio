import { ChevronRightIcon, CpuIcon } from 'lucide-react';
import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { buttonVariants } from '@/components/ui/button';
import { ShortcutSettings } from './shortcut-settings';
import { RefinementSettings } from './refinement-settings';
import { DictationCleanupSettings } from './dictation-cleanup-settings';
import { LlmSkills } from './llm-skills';
import { LlmProviders } from './llm-providers';
import { TranslationSettings } from './translation-settings';
import { PerformanceModelPacks } from './model-library';
import { familyIcons, modelFamilies, type ModelFamily } from './model-family';
import { SettingsSection } from './settings-layout';
import { VoicePreviewsSettings } from './voice-previews-settings';
import { AsrOpenAiCompatSettings } from './asr-openai-compat-settings';
import { AecSettings } from './aec-settings';
import { DiarisationSettings } from './model-list/diarisation-list';
import { DictationList } from './model-list/dictation-list';
import { EngineFamilyList } from './model-list/engine-family-list';

export { DiarisationSettings };

function FamilyIndex() {
  const { t } = useTranslation();
  return (
    <>
      <PerformanceModelPacks />
      <VoicePreviewsSettings />
      <SettingsSection icon={CpuIcon} title={t('modelSettings.models')} contentVariant="cards">
        {modelFamilies.map((item) => {
          const Icon = familyIcons[item];
          return (
            <Link
              key={item}
              to="/settings/models/$family"
              params={{ family: item }}
              className={buttonVariants({
                variant: 'ghost',
                className:
                  'glass-panel min-h-20 min-w-0 justify-between rounded-xl border border-border/60 bg-card/40 px-4 py-4 shadow-xs/5 transition-[border-color,background-color,box-shadow] hover:border-primary/25 hover:bg-primary/[0.04]',
              })}
            >
              <span className="flex items-center gap-2.5">
                <Icon className="size-4 text-muted-foreground transition-colors duration-150 group-hover:text-foreground" />
                {t('engineSidebar.' + item)}
              </span>
              <ChevronRightIcon className="text-muted-foreground transition-colors duration-150 group-hover:text-foreground" />
            </Link>
          );
        })}
      </SettingsSection>
    </>
  );
}

/**
 * Settings -> Models. Each family page is ONE list: every engine (and any
 * model no engine claims) with its install and select actions on the row.
 */
export function ModelSettings({ family }: { family?: ModelFamily }) {
  switch (family) {
    case undefined:
      return <FamilyIndex />;
    case 'llm':
      return (
        <>
          <LlmProviders />
          <LlmSkills />
        </>
      );
    case 'translation':
      return <TranslationSettings />;
    case 'diarisation':
      return <DiarisationSettings />;
    case 'dictation':
      return (
        <>
          <DictationList />
          <ShortcutSettings />
          <DictationCleanupSettings />
          <RefinementSettings />
          <AecSettings />
        </>
      );
    case 'asr':
      return (
        <>
          <EngineFamilyList family="asr" />
          <AsrOpenAiCompatSettings />
        </>
      );
    case 'tts':
      return <EngineFamilyList family="tts" />;
  }
}
