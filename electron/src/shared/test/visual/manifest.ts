// Single source of truth for what the visual-regression suite captures.
//
// COMPONENTS must stay in sync with the keys in specs.jsx (the harness
// renders an error state for any name not found there, which would itself
// fail the snapshot — so drift is caught, not silent).
//
// THEMES: 'default' is the bare-:root Gruvbox Dark; every other name maps to
// a [data-theme="…"] block in ui/themes.css.
export const COMPONENTS = [
  'Badge',
  'Segmented',
  'Progress',
  'Button',
  'Panel',
  'SettingRow',
  'SettingsToggle',
  'Slider',
  'Table',
  'Tabs',
  'Input',
  // shadcn/ui proof components (themed via the VoiceStudio token bridge).
  'ShadcnButton',
  'ShadcnInput',
  // Provider-wrapped PANEL specs — rendered with a seeded store / i18n /
  // react-query / fetch (see specs.jsx `providers`). Same snapshot loop.
  'AppearancePanel',
  'TitleTabs',
  'GeneralTab',
  'StoragePanel',
  'ResetPanel',
  'UninstallPanel',
] as const;

export const THEMES = ['default', 'midnight', 'catppuccin'] as const;
