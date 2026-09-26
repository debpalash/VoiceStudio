/**
 * Updater release channels. Kept tiny + dependency-free so both the updater
 * utility and the Settings UI share one source of truth for valid values.
 *
 * - stable  → the tagged `releases/latest` manifest (default).
 * - preview → the rolling `preview` prerelease manifest (latest `main`).
 *
 * The actual endpoint selection lives in Rust (`updater_channel.rs`); JS only
 * needs to pass a validated channel string to the `check_update` /
 * `install_update` commands.
 */
export const UPDATE_CHANNELS = ['stable', 'preview'];

/** Clamp any value to a known channel; anything unexpected → 'stable'. */
export function normalizeChannel(ch) {
  return UPDATE_CHANNELS.includes(ch) ? ch : 'stable';
}
