# VoiceStudio — Install with Docker

For headless servers, dedicated GPUs, or "I want one command" deployments.
The docker image bundles the backend; the UI is served over HTTP and you open
it in a normal browser. It is built from the same maintained renderer as the
Electron desktop app; controls that require native desktop access are hidden.

Inference runs inside the container. Nothing is sent off the machine without
your explicit yes: product analytics are opt-in (PostHog, content-free usage
metadata only) behind the same first-run consent prompt as the desktop app,
and declining or skipping it keeps them off. Set
`-e OMNIVOICE_ANALYTICS_DISABLED=1` to switch analytics off entirely. Model
weights download from Hugging Face when you install a model.

**Official images:** [`ghcr.io/debpalash/voicestudio`](https://github.com/debpalash/VoiceStudio/pkgs/container/voicestudio)
and [`palashdeb/omnivoice-studio` on Docker Hub](https://hub.docker.com/r/palashdeb/omnivoice-studio) — same images, same tags.
The former `ghcr.io/debpalash/omnivoice-studio` path remains a compatible alias.

## Architecture

The standard CUDA/CPU images and all ROCm variants are `linux/amd64`. The only
native `linux/arm64` image is the NVIDIA CUDA build, identified by
`-cuda-arm64` tags. On an ARM64 machine, pulling an unsuffixed tag may fail
with `no matching manifest for linux/arm64/v8`.

- **Apple Silicon:** use the [native macOS app](macos.md). Linux containers
  cannot use an Apple GPU through MPS or MLX.
- **AMD64 emulation on ARM64:** if Docker supports emulation, put
  `--platform linux/amd64` before the image name in both `docker pull` and
  `docker run` from the CPU instructions below. This is emulated CPU execution,
  not native ARM64 support or a GPU workaround, and it can be much slower. Use
  an AMD64 server when emulation is unavailable.
- **ARM64 with an NVIDIA GPU:** run `:stable-cuda-arm64` as described in
  [Pull and run (ARM64 NVIDIA GPU)](#pull-and-run-arm64-nvidia-gpu). Jetson
  devices using L4T/JetPack are outside this image's support because they need
  JetPack-specific PyTorch packages.

## Image tags

> **Image ↔ version mapping**
>
> | Tag | What you get |
> |-----|--------------|
> | `:latest` | **Rolling preview** — the latest commit on `main`, at or ahead of the last release. Only `main` builds move it; publishing a release never does. Pin `:stable` for production. |
> | `:main` | Alias of the same rolling `main` build as `:latest` |
> | `:stable` | Most recent stable release — moves when a GitHub Release is published |
> | `:0.5.6` | Exact release version, published with its GitHub Release |
> | `:0.5` | Latest released patch within the 0.5 minor |
> | `:sha-xxxxxxx` | Exact commit — produced by every image build (main, releases, manual runs) |
> | `:rocm` | **AMD GPU (ROCm) build** of the rolling preview — the ROCm analogue of `:latest` |
> | `:stable-rocm`, `:0.5.6-rocm`, `:0.5-rocm`, `:sha-xxxxxxx-rocm` | ROCm builds of the corresponding CUDA tags above |
> | `:cuda-arm64` | **ARM64 NVIDIA GPU build** (`linux/arm64`) of the rolling preview |
> | `:stable-cuda-arm64`, `:0.5.6-cuda-arm64`, `:0.5-cuda-arm64`, `:sha-xxxxxxx-cuda-arm64` | ARM64 NVIDIA builds of the corresponding CUDA tags above |
> | `:beta`, `:0.5.9-beta.1` | Newest beta prerelease and its exact version. A beta never moves `:stable` or `:0.5`; `-rocm` and `-cuda-arm64` forms exist too (`:beta-rocm`, `:beta-cuda-arm64`) |
>
> Release tags (`:X.Y.Z`, `:X.Y`, `:stable` and their `-rocm` and `-cuda-arm64`
> forms) are published only when the GitHub Release is published. Previews come
> only from `main` (`:latest`, `:main`, `:rocm`, `:cuda-arm64`); to follow
> previews, use `:latest`. The `-cuda-arm64` tags start with the first release
> after they were added; older versions have no arm64 image.
>
> **Note on desktop-only controls:** Electron updates, global shortcuts, native
> file pickers, and runtime management do **not** apply to Docker. To update a
> Docker deployment, pull the new image tag and recreate the container
> (`docker compose pull && docker compose up -d`).

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
docker pull ghcr.io/debpalash/voicestudio:stable

docker run -d --name omnivoice \
  -p 127.0.0.1:3900:3900 \
  -e OMNIVOICE_API_KEY="$OMNIVOICE_API_KEY" \
  -v omnivoice-data:/app/omnivoice_data \
  ghcr.io/debpalash/voicestudio:stable
```

> **Docker Hub mirror:** the same images are published to
> `palashdeb/omnivoice-studio` on Docker Hub with identical tags — swap the
> image for `palashdeb/omnivoice-studio:stable` if you prefer Docker Hub.
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
  ghcr.io/debpalash/voicestudio:stable
```

GPU mode requires the
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
on the host.

## Pull and run (ARM64 NVIDIA GPU)

<a id="pull-and-run-arm64-nvidia-gpu"></a>

For ARM servers with an NVIDIA GPU (DGX Spark / GB10, GH200, GB200), use the
native `linux/arm64` image:

```bash
docker run -d --name omnivoice --gpus all \
  -p 127.0.0.1:3900:3900 \
  -e OMNIVOICE_API_KEY="$OMNIVOICE_API_KEY" \
  -v omnivoice-data:/app/omnivoice_data \
  ghcr.io/debpalash/voicestudio:stable-cuda-arm64
```

It needs the NVIDIA Container Toolkit and a driver that supports CUDA 12.9.
The image is built from Ubuntu 24.04 with PyTorch 2.8.0 for CUDA 12.9 (PyTorch
publishes no CUDA 12.8 build of 2.8.0 for aarch64), on GitHub's native arm64
runners. Differences from the x86-64 CUDA image:

- CTranslate2-based ASR engines (faster-whisper, WhisperX) report unavailable:
  NVIDIA ships no aarch64 build of the cuDNN 8 libraries they need. Use
  `pytorch-whisper` for GPU transcription.
- PyTorch engines such as OmniVoice and CosyVoice use the GPU.
- Jetson (L4T/JetPack) is not supported.

CI builds this image but cannot run it on a GPU; please report problems with
your host and `docker logs` output.

To build it yourself on an arm64 host:

```bash
docker build -f deploy/Dockerfile --build-arg BASE_IMAGE=cuda-arm64-base -t voicestudio:cuda-arm64 .
```

All image variants verify installed dependency versions against
`deploy/uv-overrides.txt` after installation. The check strips surrounding
whitespace and line endings before parsing requirements, including with
`packaging` 26.3; an installed version below the required floor still fails the build.

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
  ghcr.io/debpalash/voicestudio:stable-rocm
```

### AMD GPU on WSL2

WSL exposes AMD compute through `/dev/dxg`, not native Linux's `/dev/kfd` and
`/dev/dri`. First install ROCm and `librocdxg` in the WSL distribution and
confirm the host-side `rocminfo` lists the GPU. Then use the WSL-specific
bridge flags from AMD's `librocdxg` container contract (to run it in a WSL
distro directly, without a container, see [wsl-amd-rocm.md](wsl-amd-rocm.md)):

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
  ghcr.io/debpalash/voicestudio:stable-rocm
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
Image=ghcr.io/debpalash/voicestudio:stable-rocm
AddDevice=/dev/kfd
AddDevice=/dev/dri
PublishPort=127.0.0.1:3900:3900
Volume=omnivoice-data:/app/omnivoice_data
Environment=OMNIVOICE_API_KEY=replace-with-a-long-random-key
```

Release pins exist too: `:stable-rocm`, `:0.5.6-rocm`, `:0.5-rocm` mirror
the CUDA tags exactly.

> **Consumer cards and APUs (RX 6000/7000/9000, Strix Point/Halo):** the backend
> auto-sets `HSA_OVERRIDE_GFX_VERSION` when — and only when — your card's GFX
> ID is missing from the shipped ROCm build's architecture list, picking the
> closest same-generation target that list contains (never across a
> generation), so try without any override first. Overriding a natively-supported GPU (gfx1151 on
> ROCm 7.x, for example) only forces it onto foreign kernels. If the GPU still
> isn't used, force a same-generation value on the container, for example
> `-e HSA_OVERRIDE_GFX_VERSION=11.0.0` for an RX 7000 (gfx11xx) card or
> `10.3.0` for an RX 6000 (gfx103x) card. It is deliberately **not** baked into
> the image, because the right value depends on your card, and a value you set
> is always respected as-is. **Never set it on an RX 9000 (RDNA4, gfx12xx)
> card:** the ROCm image supports those natively, and an RDNA3 value maps them
> onto kernels they cannot run.
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
**Model Catalogue** should report both `omnivoice` and
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

> **ARM64 hosts:** Compose has no per-command `--platform` flag, so the
> override that works for `docker pull` and `docker run` does not reach it.
> Set `DOCKER_DEFAULT_PLATFORM=linux/amd64` in the shell you run Compose from,
> or the image resolves to the ARM64 manifest that does not exist and fails
> with `no matching manifest for linux/arm64/v8`. Only the CPU profile makes
> sense under emulation — it is not a GPU workaround.
>
> ```bash
> export DOCKER_DEFAULT_PLATFORM=linux/amd64
> docker compose -f deploy/docker-compose.yml --profile cpu pull
> docker compose -f deploy/docker-compose.yml --profile cpu up -d
> ```
>
> In PowerShell, set both the administrator key and target platform for the
> current session before issuing the same Compose commands. This uses .NET and
> does not require Python on the host:
>
> ```powershell
> $randomBytes = [byte[]]::new(32)
> $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
> try {
>   $generator.GetBytes($randomBytes)
> } finally {
>   $generator.Dispose()
> }
> # URL-safe encoding matters when the key is supplied through ?api_key=,
> # where an unescaped plus sign would otherwise decode as a space.
> $env:OMNIVOICE_API_KEY = [Convert]::ToBase64String($randomBytes).Replace('+', '-').Replace('/', '_').TrimEnd('=')
> $env:DOCKER_DEFAULT_PLATFORM = 'linux/amd64'
> docker compose -f deploy/docker-compose.yml --profile cpu pull
> docker compose -f deploy/docker-compose.yml --profile cpu up -d
> ```
>
> Either way the setting lives only in that shell and the processes it starts.
> The [architecture limits](#architecture) still apply: this is emulated CPU
> inference, not native ARM64 support.

The `docker-compose.yml` shipped in `deploy/` defaults to `127.0.0.1:3900`
on the host. The backend inside the container binds to `0.0.0.0` so the
host port mapping can forward — the host-side `127.0.0.1` binding is what
enforces loopback-only.

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
  -e OMNIVOICE_ALLOWED_ORIGINS=https://ui.your-host.example \
  -p 0.0.0.0:3900:3900 \
  ghcr.io/debpalash/voicestudio:stable
```

List the UI's origin in `OMNIVOICE_ALLOWED_ORIGINS` so the API accepts its
requests. Video previews and download links send no `Origin`; the API accepts
them when their `Referer` is that origin, so keep the browser's default
referrer policy on the UI host (a proxy that sets `Referrer-Policy: no-referrer`
breaks them).

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

### Share PIN for the host's own browser (opt-in)

<a id="trust-host-loopback"></a>

When network sharing is on, the share PIN also applies to the browser on the
Docker host itself: Docker forwards a published port through its proxy, so
`http://127.0.0.1:3900` arrives inside the container from the bridge gateway
(for example `172.17.0.1`), not from loopback. That is the default and stays
so. If the host is yours alone, you can let it skip the PIN:

```bash
docker run -d -p 127.0.0.1:3900:3900 \
  -e OMNIVOICE_API_KEY="$OMNIVOICE_API_KEY" \
  -e OMNIVOICE_TRUST_HOST_LOOPBACK=1 \
  -v voicestudio_data:/app/omnivoice_data \
  ghcr.io/debpalash/voicestudio:stable
```

With `OMNIVOICE_TRUST_HOST_LOOPBACK=1`, requests whose socket address is
exactly the container's default gateway skip the PIN on the main port.
Everything else is unchanged:

- Other LAN machines, other containers and requests on the LAN-share port
  still need the PIN.
- `X-Forwarded-For`, `X-Real-IP` and `Forwarded` headers are never used for
  this check; Uvicorn only applies them for peers listed in
  `FORWARDED_ALLOW_IPS` (default `127.0.0.1`).
- It replaces the PIN only. It does not replace `OMNIVOICE_API_KEY` and never
  unlocks admin routes.

> **Risk:** anything that can reach the gateway address skips the PIN, which
> means every local user and process on the host, and anything a host-side
> proxy forwards. Keep the main port published on `127.0.0.1` only. Docker
> Desktop (macOS, Windows) does not keep the real client address for
> published ports, so on `0.0.0.0:3900:3900` it would present LAN clients
> from the same gateway address, and they would skip the PIN too. Do not set
> this behind a reverse proxy on the host unless that proxy is listed in
> `FORWARDED_ALLOW_IPS`.

## Volume mounts

Persist one path across container restarts:

| Mount | Purpose |
|-------|---------|
| `omnivoice-data:/app/omnivoice_data` | Project DB, user voices, settings, encrypted HF token, **and the Hugging Face model cache** — survives upgrades |

The image sets `HF_HOME=/app/omnivoice_data/huggingface`, so downloaded models
already live in that volume; mounting a host cache at
`/root/.cache/huggingface` has no effect. To reuse an existing host cache
instead, bind it to the image's cache path:
`-v ~/.cache/huggingface:/app/omnivoice_data/huggingface`. The container runs
as root, so files it adds there are root-owned on the host.

## Troubleshooting

- **"Enter access PIN" on the Docker host:** see [Recovering from a lost share PIN](#recovering-from-a-lost-share-pin) below.
- **Container reports an older or newer version than expected:** `:latest`
  follows `main`, not releases. For a released version, pull `:stable` or the
  exact `:X.Y.Z` tag and recreate the container. The running version is shown
  in **Settings → About → Version** (read live from the backend).
- **Checking which version is running:** `docker exec <container> python3 -c "import importlib.metadata; print(importlib.metadata.version('omnivoice'))"`, or hit the `/health` endpoint — it returns `{"status": "ok", "device": ..., "engine": ..., "version": "0.3.x"}`, where `device` is what the active TTS engine generates on (`vulkan (...)`, `cuda (...)`, `rocm (...)`, `xpu (...)`, `mps` or `cpu`). Use the container name listed by `docker compose ps` (or `omnivoice` for the `docker run` examples).
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
- **"Request refused: VoiceStudio was addressed by an unrecognized host name":**
  without an API key, the backend answers only to `localhost`, IP addresses and
  configured names, so another website cannot rebind its domain onto it. Open
  it by IP address, sign in with the API key, or add the name with
  `-e OMNIVOICE_ALLOWED_HOSTS=nas.lan` (see
  [API authentication](../api-auth.md#requests-from-other-websites-and-host-names)).
- **URL import refused as a private network address:** imports fetch public
  addresses only. To import from a media server on your network, set
  `-e OMNIVOICE_ALLOW_PRIVATE_URL_IMPORTS=1`.
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

### Recovering from a lost share PIN

If you enable **Settings → Sharing & Remote Access** and then lose the browser's
PIN cookie, even `http://127.0.0.1:3900/` on the Docker host can ask for the PIN.
Docker forwards that connection through its bridge, so the backend sees a
non-loopback peer (for example, `172.17.0.1`). The host's loopback port binding
limits who can connect; it does not make the peer inside the container loopback.

The PIN lives only in the running backend process, not in `prefs.json`. The API
key and share PIN are separate gates: signing in with `OMNIVOICE_API_KEY` does
not bypass an active PIN gate on ordinary HTTP requests.

To recover, restart the existing container. This interrupts running jobs and
clears the in-memory sharing state and server-side administrator sessions. It
does not erase the administrator credential stored in the browser:

```bash
docker restart omnivoice
```

Replace `omnivoice` with your container's name if different. Reopen
`http://127.0.0.1:3900/` once startup completes and sign in with the same
`OMNIVOICE_API_KEY` supplied when the container was created. Restarting keeps
the existing data volume, voices, projects, settings and downloaded models;
there is no need to delete the container or its volume.

For host-only Docker use, leave in-app network sharing off and retain
`-p 127.0.0.1:3900:3900`. For access from another device, follow
[LAN access](#lan-access) and keep the administrator API key configured.

On a host you alone use, set `OMNIVOICE_TRUST_HOST_LOOPBACK=1` to let that
browser skip the share PIN without trusting a Docker subnet. It applies only
to the container's default gateway on the main port, and it does not replace
`OMNIVOICE_API_KEY`. See
[Share PIN for the host's own browser](#trust-host-loopback).

`OMNIVOICE_TRUSTED_NETWORKS` can exempt selected peer addresses from the PIN
and API-key consumption gates, but it does **not** grant administrator access.
Do not trust an entire Docker subnet just to dismiss the PIN prompt: other
containers or forwarded clients may share that network identity. See
[trusted networks](../api-auth.md#trusted-networks) before choosing this deployment policy.

### CTranslate2 compatibility

CUDA images include an isolated cuDNN 8 compatibility library directory for WhisperX and faster-whisper alongside the base PyTorch cuDNN 9 runtime. The image build verifies the compatibility libraries exist. ROCm images skip this NVIDIA-only dependency.
