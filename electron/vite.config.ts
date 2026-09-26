// Vite+ (`vp test` / `vp check`) config for the RENDERER unit tests only.
// The Electron dev/build pipeline lives in electron.vite.config.ts.
import { resolve } from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  // `vp fmt` / `vp check` read Oxfmt settings from here, not from .oxfmtrc.json
  // (kept in sync with it for editor integrations).
  fmt: {
    printWidth: 100,
    singleQuote: true,
    ignorePatterns: ['out/**', 'dist/**', 'release/**', 'src/renderer/src/components/ui/**'],
  },
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify('0.0.0-test'),
    __WEB_DEPLOYMENT__: false,
  },
  resolve: {
    alias: {
      '@': resolve(import.meta.dirname, 'src/renderer/src'),
      '@vercel/oidc': resolve(import.meta.dirname, 'src/renderer/src/lib/vercel-oidc-browser.ts'),
    },
    dedupe: ['react', 'react-dom'],
  },
  optimizeDeps: { exclude: ['@scalar/api-reference-react'] },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/renderer/src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
  },
});
