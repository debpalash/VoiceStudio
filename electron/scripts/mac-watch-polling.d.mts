import type { Plugin } from 'vite';

export function macWatchPollingPlugin(platform?: NodeJS.Platform | string): Plugin & {
  config(config: {
    build?: { watch?: object | null };
  }): { build: { watch: { chokidar: { usePolling: true } } } } | undefined;
};
