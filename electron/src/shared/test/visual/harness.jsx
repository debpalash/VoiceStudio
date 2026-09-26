// ─────────────────────────────────────────────────────────────────
//  Visual-regression harness entry.
//
//  Renders ONE presentational component (chosen via the `component` URL
//  param) wrapped in the requested theme (`theme` URL param) so Playwright
//  can snapshot it in isolation — no Python backend, no app shell, no
//  network. This is the gating safety net for the CSS → Tailwind v4
//  migration: see ./README.md for how to add a component or update a
//  baseline.
//
//  URL: /src/test/visual/harness.html?component=Badge&theme=midnight
// ─────────────────────────────────────────────────────────────────

// React-Refresh preamble guard — mirrors src/main.jsx. @vitejs/plugin-react
// injects this for HTML it transforms, but we install it defensively so the
// harness never trips the "can't detect preamble" error on any Vite version.
if (import.meta.env.DEV && !window.__vite_plugin_react_preamble_installed__) {
  const RefreshRuntime = await import('/@react-refresh');
  RefreshRuntime.default.injectIntoGlobalHook(window);
  window.$RefreshReg$ = () => {};
  window.$RefreshSig$ = () => (type) => type;
  window.__vite_plugin_react_preamble_installed__ = true;
}

import { createRoot } from 'react-dom/client';

// Load the exact same fonts + token layers the real app loads (main-app.jsx),
// in the same order, so snapshots match production rendering.
import '@fontsource-variable/inter';
import '@fontsource/ibm-plex-mono/400.css';
import '@fontsource/ibm-plex-mono/500.css';
import '@fontsource/ibm-plex-mono/600.css';
import '@fontsource-variable/source-serif-4';
// index.css is now the single stylesheet: Tailwind foundation + token scale +
// every [data-theme] block + all former residual/component CSS consolidated in.
// This one import pulls in the full cascade — same order as production
// (main-app.jsx) — so harness snapshots match prod.
import '../../index.css';

import { SPECS } from './specs.jsx';
import { applyProviders } from './providers.jsx';

const params = new URLSearchParams(window.location.search);
const componentName = params.get('component') || '';
const theme = params.get('theme') || 'default';
const spec = SPECS[componentName];

// Pure leaf specs render bare (unchanged). A spec that declares a `providers`
// block opts into the store / i18n / react-query / fetch wrapper (see
// ./providers.jsx) so a PAGE or PANEL can render with no backend. Seeding
// happens here, before first render, so the very first paint has its data.
const Wrap = spec?.providers ? applyProviders(spec.providers, { theme }) : null;

function Harness() {
  // Default theme (Gruvbox Dark) is the bare :root tokens — no data-theme.
  // Every other theme is applied via the [data-theme="…"] selector in
  // ui/themes.css, set on the wrapper so the token overrides cascade in.
  const themeAttr = theme === 'default' ? {} : { 'data-theme': theme };
  const content = spec ? (
    spec.render()
  ) : (
    <div className="visual-root__error">Unknown component: {componentName || '(none)'}</div>
  );
  // Pages/panels need a wider, deterministic frame than the 460px leaf frame.
  // A provider-spec defaults to 720px; a spec can override with `width`.
  const isPage = Boolean(spec?.providers);
  const widthStyle = spec?.width ? { width: spec.width } : isPage ? { width: 720 } : undefined;
  return (
    <div
      id="visual-root"
      className={`visual-root${isPage ? ' visual-root--page' : ''}`}
      style={widthStyle}
      {...themeAttr}
    >
      {Wrap ? <Wrap>{content}</Wrap> : content}
    </div>
  );
}

createRoot(document.getElementById('root')).render(<Harness />);

// Signal readiness once webfonts have settled so Playwright never snapshots a
// fallback-font frame. toHaveScreenshot also retries until pixels are stable.
document.fonts.ready.then(() => {
  document.documentElement.setAttribute('data-visual-ready', 'true');
});
