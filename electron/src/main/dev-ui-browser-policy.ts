/**
 * Dev-only: open the Vite renderer URL in the system browser.
 * Desktop Electron is the default; set VOICESTUDIO_OPEN_DEV_UI_IN_BROWSER=1
 * when you also want a browser tab for dual debugging.
 */
export function shouldOpenDevUiInBrowser(
  environment: Record<string, string | undefined> = process.env,
): boolean {
  return environment.VOICESTUDIO_OPEN_DEV_UI_IN_BROWSER === '1';
}
