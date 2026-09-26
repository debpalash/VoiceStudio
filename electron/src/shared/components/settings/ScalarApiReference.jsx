import React from 'react';
import { ApiReferenceReact } from '@scalar/api-reference-react';
import '@scalar/api-reference-react/style.css';

/**
 * Thin wrapper around Scalar's bundled React API-reference component.
 *
 * LOCAL-FIRST / CDN-FREE (hard constraint): Scalar ships as an npm dependency
 * and is bundled by Vite — there is NO `<script src="cdn.jsdelivr…">` tag and
 * nothing is fetched from a CDN at runtime. Two escape hatches Scalar would
 * otherwise reach for over the network are disabled here:
 *
 *   • `withDefaultFonts: false` — drops the `@font-face` rules that would pull
 *     Inter / JetBrains Mono from `https://fonts.scalar.com`. Scalar injects
 *     those only when fonts are enabled (see @scalar/themes `getThemeStyles`:
 *     `fonts ? fonts_default : ''`), so turning them off removes every external
 *     font request. The app's own bundled fonts render the reference instead.
 *   • `proxyUrl: ''` — the interactive "Test Request" client sends requests
 *     DIRECT to the local backend instead of routing them through
 *     `proxy.scalar.com`.
 *
 * The spec is passed inline as `content` (already fetched from the LOCAL
 * backend by OpenApiPanel), so Scalar never fetches a spec URL of its own. The
 * bundled `style.css` is self-contained (no external `url()` fetches). The
 * Tauri CSP (connect-src / font-src / script-src limited to self + localhost)
 * is the hard backstop that would block any residual external request anyway.
 *
 * Lazy-loaded by OpenApiPanel so this ~heavy bundle only downloads when the
 * user actually opens the OpenAPI settings page (it never enters the main
 * chunk, and the unreachable-backend fallback path never needs it).
 */
export default function ScalarApiReference({ spec, darkMode = true }) {
  return (
    <ApiReferenceReact
      configuration={{
        content: spec,
        withDefaultFonts: false,
        proxyUrl: '',
        darkMode,
      }}
    />
  );
}
