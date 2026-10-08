# Installer review: remaining findings and overlap

**Source-review baseline:** [`fb9a960c88744edc36daf2cf38d48df0971ee713`][baseline],
2026-10-04, app 0.5.6. [Proposal](readme.md); no finding is fixed by this docs-only
patch. The active renderer is `electron/src/renderer/src`, not the legacy shared
wizard. Historical tests below used a clean archive of that commit, not the integrated branch.

## Integration refresh: 2026-10-05

The branch now includes main [`990f0627ba14dbb309b88c0051ebb953d3101800`](https://github.com/debpalash/VoiceStudio/commit/990f0627ba14dbb309b88c0051ebb953d3101800),
including the CPU-budget change from #2611. This proposal adds no runtime behavior.
This is a documentation integration refresh, not a rerun of the source probes or
native acceptance. The findings, line-pinned evidence and local results below
remain the 2026-10-04 snapshot and must be revalidated before implementation.

- [#2602][pr2602] merged at `5258077d5967d860da3954bad11be447256e8a61`,
  including disk-headroom wording, Rosetta guidance and runtime package-progress
  identity. Preserve these fixes; they are no longer open-PR work
- [#2605][pr2605] merged at `3fcd381e5c3282b084384dbc38be28ddaba0e6bf`,
  including source CPU selection/no-sync restarts and native compatibility helpers
- [#2585][pr2585] remains open at `e87cba0449485b226674961be796b9d3e20ed0a1`.
  Main has absorbed overlapping fixes through #2602; compare the remaining diff
  before using this branch as implementation evidence
- [#2607][pr2607] remains a draft, now at
  `51e4eb33cc7cbec39493cfeb9027e6a6b15710fb`. Its older reviewed head below is
  historical. Reconcile the current Windows ROCm work with the landed #2605
  selector/interpreter changes and retain its native qualification gates

These status checks do not establish new platform support or merge readiness.
Fresh hosted CI on the integrated PR head is the merge gate; none of the local
test or hardware results below is claimed for that head.

## Already addressed at the source-review baseline

- [#2500][pr2500]: packaged non-NVIDIA x64 CPU wheels, CPU float32 and experimental
  Windows ARM packaging. Do not reintroduce the old broad CPU/CUDA bootstrap claim
- [#2578][pr2578]: Electron-native platform install guides, AppImage/deb truth,
  current uninstall folders/elevation safeguards, best-effort `.pth` repair and
  stable-only desktop updates. Retire old finding R11 (legacy install guides);
  do not propose restoring Preview or blanket uninstall-script changes
- [#2557][pr2557]: package-managed installations can disable in-app updating via
  `VOICESTUDIO_DISABLE_UPDATER=1`; the Linux guide covers the community AUR package

These merged changes do not establish native acceptance for this review.

## Open-PR overlap

Historical snapshot: read-only PR metadata/diffs were checked on 2026-10-04.
For current merged/open status, use the [integration refresh](#integration-refresh-2026-10-05):

| PR / reviewed head | Relevant work already proposed | Treatment here |
|---|---|---|
| [#2602][pr2602] · `4cf3db00993b910a50c00b19ca6cb3cb823b440b` · open | Named disk-headroom/total warnings, Mac architecture/Rosetta guidance and runtime package-progress identity | Do not implement these again; recheck after landing. Headroom wording is distinct from R5/R7; runtime package bytes are distinct from R3's model-pack aggregation |
| [#2585][pr2585] · `e87cba0449485b226674961be796b9d3e20ed0a1` · open | Runtime package-progress identity, storage scan/cleanup and broader desktop/audio fixes | Avoid duplicate progress/storage work. Its general storage cleanup changes are distinct from R8's sidecar uninstall function |
| [#2605][pr2605] · `441673e1ff5423e6713aef834ec0b1b62dc6c703` · draft | Source CPU/CUDA dependency groups, CPU selection before sync, no-sync restarts, extracted native-compatibility helpers and Electron lazy-executable startup | Reuse this source-install work; packaged CPU bootstrap already landed in #2500. R6's remaining recipe edges are not resolved by this CPU change |
| [#2607][pr2607] · `265295e7e1b1687308314aefe27e219c5994e95f` · draft | Broader Windows AMD/ROCm desktop and engine integration, depending on [#2600][pr2600]'s source recipe; Windows x64 Python 3.12, HIP Torch/CT2 checks and recipe-aware runtime identity | Reconcile with #2605 and #2602 instead of creating another selector or recipe. Keep the documented separate source flow until launcher/interpreter selection agrees; this draft does not establish shipped or qualified AMD support |

No open-PR tests or merge readiness are asserted here. Their scopes overlap each
other too; this proposal does not pick a winner or absorb these branches. Refresh
this snapshot before implementation, especially if any reviewed head changes.

**Historical #2605 / #2607 reconciliation:** seven paths overlapped at the heads above: `CHANGELOG.md`,
`docs/install/windows.md`, `electron/src/main/runtime-project.ts`,
`electron/src/main/runtime-project.test.ts`,
`electron/src/main/runtime-torch-variant.test.ts`, `scripts/setup.py` and
`tests/test_setup_rocm_variant.py`. Preserve #2605's helper extraction and optional
cuDNN HIP exclusion alongside #2607's recipe-aware runtime work. Their runtime
tests independently add global `fetch` mocks (success versus offline rejection);
choose one intentional baseline with per-test overrides, not duplicate stubs.
The [source selector][cpu-selector] in #2605 falls back to CPU/CUDA for a Windows
ROCm request, and its [launcher][cpu-launcher] does not select Python 3.12 on x64.
#2607's [setup gate][rocm-setup] requires Windows x64 Python 3.12: combining the
branches unchanged can sync a CPU/CUDA stack first, then reject an existing 3.11
environment. Preserve its [separate source recipe][rocm-source] or reconcile and
test the launcher before presenting a unified path. #2607 also retains clean
Windows/packaged-artifact, engine-coverage and non-ASCII-path validation gates;
its reported hardware experiments are not acceptance for this proposal.

## Remaining findings

P1: correctness reaching working first use. P2: recovery/resource/usability gaps.
Priorities do not imply observed data loss or a measured user failure rate.

| ID | Evidence / priority | Remaining behavior at the baseline | Smallest next step |
|---|---|---|---|
| R1 | Isolated source probe · P1 | Setup's positive-size cache predicate disagrees with catalogue completeness | Reuse a target-aware completeness/readiness projection |
| R2 | Source review · P2 | Simple pack view excludes failed rows and has no retained failure/Cancel controls; detail requires Advanced | Expose existing recovery in the simple view |
| R3 | Isolated source probe · P2 | Pack percentage sums only active jobs, so completed work disappears | Retain completed work in the accepted-plan denominator |
| R4 | Source review · P2 | Model/sidecar state is process-local; detailed model failures disappear from status after 60 seconds | Separate retry cooldown from terminal history; durable interrupted state |
| R5 | Source review · P2 | Pack confirmation uses catalogue estimates; exact file/dependency metadata resolves inside the started job | Read-only plan before confirmation; explicit size uncertainty |
| R6 | Source review · P2 | Explicit ROCm syncs default wheels before replacement; explicit CUDA/default precedes the Windows ARM automatic CPU branch | Validate recipes and avoid CUDA-first replacement; reconcile [#2605][pr2605] and [#2607][pr2607] before adding runtime work |
| R7 | Isolated source probes · P2 | Both backend guards treat measured zero free bytes like a failed/unknown probe | Represent unknown separately; reject positive work at zero |
| R8 | Isolated source probe · P2 | Managed sidecar removal ignores deletion errors and clears registration despite remaining files | Observe removal outcome and preserve retry/registration on failure |
| R9 | Source review · P2 | Wizard position and preview selection are component state; persisted setup flags cannot restore them | Persist and reconcile a versioned setup session |
| R10 | Source review · P2 | Most preflight text bypasses i18n; compact progress has no progressbar semantics; consent details targets an absent FAQ | Localized reason codes, accessible status/focus and valid privacy details |

### Evidence details

**R1:** [`setup_status`][setup-status] calls [`is_cached`][is-cached], while the
[catalogue][catalogue] also calls `cache_is_complete`. The [setup gate][setup-gate]
uses `models_ready` to advance. A fresh AST-extracted probe ran the unchanged
predicate bodies with a temporary snapshot containing only a 25-byte config file
and mocked HF inventory: `is_cached=true`, `cache_is_complete=false`,
`models_ready=true`, `missing=[]`. This proves predicate disagreement, not a native
click-through or a full completeness specification.

**R2–R3:** [PerformanceModelPacks][pack-ui] filters all terminal states before
summing bytes. Executing those exact aggregation statements with two 100-byte jobs
at 90 and 10 bytes gives 50%; completing the first at 100 gives 10%, though the
combined total should be 55%. A terminal-only set has zero visible active rows.
Detailed recovery exists in the same module; this is not a claim that the app has
no cancellation or retry implementation.

**R4:** [Model job status][model-jobs] omits failures once `now - failed_at >= 60`;
its maps and the [sidecar jobs][sidecar-jobs] dictionary are in memory. Existing
range manifests, cached partial files, runtime markers and renderer reconnection
remain valuable, but do not provide durable user-visible operation history.

**R5:** The [pack resolver][pack-resolver] sums `size_gb`. The
[download worker][model-plan] resolves pinned snapshots/dependencies and checks
space before weight transfer, but after the user has started the job. No concrete
size undercount or transfer benchmark was measured. #2602's headroom warning does
not introduce a pre-confirmation plan or combined per-volume reservation.

**R6:** [Runtime selection/sync][runtime] excludes CUDA packages for CPU only,
then performs default frozen sync before the ROCm replacement. Explicit
`cuda/default` returns before the Windows ARM auto-CPU branch; the ROCm platform
gate is OS-only. This is source evidence of unsupported/unverified recipe edges,
not proof of native ARM/ROCm installation failure. The shipped auto-CPU path is
already present; avoid a second implementation while CPU/runtime work is active.
[#2605][pr2605] preserves these baseline edges while adding source CPU selection.
[#2607][pr2607] proposes a narrower Windows x64 ROCm recipe and changes runtime
selection; it must be reconciled with #2605, not treated as proof that baseline R6
is fixed. Recheck default-sync ordering and explicit Windows ARM overrides in the
combined code, with separate source and packaged acceptance.

**R7:** The [model guard][disk-guard] and [sidecar guard][sidecar-disk] both return
no error for `free <= 0`. Fresh extracted-function probes with positive requested
bytes and zero measured free reproduced this for both. This concerns direct
backend admission; the compact UI's low-disk gate can still block its own path.
Existing preflight and mid-transfer ENOSPC handling are not claimed absent.

**R8:** [Sidecar uninstall][sidecar-uninstall] uses `rmtree(ignore_errors=True)`
and bases success on pre-deletion existence. A fresh unchanged-function probe
with an isolated managed directory and a mocked ignored deletion failure returned
`uninstalled`, cleared environment/preferences and left the file present. No real
Windows sharing violation was exercised. #2578's app-uninstall script safeguards
and open storage-temp cleanup work are different paths.

**R9–R10:** [Setup flags][setup-progress] store started/completed only; the gate
initializes step zero. [Preflight][preflight-ui] translates only the architecture
case. Compact pack progress uses plain divs. [Consent][consent] correctly saves
`enabled:false` for No thanks and waits for success with equal outline buttons;
its details URL points to `README#-faq`, absent from the [baseline README][readme].
There is no newly reproduced consent reversal, full-Max selection bug or performance
benchmark. Source inspection is not a screen-reader/viewport audit.

## Historical verification: 2026-10-04

| Check | Result / limits |
|---|---|
| `sh -n scripts/install.sh` | Passed |
| From `electron/`: `node --test tests/shell-installer.test.mjs tests/installer-worker.test.mjs tests/native-linux-libraries.test.mjs` | 27 passed, 0 skipped; Linux / Node 24.19.0; fixture contracts, not native installers |
| Python 3.12.14 / Node source probes | Reproduced R1, R3, both R7 guards and R8 without ML dependencies/network |
| Local Markdown targets/anchors, pinned source paths/line ranges, SVG XML, `git diff --check`, clean-baseline patch application | Passed |
| SVG | Existing illustrative asset rendered and visually inspected; not a product screenshot |

No runtime/dependency/workflow/version files changed. The previous proposal's
19-pytest result is historical and is not carried forward as a fresh pass: pytest
is unavailable in this review environment and no software was installed.

**Not run:** full backend/React/Vitest suites, typecheck, builds, PowerShell,
packaged install/first-output/update/rollback, native picker/accessibility tools,
macOS/Windows/ARM/GPU hardware. Proposed designs and source-level defects are
explicitly separate from platform acceptance.

[baseline]: https://github.com/debpalash/VoiceStudio/commit/fb9a960c88744edc36daf2cf38d48df0971ee713
[pr2500]: https://github.com/debpalash/VoiceStudio/pull/2500
[pr2578]: https://github.com/debpalash/VoiceStudio/pull/2578
[pr2557]: https://github.com/debpalash/VoiceStudio/pull/2557
[pr2602]: https://github.com/debpalash/VoiceStudio/pull/2602
[pr2585]: https://github.com/debpalash/VoiceStudio/pull/2585
[pr2600]: https://github.com/debpalash/VoiceStudio/pull/2600
[pr2605]: https://github.com/debpalash/VoiceStudio/pull/2605
[pr2607]: https://github.com/debpalash/VoiceStudio/pull/2607
[cpu-selector]: https://github.com/debpalash/VoiceStudio/blob/441673e1ff5423e6713aef834ec0b1b62dc6c703/electron/scripts/torch-variant.mjs#L65-L88
[cpu-launcher]: https://github.com/debpalash/VoiceStudio/blob/441673e1ff5423e6713aef834ec0b1b62dc6c703/scripts/setup-api.mjs#L18-L32
[rocm-setup]: https://github.com/Pates2004/VoiceStudio/blob/265295e7e1b1687308314aefe27e219c5994e95f/scripts/setup.py#L163-L172
[rocm-source]: https://github.com/Pates2004/VoiceStudio/blob/265295e7e1b1687308314aefe27e219c5994e95f/docs/install/windows-rocm-source.md
[setup-status]: https://github.com/debpalash/VoiceStudio/blob/fb9a960c88744edc36daf2cf38d48df0971ee713/backend/api/routers/setup/wizard.py#L41-L58
[is-cached]: https://github.com/debpalash/VoiceStudio/blob/fb9a960c88744edc36daf2cf38d48df0971ee713/backend/api/routers/setup/models.py#L497-L517
[catalogue]: https://github.com/debpalash/VoiceStudio/blob/fb9a960c88744edc36daf2cf38d48df0971ee713/backend/api/routers/setup/models.py#L650-L670
[setup-gate]: https://github.com/debpalash/VoiceStudio/blob/fb9a960c88744edc36daf2cf38d48df0971ee713/electron/src/renderer/src/components/setup-gate.tsx
[pack-ui]: https://github.com/debpalash/VoiceStudio/blob/fb9a960c88744edc36daf2cf38d48df0971ee713/electron/src/renderer/src/features/settings/model-library.tsx#L133-L314
[model-jobs]: https://github.com/debpalash/VoiceStudio/blob/fb9a960c88744edc36daf2cf38d48df0971ee713/backend/api/routers/setup/download.py#L400-L456
[sidecar-jobs]: https://github.com/debpalash/VoiceStudio/blob/fb9a960c88744edc36daf2cf38d48df0971ee713/backend/services/sidecar_install.py#L988-L1029
[pack-resolver]: https://github.com/debpalash/VoiceStudio/blob/fb9a960c88744edc36daf2cf38d48df0971ee713/electron/src/renderer/src/features/settings/performance-model-packs.ts
[model-plan]: https://github.com/debpalash/VoiceStudio/blob/fb9a960c88744edc36daf2cf38d48df0971ee713/backend/api/routers/setup/download.py#L658-L725
[runtime]: https://github.com/debpalash/VoiceStudio/blob/fb9a960c88744edc36daf2cf38d48df0971ee713/electron/src/main/runtime-project.ts
[disk-guard]: https://github.com/debpalash/VoiceStudio/blob/fb9a960c88744edc36daf2cf38d48df0971ee713/backend/api/routers/setup/models.py#L230-L275
[sidecar-disk]: https://github.com/debpalash/VoiceStudio/blob/fb9a960c88744edc36daf2cf38d48df0971ee713/backend/services/sidecar_install.py#L938-L985
[sidecar-uninstall]: https://github.com/debpalash/VoiceStudio/blob/fb9a960c88744edc36daf2cf38d48df0971ee713/backend/services/sidecar_install.py#L1231-L1271
[setup-progress]: https://github.com/debpalash/VoiceStudio/blob/fb9a960c88744edc36daf2cf38d48df0971ee713/electron/src/renderer/src/lib/setup-progress.ts
[preflight-ui]: https://github.com/debpalash/VoiceStudio/blob/fb9a960c88744edc36daf2cf38d48df0971ee713/electron/src/renderer/src/features/settings/system-preflight.tsx
[consent]: https://github.com/debpalash/VoiceStudio/blob/fb9a960c88744edc36daf2cf38d48df0971ee713/electron/src/renderer/src/components/analytics-consent.tsx#L76-L129
[readme]: https://github.com/debpalash/VoiceStudio/blob/fb9a960c88744edc36daf2cf38d48df0971ee713/README.md
