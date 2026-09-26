import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { defineConfig, externalizeDepsPlugin } from 'electron-vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// The app version is single-sourced from the root package.json (CLAUDE.md,
// Versioning). Nothing in electron/ mirrors it: the renderer reads it through
// __APP_VERSION__ and the installer through electron-builder.config.mjs.
const frontendPkg = JSON.parse(
  readFileSync(resolve(__dirname, '../package.json'), 'utf-8'),
) as { version: string };

const define = {
  __APP_VERSION__: JSON.stringify(frontendPkg.version),
  __WEB_DEPLOYMENT__: false,
  __PRO_STORE_ID__: JSON.stringify(process.env.VOICESTUDIO_PRO_STORE_ID ?? ''),
  __PRO_PRODUCT_ID__: JSON.stringify(process.env.VOICESTUDIO_PRO_PRODUCT_ID ?? ''),
  __PRO_YEARLY_VARIANT_ID__: JSON.stringify(process.env.VOICESTUDIO_PRO_YEARLY_VARIANT_ID ?? ''),
  __PRO_LIFETIME_VARIANT_ID__: JSON.stringify(
    process.env.VOICESTUDIO_PRO_LIFETIME_VARIANT_ID ?? '',
  ),
  // Keep the renderer and its managed Python backend on one analytics project.
  __POSTHOG_PROJECT_TOKEN__: JSON.stringify(
    process.env.VITE_POSTHOG_KEY ?? process.env.POSTHOG_PROJECT_TOKEN ?? '',
  ),
  __POSTHOG_HOST__: JSON.stringify(process.env.VITE_POSTHOG_HOST ?? process.env.POSTHOG_HOST ?? ''),
};

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    define,
    build: {
      rollupOptions: {
        input: {
          index: resolve(__dirname, 'src/main/index.ts'),
          'repair-mcp-server': resolve(__dirname, 'src/main/repair-mcp-server.ts'),
        },
      },
    },
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
    define,
    build: {
      rollupOptions: { input: resolve(__dirname, 'src/preload/index.ts') },
    },
  },
  renderer: {
    root: resolve(__dirname, 'src/renderer'),
    publicDir: resolve(__dirname, 'public'),
    plugins: [
      react(),
      tailwindcss(),
      {
        name: 'renderer-boot-script',
        configureServer(server) {
          server.middlewares.use((req, res, next) => {
            if (req.url?.split('?')[0] !== '/early-error-capture.js') return next();
            res.setHeader('Content-Type', 'application/javascript');
            res.end(readFileSync(resolve(__dirname, 'public/early-error-capture.js')));
          });
        },
        generateBundle() {
          this.emitFile({
            type: 'asset',
            fileName: 'early-error-capture.js',
            source: readFileSync(resolve(__dirname, 'public/early-error-capture.js')),
          });
        },
      },
    ],
    define,
    resolve: {
      alias: {
        '@': resolve(__dirname, 'src/renderer/src'),
        // Scalar's optional AI client reaches a CommonJS browser entry that
        // native ESM cannot import by name. Mirror its browser API as real ESM.
        '@vercel/oidc': resolve(__dirname, 'src/renderer/src/lib/vercel-oidc-browser.ts'),
      },
      // Workspace installs can place Scalar outside electron/node_modules.
      // Always resolve hook-owning packages from this renderer's one runtime.
      dedupe: ['react', 'react-dom'],
    },
    optimizeDeps: {
      // Scalar's React wrapper is ESM. Prebundling it inlines a private CJS
      // React runtime, so its hooks cannot see this renderer's dispatcher.
      exclude: ['@scalar/api-reference-react'],
    },
    server: {
      port: Number(process.env.VOICESTUDIO_UI_PORT) || 3902,
      strictPort: true,
      // Dev counterpart of main's app:// proxy (CONTRACT.md, same-origin API rule).
      proxy: {
        '/api/ws': {
          target: 'http://127.0.0.1:' + (process.env.OMNIVOICE_PORT || 3900),
          changeOrigin: true,
          rewrite: (p) => p.replace(/^\/api/, ''),
          ws: true,
          configure(proxy) {
            proxy.on('proxyReqWs', (outgoing, incoming) => {
              const port = Number(process.env.VOICESTUDIO_UI_PORT) || 3902;
              const origin = incoming.headers.origin;
              if (origin === `http://localhost:${port}` || origin === `http://127.0.0.1:${port}`) {
                outgoing.removeHeader('origin');
                outgoing.removeHeader('referer');
              }
            });
          },
        },
        '/api': {
          target: 'http://127.0.0.1:' + (process.env.VOICESTUDIO_ELECTRON_PROXY_PORT || 3903),
          changeOrigin: true,
          rewrite: (p) => p.replace(/^\/api/, ''),
          configure(proxy) {
            proxy.on('proxyReq', (req) => {
              req.removeHeader('origin');
              req.removeHeader('referer');
            });
          },
        },
      },
    },
    build: {
      rollupOptions: { input: resolve(__dirname, 'src/renderer/index.html') },
    },
  },
});
