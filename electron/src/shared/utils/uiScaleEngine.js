const loadTauriWebview = () => import('@tauri-apps/api/webview');
let scaleApplicationQueue = Promise.resolve();

/**
 * @typedef {() => Promise<{ getCurrentWebview: () => { setZoom: (scale: number) => Promise<void> } }>} WebviewLoader
 */

/**
 * Native Tauri zoom scales the painted webview without shrinking the DOM's
 * reported container width. Responsive breakpoints need the width in visible
 * CSS pixels, while the browser fallback has already shrunk its layout box via
 * CSS and must not be divided a second time.
 */
export function responsiveShellWidth(shellWidth, scale, engine) {
  const safeScale = Number.isFinite(scale) && scale > 0 ? scale : 1;
  return engine === 'native' ? shellWidth / safeScale : shellWidth;
}

/**
 * @param {number} scale
 * @param {WebviewLoader} loadWebview
 */
async function applyUiScaleNow(scale, loadWebview) {
  const root = document.documentElement;
  if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) {
    root.dataset.uiScaleEngine = 'css';
    return 'css';
  }

  try {
    const { getCurrentWebview } = await loadWebview();
    await getCurrentWebview().setZoom(scale);
    // Switch off CSS zoom only after native zoom succeeds, avoiding an
    // unscaled flash during startup or a transient IPC failure.
    root.dataset.uiScaleEngine = 'native';
    return 'native';
  } catch {
    root.dataset.uiScaleEngine = 'css';
    return 'css';
  }
}

/**
 * Apply the user's UI scale at the webview boundary when Tauri is available.
 * Native zoom keeps the CSS viewport equal to the visible window on every
 * platform; CSS zoom remains the browser/dev fallback.
 *
 * @param {number} scale
 * @param {WebviewLoader} [loadWebview]
 */
export function applyUiScale(scale, loadWebview = loadTauriWebview) {
  // Webview.setZoom is asynchronous. Keep applications ordered so a slower
  // older request cannot finish after a newer one and restore stale native
  // zoom while React holds the newer engine/scale state.
  const application = scaleApplicationQueue.then(() => applyUiScaleNow(scale, loadWebview));
  scaleApplicationQueue = application.then(
    () => undefined,
    () => undefined,
  );
  return application;
}
