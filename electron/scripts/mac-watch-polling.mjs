// `bun run dev` builds main and preload with `--watch`, and electron-vite only
// launches Electron after each watch build's first `closeBundle`. On macOS,
// rolldown's native (FSEvents) watcher can end the first build without writing
// it: the watcher emits START -> BUNDLE_START -> END with no BUNDLE_END and no
// ERROR, so `closeBundle` never runs and dev sits idle after "modules
// transformed" (reproduced on rolldown 1.2.7 and 1.2.16). Its polling watcher
// writes the bundle normally. Main + preload are a small module graph, so
// polling them is cheap. Watching behaves the same; only how changes are
// detected differs. Vite rebuilds rolldown's `watcher` option from
// `build.watch.chokidar`, so the flag has to go there.
export function macWatchPollingPlugin(platform = process.platform) {
  return {
    name: 'voicestudio:mac-watch-polling',
    config(config) {
      // Production builds clear `build.watch`, so this only touches `--watch`.
      if (platform !== 'darwin' || !config.build?.watch) return;
      return { build: { watch: { chokidar: { usePolling: true } } } };
    },
  };
}
