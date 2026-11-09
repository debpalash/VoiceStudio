# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| Latest 0.5.x release (and `main`) | ✅ All fixes, including security fixes, land here |
| Older releases, including final Tauri builds (≤ 0.5.3) | ❌ No backports — upgrade ([Tauri migration guide](../docs/electron-migration.md)) |

## Model supply chain

VoiceStudio supports models from **public, verifiable sources only** (Hugging
Face repos, official project releases). Privately sold or gated model files
are not supported: an archive from a private source can carry anything
(bundled executables, modified configs), and nobody else can verify or
reproduce it. Treat any privately distributed model file as an untrusted
download, and never run executables bundled with model archives.

## Reporting a Vulnerability

**Please do not open a public issue for security vulnerabilities.**

Instead, report them privately via one of these channels:

1. **GitHub Security Advisories** (preferred) — [Report a vulnerability](https://github.com/debpalash/VoiceStudio/security/advisories/new)
2. **Email** — Send details to **security@voicestudio.sh**

### What to include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if you have one)

### Response timeline

| Step | Timeline |
|------|----------|
| Acknowledgment | Within **48 hours** |
| Initial assessment | Within **5 business days** |
| Fix or mitigation | Within **30 days** (critical), **90 days** (non-critical) |
| Public disclosure | After fix is released, coordinated with reporter |

### Scope

VoiceStudio runs **100% locally** by default. The primary attack surface is:

- **Network exposure** — if the user binds to `0.0.0.0` without a reverse proxy
- **Model downloads** — fetched from Hugging Face Hub over HTTPS
- **Dependency supply chain** — Python/npm packages
- **File handling** — audio/video uploads processed by FFmpeg, torchaudio, etc.

### Out of scope

- Vulnerabilities in upstream dependencies (PyTorch, FFmpeg, etc.) — report those upstream
- Issues requiring physical access to the machine
- Social engineering attacks

## Automated scanning

Every pull request and push to `main` runs [`.github/workflows/security.yml`](workflows/security.yml):

- **gitleaks** — secret scanning (fails CI on a leaked credential)
- **CodeQL** — Python + JavaScript/TypeScript static analysis → Security tab
- **bandit** — Python static analysis → Security tab
- **pip-audit** / **bun audit** — dependency advisory checks (reporting)

Pull requests are additionally reviewed by the **CodeRabbit** and **Greptile**
GitHub Apps on creation.

## Source archives

GitHub source ZIPs and tarballs omit `tests/test_scam_guard.py` through
`.gitattributes` (`export-ignore`). This repository-only moderation test contains
copied scam commands and encoded-command examples that can trigger antivirus
heuristics when distributed as source text. Git clones and CI retain the test
and its full coverage; the moderation script and application/build files remain
in source archives. Clone the repository to run the complete test suite.

This packaging precaution does not establish which file caused an antivirus
verdict or guarantee a clean scan. Confirm any reported detection against the
specific file inside the archive and rescan a newly generated archive after a
change.

### Manual VirusTotal check

Run `make virustotal` to create and upload the **committed HEAD** source ZIP,
using the same archive exclusions as GitHub. Commit changes before checking a
future source download. To check an existing ZIP, installer, or individual
public script, use `make virustotal FILE=/path/to/file` instead. Local edits and
untracked files are not included in the default archive.

For a remote scan, set the repository Actions secret `VIRUSTOTAL_API_KEY` and
manually dispatch [the VirusTotal workflow](workflows/virustotal.yml):

```bash
gh workflow run virustotal.yml --repo debpalash/VoiceStudio --ref main
```

The workflow must first be merged into the default branch. It scans the
selected branch's committed source ZIP, keeps API usage serial, and adds the
report link and vendor results to the Actions run summary even if detections
fail the job. Local `make virustotal` cannot read repository Actions secrets;
it uses the local prompt/environment described below.

The command asks for your API key at a hidden prompt without saving it, or reads
`VIRUSTOTAL_API_KEY` from the environment. Obtain a key from your
[VirusTotal account](https://docs.virustotal.com/reference/getting-started);
never commit the key or paste it into an issue. Python 3.11+, Git and Make are
required (`make virustotal PYTHON=python` if that is your Python executable).

Uploads enter VirusTotal's shared dataset: use this command only for public
source/artifacts, not recordings, models, user data or private files. This is a
manual maintainer tool; the app, automatic CI and releases never invoke it. The
[Public API terms](https://docs.virustotal.com/reference/public-vs-premium-api)
limit use to 4 requests/minute and 500/day and exclude commercial products or
services and business workflows that do not contribute new files. Use an
appropriate paid licence when those terms do not cover your use.

The tool spaces requests 16 seconds apart, automatically requests the
[large-file upload URL](https://docs.virustotal.com/reference/files-upload-url)
above 32 MiB, and streams files up to 650 MiB.
VirusTotal's legacy HTTP-form `www.virustotal.com/_ah/upload/` URLs are upgraded
locally to HTTPS before uploading; credentials and files are never sent over
HTTP. Other origins and redirects remain rejected. It prints the SHA-256, report URL,
analysis ID and flagged vendor labels, and waits up to 10 minutes for results.
Exit status is 0 for a completed analysis with no flags, 1 for malicious or
suspicious detections, and 2 for errors or an inconclusive/pending analysis.
Timeouts and unsupported engines are not evidence that a file is safe.
For upload-only use, run `python3 scripts/virustotal.py FILE --no-wait`; its
successful exit means the upload was accepted, not that a clean scan completed.

## Security Best Practices for Users

- **Do not expose VoiceStudio beyond loopback without authentication.** The API is unauthenticated on loopback by default. Before remote access, set `OMNIVOICE_API_KEY` (or enable the in-app share PIN for LAN guests) as described in [API authentication](../docs/api-auth.md), and use TLS through a reverse proxy or a private network such as Tailscale.
- **Browser access is limited to VoiceStudio's own interface.** Other websites cannot send requests to the local API, and requests must address it by `localhost`, an IP address or a configured host name (`OMNIVOICE_ALLOWED_HOSTS`); see [API authentication](../docs/api-auth.md#requests-from-other-websites-and-host-names).
- **URL imports fetch public addresses only.** Set `OMNIVOICE_ALLOW_PRIVATE_URL_IMPORTS=1` only if you need to import from a server on your own network.
- **Keep your installation updated.** The desktop app checks for updates via the built-in updater, downloads and verifies them in the background (Settings → Updates can turn that off), and installs them on restart or quit.
- **Review model sources.** Only download models from trusted Hugging Face repositories.
