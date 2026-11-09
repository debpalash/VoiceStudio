# Microsoft Store publishing runbook

Status: implementation complete; Partner Center identity, signed CI validation
and certification remain pending.

- Branch: `feat/microsoft-store`
- Base: `feat/store-publishing-foundation` at `e8224990`
- Final commit: branch head recorded in the merged pull request

## Package contract

VoiceStudio uses Microsoft's unpackaged Win32 EXE path. The Store package is a
separately named build of the existing NSIS installer:

`VoiceStudio-MicrosoftStore-<version>-win-<arch>.exe`

It embeds `VOICESTUDIO_DISTRIBUTION=microsoft-store`, remains a standalone
installer, accepts the silent `/S` switch and keeps VoiceStudio's application
updater enabled. Direct installers, names and update feeds are unchanged.

Microsoft requires a versioned HTTPS package URL that is not replaced after
submission, a silent offline installer, and trusted Authenticode signatures on
the installer and installed PE files. Partner Center submission also requires
at least one screenshot and 1:1 box art. Current requirements:

- [MSI/EXE package upload](https://learn.microsoft.com/windows/apps/publish/publish-your-app/msi/upload-app-packages)
- [Manual package validation](https://learn.microsoft.com/windows/apps/publish/publish-your-app/msi/manual-package-validation)
- [MSI/EXE listing assets](https://learn.microsoft.com/windows/apps/publish/publish-your-app/msi/screenshots-and-images)

## Owner prerequisites

Before staging a package URL or submitting:

1. Reserve `VoiceStudio` as an EXE/MSI product in Partner Center.
2. Confirm the seller/publisher identity is `Yupcha Softwares Private Limited`.
3. Add `ELECTRON_WINDOWS_CSC_LINK` and
   `ELECTRON_WINDOWS_CSC_KEY_PASSWORD` as repository secrets. The certificate
   must chain to a CA in the Microsoft Trusted Root Program and its subject must
   match the legal publisher.
4. Approve the age rating, generative-AI and voice-cloning declarations,
   territories, pricing and publication timing.

Never place credentials or exported certificates in the repository or workflow
artifacts.

## Build and validate

Run the `Microsoft Store package` workflow. Leave `stage_package_urls` disabled
while iterating. `release_tag` may be empty for branch validation; production
staging requires the exact `v<package-version>` tag.

For x64 and experimental ARM64, the workflow:

1. validates the shared `microsoft-store` policy;
2. builds the existing NSIS target with the Store distribution identity;
3. verifies the installer and every packaged `.exe`, `.dll` and `.node`
   Authenticode signature;
4. checks the installer signer and Add/Remove Programs publisher against the
   canonical legal publisher;
5. performs a silent clean install, launches the installed setup screen and
   silently uninstalls it;
6. on x64, installs the previous direct release and upgrades it in place to the
   Store build, confirming one current uninstall entry and a working launch;
7. writes provenance and Authenticode evidence;
8. assembles canonical metadata, box art and screenshots into the Partner
   Center handoff artifact.

Download `microsoft-store-partner-center-handoff` and retain:

- `microsoft-store-submission.json`;
- `store-artifact-manifest-win32-<arch>.json`;
- `authenticode-win32-<arch>.json`;
- the signed EXE packages and `listing/` assets.

The ARM64 leg is experimental and does not block x64. Do not offer ARM64 in
Partner Center unless that leg passes for the submitted version.

## Stage and submit

After the matching GitHub release exists, the repository owner may rerun the
workflow with the exact tag and `stage_package_urls=true`. The workflow adds
the Store-named installers to that release and refuses to overwrite an existing
asset. Confirm each URL in `microsoft-store-submission.json` downloads bytes
matching its SHA-256 digest.

In Partner Center:

1. select the EXE package type and the matching x64/ARM64 architecture;
2. enter the immutable package URL and `/S` installer parameter;
3. use `store/metadata.json`, `store/privacy.json` and the generated listing
   assets rather than maintaining separate copy;
4. complete the permissions, AI, voice-cloning, age-rating and privacy answers
   with owner approval;
5. submit privately and record the submission ID and reviewer findings here.

No repository workflow submits or publishes the Partner Center listing.

## Acceptance and rollback

Before production publication, manually verify clean install, upgrade from the
previous direct release, first-run runtime setup, application launch, in-app
update, silent uninstall and the single Add/Remove Programs entry on supported
Windows versions. Record the certification result and installed package URL.

For a failed submission, fix the branch and stage a new higher version after an
owner-approved version bump; never replace the certified URL's bytes. Before
publication, withdraw the submission in Partner Center. After publication,
remove availability or delist it there while leaving the immutable installer
URL and direct-download release intact.

Lifecycle: `built -> validated -> submitted -> approved -> scheduled -> published -> verified`
