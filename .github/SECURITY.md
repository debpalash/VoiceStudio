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

## Security Best Practices for Users

- **Do not expose VoiceStudio beyond loopback without authentication.** The API is unauthenticated on loopback by default. Before remote access, set `OMNIVOICE_API_KEY` (or enable the in-app share PIN for LAN guests) as described in [API authentication](../docs/api-auth.md), and use TLS through a reverse proxy or a private network such as Tailscale.
- **Browser access is limited to VoiceStudio's own interface.** Other websites cannot send requests to the local API, and requests must address it by `localhost`, an IP address or a configured host name (`OMNIVOICE_ALLOWED_HOSTS`); see [API authentication](../docs/api-auth.md#requests-from-other-websites-and-host-names).
- **URL imports fetch public addresses only.** Set `OMNIVOICE_ALLOW_PRIVATE_URL_IMPORTS=1` only if you need to import from a server on your own network.
- **Keep your installation updated.** The desktop app auto-checks for updates via the built-in updater.
- **Review model sources.** Only download models from trusted Hugging Face repositories.
