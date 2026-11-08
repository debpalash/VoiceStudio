/** Injected by electron.vite.config.ts (main + preload) from the root package.json. */
declare const __APP_VERSION__: string;
declare const __BUILD_COMMIT__: string;
declare const __DISTRIBUTION_TARGET__: string;

declare module '*.lock?raw' {
  const content: string;
  export default content;
}
