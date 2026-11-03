/** Injected by electron.vite.config.ts (main + preload) from the root package.json. */
declare const __APP_VERSION__: string;
declare const __BUILD_COMMIT__: string;
declare const __DISTRIBUTION_TARGET__: string;
declare const __POSTHOG_PROJECT_TOKEN__: string;
declare const __POSTHOG_HOST__: string;
declare const __CLOUD_ACCOUNTS__: boolean;
declare const __CLOUD_CLIENT_ID__: string;

declare module '*.lock?raw' {
  const content: string;
  export default content;
}
