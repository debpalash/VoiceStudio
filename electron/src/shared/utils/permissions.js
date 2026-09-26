/**
 * permissions — browser-safe wrappers around the Tauri shell's OS-permission
 * probes (microphone TCC / ConsentStore, macOS Accessibility) and the
 * deep-links into the matching OS settings panes.
 *
 * Every function degrades gracefully outside the Tauri shell (web UI /
 * Docker / vite dev): probes return the honest "we can't know" value
 * ('unknown' for the mic, `true` for Accessibility — there is nothing to
 * grant in a browser), and the open-settings deep-links resolve `false`
 * without throwing. A failure INSIDE Tauri (old shell without the command,
 * IPC hiccup) also degrades to the same values — the probes are advisory
 * and must never block dictation/recording on their own.
 *
 * Pure JS module (no React) so it is unit-testable; the React glue
 * (state + recheck-on-focus) lives in hooks/usePermissions.js.
 */
import { detectPlatform } from './micError';

export { detectPlatform };

/** True inside the Tauri shell; false in the browser web UI / Docker. */
export function inTauri() {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
}

// One shared import of the Tauri core module. Probes run concurrently
// (Promise.all in usePermissions), and racing two dynamic imports of the
// same specifier is both wasteful and — under vitest's module mocker —
// nondeterministic; a single cached promise removes the race entirely.
let corePromise = null;
function tauriCore() {
  if (!corePromise) {
    corePromise = import('@tauri-apps/api/core').catch((err) => {
      // A transient import failure must not be cached forever — clear the
      // memo so the next probe/deep-link retries instead of re-rejecting.
      corePromise = null;
      throw err;
    });
  }
  return corePromise;
}

/** The four honest microphone grant states the shell can report. */
const MIC_STATES = new Set(['granted', 'denied', 'prompt', 'unknown']);

/**
 * Microphone permission: 'granted' | 'denied' | 'prompt' | 'unknown'.
 * macOS reads the TCC grant; Windows the per-user ConsentStore toggle;
 * Linux (no per-app mic permission) and non-Tauri are always 'unknown'.
 * Callers must treat 'unknown' as neither granted nor denied.
 */
export async function checkMicrophone() {
  if (!inTauri()) return 'unknown';
  try {
    const { invoke } = await tauriCore();
    const state = await invoke('check_microphone');
    return MIC_STATES.has(state) ? state : 'unknown';
  } catch (err) {
    // Older shell without the command — don't guess, don't block.
    console.warn('check_microphone failed:', err);
    return 'unknown';
  }
}

/**
 * macOS Accessibility grant (needed so dictation can paste/type into other
 * apps). Resolves `true` on Windows/Linux and outside Tauri — nothing to
 * grant there.
 */
export async function checkAccessibility() {
  if (!inTauri()) return true;
  try {
    const { invoke } = await tauriCore();
    return (await invoke('check_accessibility')) !== false;
  } catch (err) {
    console.warn('check_accessibility failed:', err);
    return true;
  }
}

/**
 * Invoke an open-*-settings command. Resolves `true` when the pane was
 * opened, `false` when it wasn't (outside Tauri, or the shell rejected —
 * e.g. Linux has no mic-privacy pane and errors with a "settings:" kind).
 * Callers use the `false` to show a "use your system settings" hint.
 */
async function openSettingsPane(command) {
  if (!inTauri()) return false;
  try {
    const { invoke } = await tauriCore();
    await invoke(command);
    return true;
  } catch (err) {
    console.warn(`${command} failed:`, err);
    return false;
  }
}

/** Deep-link the OS microphone-privacy pane (macOS/Windows; false on Linux). */
export function openMicrophoneSettings() {
  return openSettingsPane('open_microphone_settings');
}

/** Deep-link macOS Privacy → Accessibility (no-op elsewhere). */
export function openAccessibilitySettings() {
  return openSettingsPane('open_accessibility_settings');
}

/**
 * Deep-link macOS Privacy → Input Monitoring. Exposed for completeness —
 * the global dictation shortcut uses tauri-plugin-global-shortcut (Carbon
 * hotkey registration), which does NOT need this grant, so no default UI
 * surfaces it.
 */
export function openInputMonitoringSettings() {
  return openSettingsPane('open_input_monitoring_settings');
}
