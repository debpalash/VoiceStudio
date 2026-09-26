export const UI_SCALE_OPTIONS = [0.8, 0.9, 1, 1.1, 1.2, 1.3];

const MIN_SUGGESTED_SCALE = UI_SCALE_OPTIONS[0];
const MAX_SUGGESTED_SCALE = UI_SCALE_OPTIONS[UI_SCALE_OPTIONS.length - 1];

/** Suggest a comfortable scale from the webview's logical CSS-pixel size. */
export function suggestUiScale({ width, height }) {
  const viewportWidth = Number(width) || 1440;
  const viewportHeight = Number(height) || 900;
  const targetFit = Math.min(viewportWidth / 1440, viewportHeight / 900);
  const clamped = Math.min(MAX_SUGGESTED_SCALE, Math.max(MIN_SUGGESTED_SCALE, targetFit));
  return UI_SCALE_OPTIONS.reduce((best, option) =>
    Math.abs(option - clamped) < Math.abs(best - clamped) ? option : best,
  );
}

/** Keep the screen-aware default until the user explicitly previews a choice. */
export function resolveUiScale({ configured, previewed, selected, suggested }) {
  return !configured && !previewed ? suggested : selected;
}
