# Preserve audio.cpp installations across publication failures

## Context
Issue #2685 reports Windows access denial while publishing the prepared audio.cpp runtime after its device probe. The installer deletes an existing target before a bare rename, so failed publication can also discard a previous installation. Bundled media tools already use bounded lock retry and rollback-aware directory publication.

## Decision
Reuse the existing media-tools directory publisher after archive validation and successful probing. Import it only at publication so ordinary installer status checks remain independent of media-tool initialization. Keep the same user-triggered action, pinned downloads, checksums, probe and bootstrap invalidation on success.

## Alternatives
A second retry loop duplicates policy without preserving old data. Retrying only os.replace after deleting the target still loses the old installation on permanent failure. Moving the shared helper into a new utility module would broaden this repair unnecessarily.

## Consequences
Transient filesystem access errors may recover within the existing bounded delay schedule. Permanent publication failure restores the old directory; if rollback is also blocked, the established publisher retains its backup and reports recovery information. This does not stop a running native server or prove Windows behavior. The installer now depends on a private helper already used for bundled media tools; future helper changes must retain both callers' directory contract.

## Validation
Frozen upstream c2ef99817fc4f2e0271a223c357e57e8df93cbde with the same new tests: two failures and six passing controls. Actual owned temporary directories reproduce transient final-rename denial and permanent publication failure without downloading or executing a native binary. Candidate installer suite: eight passed. Installer suite plus four existing shared publisher controls (non-lock errors, restoration, failed rollback backup retention, cleanup denial): twelve passed with one installed Starlette/httpx deprecation warning. Whitespace check passed.

The normal pytest collection initially failed because the engine package initializer imports unavailable Torch. Tests were run through a retained local harness that substitutes only the engines.audiocpp package shell and loads the real bootstrap, installer and media-tools implementations unchanged. This is scoped isolated filesystem evidence, not a passing full backend suite or native engine integration. Broader collection initially lacked FastAPI; a lightweight local FastAPI/httpx environment enabled the selected existing publisher checks. No Windows run, Electron interaction, live user data, model/provider call, downloaded executable or full suite was performed. Empty owned HF cache and offline mode were enforced. Existing CLA 1.0 signature was verified; no new legal acceptance was submitted.
