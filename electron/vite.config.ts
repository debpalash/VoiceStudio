// Vite+ (`vp test` / `vp check`) config for the RENDERER unit tests only.
// The Electron dev/build pipeline lives in electron.vite.config.ts.
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
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
    __BUILD_COMMIT__: JSON.stringify('000000000'),
    __DISTRIBUTION_TARGET__: JSON.stringify('direct'),
    __WEB_DEPLOYMENT__: false,
  },
  resolve: {
    alias: {
      '@': resolve(import.meta.dirname, 'src/renderer/src'),
      '@shared': resolve(import.meta.dirname, 'src/shared'),
      '@vercel/oidc': resolve(import.meta.dirname, 'src/renderer/src/lib/vercel-oidc-browser.ts'),
    },
    dedupe: ['react', 'react-dom'],
  },
  optimizeDeps: { exclude: ['@scalar/api-reference-react'] },
  test: {
    globals: true,
    setupFiles: ['./src/renderer/src/test/setup.ts'],
    // Code under test saves choices to this file; the real one steers the installed app.
    env: { OMNIVOICE_ENV_FILE: join(tmpdir(), `voicestudio-vitest-${process.pid}`, 'env') },
    // Node 25+ ships a global localStorage that is undefined without --localstorage-file
    // and shadows jsdom's Storage; CI runs Node 22, where this flag is a no-op.
    execArgv: ['--no-experimental-webstorage'],
    // Shared release runners (Intel macOS, Windows ARM) run the suite many times slower
    // than a laptop; a real hang still fails, a busy runner no longer does.
    testTimeout: process.env.CI ? 30_000 : 5_000,
    css: false,
    projects: [
      {
        extends: true,
        test: {
          // Main-process code runs in Node, so its tests do too (no DOM to set up per file).
          name: 'main',
          environment: 'node',
          pool: 'forks',
          include: ['src/main/**/*.test.{ts,tsx}'],
        },
      },
      {
        extends: true,
        test: {
          name: 'renderer',
          environment: 'jsdom',
          // Each file still gets its own VM context and module graph, but the worker keeps
          // jsdom loaded instead of rebuilding a process per file: that setup was about half
          // of this suite's CI time. Workers are recycled past the memory limit.
          pool: 'vmForks',
          vmMemoryLimit: '2GB',
          setupFiles: ['./tests/vitest-web-streams.mjs'],
          include: [
            'src/renderer/**/*.test.{ts,tsx}',
            'src/shared/repair-request.test.ts',
            'src/shared/download-failure.test.ts',
          ],
        },
      },
    ],
  },
});
