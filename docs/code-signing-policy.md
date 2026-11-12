# Code signing policy

VoiceStudio is free and open source software, licensed under the
[AGPL-3.0](../LICENSE). Windows installers are signed through the SignPath
Foundation's free programme for open source projects once it is enabled for
this repository.

> Free code signing provided by [SignPath.io](https://signpath.io), certificate
> by [SignPath Foundation](https://signpath.org).

Status: the application to SignPath Foundation is pending. Until a release says
otherwise in its **Download** table, Windows installers are unsigned
and Windows SmartScreen shows a warning.

## Project

| | |
|---|---|
| Project | VoiceStudio |
| Repository | <https://github.com/debpalash/VoiceStudio> |
| Licence | AGPL-3.0 (see [LICENSE](../LICENSE)) |
| Product name in signed files | VoiceStudio |
| Downloads | <https://github.com/debpalash/VoiceStudio/releases/latest> |
| Description | Local voice cloning, voice design, video dubbing and dictation. Runs on the user's own machine. |

## What is signed

Signed: the Windows installers (`VoiceStudio-<version>-win-x64.exe` and
`VoiceStudio-<version>-win-arm64.exe`) produced by the release workflow
[`electron-release.yml`](../.github/workflows/electron-release.yml) from a tagged
commit of this repository. Only artifacts built by that workflow from the
repository source are submitted; nothing is signed from a developer machine.

Not signed by this programme:

- Third-party binaries that are not built from this repository: the Python
  interpreter and Python packages (PyTorch, NumPy, ONNX Runtime and others) that
  VoiceStudio downloads at first-run setup, ffmpeg, and model weights. Their
  publishers sign them, or do not. Windows Smart App Control can still block
  unsigned third-party files; see
  [troubleshooting](install/troubleshooting.md).
- The Electron runtime files and the application executable inside the installer
  are packaged by electron-builder; the programme signs the installer file.
- Linux and macOS builds (macOS uses Apple Developer ID and notarization).

## Team roles

All members use multi-factor authentication on GitHub and on SignPath.

| Role | Who | Responsibility |
|---|---|---|
| Authors / committers | [@debpalash](https://github.com/debpalash) and other maintainers with write access | Change the source. |
| Reviewers | [@debpalash](https://github.com/debpalash) | Review every change from contributors without write access (pull requests; the required checks apply, and a pull request merges only once they pass). |
| Approvers | [@debpalash](https://github.com/debpalash) | Approve each signing request in SignPath, after checking the request's origin (this repository, the release workflow, an exact version tag, and its commit) and the uploaded unsigned artifact `signpath-unsigned-win32-<arch>` of that workflow run. |

<!-- OWNER: add further maintainers here when they get write access, and list
them under the right role. Every listed person needs MFA. -->

Each signing request needs a manual approval. Release builds run only from
exact version tags (`vX.Y.Z` or `vX.Y.Z-beta.N`) in this repository.

## Privacy policy

VoiceStudio runs on your computer. Nothing leaves the machine without your
explicit yes, and the app stays fully usable with everything declined. The
signed installer adds no network behaviour. The optional network calls the
program makes, all started or consented to by the user, are listed in the
[Quality gates section of the contributing guide](../.github/CONTRIBUTING.md#quality-gates-your-pr-must-pass):

- First-run setup the user starts: the pinned ffmpeg download from GitHub,
  Python and package downloads from their locked URLs or the chosen mirror,
  and Hugging Face model downloads.
- Update checks of packaged builds against GitHub Releases, and the background
  download of a found update from that same release. Both are on by default
  (an owner-approved exception to the local-first rule). Settings, Updates,
  "Download updates automatically" turns the background download off, so
  downloads wait for the user.
- Product analytics, off unless the user says yes at the first-run prompt;
  allowlisted, content-free metadata only.
- Account features, off by default and stopped at once when switched off.
- The GitHub star badge, which fetches the public repository star count while
  shown and refreshes it every 20 minutes while visible, with no credentials or
  referrer. It sends no project, recording, or account data and keeps the last
  count on failure (an owner-approved exception to the local-first rule).

Apart from the update check above, the program does not transfer information to
networked systems unless the user or the installer requests it. Third-party components keep their own
privacy policies (for example Hugging Face and GitHub when you download from
them). More detail: [Electron privacy](electron-privacy.md).

## Reporting a problem with a signed file

Report suspected misuse of the certificate, or any vulnerability in a signed
release, through the private channel in [SECURITY.md](../.github/SECURITY.md),
not a public issue. Other problems with a signed file can go to
[GitHub Issues](https://github.com/debpalash/VoiceStudio/issues). We will
cooperate with SignPath Foundation on any investigation.

## Owner checklist: enabling SignPath signing

Only the owner can do these steps. Until they are done the release workflow
signs nothing and behaves as before (`allow_unsigned` rules unchanged).

1. Apply at <https://signpath.org/apply> as the project owner. Expect questions
   on reputation, licence and the Pro build (see the open points below).
2. After approval, in SignPath: add the GitHub.com trusted build system to the
   organization and link it to the project. Create the project, a signing
   policy (for example `release-signing`, manual approval required) and an
   artifact configuration. The workflow uploads a ZIP with the installers at its
   root, so the configuration is:

   ```xml
   <artifact-configuration xmlns="http://signpath.io/artifact-configuration/v1">
     <zip-file>
       <pe-file path="VoiceStudio-*.exe">
         <authenticode-sign/>
       </pe-file>
     </zip-file>
   </artifact-configuration>
   ```

   Enforce the metadata rule: product name `VoiceStudio`, and one product
   version for all signed files within a build. Set the policy origin to the GitHub trusted build system and
   restrict it to this repository's release workflow.
3. In GitHub, Settings, Secrets and variables, Actions:
   - Secret `SIGNPATH_API_TOKEN`: token of a SignPath user with submitter
     permission on the project and policy.
   - Variables `SIGNPATH_ORGANIZATION_ID`, `SIGNPATH_PROJECT_SLUG`,
     `SIGNPATH_SIGNING_POLICY_SLUG`.
   - Leave `ELECTRON_WINDOWS_CSC_LINK` unset: an own certificate takes
     precedence and disables the SignPath path.
   - Optional: a second policy with the slug `test-signing` (test certificate)
     lets a [release rehearsal](RELEASING.md#rehearse-a-release) dispatched with
     `signpath_policy=test-signing` exercise this path from a branch. Rehearsals
     never submit to the release policy.
4. Test run: dispatch **Electron desktop release** with `release_tag` set to an
   existing tag, `publish=false`. Approve the signing request in SignPath. The
   Windows legs should pass "Sign the Windows installer with SignPath",
   "Replace the installer with the signed one and refresh the update feed" and
   the signature verification.
5. Verify the downloaded workflow artifact `electron-release-win32-x64` on
   Windows (a `publish=false` run publishes nothing, so do not test an in-app
   update against GitHub Releases yet; it would use old assets):
   `Get-AuthenticodeSignature` on the installer is `Valid` and names SignPath
   Foundation; the `electron-*-win32-x64.yml` sha512 (base64) equals the
   installer's SHA-512 and its size equals the file size; the `.blockmap` sits
   next to it. Installing it by hand is a useful extra check.
6. Publish normally (for example the next beta). Check that the release notes'
   Download table shows Windows as signed, and that README and the
   download page link here (SignPath requires the words "Code signing policy"
   on both).
7. After that first signed publish, run the real in-app update test: update an
   older installed build to the signed release (differential and full download)
   and confirm it applies.

Open points to settle with SignPath before applying:

- The README advertises a closed-source Pro build and a commercial licence.
  SignPath's conditions require no proprietary code from maintainers and no
  commercial dual-licensing for the signed project; confirm the Pro build is a
  separate product and that the signed installers are the AGPL ones only.
- Unsigned third-party files inside or downloaded by the app are allowed in
  signed installers today, but SignPath says future rules may require all
  included files to be signed.
