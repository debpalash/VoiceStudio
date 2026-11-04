# Install doctor: every install failure names itself

Status: draft for owner review · 2026-11-04 · branch `docs/install-doctor-design`

## Why

CLAUDE.md's core value is a first run that works. We can't currently tell why it
doesn't, or how often.

Evidence from the 150 most recent issues (2026-10-24 to 2026-11-04, grouped by
title, so counts are approximate):

| Failure | Issues (open) |
|---|---|
| `spawn UNKNOWN` on Windows | 27 (18) |
| Backend not responding or timing out | 7 (1) |
| Native crash (`0xC0000005`, `0xC000001D`) | 6 (3) |
| Torch download stalls or fails | about 6 (1+) |
| AMD/ROCm setup | 6 (4) |
| Backend exits with code 1 | 6 (1) |
| Source checkout environment missing or incomplete | 5 (2) |

Why these reports can't be acted on:

- **Reporters don't come back.** Of the 27 `spawn UNKNOWN` reporters, 1 wrote
  anything of their own and 0 replied to follow-up questions. Seven were closed
  as not planned. Any design that depends on asking the user for more fails.
- **The reports lack the deciding facts.** None of the 27 names the refused
  program. Smart App Control state is not a report field. Only #3635 confirms a
  cause.
- **The reports contain a wrong fact.** All 27 say `Managed: false` and
  `attached local`. `start()` sets `managed: false` while attaching, and the
  spawn failure paths (`electron/src/main/backend.ts:1831` and `:1859`) never
  correct it, so this is still wrong on `main`.
- **The failure rate is invisible.** The renderer sends only `screen_viewed` and
  `workflow_action`. `app_installed` comes from the Python backend, and consent
  is stored in the backend and asked in `SetupGate`, after the backend is up. An
  install that fails has never been asked and has nothing to send with.
- **Failures surface late.** Smart App Control is read only after a refusal, so
  a blocked user can download 3 GB first.

## Goals

1. Every install or startup failure carries a stable code and the facts needed
   to act on it, with no reporter effort beyond clicking Report.
2. Known failures show their fix before the user thinks about filing an issue.
3. Blocks that can be detected early are detected before the large download.
4. With consent, we can see setup success and failure rates by code and platform.
5. A new failure class can't be added without its fix text, docs and regression test.

## Non-goals

These are separate decisions, to be made with the data this work produces:

- Signing the Python runtime or shipping a prebuilt signed runtime (#1779, #3635).
- An approved mirror for the PyTorch index (#3720).
- Fixing the native crashes themselves (#3772, #3684). This work only classifies them.
- Mobile apps. Their first-run path doesn't use this runtime.
- A bot that labels or answers issues by code. The marker in Part 2 makes it
  possible later.

## Anti-goals

This has failed if any of these become true:

- Any fact other than the failure code and the allowlisted metadata leaves the
  machine without the user clicking Report and submitting from their own browser.
- A healthy start gets slower. Evidence collection runs only after a failure,
  except the checks in Part 4.
- Reporting is hidden or blocked behind a suggested fix.
- A refused launch triggers a reinstall, or an existing runtime gets replaced.
- Setup is blocked on a guess: a Smart App Control warning must let the user continue.
- The classifier states a cause it has no evidence for. `UNCLASSIFIED` is a valid answer.

## Prerequisite: ship what's already on main

v0.5.7 was tagged 2026-10-26 and `main` is 897 commits ahead with no beta since.
The fixes that name the refused program, detect Smart App Control and speed up
large downloads are all unreleased. Cut the patch first, through the normal
`docs/RELEASING.md` process, so this design is built against what users run.

## Design

Five parts, each shippable alone, in this order, with the regression guards
below built alongside them.

### Part 1: failure record and classifier

Electron main owns one classifier and one record, and needs no Python. Add
`failure?: SetupFailure` to `BackendStatus`, set wherever a failing stage is set
(`failed`, `crashed`, `port_in_use`, `unresponsive`, and `setup_required` with a
message).

**Record fields.** Each is optional and `unknown` is valid:

| Group | Fields |
|---|---|
| Identity | code, app version, build, install channel, OS, arch |
| Where | stage, setup phase, elapsed seconds |
| Launch | program basename, program location (`runtime`, `bundled`, `custom`), error code, syscall, exit code, signal |
| Windows trust | Smart App Control state, latest CodeIntegrity 3077 entries (scrubbed file name, policy), Authenticode status of the program, installed antivirus names |
| macOS and Linux trust | quarantine attribute on the program, `noexec` on the runtime mount |
| Download | host, bytes received, seconds since last progress, error class, proxy set (yes/no), region |
| Host | free disk, Python and uv present, path flags |

Collection runs once per failure, in parallel, with a shared deadline of about
3 s. It runs after the failure stage is set and updates the status when it
finishes, so a slow or blocked PowerShell can't delay the error screen. Reading
the CodeIntegrity log may need rights the user lacks; that yields `unknown`.

**Codes.** Reuse the existing taxonomy where a class exists, and follow the
mirror contract in `backend/core/error_docs_map.py` and
`electron/src/shared/utils/errorDocsMap.ts` for new ones. The existing `setupIssue` values
keep working and map onto codes.

| Code | Evidence required | From |
|---|---|---|
| `WINDOWS_APP_CONTROL_BLOCKED` (exists) | Refused launch or load block, and Smart App Control on/evaluation or a 3077 entry | #3635 |
| `ANTIVIRUS_BLOCKED` | `windowsLoadBlock` says antivirus | |
| `EXEC_REFUSED` | Refused launch with no evidence of who refused | most of the 27 |
| `GATEKEEPER_QUARANTINE` (exists) | Refused launch and quarantine attribute | |
| `EXEC_NOEXEC_MOUNT` | Refused launch and `noexec` mount | |
| `RUNTIME_DOWNLOAD_STALLED` | No progress past the stall limit | #3748 |
| `RUNTIME_DOWNLOAD_INTERRUPTED` | Connection or TLS error during sync | #3720 |
| `DISK_FULL` (exists) | `ENOSPC` | |
| `RUNTIME_FOLDER_DENIED` | Existing `access` issue | |
| `RUNTIME_FILES_LOCKED` | Existing `files_locked` issue | |
| `RUNTIME_IN_SYNCED_FOLDER` | Path inside a cloud-synced folder | |
| `SYSTEM_LIBRARY_PROBLEM` | Existing missing or outdated system library issues | |
| `UNSUPPORTED_PLATFORM` | Existing `unsupported_platform` and `wrong_architecture` issues | |
| `SOURCE_ENV_INCOMPLETE` | Source checkout `.venv` missing or an import failed | #3722, #3589 |
| `BROKEN_VENV` (exists) | Managed runtime failed imports after repair | |
| `NATIVE_ACCESS_VIOLATION` | Exit `0xC0000005` or `SIGSEGV` | #3772 |
| `NATIVE_ILLEGAL_INSTRUCTION` | Exit `0xC000001D` or `SIGILL` | #3684 |
| `NATIVE_CRASH` | Any other native fault exit | |
| `STARTUP_TIMEOUT_SILENT` | Startup budget spent with no output | #3713 |
| `STARTUP_TIMEOUT_OUTPUT` | Startup budget spent with output | |
| `BACKEND_STARTUP_FAILED` | Exit during startup with a named step | #3612 |
| `PORT_IN_USE` | Existing `port_in_use` stage | #3704 |
| `ROCM_NOT_CONFIGURED` | AMD GPU with ROCm requested or recommended and missing | #3780, #3804 |
| `UNCLASSIFIED` | Anything else; raw message always kept | |

The regexes in `electron/src/shared/utils/backendHint.ts` move into this
classifier, so there is one place that interprets failure text. Python words some
errors in the Windows display language, so matches that can't rely on English
must use state, as `backend.ts:1932` already does.

Fix `managed` on the spawn failure paths in the same change. Those failures
belong to the managed runtime unless the launch uses `OMNIVOICE_BACKEND_CMD`.

### Part 2: reports that need no follow-up

- When a failure record exists, `report-bug.tsx` puts a compact facts block at the
  top of the body and titles the issue `[Install] <CODE>: <summary>` instead of
  `[Bug] spawn UNKNOWN`. Reports with no failure record stay as they are.
- The block has a reserved budget inside the 7000-character encoded body limit
  and is never truncated. Log tails are what get cut. Repeated identical lines
  are collapsed (#3788 quotes `spawn UNKNOWN` 40 times).
- The body carries `<!-- vs-failure:CODE:1 -->` for later search and tooling.
- `getSetupDiagnostics()` starts with the full record. Report offers **Save full
  diagnostics**, which reveals the file so it can be dragged into the issue. The
  report still works if the user ignores it.
- Everything passes through the existing scrubbers, and the user reviews it in
  the prefilled form before submitting. Antivirus names and file names stay out
  of analytics (Part 5).
- `.github/ISSUE_TEMPLATE/install_problem.yml` stops suggesting
  `python backend/main.py --diagnose`, which can't help when Python won't start,
  and points at in-app Report instead. It gains an optional failure code field.

### Part 3: the fix first

The failure screen in `backend-gate.tsx` picks its message and primary action
from the code: one line saying what happened and what to do, a docs link, and
Report still visible. `backendFailureHints` becomes a code-to-key lookup. Known
codes drop the generic "report this" prominence, but never remove it.

For `WINDOWS_APP_CONTROL_BLOCKED` the screen states all three options plainly: turn
it off (one-way without a Windows reset), run the engine on another computer, or
use WSL or Docker. It must not suggest antivirus exclusions.

### Part 4: fail early

- **Before the download.** On Windows, read Smart App Control state when the
  setup screen appears. If on or in evaluation, warn with the Part 3 options and
  an equal **Continue anyway**. `reg.exe` is already capped at 3 s; this read is
  not on the healthy-start path because it only runs when setup is needed.
- **Interpreter canary.** For managed runtimes, in `installRuntime`
  (`electron/src/main/runtime-project.ts`), after uv is available and before
  `uv sync`, install the pinned Python with uv and run it once, importing a
  stdlib extension such as `_ctypes`. No new host is contacted. A refusal raises
  `RuntimeLaunchRefusedError` before any wheel downloads and leaves files in place.
- **Limit, stated honestly.** The canary can't prove wheel binaries will load.
  The existing `verifying` phase still catches those, and they classify as
  `WINDOWS_APP_CONTROL_BLOCKED` through the load-block path.

### Part 5: consented setup funnel

**Consent moves earlier.** For a fresh install, show the existing two equal-weight
Yes/No prompt on the pre-install setup screen. Fresh means no consent file and the
renderer's setup-completed marker is absent, so a user repairing an existing runtime
is not asked again. Answering is not required to start
setup. No answer means off, and the existing `SetupGate` step still asks later.
The prompt wording in the locale catalogs says setup failure codes are included.

**Where it lives.** The backend remains the authority once reachable.

1. The renderer keeps the early answer in `localStorage`, used only until a backend
   first answers, and never again after that.
2. When the backend answers, a choice it has not recorded is sent with
   `PUT /api/settings/analytics`. If it holds a choice, the backend wins.
3. `OMNIVOICE_ANALYTICS_DISABLED` is honored by the early path through `BackendStatus`.
   Turning analytics off in Settings takes effect immediately.
4. `AnalyticsRuntime` runs only when the backend is reachable today. It gains a path
   that uses the early answer before then.

**Events**, from the existing hardened renderer client, consent-gated:
`setup_started`, `setup_failed`, `setup_completed`, `backend_first_ready`.
Properties use only keys already on the allowlist: `stage` (setup phase),
`error_class` (failure code), `app_version`, `platform`, `install_channel`. Nothing
else from the failure record is sent, and no allowlist keys are added. Each attempt
sends at most one `setup_failed` per code.

**Identity.** All funnel events come from the renderer's PostHog client, whose
anonymous id persists in `localStorage`, so one install counts once across the
backend-less and backend periods. `installation_id` is unchanged. The funnel runs only
until setup is completed.

## Regression guards

Start these in Part 1 and extend them with each part:

- **Failure corpus.** Fixtures built from real report text (#3635, #3748, #3720,
  #3772, #3684, #3713, #3589, #3722, #3704, #3780), run through the classifier with
  the expected code asserted. Each issue that reveals a new class adds a fixture.
- **Completeness test.** Every code has an English string, a docs anchor that
  exists in `docs/install/troubleshooting.md`, and an entry in the mirror where the
  mirror contract applies. Adding a code without them fails CI.
- **Report budget test.** With maximum-size logs, the facts block and marker
  survive truncation intact.
- **Privacy test.** Setup events can't carry anything beyond the allowlisted keys,
  and nothing is sent when consent is off, unanswered or kill-switched.
- **Parity.** Classifier tests run on the Windows, macOS and Linux CI legs. Tests
  use injected probes, as `host-snapshot.test.ts` does, because CI can't turn on
  Smart App Control.
- **Manual validation, required before claiming Part 4 works.** A clean Windows 11
  install with Smart App Control on: confirm the warning, the canary's refusal, the
  code and the report. Record the result in the PR.

## Constraints

- **Parity:** the record, codes, screens and funnel behave the same on all three
  desktop platforms. Only the OS probes differ.
- **Local-first:** no new network host. The funnel uses the existing opt-in PostHog
  path. `CLAUDE.md` and `.github/CONTRIBUTING.md` describe the consent timing and must
  change with Part 5.
- **Compatibility:** existing runtimes and `omnivoice_data/` stay as they are.
  Existing users keep their consent choice and aren't asked again.
- **Localization:** user-facing strings go through the locale catalogs, kept complete by
  the existing parity checks.
  `BackendStatus.message` stays English and verbatim for reports.
- **Docs-sync:** each PR updates `docs/install/troubleshooting.md` and the relevant
  install guide, and adds a `changelog.d/` fragment.

## How we'll know it worked

Measured over the first release containing Parts 1 to 3 (targets are the author's
proposal, not owner-set):

- At least 90% of in-app install reports carry a code other than `UNCLASSIFIED`.
- No new `spawn UNKNOWN` report lacks the program and Smart App Control state.
- New issues per known code fall release over release.
- After Part 5, setup completion rate is visible per platform, and the top failure
  code decides the next fix, including the non-goals above.

## Calls made by the author, for owner review

- The code names and list. Part 1 builders may split or merge where evidence demands.
- Moving the consent prompt before setup, with the backend staying the authority.
- Warning about Smart App Control but letting the user continue, since evaluation
  mode doesn't always block.
- Keeping the early answer in the renderer and leaving `installation_id` alone, instead
  of a main-process file and id. Both turned out unnecessary.
- Leaving the issue bot out of scope.

## Left to the builder

- Module names and how the record passes over IPC.
- The stall limit for `RUNTIME_DOWNLOAD_STALLED`, after checking what
  `wheel-prefetch.ts` and uv already time out on.
- The exact facts block layout and how much of it each report type shows.
- Whether Report's save action reuses the existing setup log download.
