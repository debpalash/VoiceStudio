import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';
import { rewriteDevApiProxyPath } from './src/shared/web-api-routing';

const root = resolve(import.meta.dirname, 'src/renderer');
const frontend = resolve(import.meta.dirname, '../frontend');
const version = JSON.parse(readFileSync(resolve(frontend, 'package.json'), 'utf8')).version;
const backendPort = process.env.OMNIVOICE_PORT || '3900';

export default defineConfig({
  root,
  publicDir: resolve(frontend, 'public'),
  plugins: [
    react(),
    tailwindcss(),
    {
      name: 'renderer-boot-script',
      generateBundle() {
        this.emitFile({
          type: 'asset',
          fileName: 'early-error-capture.js',
          source: readFileSync(resolve(root, 'public/early-error-capture.js')),
        });
      },
    },
  ],
  define: {
    __APP_VERSION__: JSON.stringify(version),
    __WEB_DEPLOYMENT__: true,
  },
  resolve: {
    alias: {
      '@': resolve(root, 'src'),
      '@vercel/oidc': resolve(root, 'src/lib/vercel-oidc-browser.ts'),
    },
    dedupe: ['react', 'react-dom'],
  },
  optimizeDeps: { exclude: ['@scalar/api-reference-react'] },
  server: {
    host: 'localhost',
    port: Number(process.env.VOICESTUDIO_UI_PORT) || 3901,
    strictPort: true,
    proxy: {
      '/api/ws': {
        target: `http://127.0.0.1:${backendPort}`,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
        ws: true,
      },
      '/api': {
        target: `http://127.0.0.1:${backendPort}`,
        changeOrigin: true,
        // Four backend router families genuinely own `/api`; every other
        // renderer request uses `/api` only as the Vite/Electron proxy prefix.
        rewrite: rewriteDevApiProxyPath,
      },
    },
  },
  build: {
    outDir: resolve(frontend, 'dist'),
    emptyOutDir: true,
    rollupOptions: { input: resolve(root, 'index.html') },
  },
});
