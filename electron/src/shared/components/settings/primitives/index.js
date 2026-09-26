// ─────────────────────────────────────────────────────────────────
//  Settings design-system primitives
//
//  Shared building blocks for the Settings redesign. Compose these
//  inside every Settings panel/tab instead of re-implementing headers,
//  rows, toggles, hints, and disclosures.
//
//    import { SettingsSection, SettingRow, InfoHint,
//             SettingsToggle, Collapsible } from './primitives';
//
//  All styling now lives as Tailwind utilities on the VoiceStudio `--chrome-*` /
//  `--space-*` token bridge directly on each primitive's JSX (FAST-mode shadcn
//  migration) — there is no longer a primitives.css stylesheet to import.
// ─────────────────────────────────────────────────────────────────

export { default as SettingsSection, SETTINGS_SECTION_SURFACE } from './SettingsSection.jsx';
export { default as SettingRow } from './SettingRow.jsx';
export { default as SettingsInput } from './SettingsInput.jsx';
export { default as InfoHint } from './InfoHint.jsx';
export { default as SettingsToggle } from './SettingsToggle.jsx';
export { default as Collapsible } from './Collapsible.jsx';
