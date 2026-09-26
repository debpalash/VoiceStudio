/**
 * staleBuild — pure version-comparison helpers for the bug reporter's
 * outdated-build deflection (utils/bugReport.js).
 *
 * Why this exists: 6 in 10 sampled "can't reach the backend" reports came
 * from builds that were already obsolete when filed, and were closed with
 * "please update" — pure triage noise. The reporter now checks whether the
 * running build is behind the latest release before opening the prefill.
 *
 * Kept dependency-free (imports nothing) so bugReport.js can use it without
 * any module-cycle risk. Only the X.Y.Z triple is compared: preview builds
 * stamp `X.Y.(Z+1)-N`, whose triple is AHEAD of the latest stable by the
 * versioning convention, so previews are never flagged as outdated.
 */

/** Parse `v?X.Y.Z(-suffix)` into [X, Y, Z], or null when unparseable.
 * Anchored: a suffix must be a semver-style `-`/`+` continuation, so
 * `1.2.3.4` and `1.2.3garbage` are rejected rather than silently read as
 * `1.2.3` and fed into an outdated/current verdict. */
export function parseVersionTriple(v) {
  const m = /^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$/.exec(String(v ?? '').trim());
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}

/**
 * Whether `current` is strictly behind `latest` by X.Y.Z comparison.
 * False whenever either side is missing or unparseable ('unknown' dev
 * builds must never trigger the deflection).
 */
export function isOutdated(current, latest) {
  const a = parseVersionTriple(current);
  const b = parseVersionTriple(latest);
  if (!a || !b) return false;
  for (let i = 0; i < 3; i += 1) {
    if (a[i] !== b[i]) return a[i] < b[i];
  }
  return false;
}
