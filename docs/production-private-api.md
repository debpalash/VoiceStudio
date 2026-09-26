# Production private API service

For a private application backend calling VoiceStudio, run the versioned Docker
image as an internal service. Do not expose port 3900 directly to the public
internet.

## Recommended baseline

```yaml
services:
  voicestudio:
    image: ghcr.io/debpalash/omnivoice-studio:0.5.2
    restart: unless-stopped
    environment:
      OMNIVOICE_API_KEY: ${OMNIVOICE_API_KEY:?set a long random key}
      OMNIVOICE_BIND_HOST: 0.0.0.0
      OMNIVOICE_DATA_DIR: /app/omnivoice_data
    ports:
      - "127.0.0.1:3900:3900"
    volumes:
      - voicestudio-data:/app/omnivoice_data
      - voicestudio-models:/app/omnivoice_data/huggingface
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:3900/health"]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 120s

volumes:
  voicestudio-data:
  voicestudio-models:
```

Generate `OMNIVOICE_API_KEY` with a password manager or
`python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`. Keep it in the
deployment platform's secret store, not in the Compose file or source control.
Send it from InterviewAce as `Authorization: Bearer <key>`.

`OMNIVOICE_BIND_HOST=0.0.0.0` is required inside the container; the host-side
`127.0.0.1` port binding still prevents LAN or public access.
`OMNIVOICE_DATA_DIR=/app/omnivoice_data` keeps application state on the named
volume across container recreation.

Pin an exact release tag. `:latest` and `:main` are rolling previews;
`:stable` moves whenever a stable release is published. AMD hosts use the
matching `:0.5.2-rocm` image and the device mapping documented in
[Docker installation](install/docker.md#pull-and-run-amd-gpu--rocm).

## Network boundary

Prefer a private container network with no published VoiceStudio port when the
calling backend runs in the same Compose or Kubernetes deployment. Otherwise,
keep the published port on `127.0.0.1` when a reverse proxy runs on the same
host. For cross-host access through Tailscale or another encrypted overlay,
publish port 3900 only on the host's private-overlay address, or attach both
services to a private container network, and restrict it with the host firewall.
Plain HTTP is appropriate only across loopback or an isolated container
network; a Bearer key must not cross a host or network in plaintext. The
six-digit share PIN is intended for casual LAN access; use the API key for an
application service.

If a reverse proxy is used:

- forward the `Authorization` header;
- disable response buffering for streaming generation;
- set its read timeout above VoiceStudio's generation timeout;
- discard client-supplied `Forwarded` and `X-Forwarded-*` headers, then set
  forwarding headers from the proxy's observed connection;
- configure VoiceStudio/Uvicorn to trust forwarding headers only from that
  proxy's exact address. Never make authorization decisions from an untrusted
  forwarded address: a same-host proxy otherwise lets a remote caller appear
  loopback-local and bypass the key gate;
- set `OMNIVOICE_ALLOWED_ORIGINS` only when a browser on another origin must
  call VoiceStudio directly.

For example, if the proxy has the fixed container address `172.30.0.2`, add
this to VoiceStudio's environment:

```yaml
FORWARDED_ALLOW_IPS: 172.30.0.2
```

Assign that address with a Compose network `ipam` block or the equivalent
orchestrator network policy. `FORWARDED_ALLOW_IPS=*`, a subnet, and a mutable
service-name lookup are not equivalent to trusting the proxy's exact address.
Configure the proxy itself to clear inbound `Forwarded`, `X-Forwarded-For`,
`X-Forwarded-Host`, and `X-Forwarded-Proto` before setting fresh values. Without
both halves, keep proxy-header trust disabled; a spoofed forwarded loopback
address can otherwise receive loopback privileges.

InterviewAce's browser should normally call the InterviewAce backend, which
then calls VoiceStudio. This keeps the VoiceStudio credential and API surface
out of the customer browser.

## Validate the integration

After selecting and installing an engine, exercise both OpenAI-compatible
paths through the same authenticated network route InterviewAce will use:

```bash
curl https://voicestudio.internal/v1/audio/speech \
  -H "Authorization: Bearer $OMNIVOICE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"<resolved-tts-engine-id>","voice":"alloy","input":"Production check.","response_format":"wav"}' \
  --output check.wav

curl https://voicestudio.internal/v1/audio/transcriptions \
  -H "Authorization: Bearer $OMNIVOICE_API_KEY" \
  -F "file=@check.wav" \
  -F "model=<resolved-asr-engine-id>"
```

Use the actual selected engine id instead of an alias when validating routing.
More SDK and authentication examples are in [API authentication](api-auth.md).

## Operations

- Persist both `/app/omnivoice_data` and the Hugging Face cache. Back up the
  data volume; treat the model cache as replaceable unless download time is
  operationally significant.
- Before admitting traffic, list the selected engine's checkpoint with
  `GET /models`, pre-fetch its `repo_id` with authenticated
  `POST /models/install`, and wait for `/setup/download-stream` to finish.
  Lazy first-request downloads can exceed an otherwise healthy request
  timeout. Then warm the engine with a representative request. `/engines`
  reports availability and the resolved execution device; `/health` proves
  service readiness.
- Keep request concurrency bounded. A capacity response is a backpressure
  signal; honor `Retry-After` instead of starting parallel retries.
- Drain callers, recreate the container with a newly pinned tag, run the engine
  checks, then restore traffic. Do not update model/runtime dependencies inside
  a running production container.
- For a failed benchmark or production request, capture `/system/info`, the
  selected `/engines` entry, response routing headers, container logs, and a
  diagnostic bundle before restarting.

Rotate the root key as a coordinated deployment: drain requests, update the
secret store, recreate VoiceStudio with the new key, update InterviewAce's
secret, validate an authenticated request through InterviewAce, then restore
traffic. The service accepts one configured root key, so changing only one side
temporarily produces `401 Unauthorized`.

The API key also authorizes server-mode administration. If the calling
application should have consumption access only, use a separate VoiceStudio
instance or a route-aware reverse proxy with a default-deny allowlist limited
to the required speech routes. L3/L4 network policy alone cannot distinguish
generation from administration. The current API key is a root credential, not
a per-route service token. Full credential, session, WebSocket,
trusted-network, admin-route, and CORS behavior is in
[API authentication](api-auth.md).

## Engine and model obligations

Deploying VoiceStudio does not settle the licenses of optional engines or model
weights. Record the exact engine, model revision, and accepted terms alongside
the deployment, and review generated-output restrictions for that combination.
See [Engine licences](engines/index.md) before enabling an engine.
