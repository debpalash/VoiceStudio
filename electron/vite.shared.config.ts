import { resolve } from 'node:path';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

const shared = resolve(import.meta.dirname, 'src/shared');
const tauriStub = resolve(import.meta.dirname, 'tests/tauri-api-stub.ts');
const retiredStylesStub = resolve(import.meta.dirname, 'tests/retired-styles-stub.css');
const tauriModules = [
  '@tauri-apps/plugin-dialog/dist-js/index.js',
  '@tauri-apps/api/app',
  '@tauri-apps/api/core',
  '@tauri-apps/api/event',
  '@tauri-apps/api/webview',
  '@tauri-apps/api/window',
  '@tauri-apps/plugin-dialog',
  '@tauri-apps/plugin-opener',
  '@tauri-apps/plugin-process',
];

export default defineConfig({
  plugins: [
    {
      name: 'retired-tauri-styles',
      enforce: 'pre',
      resolveId(source, importer) {
        if (source === '../index.css' && importer?.includes('/src/shared/ui/')) {
          return retiredStylesStub;
        }
      },
    },
    react(),
  ],
  define: { __APP_VERSION__: JSON.stringify('0.0.0-test') },
  resolve: {
    alias: [
      ...tauriModules.map((id) => ({ find: id, replacement: `${tauriStub}?module=${id}` })),
      { find: resolve(shared, 'index.css'), replacement: retiredStylesStub },
      { find: /^@\//, replacement: `${shared}/` },
    ],
    dedupe: ['react', 'react-dom'],
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/shared/test/setup.js'],
    include: ['src/shared/**/*.test.{js,jsx,ts,tsx}'],
    css: false,
  },
});
