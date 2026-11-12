import type { UpdateChannel, UpdateReleaseInfo } from '../../../preload/index.d';

const RELEASE = /^v?(\d+)\.(\d+)\.(\d+)(?:-beta\.(\d+))?$/;

/** [major, minor, patch, beta]; a stable sorts after its own betas. Null for untagged builds. */
function rank(version: string): number[] | null {
  const match = RELEASE.exec(version);
  if (!match) return null;
  return [
    Number(match[1]),
    Number(match[2]),
    Number(match[3]),
    match[4] === undefined ? Number.MAX_SAFE_INTEGER : Number(match[4]),
  ];
}

function newer(left: number[], right: number[]): boolean {
  for (let index = 0; index < left.length; index += 1) {
    if (left[index] !== right[index]) return left[index] > right[index];
  }
  return false;
}

export interface ChannelReleases {
  /** The channel's own releases, newest first: Stable lists stables, Beta lists betas. */
  rows: (UpdateReleaseInfo & { current: boolean })[];
  /**
   * Beta only: the newest stable when it is newer than every beta (and than the
   * installed beta), so the list can suggest switching instead of mixing it in.
   */
  newerStable: (UpdateReleaseInfo & { current: boolean }) | null;
}

export function channelReleases(
  releases: UpdateReleaseInfo[],
  channel: UpdateChannel | undefined,
  currentVersion: string,
): ChannelReleases {
  const tagged = releases
    .map((release) => ({ release, rank: rank(release.version) }))
    .filter((item): item is { release: UpdateReleaseInfo; rank: number[] } => item.rank !== null)
    // A stable is a non-prerelease without a beta suffix; anything else tagged is a beta.
    .map((item) => ({
      ...item,
      stable: !item.release.prerelease && item.rank[3] === Number.MAX_SAFE_INTEGER,
    }))
    .sort((left, right) =>
      newer(left.rank, right.rank) ? -1 : newer(right.rank, left.rank) ? 1 : 0,
    );
  const withCurrent = (release: UpdateReleaseInfo) => ({
    ...release,
    current: release.version === currentVersion,
  });
  const beta = channel === 'beta';
  const rows = tagged
    .filter((item) => item.stable !== beta)
    .map((item) => withCurrent(item.release));
  if (!beta) return { rows, newerStable: null };
  const newestStable = tagged.find((item) => item.stable);
  const newestBeta = tagged.find((item) => !item.stable);
  const installed = rank(currentVersion);
  const installedBeta = installed && installed[3] !== Number.MAX_SAFE_INTEGER ? installed : null;
  const suggest =
    newestStable &&
    (!newestBeta || newer(newestStable.rank, newestBeta.rank)) &&
    (!installedBeta || newer(newestStable.rank, installedBeta));
  return { rows, newerStable: suggest ? withCurrent(newestStable.release) : null };
}
