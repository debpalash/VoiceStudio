# Store publishing

The shared publishing foundation keeps identity, disclosures, distribution
policy, assets and artifact provenance consistent across stores. It does not
publish or reserve listings.

Use the platform runbooks below for packaging and submission. Start changes
from freshly fetched `origin/main`; the original foundation-branch handover
is retired.

## Canonical inputs

- `store/metadata.json`: product identity, listing copy, requirements,
  permissions and store categories.
- `store/privacy.json`: local data and network transmission disclosures.
- `store/compliance.json`: bundled and downloaded component review ledger.
- `store/assets.json`: canonical icon and screenshot sources.
- `config/distributions.mjs`: immutable runtime policy per distribution.

Run the shared checks before any platform packaging work:

```bash
node scripts/validate_store_foundation.mjs
```

Platform workers must also run the validator with their target, for example
`--distribution=mac-app-store`. A failure is a submission blocker, not a reason
to weaken the shared policy.

## Platform handoffs

| Worker  | Target            | Starting package         | Update owner  | First blocking work                                                                                           |
| ------- | ----------------- | ------------------------ | ------------- | ------------------------------------------------------------------------------------------------------------- |
| Windows | `microsoft-store` | Existing signed NSIS EXE | VoiceStudio   | Run the signed package workflow, reserve Partner Center identity, submit privately and complete certification |
| Linux   | `snap-store`      | New confined Snap        | Snap Store    | Confinement interfaces, writable paths, runtime installation and store review                                 |
| macOS   | `mac-app-store`   | New sandboxed MAS app    | Mac App Store | Entitlements, sandbox file access, bundled executable/runtime licensing and StoreKit receipt behavior         |

The Linux implementation and evidence checklist live in the
[`Snap Store runbook`](store-publishing/snap-store.md).

Windows reuses the signed installer and updater contract; Linux introduces
confinement; macOS requires sandboxed packaging with bundled executables.

Each worker owns only its package configuration, platform CI, store-specific
assets and submission runbook. Changes to shared identity, privacy,
capabilities or compliance records must update the canonical files rather than
being copied into a platform-only document. Electron remains the only UI and
desktop shell; no Tauri path may be restored.

The Microsoft Store packaging and submission handoff is implemented in
`.github/workflows/microsoft-store.yml`; operating steps and pending owner work
are in [`store/microsoft-store.md`](store/microsoft-store.md).

The Mac App Store sandbox target and private packaging workflow are scaffolded
in `electron/electron-builder.config.mjs` and `.github/workflows/mac-app-store.yml`.
The workflow remains blocked by the bundled-runtime compliance gate and never
submits to App Store Connect. Current blockers and owner actions are in the
[`Mac App Store runbook`](../store/mac-app-store.md).

## Artifact and release contract

Packaging workflows write `store-artifact-manifest-<platform>-<arch>.json`
beside the installer. It records the source version and commit, distribution
policy, compliance-manifest digest, artifact sizes and SHA-256 digests. A store
worker must generate it with its explicit distribution target and add the
platform signing verification result before submission.

Use the same lifecycle everywhere:

`built -> validated -> submitted -> approved -> scheduled -> published -> verified`

Only the owner supplies or authorizes store accounts, legal agreements,
payment/tax details, signing credentials, age ratings and final publication.
Never bump the application version, submit an artifact or publish a listing
without that approval. Store channels become maintained release channels only
after their first verified production publication.

## Open foundation blockers

- Record the exact bundled `uv` binary digest and upstream notices.
- Resolve the locked Python/runtime redistribution ledger before bundling it.
- Keep default model weights outside store packages unless every model license
  is separately approved.
- The Mac App Store target remains blocked until every executable component is
  bundled and approved; runtime code downloads cannot be enabled there.
