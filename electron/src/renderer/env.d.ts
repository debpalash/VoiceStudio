/// <reference types="vite/client" />

/** Injected by electron.vite.config.ts from the root package.json (single source of truth). */
declare const __APP_VERSION__: string;

/** First 9 characters of the commit this build was made from, or "unknown". */
declare const __BUILD_COMMIT__: string;

/** True only for the browser bundle served by the Python backend. */
declare const __WEB_DEPLOYMENT__: boolean;
