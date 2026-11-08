/**
 * Splits the crawler's routes across CI shards by cost, not by position.
 *
 * Crawl time is dominated by a few routes. Heaviest routes go first, each to the currently
 * lightest shard (ties to the lowest number), so every shard computes the same split without
 * coordinating.
 *
 * One weight is about 3 s of crawling. Measured 2026-11-08 on an Apple Silicon Mac in fixture
 * mode, one shard at a time (`VOICESTUDIO_CRAWL_MODES=fixture VOICESTUDIO_CRAWL_SHARD=i/3`);
 * the crawler prints the per-route seconds at the end of every run ("Route seconds: ..."), so
 * re-measure from a CI log or a local run when a shard drifts. Weights under 2 are left out.
 *
 * A route missing from ROUTE_WEIGHTS counts as 1, so a new route is still crawled by exactly one
 * shard; add it here when a crawl shows it is slow.
 */
export const ROUTE_WEIGHTS = {
  '/dub': 32,
  '/batch': 31,
  '/design': 28,
  '/audiobook': 27,
  '/stories': 24,
  '/workflows': 18,
  '/integrations': 15,
  '/calls': 12,
  '/projects': 12,
  '/tools': 12,
  '/clone': 11,
  '/gallery': 11,
  '/pro': 10,
  '/transcriptions': 10,
  '/personas': 9,
  '/settings/performance': 7,
  '/settings/appearance': 6,
  '/settings/credentials': 2,
  '/settings/media': 2,
  '/settings/pronunciation': 2,
  '/settings/sharing': 2,
  '/settings/storage': 2,
  '/settings/support': 2,
};

/** The routes shard `shard` (1-based) of `shards` crawls, in a stable order. */
export function routesForShard(routes, shard, shards, weights = ROUTE_WEIGHTS) {
  const weight = (route) => weights[route] ?? 1;
  const load = Array.from({ length: shards }, () => 0);
  const owned = Array.from({ length: shards }, () => []);
  const heaviestFirst = [...routes].sort((a, b) => weight(b) - weight(a) || (a < b ? -1 : 1));
  for (const route of heaviestFirst) {
    const lightest = load.indexOf(Math.min(...load));
    load[lightest] += weight(route);
    owned[lightest].push(route);
  }
  return owned[shard - 1].sort();
}
