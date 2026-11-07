import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { defineConfig, externalizeDepsPlugin } from 'electron-vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import {
  DEFAULT_BACKEND_PORT,
  DEFAULT_ELECTRON_DEV_PORT,
  DEFAULT_ELECTRON_PROXY_PORT,
  PRODUCT_NAME,
} from '../config/app-config.mjs';
import { DISTRIBUTION_TARGET } from '../config/distributions.mjs';
import { buildCommit } from '../config/build-commit.mjs';
import { macWatchPollingPlugin } from './scripts/mac-watch-polling.mjs';

// The app version is single-sourced from the root package.json (CLAUDE.md,
// Versioning). Nothing in electron/ mirrors it: the renderer reads it through
// __APP_VERSION__ and the installer through electron-builder.config.mjs.
const frontendPkg = JSON.parse(readFileSync(resolve(__dirname, '../package.json'), 'utf-8')) as {
  version: string;
};

const define = {
  __APP_VERSION__: JSON.stringify(frontendPkg.version),
  __BUILD_COMMIT__: JSON.stringify(buildCommit()),
  __DISTRIBUTION_TARGET__: JSON.stringify(DISTRIBUTION_TARGET),
  __WEB_DEPLOYMENT__: false,
  // Keep the renderer and its managed Python backend on one analytics project.
  __POSTHOG_PROJECT_TOKEN__: JSON.stringify(
    process.env.VITE_POSTHOG_KEY ?? process.env.POSTHOG_PROJECT_TOKEN ?? '',
  ),
  __POSTHOG_HOST__: JSON.stringify(process.env.VITE_POSTHOG_HOST ?? process.env.POSTHOG_HOST ?? ''),
  // VoiceStudio Cloud sign-in ships dark in packaged builds until the vssaas production API is live.
  __CLOUD_ACCOUNTS__: JSON.stringify(process.env.VOICESTUDIO_CLOUD_ACCOUNTS === '1'),
  // The production Zitadel client ID of this app (vssaas assigns it); packaged builds need it to sign in.
  __CLOUD_CLIENT_ID__: JSON.stringify(process.env.VOICESTUDIO_CLOUD_CLIENT_ID ?? ''),
};

// OMNIVOICE_UI_PORT is canonical (the backend reads it for its origin
// allow-list); VOICESTUDIO_UI_PORT is the accepted legacy alias.
const rendererPort =
  Number(process.env.OMNIVOICE_UI_PORT || process.env.VOICESTUDIO_UI_PORT) ||
  DEFAULT_ELECTRON_DEV_PORT;

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin(), macWatchPollingPlugin()],
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
    plugins: [externalizeDepsPlugin(), macWatchPollingPlugin()],
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
        name: 'app-identity',
        transformIndexHtml(html) {
          return html.replaceAll('__APP_PRODUCT_NAME__', PRODUCT_NAME);
        },
      },
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
        '@shared': resolve(__dirname, 'src/shared'),
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
      port: rendererPort,
      strictPort: true,
      // Dev counterpart of main's app:// proxy (CONTRACT.md, same-origin API rule).
      proxy: {
        '/api/ws': {
          target: 'http://127.0.0.1:' + (process.env.OMNIVOICE_PORT || DEFAULT_BACKEND_PORT),
          changeOrigin: true,
          rewrite: (p) => p.replace(/^\/api/, ''),
          ws: true,
          configure(proxy) {
            proxy.on('proxyReqWs', (outgoing, incoming) => {
              const port = rendererPort;
              const origin = incoming.headers.origin;
              if (origin === `http://localhost:${port}` || origin === `http://127.0.0.1:${port}`) {
                outgoing.removeHeader('origin');
                outgoing.removeHeader('referer');
              }
            });
          },
        },
        '/api': {
          target:
            'http://127.0.0.1:' +
            (process.env.VOICESTUDIO_ELECTRON_PROXY_PORT || DEFAULT_ELECTRON_PROXY_PORT),
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
