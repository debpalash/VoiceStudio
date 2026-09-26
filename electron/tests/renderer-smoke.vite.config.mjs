import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

const electronRoot = resolve(import.meta.dirname, '..');
const rendererRoot = resolve(electronRoot, 'src/renderer');
const version = JSON.parse(
  readFileSync(resolve(electronRoot, '../package.json'), 'utf8'),
).version;
const backendPort = process.env.OMNIVOICE_PORT || '3900';
const uiPort = Number(process.env.VOICESTUDIO_SMOKE_PORT) || 3912;

export default defineConfig({
  root: rendererRoot,
  plugins: [
    react(),
    tailwindcss(),
    {
      name: 'shared-capture-worklet',
      configureServer(server) {
        server.middlewares.use((request, response, next) => {
          if (request.url?.split('?')[0] !== '/aec-worklet.js') return next();
          response.setHeader('Content-Type', 'application/javascript');
          response.end(readFileSync(resolve(electronRoot, 'public/aec-worklet.js')));
        });
      },
    },
  ],
  define: {
    __APP_VERSION__: JSON.stringify(version),
    __WEB_DEPLOYMENT__: false,
  },
  resolve: {
    alias: {
      '@': resolve(rendererRoot, 'src'),
      '@vercel/oidc': resolve(rendererRoot, 'src/lib/vercel-oidc-browser.ts'),
    },
    dedupe: ['react', 'react-dom'],
  },
  optimizeDeps: { exclude: ['@scalar/api-reference-react'] },
  server: {
    host: 'localhost',
    port: uiPort,
    strictPort: true,
    proxy: {
      '/api/ws': {
        target: `http://127.0.0.1:${backendPort}`,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
        ws: true,
        configure(proxy) {
          proxy.on('proxyReqWs', (outgoing) => {
            outgoing.removeHeader('origin');
            outgoing.removeHeader('referer');
          });
        },
      },
      '/api': {
        target: `http://127.0.0.1:${backendPort}`,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
        configure(proxy) {
          proxy.on('proxyReq', (request) => {
            request.removeHeader('origin');
            request.removeHeader('referer');
          });
        },
      },
    },
  },
});
