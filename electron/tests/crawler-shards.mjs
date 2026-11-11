/**
 * Splits the crawler's routes across CI shards by cost, not by position.
 *
 * Crawl time is dominated by a few routes. Heaviest routes go first, each to the currently
 * lightest shard (ties to the lowest number), so every shard computes the same split without
 * coordinating.
 *
 * One weight is about 3 s of crawling (all four fault modes run concurrently). Measured
 * 2026-11-11 as the median of three main CI runs' shards (ubuntu-24.04). The crawler prints the
 * per-route seconds at the end of every run ("Route seconds: ..."), so
 * re-measure from a CI log or a local run when a shard drifts. Weights under 2 are left out.
 *
 * A route missing from ROUTE_WEIGHTS counts as 1, so a new route is still crawled by exactly one
 * shard; add it here when a crawl shows it is slow.
 */
export const ROUTE_WEIGHTS = {
  '/stories': 49,
  '/audiobook': 48,
  '/dub': 46,
  '/batch': 40,
  '/design': 34,
  '/workflows': 23,
  '/projects': 22,
  '/clone': 19,
  '/gallery': 19,
  '/integrations': 19,
  '/calls': 18,
  '/personas': 17,
  '/transcriptions': 16,
  '/tools': 15,
  '/pro': 14,
  '/settings/performance': 13,
  '/settings/appearance': 10,
  '/settings/sharing': 9,
  '/settings/openapi': 7,
  '/settings/storage': 5,
  '/settings/media': 4,
  '/settings/pronunciation': 3,
  '/settings/support': 3,
  '/settings/credentials': 2,
  '/settings/models': 2,
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
