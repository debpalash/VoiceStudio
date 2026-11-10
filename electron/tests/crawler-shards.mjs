/**
 * Splits the crawler's routes across CI shards by cost, not by position.
 *
 * Crawl time is dominated by a few routes. Heaviest routes go first, each to the currently
 * lightest shard (ties to the lowest number), so every shard computes the same split without
 * coordinating.
 *
 * One weight is about 3 s of crawling (all four fault modes run concurrently). Measured
 * 2026-11-08 from a CI run's three shards (PR #3874, ubuntu-24.04). The crawler prints the
 * per-route seconds at the end of every run ("Route seconds: ..."), so
 * re-measure from a CI log or a local run when a shard drifts. Weights under 2 are left out.
 *
 * A route missing from ROUTE_WEIGHTS counts as 1, so a new route is still crawled by exactly one
 * shard; add it here when a crawl shows it is slow.
 */
export const ROUTE_WEIGHTS = {
  '/dub': 63,
  '/audiobook': 48,
  '/design': 40,
  '/batch': 33,
  '/stories': 31,
  '/workflows': 31,
  '/settings/performance': 25,
  '/projects': 22,
  '/integrations': 21,
  '/tools': 20,
  '/gallery': 19,
  '/pro': 17,
  '/calls': 16,
  '/personas': 16,
  '/clone': 15,
  '/transcriptions': 14,
  '/settings/appearance': 9,
  '/settings/storage': 5,
  '/settings/logs': 4,
  '/settings/account': 3,
  '/settings/sharing': 3,
  '/settings/support': 3,
  '/settings/credentials': 2,
  '/settings/media': 2,
  '/settings/pronunciation': 2,
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
