import { resolve } from 'node:path';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

const shared = resolve(import.meta.dirname, 'src/shared');

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify('0.0.0-test'),
    __BUILD_COMMIT__: JSON.stringify('000000000'),
  },
  resolve: {
    alias: [{ find: /^@\//, replacement: `${shared}/` }],
    dedupe: ['react', 'react-dom'],
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/shared/test/setup.js', './tests/vitest-async-timeout.mjs'],
    // Node 25+ ships a global localStorage that is undefined without --localstorage-file
    // and shadows jsdom's Storage; CI runs Node 22, where this flag is a no-op.
    execArgv: ['--no-experimental-webstorage'],
    // Shared release runners (Intel macOS, Windows ARM) run the suite many times slower
    // than a laptop; a real hang still fails, a busy runner no longer does.
    testTimeout: process.env.CI ? 30_000 : 5_000,
    include: ['src/shared/**/*.test.{js,jsx,ts,tsx}'],
    css: false,
  },
});
