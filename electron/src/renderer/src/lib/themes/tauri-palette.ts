import { GROVE_THEME, type ThemeColors, type ThemeDefinition } from './t3-palettes';

// VoiceStudio Classic: a warm near-black canvas with a low dark-purple tint,
// warm off-white text and muted lavender actions.
// The light variant is a complementary adaptation; the original default is dark.
function colors(light: boolean): ThemeColors {
  const canvas = light ? '#f8f6f4' : '#1b181d';
  const chrome = light ? '#efecea' : '#151316';
  const surface = light ? '#fffdfb' : '#252128';
  const text = light ? '#2e2a30' : '#ece8e4';
  const muted = light ? '#6f6870' : '#a69ea4';
  const action = light ? '#8f3f71' : '#b8a2d6';
  const border = light ? 'rgb(60 45 70 / 12%)' : 'rgb(236 225 240 / 9%)';
  const hover = light ? '#ebe6e8' : '#322d35';
  return {
    ...(light ? GROVE_THEME.colors : GROVE_THEME.variants!.dark!),
    canvas,
    chrome,
    toolbar: chrome,
    toolbarForeground: text,
    toolbarBorder: border,
    toolbarControl: surface,
    toolbarControlForeground: text,
    toolbarControlHover: hover,
    surface,
    surfaceRaised: light ? '#ffffff' : '#2b272e',
    surfaceOverlay: light ? '#fffdfb' : '#2b272e',
    text,
    textMuted: muted,
    border,
    input: border,
    focus: action,
    accent: action,
    accentForeground: light ? '#ffffff' : '#1d2021',
    secondary: hover,
    secondaryForeground: text,
    muted: hover,
    mutedForeground: muted,
    placeholder: muted,
    secondaryLabel: muted,
    iconMuted: muted,
    error: light ? '#9d0006' : '#fb4934',
    errorForeground: light ? '#9d0006' : '#fb4934',
    errorSurface: surface,
    warning: light ? '#af3a03' : '#fabd2f',
    warningForeground: light ? '#af3a03' : '#fabd2f',
    warningSurface: surface,
    update: action,
    updateForeground: action,
    updateSurface: hover,
    accentSurface: hover,
    accentSurfaceForeground: text,
    messageSurface: surface,
    messageForeground: text,
    messageAction: action,
    messageActionForeground: light ? '#ffffff' : '#1d2021',
    messageActionHover: light ? '#7b305e' : '#a58ac6',
    codeBackground: chrome,
    codeForeground: text,
    sidebar: chrome,
    sidebarForeground: text,
    sidebarMutedForeground: muted,
    sidebarControlSurface: surface,
    sidebarRowHover: hover,
    sidebarRowActive: light ? '#e3dbe6' : '#3a3043',
    sidebarRowSelected: surface,
    sidebarBorder: border,
    terminalBackground: canvas,
    terminalForeground: text,
    terminalCursor: action,
    terminalSelection: hover,
    terminalScrollbar: border,
    terminalScrollbarHover: muted,
  };
}
export const TAURI_THEME: ThemeDefinition = {
  id: 'heritage',
  label: 'VoiceStudio Classic',
  appearance: 'dark',
  colors: colors(false),
  variants: { light: colors(true) },
  sidebarArtwork: true,
};
