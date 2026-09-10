# VoiceStudio — Install with Docker

For headless servers, dedicated GPUs, or "I want one command" deployments.
The docker image bundles the backend; the UI is served over HTTP and you open
it in a normal browser.

**Official images:** [`ghcr.io/debpalash/omnivoice-studio`](https://github.com/debpalash/VoiceStudio/pkgs/container/omnivoice-studio)
and [`palashdeb/omnivoice-studio` on Docker Hub](https://hub.docker.com/r/palashdeb/omnivoice-studio) — same images, same tags.

## Architecture

The published images are **`linux/amd64` (x86-64) only**, including `:stable`,
`:latest`, and the ROCm variants. There is no native `linux/arm64` image.
On an ARM64 host, pulling without an explicit platform can fail with
`no matching manifest for linux/arm64/v8 in the manifest list entries`.

- **Apple Silicon (M-series Macs):** use the [native macOS app](macos.md),
  which supports Apple GPU acceleration. The Linux container cannot access
  the Mac's Apple GPU through MPS or MLX.
- **AMD64 emulation on ARM64 (including Apple Silicon):** if your Docker
  installation supports it, place `--platform linux/amd64` **before the image
  name** in both `docker pull` and `docker run` from the CPU instructions
  below. This is an emulated CPU option, not native
  ARM64 support; inference can be much slower and is not a GPU workaround.
  Without emulation, use an AMD64 server for this Docker deployment.

## Image tags

> **Image ↔ version mapping**
>
> | Tag | What you get |
> |-----|--------------|
> | `:latest` | **Rolling preview** — latest commit on `main`, at or ahead of the last release. This is the preview channel; pin `:stable` for production. |
> | `:stable` | Most recent versioned release (updated on every `v*` git tag) |
> | `:0.5.2` | Exact release version |
> | `:0.5` | Latest patch within the 0.5 minor |
> | `:main` | Alias of the same rolling `main` build as `:latest` |
> | `:sha-xxxxxxx` | Specific commit (produced by manual workflow dispatch) |
> | `:rocm` | **AMD GPU (ROCm) build** of the rolling preview — the ROCm analogue of `:latest` |
> | `:stable-rocm`, `:0.5.2-rocm`, `:0.5-rocm`, `:sha-xxxxxxx-rocm` | ROCm builds of the corresponding CUDA tags above |
>
> Versioning rule: preview builds always come from `main` and never
> version-sort below `:stable` — upgrades flow naturally.
>
> **Note on the update-channel toggle:** The update-channel UI (Settings → About → Update channel) is part of the Tauri desktop app's built-in auto-updater. It does **not** apply to the Docker image — the Docker image is the headless web-server build. To update your Docker deployment, pull the new image tag and recreate the container (`docker compose pull && docker compose up -d`).

Docker's NAT prevents the backend from proving that a browser is on the host,
so server-mode settings and diagnostics require an administrator API key even
when the published port is loopback-only. Generate one before using any Studio
profile or `docker run` command below:

```bash
export OMNIVOICE_API_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
```

Keep that shell open until the container starts. The web UI asks for this key
and exchanges it for a short-lived browser session; it does not persist the
master key.

## Pull and run (CPU)

```bash
docker pull ghcr.io/debpalash/omnivoice-studio:latest

docker run -d --name omnivoice \
  -p 127.0.0.1:3900:3900 \
  -e OMNIVOICE_API_KEY="$OMNIVOICE_API_KEY" \
  -v omnivoice-data:/app/omnivoice_data \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  ghcr.io/debpalash/omnivoice-studio:latest
```

> **Docker Hub mirror:** the same images are published to
> `palashdeb/omnivoice-studio` on Docker Hub with identical tags — swap the
> image for `palashdeb/omnivoice-studio:latest` if you prefer Docker Hub.
> Tag semantics (`:latest` = rolling main preview, `:stable`/`:X.Y.Z` =
> releases) are the same on both registries.

Open [http://localhost:3900](http://localhost:3900). The first run downloads
~2.4 GB of model weights — follow `docker logs -f omnivoice` to watch.

## Pull and run (NVIDIA GPU)

```bash
docker run -d --name omnivoice --gpus all \
  -p 127.0.0.1:3900:3900 \
  -e OMNIVOICE_API_KEY="$OMNIVOICE_API_KEY" \
  -v omnivoice-data:/app/omnivoice_data \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  ghcr.io/debpalash/omnivoice-studio:latest
```

GPU mode requires the
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
on the host.

## Pull and run (AMD GPU / ROCm)

AMD GPUs use the dedicated **`:rocm` image variant** — the default (CUDA)
image runs CPU-only on AMD hardware. The ROCm userspace ships inside the
image; the host only needs the `amdgpu` kernel driver. Pass the GPU through
as plain device nodes (no container toolkit needed):

```bash
docker run -d --name omnivoice \
  --device /dev/kfd --device /dev/dri \
  -p 127.0.0.1:3900:3900 \
  -e OMNIVOICE_API_KEY="$OMNIVOICE_API_KEY" \
  -v omnivoice-data:/app/omnivoice_data \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  ghcr.io/debpalash/omnivoice-studio:rocm
```

### AMD GPU on WSL2

WSL exposes AMD compute through `/dev/dxg`, not native Linux's `/dev/kfd` and
`/dev/dri`. First install ROCm and `librocdxg` in the WSL distribution and
confirm the host-side `rocminfo` lists the GPU. Then use the WSL-specific
bridge flags from AMD's `librocdxg` container contract:

```bash
docker run -d --name omnivoice \
  --device /dev/dxg \
  -v /usr/lib/wsl/lib/libdxcore.so:/usr/lib/libdxcore.so \
  -v /opt/rocm/lib/librocdxg.so:/usr/lib/librocdxg.so \
  -v /opt/rocm/share/rocdxg/dids.conf:/usr/share/rocdxg/dids.conf \
  -e HSA_ENABLE_DXG_DETECTION=1 \
  --cap-add SYS_PTRACE \
  --security-opt seccomp=unconfined \
  --ipc=host --shm-size 8G \
  -p 127.0.0.1:3900:3900 \
  -e OMNIVOICE_API_KEY="$OMNIVOICE_API_KEY" \
  -v omnivoice-data:/app/omnivoice_data \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  ghcr.io/debpalash/omnivoice-studio:rocm
```

The image currently uses ROCm 7.2.x, so `HSA_ENABLE_DXG_DETECTION=1` is
required; AMD removed that requirement only in ROCk 7.13. The ptrace and
unconfined-seccomp flags weaken container isolation, so keep the published port
on `127.0.0.1` and do not run untrusted workloads in this container. See AMD's
[`librocdxg` WSL container instructions](https://github.com/ROCm/librocdxg#4-container-launch--wsl-specific-flags)
for the driver/runtime compatibility matrix.

#### WSL2 architecture compatibility matrix

VoiceStudio classifies the architecture result separately from device-node
visibility. `/dev/dxg` alone is not proof of acceleration; a supported claim
also needs the runtime probe, application routing, a completed workload, and
GPU-utilization evidence.

| Classification | Evidence required | VoiceStudio behavior |
|---|---|---|
| **Supported** | The native GFX tag is in the shipped PyTorch architecture list, and the named hardware has a published successful workload with GPU-utilization evidence. | Report the measured provider and device from Settings and diagnostics. |
| **Best-effort override** | The native tag is absent, a mapped target is present in the PyTorch build, and `HSA_OVERRIDE_GFX_VERSION` is applied. No hardware validation is implied. | Attempt the mapped kernels; capture execution evidence and treat failures as unsupported for that host. |
| **Unverified** | The bridge or override is configured, but no published end-to-end result exists for the named card and stack. | Do not advertise the card as supported; run the checks below before relying on it. |
| **Unsupported** | Neither the native tag nor a usable mapped target is present, or the runtime/workload rejects the device. | Use an intentional CPU route or a different supported accelerator. |

| Hardware / architecture | Current classification | Detail |
|---|---|---|
| AMD Radeon RX 6700 XT / `gfx1031` through WSL2 ROCDXG | **Unverified** | VoiceStudio can map `gfx1031` to `gfx1030` when that target exists in the PyTorch build, but no RX 6700 XT end-to-end validation has been published. |

For an RX 6700 XT result to move out of **Unverified**, record the Windows AMD
driver, WSL kernel/distribution, image and ROCDXG/ROCm versions,
`torch.version.hip`, device name/count and compiled architecture list, effective
HSA override, `rocminfo`, VoiceStudio self-check and engine-routing output, and
one successful PyTorch TTS and ASR workload with utilization plus cold/warm
latency. Record whether either workload fell back to CPU and, when it did, the
CPU fallback stage and reason reported by VoiceStudio. A CPU-only completion
does not qualify as successful GPU validation.

The same flags work with **Podman** (`podman run --device /dev/kfd
--device /dev/dri …`); in a **Quadlet** unit that's two `AddDevice=` lines:

```ini
# ~/.config/containers/systemd/omnivoice.container
[Container]
Image=ghcr.io/debpalash/omnivoice-studio:rocm
AddDevice=/dev/kfd
AddDevice=/dev/dri
PublishPort=127.0.0.1:3900:3900
Volume=omnivoice-data:/app/omnivoice_data
Environment=OMNIVOICE_API_KEY=replace-with-a-long-random-key
```

Release pins exist too: `:stable-rocm`, `:0.5.2-rocm`, `:0.5-rocm` mirror
the CUDA tags exactly.

> **Consumer cards and APUs (RX 6000/7000, Strix Point/Halo):** the backend
> auto-sets `HSA_OVERRIDE_GFX_VERSION` when — and only when — your card's GFX
> ID is missing from the shipped ROCm build's architecture list, so try
> without any override first. Overriding a natively-supported GPU (gfx1151 on
> ROCm 7.x, for example) only forces it onto foreign kernels. If the GPU still
> isn't used, force it explicitly with `-e HSA_OVERRIDE_GFX_VERSION=11.0.0`
> (user-set on the container — it is deliberately **not** baked into the
> image, because the right value depends on your card); a value you set is
> always respected as-is.
>
> **Rootless / non-root hosts:** if `/dev/kfd` is group-owned, the container
> user needs those groups too — add `--group-add` for your host's `render` and
> `video` GIDs (`getent group render video`).

Verify the container sees the GPU:

```bash
docker exec <container> python3 -c \
  "import torch; ok = torch.cuda.is_available(); print(ok, torch.cuda.get_device_name(0) if ok else 'unavailable')"
```

Use `omnivoice` for the `docker run` examples above. Docker Compose names the
ROCm container `omnivoice-studio-rocm` (CPU: `omnivoice-studio`, NVIDIA:
`omnivoice-studio-gpu`); `docker compose ps` shows the exact active name.

(ROCm-built PyTorch reports through `torch.cuda.*` — `True` plus your card's
name means torch can see the GPU.) That check alone isn't proof the app is
using it: **Settings → Performance & Device** shows the device VoiceStudio
actually resolved.
**Model Catalogue → Engines** should report both `omnivoice` and
`omnivoice-subprocess` as accelerated on ROCm, rather than a CPU-fallback
warning.
If it reads `cpu` while the command above prints `True`, the backend log line
starting `Falling back to CPU:` names the architecture mismatch it hit.

The image installs and launches VoiceStudio through that same `python3`
interpreter. To verify this invariant on an older or custom image, compare
`docker exec <container> python3 -c "import sys, torch; print(sys.executable,
torch.version.hip)"` with `docker exec <container> sh -c 'tr "\\0" " "
</proc/1/cmdline'`; PID 1 must begin with `python3 -m uvicorn`.

If the command prints `False`, run **Settings → About → Run self-check**;
the GPU row says why. Native Linux has three common answers:

| What it says | What to do |
|---|---|
| `/dev/kfd is not present` | The container was started without `--device /dev/kfd --device /dev/dri`, or the host's `amdgpu` driver isn't loaded. |
| `this process cannot open it` | A group problem. Run `ls -l /dev/kfd /dev/dri/render*` **on the host**, and pass those GIDs with `--group-add`. The numbers differ between machines — a `--group-add 39` copied from someone else's command grants nothing. |
| `no GPU was enumerated` | The device nodes are fine and the runtime still found nothing — usually a card newer than the image's ROCm. Check `rocminfo` on the host, and see the `HSA_OVERRIDE_GFX_VERSION` note above. |

On WSL, the self-check instead distinguishes a missing `/dev/dxg` permission,
the pre-7.13 `HSA_ENABLE_DXG_DETECTION` opt-in, and incomplete ROCDXG runtime
mounts.

## Docker Compose (recommended)

```bash
# Generate this once in the shell that runs Compose.
export OMNIVOICE_API_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"

# CPU
docker compose -f deploy/docker-compose.yml --profile cpu up -d

# NVIDIA GPU
docker compose -f deploy/docker-compose.yml --profile gpu up -d

# AMD GPU (ROCm)
docker compose -f deploy/docker-compose.yml --profile rocm up -d
```

On an ARM64 host with AMD64 emulation available, use these **CPU-only**
commands instead. Set `DOCKER_DEFAULT_PLATFORM` for both pulling and starting
the service:

```bash
DOCKER_DEFAULT_PLATFORM=linux/amd64 docker compose \
  -f deploy/docker-compose.yml --profile cpu pull
DOCKER_DEFAULT_PLATFORM=linux/amd64 docker compose \
  -f deploy/docker-compose.yml --profile cpu up -d
```

The override applies only to each command. The [architecture limitations](#architecture)
still apply; emulation does not enable GPU acceleration.

The `docker-compose.yml` shipped in `deploy/` defaults to `127.0.0.1:3900`
on the host. The backend inside the container binds to `0.0.0.0` so the
host port mapping can forward — the host-side `127.0.0.1` binding is what
enforces loopback-only.

### Worker-only GPU container

To lend a headless GPU to VoiceStudio running on another machine, generate a
join code on that control plane and start one of the worker profiles:

```bash
# NVIDIA
OMNIVOICE_WORKER_TOKEN='ovw_…' docker compose \
  -f deploy/docker-compose.yml --profile worker-gpu up -d

# AMD / ROCm
OMNIVOICE_WORKER_TOKEN='ovw_…' docker compose \
  -f deploy/docker-compose.yml --profile worker-rocm up -d
```

These profiles publish no HTTP port and require no browser UI. The join code
must advertise a LAN or private-overlay address the container can reach, not
the control plane's `127.0.0.1`. Enrollment state persists in a dedicated
volume, so the container reconnects after a restart even though the join code
is single-use. Container health becomes green only after the control plane
accepts that registration; a missing or invalid token stays unhealthy instead
of reporting the generic web backend as ready. See [Remote GPU
workers](../remote-workers.md) for enrollment, approval, routing, and security
details.

## LAN access

<a id="lan-access"></a>

To expose VoiceStudio on your LAN (e.g. you're running it on a homelab box and
opening the UI from a laptop), change the host port mapping:

```yaml
# deploy/docker-compose.yml
services:
  omnivoice:
    ports:
      - "0.0.0.0:3900:3900"   # ← was 127.0.0.1:3900:3900
```

The VoiceStudio frontend defaults to the **same origin** the page was served
from, so opening the UI from `http://<lan-ip>:3900` Just Works for both the
page load *and* the API/media requests it makes afterwards.

If you front the app with a **reverse proxy** and the API and UI land on
different origins, pin the API base explicitly. Use **`OMNIVOICE_PUBLIC_API_BASE`**
— a *runtime* env var the backend injects into the page, so it works with the
prebuilt image via `docker run -e` (the older `VITE_OMNIVOICE_API` is inlined at
*build* time and cannot be set on a prebuilt image):

```bash
docker run -e OMNIVOICE_API_KEY="$OMNIVOICE_API_KEY" \
  -e OMNIVOICE_PUBLIC_API_BASE=https://api.your-host.example \
  -p 0.0.0.0:3900:3900 \
  ghcr.io/debpalash/omnivoice-studio:latest
```

> `OMNIVOICE_PUBLIC_API_BASE` must be a plain `http(s)://…` URL; anything else
> is ignored and the app falls back to same-origin. If you build from source you
> may instead bake `VITE_OMNIVOICE_API` at build time, but the runtime var above
> is simpler and image-agnostic.

> **Security:** Loopback-only publishing is the safe default. On a trusted LAN,
> set a long random `OMNIVOICE_API_KEY` with `docker run -e` or Compose; the
> browser will prompt for it. The optional six-digit share PIN permits casual
> consumption access but does not authorize administration or dictation. On any
> untrusted network, plain HTTP is not safe for the API key or session cookie.
> Keep the backend on an encrypted private overlay such as Tailscale/ZeroTier;
> do not expose it directly to the public internet. See [API
> authentication](../api-auth.md) for the complete access model.

## Volume mounts

Two paths are worth persisting across container restarts:

| Mount | Purpose | Why |
|-------|---------|-----|
| `omnivoice_data:/app/omnivoice_data` | Project DB, user voices, settings | Survives upgrade; encrypted HF token lives here |
| `~/.cache/huggingface:/root/.cache/huggingface` | HF model cache | Re-using your host's cache saves ~2.4 GB of re-downloads |

## Troubleshooting

- **Container reports 0.2.7 but image is tagged 0.3.x:** This was a workflow bug
  (fixes #249, #251) — the `:latest` tag was not being updated on release tag
  pushes. Pull the image again after the fix is merged: `docker pull ghcr.io/debpalash/omnivoice-studio:latest`.
  The running version is now shown in **Settings → About → Version** (read live
  from the backend), so the web UI no longer displays a dash in Docker.
- **Checking which version is running:** `docker exec <container> python3 -c "import importlib.metadata; print(importlib.metadata.version('omnivoice'))"`, or hit the `/health` endpoint — it returns `{"status": "ok", "device": ..., "version": "0.3.x"}`. Use the container name listed by `docker compose ps` (or `omnivoice` for the `docker run` examples).
- **Watching startup:** the port answers within about a second of container
  start, but heavy initialization (PyTorch, API routes, database migration)
  continues in the background. During that window `/health` returns **503**
  with the current step, and `GET /startup/progress` returns the full
  step-by-step ledger (`status`, current `step`/`label`, per-step states) —
  useful when a start seems slow and you want to see where it actually is.
  The Docker `HEALTHCHECK` flips healthy only once `/health` is 200.
- **"Loopback origin required" errors (and a blank version):** the desktop
  build restricts the `/system/*` and `/api/settings/*` routes to a loopback
  origin, but Docker's NAT makes every request look non-loopback, so the gate
  used to 403 the whole admin UI (issue #261). The image now ships with
  `OMNIVOICE_SERVER_MODE=1`, which relaxes that gate for the headless
  deployment. Admin mutations still require `OMNIVOICE_API_KEY`; all commands
  above pass it into the container, and the UI prompts for it on first use.
  Exposure is governed by your `-p` port mapping (keep the `127.0.0.1:` prefix
  to stay local) plus authentication. If you front
  the container with your own auth proxy on loopback, set `OMNIVOICE_SERVER_MODE=0`
  to re-enable the strict gate.
- **Media-preview 404 in LAN mode:** see the [LAN access](#lan-access) section
  above — the `window.location.host` fix shipped in v0.3.
- **GPU not detected (NVIDIA):** verify `docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu22.04 nvidia-smi` succeeds first.
- **GPU not detected (AMD):** make sure you pulled the `:rocm` tag (the default
  image is CUDA-only) and passed `--device /dev/kfd --device /dev/dri`. Check
  the container sees the card with
  `docker exec omnivoice rocminfo | grep -i gfx`. On consumer cards, run
  **without** any `HSA_OVERRIDE_GFX_VERSION` first — the backend sets it
  itself when your card needs it, and overriding a natively-supported GPU
  only forces it onto foreign kernels. See
  [Pull and run (AMD GPU / ROCm)](#pull-and-run-amd-gpu--rocm) above for when
  to set one by hand.
- More entries: [docs/install/troubleshooting.md](troubleshooting.md).
