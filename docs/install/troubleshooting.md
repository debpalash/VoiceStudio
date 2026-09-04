# VoiceStudio — Install Troubleshooting

The top 10 errors users have actually hit on `v0.2.x`, with their causes and
fixes. Most have a deeplink anchor that the in-app error UI's "Open docs for
this error" button targets directly.

## Start here: self-diagnosis

<a id="self-diagnosis"></a>

Before digging through the entries below, let the app diagnose itself:

- **In the app:** **Settings → About → "Run self-check"** verifies your
  compute device (CUDA/MPS/CPU), ffmpeg, HuggingFace token, disk space,
  data-directory permissions, RAM, installed TTS engines, and hub
  reachability — each with a hint when something's off.
- **Headless / terminal:**

  ```bash
  uv run python backend/main.py --diagnose          # same checks, exits 1 on failure
  uv run python backend/main.py --diagnose --deep   # also loads the active engine
                                                    # and synthesizes a test utterance
  ```

  `--deep` catches "installed but broken" engines. On a fresh install it may
  cold-load the model (minutes, plus a large download).

- **Filing an issue?** **Settings → About → "Save diagnostic bundle"**
  produces a zip (self-check report, recent classified errors, scrubbed log
  tails) you can drag straight onto the GitHub issue. Home paths and
  anything token-shaped are redacted before they leave your machine.

  The report's `engine_execution` rows distinguish declared compatibility
  from observed runtime state. `evidence_state: not_loaded` means no actual
  provider can yet be claimed. Run the deep self-check for TTS, or issue a
  representative ASR request for ASR evidence. Loaded rows include the execution provider/device, precision
  or quantization when the engine exposes it, GPU identity, CPU-fallback stage,
  relevant library versions, and whether parent-process memory counters cover
  the engine. Subprocess engines report memory visibility as false because
  their accelerator allocations belong to the child process.

## 1. `pkg_resources` missing (ModuleNotFoundError)

<a id="pkg_resources-missing"></a>

**Symptom:** the splash screen shows `ModuleNotFoundError: No module named
'pkg_resources'` during WhisperX import, and the app never advances past the
"Setting up models" step.

**Cause:** WhisperX (and a couple of its transitive deps) still imports
`pkg_resources`, which `setuptools >= 80` dropped. `pyproject.toml` pins
`setuptools>=75,<80` so it stays present — but the venv can still lose it two
other ways: **(a)** antivirus (commonly Windows Defender) quarantines
`pkg_resources`' files, or **(b)** a partial/interrupted extract. In both cases
setuptools' *metadata* remains, so `uv`/`pip` report it "already satisfied" and
a plain install **no-ops** — the files are never restored.

**Fix:** in the backend venv, **force a reinstall** (a plain install won't work
for the reasons above):

```
uv pip install --reinstall 'setuptools>=75,<80'
```

then restart. If it recurs, your antivirus is removing the files again — add the
backend **`.venv`** folder to its exclusions (Windows Security → Virus & threat
protection → Exclusions). The app's auto-repair now uses `--reinstall` too, so a
fresh install heals itself.

**Linked issues:** [#58](https://github.com/debpalash/VoiceStudio/issues/58),
[#248](https://github.com/debpalash/VoiceStudio/issues/248)

### 1a. Model load fails: `[Errno 2] No such file or directory: '…/transformers/…/modeling_*.py'`

**Symptom:** the System Check / model load fails with e.g.
`[Errno 2] No such file or directory:
'…/site-packages/transformers/models/qwen3/modeling_qwen3.py'`.

**Cause:** same class as §1 — a **corrupted/incomplete `transformers` install**.
A model load lazily resolves a module file that's **missing from `site-packages`**
(an interrupted `uv sync`, antivirus quarantine, or a partial update). The
package's metadata is intact, so a plain install no-ops and never restores the
file. Restarting does **not** help (the file is still gone).

**Fix:** force-reinstall transformers in the backend venv, then restart:

```
uv pip install --reinstall transformers
```

Or, as a quick workaround, switch ASR to **faster-whisper** in
**Model Catalogue → Models**. If it recurs, add the backend **`.venv`** to your
antivirus exclusions (see §1). Newer builds classify this error and show the
reinstall hint directly instead of a bare path + "try restarting".

### 1a-bis. Same wording, different cause: torch ↔ torchvision mismatch

**Symptom:** identical `Could not import module 'AutoFeatureExtractor'` /
`'GenerationMixin'` errors — but reinstalling transformers alone changes
nothing.

**Cause:** transformers' lazy importer wraps whatever really failed in that
generic message. When the real failure is
`RuntimeError: operator torchvision::nms does not exist`, the problem is a
**torch/torchvision version mismatch** (one was upgraded without the other),
and transformers itself is fine.

**Fix:** reinstall the trio **together, at the pinned versions** — a plain
unpinned reinstall can itself resolve a drifted pair
([#1357](https://github.com/debpalash/VoiceStudio/issues/1357)):

```
uv pip install --python .venv --reinstall torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 transformers
```

run in the project folder. The versions mirror `deploy/torch-constraints.txt`
(source checkouts can pass `--constraint deploy/torch-constraints.txt` instead;
desktop installs don't ship that file, which is why the literal pins are shown).
They carry no `+cu128`/`+rocm` suffix on purpose — vendor GPU builds match the
pins rather than being replaced.

**Linked issues:** [#1357](https://github.com/debpalash/VoiceStudio/issues/1357),
[#1376](https://github.com/debpalash/VoiceStudio/issues/1376)

### 1b. Dubbing: `ASR backend initialization failed: No module named 'lightning_fabric'`

**Symptom:** transcription/dubbing fails at the start with
`ASR backend initialization failed: No module named 'lightning_fabric'`.

**Cause:** same class as §1 — a **partial/broken install**. `lightning_fabric`
ships *inside* the `pytorch-lightning` wheel (WhisperX needs it via
pyannote-audio), and an interrupted install or antivirus quarantine can strip
it while the package's metadata stays intact — so a plain install no-ops and
never restores the files.

**Fix:** force-reinstall pytorch-lightning in the backend venv, then restart:

```shell
uv pip install --reinstall pytorch-lightning
```

If it recurs, add the backend **`.venv`** to your antivirus exclusions (see
§1). Since this fix landed the app also degrades gracefully: WhisperX is
marked unavailable (Model Catalogue → Engines shows why, with this repair command)
and dubbing automatically falls through to **faster-whisper** instead of
failing outright.

**Linked issue:** [#1185](https://github.com/debpalash/VoiceStudio/issues/1185)

## 1c. Setup blocked: "System RAM … The app will OOM on first dub"

**Symptom:** the setup wizard's System Check shows **System RAM** in red and
"Resolve blockers to continue" stays disabled — often on an 8 GB machine that
reports ~7.8 GB usable (firmware and integrated graphics reserve a slice of
installed RAM).

**Cause:** the preflight compares OS-reported RAM against the 8 GB minimum.
Since [#1618](https://github.com/debpalash/VoiceStudio/issues/1618) the check
tolerates that reserved-memory gap, so 8 GB-installed machines pass.

**Fix:** update to the latest release. If your machine is genuinely below the
minimum and you accept the out-of-memory risk (long dubs may crash), set
`OMNIVOICE_RAM_PREFLIGHT=0` before launching — the hard block becomes a
warning. On Windows run PowerShell
`[Environment]::SetEnvironmentVariable('OMNIVOICE_RAM_PREFLIGHT','0','User')`
and relaunch; on macOS/Linux export it in the shell that starts the app. (The
in-app Settings panel can't help here — this blocker appears before setup
completes.)

**Linked issues:** [#1618](https://github.com/debpalash/VoiceStudio/issues/1618)

## 2. HF 401 / pyannote license not accepted

**Symptom:** dubbing fails with `HfHubHTTPError: 401 Client Error: Unauthorized
for url …pyannote/speaker-diarization-3.1…`, or
diarization silently falls back to a single speaker.

**Cause:** `pyannote/speaker-diarization-3.1` is a **gated** model — even with a
valid HF token, you need to accept the model's license on its HuggingFace page
before the token works for downloads.

**Fix:**

1. Open **Settings → API Keys** in the app and paste a working HF token (or set
   `HF_TOKEN` in your env). See [docs/setup/huggingface-token.md](../setup/huggingface-token.md).
2. Visit https://huggingface.co/pyannote/speaker-diarization-3.1 while signed
   in with the same HF account → click **"Agree and access repository"**.
3. Retry the job. The token state in **Settings → API Keys** should now show
   the "App" row with a green check next to your username.

**Linked issue:** [#35](https://github.com/debpalash/VoiceStudio/issues/35)

### PocketTTS gated weights

**Symptom:** PocketTTS reports `POCKETTTS_GATED_WEIGHTS`, `gated repo`, or
asks you to share your contact information instead of generating audio.

**Cause:** the PocketTTS model files are public but gated. Hugging Face only
serves them after your account accepts Kyutai's access conditions. A token by
itself does not grant access.

**Fix:**

1. Visit https://huggingface.co/kyutai/pocket-tts while signed in, review the
   license and prohibited-use conditions, share the requested contact details,
   and accept the conditions.
2. Open **Settings → API Keys** and save a read token from that same account.
3. Open **Model Catalogue → Engines**, review and accept the PocketTTS terms locally,
   then retry. VoiceStudio stores this acknowledgement only on your machine.

## 3. Gatekeeper quarantine on macOS

**Symptom:** "VoiceStudio.app is damaged and can't be opened."

**Cause:** the app is not yet notarised (signing is wired in `release.yml` and
activates once the maintainer adds the Apple cert secrets) — until then macOS
quarantines every download.

**Fix:** see [macos.md#gatekeeper-quarantine](macos.md#gatekeeper-quarantine).

## 4. AppImage white screen / EGL errors (Fedora 44, Ubuntu 24.04+, 26.04)

**Symptom:** the AppImage window opens fully white. No UI ever appears. On
newer distros (Ubuntu 24.04 and later, incl. 26.04) the terminal often shows
`Could not create default EGL display: EGL_BAD_PARAMETER`.

**Cause:** WebKitGTK rendering regressions — the DMA-BUF renderer on modern
WebKitGTK (2.48+), or the 2.44 / 2.46 compositing mode.

**Fix:** try `WEBKIT_DISABLE_DMABUF_RENDERER=1` first (modern WebKitGTK / the
EGL error), then `WEBKIT_DISABLE_COMPOSITING_MODE=1` — full walkthrough incl.
the software-rendering last resort:
[linux.md#appimage-white-screen-on-fedora-44--ubuntu-2404](linux.md#appimage-white-screen-on-fedora-44--ubuntu-2404).

**Linked issues:** [#62](https://github.com/debpalash/VoiceStudio/issues/62),
[#961](https://github.com/debpalash/VoiceStudio/issues/961)

## 5. Windows Triton / torch.compile OOM

**Symptom:** the first synthesis call fails with `OutOfMemoryError: CUDA out
of memory` or `RuntimeError: Triton compilation failed`, especially on
<16 GB VRAM GPUs.

**Cause:** the engine's `torch.compile` step compiles Triton kernels with a
peak memory footprint that exceeds free VRAM. Windows-only quirk.

**Fix:** see [windows.md#torch-compile-oom](windows.md#torch-compile-oom).

**Linked issue:** [#65](https://github.com/debpalash/VoiceStudio/issues/65)

## 6. `uv venv` Python download fails (restricted network)

**Symptom:** during first launch, `uv` exits with a network error pulling
`python-build-standalone` from GitHub. Common in China, intermittently in
Russia, sometimes on corporate proxies.

**Fix:** see [linux.md#restricted-networks-china--russia](linux.md#restricted-networks-china--russia)
(same env vars work on macOS and Windows — `UV_PYTHON_INSTALL_MIRROR`,
`UV_HTTP_TIMEOUT=120`, `UV_HTTP_RETRIES=5`, `UV_PYTHON_PREFERENCE=only-system`).

**Linked issues:**
[#57](https://github.com/debpalash/VoiceStudio/issues/57),
[#60](https://github.com/debpalash/VoiceStudio/issues/60).

## 7. `.deb` ffprobe path conflict on upgrade

**Symptom:** after upgrading from a pre-v0.3 .deb, `ffprobe -version` reports
"VoiceStudio bundled ffprobe" instead of the system ffmpeg, breaking other apps
that rely on `/usr/bin/ffprobe`.

**Fix:** see [linux.md#deb-ffprobe-conflict](linux.md#deb-ffprobe-conflict).

## 7b. "Media engine unavailable" / FFmpeg questions

FFmpeg, FFprobe, and yt-dlp are **not** things you install for VoiceStudio.
The app resolves them itself, in order: a path provided by the desktop shell →
the static build shipped with the Python environment → the app's own
downloaded build → whatever is on your PATH. When nothing resolves at all
(some source installs on a fresh machine), the Setup Wizard downloads a
pinned, checksum-verified static build in the background — you'll see a
one-line "Preparing media engine…" progress and, only if that download fails,
a card with **Retry** and **Use a system copy**.

If a running install ever reports "Media engine unavailable":

1. Open **Settings → Audio tools**. Each row shows the binary actually in use
   (version, path, and origin — Bundled / System / Custom).
2. Press **Restore bundled** to re-fetch the app's own build (needs network
   once), or **Use system copy** / **Choose file…** to point at an FFmpeg you
   already have. Installing via a package manager (`brew install ffmpeg`,
   `sudo apt install ffmpeg`, `winget install ffmpeg`) also works — press
   **Use system copy** afterwards.

The same panel updates **yt-dlp** (video imports): site support changes
faster than app releases, so when video-URL imports start failing, press
**Update** there — the new version survives app updates, and **Restore tested
version** reverts to the build the app shipped with.

### YouTube asks you to sign in or confirm you are not a bot

First update yt-dlp under **Settings → Audio tools**. If YouTube still requires
your signed-in session, export its cookies in Netscape `cookies.txt` format,
then choose that file beside the URL field before importing. VoiceStudio uses
the export for that import only and makes two best-effort attempts to delete
its temporary copy.

Cookie exports are login credentials. VoiceStudio never reads a browser's
cookie database automatically, never saves the export in your project, and
never uploads it anywhere except to your own VoiceStudio backend. Use an export
limited to YouTube where your browser extension supports domain filtering.
For a backend on another machine, the picker is enabled only over HTTPS; plain
HTTP is accepted solely on the desktop app's loopback connection.
Remote backends must also use `OMNIVOICE_API_KEY` as the bearer key and remain
restricted to a private tailnet; see [API authentication](../api-auth.md).

## 8. Docker LAN access — media preview 404

**Symptom:** VoiceStudio loads on `http://<lan-ip>:3900` but the audio preview
pane shows 404s for `/media/...`.

**Cause:** pre-v0.3, the frontend hardcoded `localhost:3900` for media-preview
URLs, which is wrong when the UI is reached from a different LAN host.

**Fix:** the frontend derives its API/media base from the page's own origin.
When running behind a reverse proxy where the UI and API are on different
origins, set the runtime override `OMNIVOICE_PUBLIC_API_BASE` (works on the
prebuilt image via `docker run -e`) — see
[docker.md#lan-access](docker.md#lan-access).

## 9. Apple Silicon `mlx-whisper` unavailable on Intel mac

**Symptom:** on an Intel mac, VoiceStudio logs `mlx-whisper backend unavailable;
falling back to faster-whisper`.

**Cause:** `mlx-whisper` and `mlx-audio` only build for arm64 (Apple Silicon).

**Fix:** none needed on Apple Silicon setups that log this transiently. Note
that Intel Macs can no longer run the local backend at all — PyTorch dropped
Intel-Mac wheels, so this entry only applies to historical installs (see
[macos.md](macos.md) and
[#889](https://github.com/debpalash/VoiceStudio/issues/889)).

## 10. Windows: `Could not locate cudnn_ops_infer64_8.dll` during transcription

**Symptom:** on Windows + NVIDIA, transcription/dubbing fails and the backend
log shows `Could not locate cudnn_ops_infer64_8.dll`. Model Catalogue → Models shows
WhisperX or faster-whisper selected.

On builds before this was fixed, the failure looked much worse than a failed
transcribe: CTranslate2 aborts the **process** rather than raising, so the whole
backend died with `exit code -1073740791` (`0xC0000409`) and no error, the app
restarted it, and the next attempt killed it again
([#1371](https://github.com/debpalash/VoiceStudio/issues/1371)). VoiceStudio now
checks whether cuDNN 8 will load *before* selecting a CTranslate2 engine and
falls back to PyTorch Whisper instead, so a missing library costs you WhisperX's
word-level alignment — not the backend. The repair below is still worth doing to
get WhisperX back.

**Cause:** WhisperX and faster-whisper run on **CTranslate2**, which needs
**cuDNN 8**, but PyTorch 2.8 ships cuDNN 9. VoiceStudio side-loads a cuDNN-8 copy
from `.venv\Lib\site-packages\cudnn8_compat\` — but the step that installs that
folder only ever lived in the dev-loop setup script, which isn't bundled into
the packaged app. **Packaged installs never had these libraries at all**, so
reinstalling never fixed it ([#827](https://github.com/debpalash/VoiceStudio/issues/827)).

**Fix:** update to the latest build and relaunch — the app's bootstrap now
detects a CUDA machine and installs the cuDNN-8 libraries into the backend venv
automatically at launch ([#869](https://github.com/debpalash/VoiceStudio/pull/869)).
(The check is skipped — and its negative result cached — on CPU/AMD/Apple
machines, so non-NVIDIA launches stay instant.)

If the automatic install can't run (offline / restricted network), install
manually into the backend venv, then restart:

```
uv pip install --no-deps --python .venv\Scripts\python.exe --target .venv\Lib\site-packages\cudnn8_compat nvidia-cudnn-cu12==8.9.7.29
```

(On Linux the target is `.venv/lib/pythonX.Y/site-packages/cudnn8_compat`.)

Or sidestep cuDNN 8 entirely: switch the ASR backend to **PyTorch Whisper** in
**Model Catalogue → Models**. It runs on PyTorch's own stack (cuDNN 9, bundled with
torch) and needs no cuDNN-8 DLL — it loads its Whisper pipeline on demand (no
extra env var).

## 11. IndexTTS / CosyVoice / ChatterboxTTS clash

**Symptom:** installing one of these engines breaks the others — e.g. after
installing CosyVoice, IndexTTS errors out with import conflicts.

**Cause:** these engines pin incompatible transformer / torch versions inside
their own engine venvs. Pre-v0.3 they shared a single venv.

**Fix:** Phase 2 ships subprocess isolation per engine (each engine runs in
its own venv). For v0.3, workaround: install only one of the conflicting
engines per VoiceStudio copy. See [docs/engines/cosyvoice.md](../engines/cosyvoice.md)
for the dedicated CosyVoice path.

**Linked issue:** [#55](https://github.com/debpalash/VoiceStudio/issues/55)

**Same class, ASR side:** the `nemo-parakeet` ASR engine has the identical
problem and currently has **no safe install path** at all — `nemo_toolkit[asr]`
hard-pins `transformers>=4.57,<4.58`, which is unsatisfiable alongside
VoiceStudio's own `transformers>=5.3` requirement. Installing it into the
shared venv breaks the backend outright. Do not `pip install nemo_toolkit`
into VoiceStudio's environment; if you want to try it, use a separate Python
environment. Isolated-venv support for this engine (matching CosyVoice/
dots-tts) is tracked in [#974](https://github.com/debpalash/VoiceStudio/issues/974).

## 12. CUDA PyTorch wheel download fails on first run

**Symptom:** first-run setup stops at **Installing dependencies** with a failure
that mentions `torch` and a `download.pytorch.org` (or `download-r2.pytorch.org`)
URL — e.g. `Failed to download torch==2.8.0+cu128 …win_amd64.whl`. The app then
won't launch.

**Cause:** on Windows/Linux NVIDIA machines, VoiceStudio installs the CUDA PyTorch
build (`torch` + `torchaudio`) from PyTorch's own index. That CUDA wheel is
large (~2.5 GB), so a flaky or restricted network drops it partway. This is a
download/network problem, **not** a bug in VoiceStudio — but the CUDA wheels come
from a *named, explicit* index that a PyPI mirror (`UV_DEFAULT_INDEX`) cannot
redirect, so the generic mirror trick doesn't help here.

**Fix, in order:**

1. **Clean & Retry.** Large downloads frequently succeed on a second attempt —
   VoiceStudio already retries each request 5× with long timeouts, and a fresh
   attempt restarts cleanly.
2. **Use a VPN** if your network throttles or blocks the PyTorch CDN.
3. **Provide the wheels manually (offline path).** Download the two wheels that
   match your machine from a source you *can* reach (the official
   [pytorch.org](https://pytorch.org/get-started/locally/) wheel index or a
   regional mirror), then drop them in the wheel folder and **Clean & Retry** —
   VoiceStudio will install from your local copies instead of the network:
   - Folder: **`<env dir>/wheels`** (the exact path is printed in the error
     message and in the setup log; `<env dir>` is your chosen install/storage
     location).
   - Files: the `torch` **and** `torchaudio` wheels for your exact Python/OS/CUDA
     — e.g. `torch-2.8.0+cu128-cp311-cp311-win_amd64.whl` and the matching
     `torchaudio-2.8.0+cu128-cp311-cp311-win_amd64.whl`. They must match the
     pinned versions (shown in the failing URL).
   - On retry, VoiceStudio re-resolves the install using those local wheels; the
     rest of the (small) dependencies still come from PyPI/your mirror.

If you don't have an NVIDIA GPU, you don't need the CUDA build at all — a CPU /
Apple-Silicon install skips this index entirely.

**Linked issue:** [#569](https://github.com/debpalash/VoiceStudio/issues/569)

## 13. Stuck on the download page / incomplete model cache ("only `refs/`")

**Symptom:** the setup screen never finishes the model download and you can't
reach the main app. Looking in the HF cache, a model folder
(`models--k2-fsa--OmniVoice`, `models--Systran--faster-whisper-large-v3`) has
`refs/` and maybe `config.json` but **no weight files** (`blobs/` empty or tiny).

**Cause:** the download started but the large weight shards never finished —
almost always the connection **dropping, throttling, or being blocked** mid-pull
(corporate/school proxy, VPN, antivirus quarantining the multi-GB file, or a
region where `huggingface.co` is slow/blocked). The app retries and verifies
weights, but a connection that *trickles* rather than dies can stall for a long
time.

**Fix — force a clean re-download:**

1. **Fully quit VoiceStudio.** Check Task Manager (Windows) / Activity Monitor
   (macOS) and end any leftover `omnivoice` / `python` process — a half-running
   one keeps the cache locked.
2. **Delete the incomplete model folder(s) entirely** from the HF cache (the
   whole `models--…` folder, not just `refs/`). Leave other models alone:
   - `models--k2-fsa--OmniVoice`
   - `models--Systran--faster-whisper-large-v3`
3. **Relaunch** — the download page re-pulls from scratch.

**If it stalls again at the same spot**, the download is being blocked — try, in
order:

- **Antivirus/firewall** — temporarily disable it for the download (large model
  files are a common false-positive quarantine), then re-enable.
- **Connection** — use a stable, direct connection; pause any VPN; avoid
  corporate/school networks.
- **Region mirror** — if `huggingface.co` is slow/blocked where you are,
  VoiceStudio normally handles this automatically: with no endpoint explicitly
  configured it probes both the official endpoint and the `hf-mirror.com`
  community mirror and downloads from whichever works (downloads are
  checksum-verified either way; see
  [downloading-models.md](../downloading-models.md)). To check or re-test the
  automatic pick, use **Settings → Models → Hugging Face mirror → Test
  again**. To pin a mirror yourself, pick one in the same panel (or the
  quick-pick the first-run system check offers when nothing is reachable), or
  set it as an env var before launching and relaunch:
  - macOS/Linux: `export HF_ENDPOINT=https://hf-mirror.com`
  - Windows (PowerShell): `[Environment]::SetEnvironmentVariable("HF_ENDPOINT","https://hf-mirror.com","User")`

**Manual fallback** (if downloads keep failing), pull the weights yourself into
the same cache, then relaunch:

```bash
pip install -U "huggingface_hub[cli]"
huggingface-cli download k2-fsa/OmniVoice
huggingface-cli download Systran/faster-whisper-large-v3
```

(If VoiceStudio uses a custom models directory, set `HF_HOME` to it first so the
files land where the app looks.)

> Newer builds detect an incomplete cache and re-offer the download instead of
> stranding you on this page — update once the fix is in your channel.

**Linked issue:** [#622](https://github.com/debpalash/VoiceStudio/issues/622)

## 14. "Can't reach the local backend" *during* generation / transcription / dubbing

**Symptom:** the app worked at startup (you reached the main menu and the model
loaded), but the moment you **generate audio, dub a video, transcribe, or
dictate**, it spins for a long time and then shows **"Can't reach the local
backend."** The backend log ends right after a line like `whisperx transcribing
…tmpXXXX.wav` (or a generate) with nothing after it — i.e. the backend is
**alive**, the GPU *job* is what stalled.

**Cause:** this is **not** a connection, download, or "network mirror" problem —
the backend started fine. A GPU job (a **generate** on the TTS model, or an ASR
transcribe with WhisperX/faster-whisper **large-v3**) is too heavy for the
available compute and runs for minutes; because it wedges its GPU-pool worker,
every *other* request — including the next generate and the health check — is
starved, which the UI surfaces as an unreachable backend. The usual trigger is
**VRAM starvation on NVIDIA**: models contend for memory on an 8 GB-class GPU
(the log shows e.g. `GPU pool sized … 7.0 GB free`). CPU-only machines hit the
same wall on long clips. This is the same root cause whether the last thing you
did was `generate:start (audio)`, a dub, or a dictation.

> There is **no "Network → Restricted/Global mirror" toggle** in Settings — that
> control (the footer/Sharing **Network** button) is for **LAN sharing**, not
> downloads. If someone pointed you there for this error, it was the wrong knob.

**Fix — reduce ASR load (any one of these):**

1. **Pick a smaller ASR model / engine** in **Model Catalogue → Models** — e.g.
   faster-whisper **medium** or **small**, instead of large-v3. Biggest win on
   low-VRAM GPUs.
2. **Free VRAM**: **Flush the TTS model** before dubbing so ASR isn't competing
   for memory (top toolbar → Flush → "Unload all + flush", or per-model from
   Model Catalogue → Models — see [Flush caches / Unload resident model](../performance.md#flush-caches--unload-resident-model)
   for exactly what it frees and the API equivalents for scripts), or
3. **Run ASR on CPU** (slower but reliable) if your GPU is small.
4. **Test with a 10-second clip** first — if that returns quickly, it confirms a
   compute/VRAM limit rather than a true hang.

Newer builds **bound** every GPU job — whole-file transcription, **chunked dub
transcription**, **and** TTS generation: instead of hanging forever and starving
the backend, a wedged job fails after a timeout with this exact guidance. Tune
the bounds with `OMNIVOICE_ASR_TRANSCRIBE_TIMEOUT_S` (whole-file transcription)
and `OMNIVOICE_GENERATE_TIMEOUT_S` (generation) — both in seconds, default 300
— and `OMNIVOICE_TRANSCRIBE_CHUNK_TIMEOUT_S` (per-chunk dub transcription,
default 120). **Raise** them for very long single files/generations, **lower**
them to fail faster on a small machine.

CPU-only hosts use a bounded 600-second generation floor because correct CPU
synthesis can take longer than the accelerated five-minute budget. Override it
with `OMNIVOICE_CPU_GENERATE_TIMEOUT_S` — an explicit value here always
governs CPU-family generation, independent of `OMNIVOICE_GENERATE_TIMEOUT_S`.
Setting *only* `OMNIVOICE_GENERATE_TIMEOUT_S` still governs CPU hosts too, the
same as it always has (a quick way to lower the watchdog everywhere with one
var); it only stops doing so once you also set an explicit
`OMNIVOICE_CPU_GENERATE_TIMEOUT_S`, which then takes precedence for CPU jobs.

Both budgets are also editable from **Settings → Performance & Device →
Compute-time budget** — no env var or config file needed. A value saved there
persists across restarts but only takes effect on the *next* backend restart
(the running process already read the old value at startup), which the panel
states — and if an external env var (shell profile, `.env`, Docker `-e`,
systemd unit, …) is already providing the same key, the panel says so instead,
since that external value keeps winning on every future restart too, not just
this one.

**Two things changed here** ([#1190](https://github.com/debpalash/VoiceStudio/issues/1190)):

- **Waiting in line is no longer counted as compute.** The generate budget used
  to start the moment a job was *queued*, so on a 1-worker machine a request
  sitting behind a busy one burned its whole 300s without executing a single
  instruction and then blamed your hardware. The budget now starts when a
  worker actually picks the job up. Queue wait has its own, far more generous
  bound (`OMNIVOICE_GPU_QUEUE_TIMEOUT_S`, default 1800s); crossing *that*
  reports a saturated pool — an explicitly **retryable** condition, not a
  too-heavy job.
- **The old message over-promised.** It said "Capacity was restored
  automatically". It wasn't: Python can't kill the abandoned worker thread, so
  it keeps running — and keeps its VRAM — until it finishes on its own. The
  pool reset only stops *new* work from queueing behind it. That is why an
  immediate retry often failed too, and why a long batch could die a few
  segments in. **Give the abandoned job time to drain (or restart the backend)
  before retrying.** The message now says so.

The generation budget also **scales with the length of the text** (the floor is
`OMNIVOICE_GENERATE_TIMEOUT_S`, plus 1 second per 40 characters past the first
1200), and every path uses it — the streaming preview the UI tries first, batch
dubbing, `/v1/audio/speech`, and dub/archetype previews included. Long inputs
should not need the env var at all.

**Scripting the API?** `/v1/audio/speech` now tells you about pressure instead
of going quiet: a **429** with a `Retry-After` header means the request was
*refused before it started* because the worker pool is already backed up (safe
to retry verbatim — nothing ran), and a **503** with `Retry-After` means the
job was accepted but hit its bound. Both carry
`X-VoiceStudio-Retryable: true`, and the streaming NDJSON error frame carries
`"retryable": true` with `retry_after`. Back off on those rather than
hammering — on a 1-worker machine, concurrent requests serialize by design.

**If transcribe timeouts keep repeating back-to-back**, pool resets aren't
recovering the underlying hang — the wedged thread keeps its VRAM until the app
exits. The error message will then recommend switching the ASR engine to
**Faster-Whisper (crash-isolated subprocess)** (`faster-whisper-isolated`) in
**Model Catalogue → Engines**: it runs transcription in a separate process that can be
force-killed to reclaim a hung transcribe *and* its VRAM, at a small per-call
overhead. It reuses your existing faster-whisper install (nothing extra to
download). VoiceStudio never switches engines automatically — this stays your
call.

> **Seeing "The backend crashed (exit code …)" instead?** That's the other
> failure mode: the backend **process died** (native CUDA abort, out-of-memory
> kill, DLL crash) rather than hanging. Newer desktop builds detect the death,
> restart the backend automatically (giving up after 3 crashes in 10 minutes),
> and show a crash notice with a **View crash details** button (exit code +
> the last error output). Use **Report this bug** from that notice — the crash
> evidence is attached to the prefilled GitHub issue automatically, with home
> paths scrubbed. The raw markers live next to the backend logs in
> `backend_crash_markers.json`. Markers are per-version: after you update the
> app, notices recorded by the previous version are cleaned up rather than
> resurfacing — the update may well have fixed that crash.
>
> Outside the desktop app (browser dev, Docker, LAN share) the same notice is
> raised by the **backend itself** on its next start — see **section 14c**.

## 14b. "Can't reach the local VoiceStudio backend" flashing during startup or an automatic restart

**Symptom (older builds):** while the backend was still starting — or while the
desktop shell was auto-restarting a crashed backend — every click produced a
**"Can't reach the local VoiceStudio backend"** toast, over and over, even though
the backend came back on its own a few seconds later.

**Cause:** a real backend start/restart takes **10–20+ seconds** (Python venv
spawn plus the PyTorch import), but the UI's transport retry only bridged ~3
seconds before giving up — so every request landing inside that window
dead-ended with the scary toast, which read as a recurring bug rather than a
self-heal in progress.

**Fixed:** newer desktop builds ask the shell whether a start/restart is
actually in progress and simply **wait for it** (up to 2 minutes, matching the
shell's own restart budget) instead of erroring, and show a single pinned
**"backend is restarting — hang tight"** banner while it happens, followed by a
"backend is back" confirmation. A backend that is *truly* dead (the shell gave
up, or you're not running the desktop app) still errors promptly. If you see
the error persistently on a current build, that's section **14** (a wedged GPU
job), section **14d** (the backend never started), or the crash notice above —
not this window.

Desktop startup, **Retry**, storage reset, setup re-entry, in-app uninstall,
app shutdown, and automatic crash recovery also share one backend lifecycle
owner. Overlapping start attempts wait and attach to the healthy process,
while reset/setup/uninstall keep exclusive ownership through teardown and
disk changes, and shutdown joins any in-progress launch before stopping it.
They no longer launch a second backend that fails on port 3900, leave a
misleading crash notice, delete an environment from under a starting child,
or orphan one on exit. Quitting also interrupts a first-run `uv` install
instead of waiting for a long download to finish. Unix builds give backend
lifespan cleanup a bounded SIGTERM grace period; Windows' hidden backend has no
console, so its initial stop is best-effort and may proceed directly to the
bounded forced tree cleanup. Surviving subprocess engines cannot retain ports
or files (#1635).

## 14c. "Can't reach the backend" in a browser — `bun run dev`, Docker, or LAN share

**Symptom:** you're using VoiceStudio **outside the desktop app** — the dev
stack (`bun run dev`), a Docker deployment, or a shared/remote backend — and
requests fail with a "can't reach the backend" error.

These deployments have no desktop shell to supervise the backend. Newer builds
make the backend **self-forensicate**, and the dev runner adds bounded recovery:

- **The error tells you what it knows.** It now says whether the backend *was
  answering and stopped* ("it was answering 12 s ago … likely crashed or was
  killed mid-request") or *never answered this session* ("it may never have
  started" — a port conflict or failed setup), and points at the right logs
  for **your** deployment: the `bun run dev` terminal + `omnivoice.log` in
  dev, `docker logs <container>` / `journalctl` on a server. (If Docker
  serves the page itself, the page can go down together with the backend —
  check the container first.)
- **Dev crash recovery and exit banner.** `bun run dev`'s backend runs through
  `scripts/dev-backend.mjs`, which owns Python-file reloads directly so a dead
  server cannot hide behind a still-running uvicorn reload parent. When the
  backend dies with a non-zero exit, a boxed
  banner prints the exit code/signal, the last 20 lines of `omnivoice.log`,
  and an OOM-check hint (`journalctl -k | grep -i oom` on Linux), then restarts
  it after one second. Three restarts are allowed in a rolling 60-second
  window; a fourth crash exits and lets `concurrently` tear down the broken
  stack. Ctrl+C and other deliberate shutdowns never restart it.
- **Crash notice on the next start.** The backend keeps a **run sentinel**
  (`run_sentinel.json` in its data folder) while running and clears it on a
  clean shutdown. If a start finds a stale sentinel whose process is gone,
  the previous run died uncleanly: a record is written to
  `last_run_crash.json` (death window, last activity — e.g. "generate" or
  "transcribe" — and a scrubbed tail of `omnivoice.log`), the UI shows the
  same crash notice the desktop app shows (**View crash details** → the log
  tail; **Report this bug** → the evidence rides along in the prefilled
  GitHub issue), and `GET /system/last-run-crash` exposes it to scripts.
  Records are capped at the last 3, survive being dismissed (bug reports
  still need them), and are per-version like the desktop markers — after an
  update, notices from the previous version don't resurface.

Where the forensics live: `omnivoice.log`, `run_sentinel.json`, and
`last_run_crash.json` are all in the backend's data folder
(`~/Library/Application Support/OmniVoice` on macOS, `%APPDATA%\OmniVoice` on
Windows, `~/.omnivoice` on Linux, or `$OMNIVOICE_DATA_DIR` — the Docker image
mounts it as the `omnivoice_data` volume).

## 14d. "Can't reach the local VoiceStudio backend" when the backend never started

**Symptom (older builds):** the app opened, but every action failed with
**"Can't reach the local VoiceStudio backend — it may still be starting up, or it
stopped."** Waiting and retrying never helped, and the message gave you nothing
to act on or to put in a bug report.

**Cause:** the message was wrong *and* evidence-free. The backend was not
starting up and had not merely stopped — it had **failed to start**, and the
desktop shell knew exactly why: it holds the exit code plus a ~30-line tail of
the backend's stderr, or the specific reason setup refused (an Intel Mac, a
failed `uv sync`, a network blocking GitHub). The UI could not read that
diagnosis, so every distinct failure collapsed into the same generic sentence.

**Fixed:** the app now surfaces the shell's own diagnosis. When a request fails
because the backend could not start, you get:

- **"The backend couldn't start"** instead of "it may still be starting up" —
  it names what happened rather than guessing.
- **A "See why" notice** whose details dialog shows the exit code and the
  captured stderr tail verbatim, plus the same actionable hints the setup
  screen gives (broken venv, blocked GitHub, port in use, unsupported Intel
  Mac — where retrying can never help, no Retry is suggested).
- **A "Report" button** that opens a prefilled GitHub issue with that output
  already attached, with your home directory path replaced by `~` and any
  credential-shaped strings redacted before anything leaves the machine.

The reason is also **retained** across a Retry or an automatic respawn, so a
later attempt can't erase the diagnosis of the first one.

**From source (`bun desktop`)?** If the app builds but the window never comes
up, the shell now prints the exit code and where to look (the cargo/tauri
output above it, plus `omnivoice.log` and `backend_err.log` in your VoiceStudio
data folder) instead of exiting silently.

If Cargo stops before the window is built with `Package gdk-3.0 was not found`,
`pango.pc` missing, `libsoup-3.0` missing, or `javascriptcoregtk-4.1` missing,
the Ubuntu/Debian WebKitGTK development packages are absent. Install the full
package block in the [Linux source-build guide](linux.md#building-from-source),
then rerun `source "$HOME/.cargo/env"` and `bun desktop`. Do not set a custom
`PKG_CONFIG_PATH` unless the libraries were deliberately installed outside the
system package manager.

**Still stuck?** Open the details, copy the output, and file it with **Report**
— that output is the thing that makes the failure diagnosable.

## 15. Stuck at "preparing" forever after a crash / BSOD (Windows)

**Symptom:** after an unclean shutdown (Windows BSOD, forced power-off), every
launch sits on the "preparing" splash indefinitely — even though the backend is
actually healthy (its log shows models loaded, and
`http://127.0.0.1:3900/health` answers `{"status":"ok"}` in a browser). The
WebView log contains:

```
IPC custom protocol failed, Tauri will now use the postMessage interface instead
TypeError: Failed to fetch
```

**Cause:** the crash corrupted cache directories inside the WebView2 profile at
`%LOCALAPPDATA%\com.debpalash.omnivoice-studio\EBWebView`. Both the IPC custom
protocol *and* its postMessage fallback break, so the splash never hears the
"ready" signal from the app shell (issue #879).

**Fix:** current builds handle this automatically — if the splash gets no IPC
signal within ~10 s it checks the backend over plain HTTP and proceeds on its
own; if the backend isn't up either, after ~45 s a recovery panel appears with
**Repair and restart** (Windows), which clears cache-only directories and
relaunches. It deliberately preserves `Default\Local Storage` and
`Default\IndexedDB`, where browser-owned settings and long-form projects live.

On older builds (≤ 0.3.8), or if the automatic repair fails, do it manually:
quit VoiceStudio, delete only the cache directories below, then start the app
again. Do not delete the whole `EBWebView` profile; doing so also deletes
browser-owned projects and settings.

<!-- validate: skip -->
```powershell
$voiceStudioWebView = "$env:LOCALAPPDATA\com.debpalash.omnivoice-studio\EBWebView"
@(
  "Default\Cache", "Default\Code Cache", "Default\GPUCache", "Default\DawnCache",
  "Default\Service Worker\CacheStorage", "Default\Service Worker\ScriptCache",
  "GPUCache", "DawnCache", "ShaderCache", "GrShaderCache", "GraphiteDawnCache"
) | ForEach-Object {
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $voiceStudioWebView $_)
}
```

## 16. macOS: microphone permission never prompts, VoiceStudio never appears in System Settings

**Symptom:** clicking record shows "Microphone access denied. macOS: open
System Settings → Privacy & Security → Microphone and enable VoiceStudio" —
but VoiceStudio never appears in that list, so there's nothing to enable.
`NSMicrophoneUsageDescription` is present in the app's `Info.plist`, and
resetting the permission (`tccutil reset Microphone
com.debpalash.omnivoice-studio`) followed by a relaunch changes nothing — no
system prompt ever appears.

**Cause:** the app bundle was missing the Hardened Runtime *entitlement* for
microphone access. An earlier revision of this section blamed an upstream
Tauri/WebKit limitation — that was wrong (a community contributor,
[@MahdiHedhli](https://github.com/MahdiHedhli), read the sources more
carefully and found the real gap). wry's `WKUIDelegate` already grants the
WebKit-layer media-capture request; but Tauri's macOS bundler enables
Hardened Runtime by default, and Hardened Runtime blocks microphone hardware
access unless `com.apple.security.device.audio-input` is present in the
signed binary's entitlements — regardless of `Info.plist`'s
`NSMicrophoneUsageDescription` (that only supplies the prompt *text*).
Without the entitlement, macOS's TCC layer never registers a request, which
is exactly why the app never appears in the System Settings list.

**Fix:** ships in the release after v0.3.12 (the bundle now carries
`src-tauri/entitlements.plist` — [#1016](https://github.com/debpalash/VoiceStudio/pull/1016),
contributed by the same person who diagnosed it). Update and live recording
works, with a normal macOS permission prompt on first use.

**Workaround on older builds (≤ v0.3.12):** record your voice sample in any
other app (Voice Memos, QuickTime, etc.) and upload the resulting file in
VoiceStudio instead of using live recording — upload-based cloning is
unaffected and works normally.

**Linked issue:** [#1013](https://github.com/debpalash/VoiceStudio/issues/1013)

> **Tip:** current builds surface the live OS grant state in-app — **Settings →
> Permissions** shows whether the microphone (and, on macOS, Accessibility) is
> granted, denied, or not asked yet, with an **Open Settings** button that
> deep-links the exact OS pane described above. The dictation blocker rechecks
> Accessibility while it is visible and closes as soon as macOS reports the grant.

## 17. Windows: backend won't start with a non-English (CJK) username — `UnicodeDecodeError` in `site`

**Symptom:** the app never gets past first-run setup / "starting_backend", and
the backend log shows the interpreter dying before any VoiceStudio code runs:

```
Fatal Python error: init_import_site: Failed to import the site module
  File "<frozen site>", line 188, in addpackage
UnicodeDecodeError: 'gbk' codec can't decode byte 0x80 in position 11: illegal multibyte sequence
```

**Cause:** Python 3.11's `site` module opens every `.pth` file in
`site-packages` using the ANSI code page — even with `PYTHONUTF8=1` set. When
the managed environment lives under a Windows user profile whose account name
contains non-ASCII characters (most commonly CJK), `uv sync`'s editable
install `.pth` embeds that path in UTF-8, which is rarely a valid byte
sequence in the active ANSI code page (`gbk`, `shift_jis`, etc.), and the
interpreter can never start (#1783).

**Fix:** current builds resolve the managed environment through Windows' 8.3
short filename for any path containing non-ASCII bytes (the same trick
already used for the HuggingFace cache — see
[`backend/core/config.py`](../../backend/core/config.py)) whenever there is
no usable environment yet, or an existing one shows exactly this crash — so
a first-time install, and an install already broken by this bug, both land
at an ASCII-safe path automatically with no user action needed. The old
broken environment (if any) is left in place, not deleted, in case manual
recovery is ever needed. An existing environment that already works — ASCII
path or not — is never touched or relocated.

**Prevention, on a brand new install:** on the first-run setup screen (before
clicking through it), the **Change…** button on the "App environment" row
(or "Portable folder" in portable mode) lets you pick an ASCII-only path up
front — nothing below is needed if you do this before setup completes.

If it still happens — most likely because Windows' 8.3 short filenames are
off on the system drive, or the affected folder already existed before this
fix shipped — the app names this cause specifically rather than the generic
"backend never reported ready." By the time this message can appear, setup
has already been confirmed (it's only reached after the first-run screen
hands off to the installer), so the error screen you're actually looking at
offers only **Retry** and **Clean & Retry** — neither changes where the
environment is stored, so both fail identically, and the first-run picker
above is no longer reachable either. The right fix depends on which install
mode you're in:

- **Standard (non-portable) install:** quit VoiceStudio, open (creating it
  if it doesn't exist) `%LOCALAPPDATA%\com.debpalash.omnivoice-studio\config.json`
  in a text editor, add `"env_dir": "C:/VoiceStudio/env"` (any path using
  only English letters/numbers — forward slashes are fine on Windows), save,
  and relaunch.
- **Portable install:** the `env_dir` config key above does **not** apply —
  portable mode resolves its own environment folder from the portable
  location and never consults it. Quit VoiceStudio, then either move the
  whole VoiceStudio folder (the app plus its `OmniVoiceStudio-Data` folder)
  to an ASCII-only path and run it from there, or create a `portable.path`
  text file beside the app containing one line — an absolute ASCII-only path
  for the data folder (e.g. `C:\VoiceStudio\Data`) — and relaunch.

Re-enabling 8.3 short filenames (`fsutil 8dot3name`) is deliberately **not**
recommended here: the setting is per-volume and only affects directories
created *after* it's changed, so toggling it does nothing for a folder that
already exists — it would not actually fix this without also recreating the
folder, which the ASCII-path options above already do more reliably.

**Linked issue:** [#1783](https://github.com/debpalash/VoiceStudio/issues/1783) (auto-captured from [#1771](https://github.com/debpalash/VoiceStudio/issues/1771))

## Dub: "translation engine needs the optional … package"

**Symptom:** in the Dub tab, translating fails with e.g. *"The 'google'
translation engine needs the optional `deep_translator` Python package, which
isn't installed in this backend."*

**Cause:** the online translation engines (Google / DeepL / Microsoft / MyMemory
via `deep_translator`, and the LLM provider via `openai`) are **optional** and
not bundled. Only **Argos** and **NLLB** work out of the box.

**Fix:**
- **From-source / Docker install:** click the highlighted **Install** button next
  to the *Engine* label in the Dub tab (or run `uv pip install deep_translator`
  in the backend venv) and restart the backend.
- **Packaged installer build:** in-app install is disabled (read-only signed
  environment). Click the highlighted button to open the popover and **Switch to
  Argos (bundled, offline)** — or copy the command to run it in a from-source
  checkout.

Full guide: [dubbing/translation-engines.md](../dubbing/translation-engines.md#installing-optional-translation-engines-from-source-vs-packaged-build).

## First-run setup fails on a restricted network (GitHub/PyPI blocked)

On networks that block or can't resolve **GitHub**, the first-run bootstrap may
fail to download the managed Python (`uv venv ... failed`, often a DNS error).
VoiceStudio now tries, in order: the default GitHub host → a gh-proxy mirror → your
**system Python** (if 3.11+ is installed). If all three fail:

1. **Install Python 3.11+** from <https://www.python.org/downloads/> (on Windows,
   tick *"Add Python to PATH"*), then relaunch — VoiceStudio will use it.
2. **Point at a reachable mirror** for the Python download:
   - `UV_PYTHON_INSTALL_MIRROR=https://gh-proxy.com/https://github.com/astral-sh/python-build-standalone/releases/download`
3. **Point at a PyPI mirror** for the dependency install (`uv sync`):
   - China: `UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple` (or `https://mirrors.aliyun.com/pypi/simple`)
   - Fully-blocked networks (e.g. some regions): use a VPN — there is no
     government-blessed PyPI mirror to rely on.
4. The bootstrap already raises the network budget for you
   (`UV_HTTP_TIMEOUT=120`, `UV_HTTP_CONNECT_TIMEOUT=30`, `UV_HTTP_RETRIES=5`);
   you can raise them further in the environment if a mirror is very slow.

**Linked issues:** [#130](https://github.com/debpalash/VoiceStudio/issues/130), [#60](https://github.com/debpalash/VoiceStudio/issues/60), [#57](https://github.com/debpalash/VoiceStudio/issues/57)

## Workspace navigation crashes with `insertBefore` / `NotFoundError`

This was a `v0.5.0` workspace-lifecycle bug exposed by rapid Launchpad ↔ Dub
navigation while media renderers were cleaning up. Current builds isolate each
workspace under its own DOM owner. Update VoiceStudio; no model or project data
repair is required.

**Linked issue:** [#1590](https://github.com/debpalash/VoiceStudio/issues/1590)

## Uninstalling / removing all of VoiceStudio's data

VoiceStudio is fully local — no accounts, no services, nothing to deactivate. To
reclaim disk space or fully remove it, run the uninstaller, which lists every
VoiceStudio folder with its size (dry-run first) and deletes on `--yes`:

```bash
scripts/uninstall.sh            # macOS/Linux — dry-run
scripts/uninstall.sh --yes      # delete app data/env/config/logs
scripts/uninstall.sh --yes --models   # also delete the shared HF model cache
```

```powershell
powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1 -Yes   # Windows
```

The two big folders are the **model cache** (Hugging Face weights, several GB)
and the **managed Python env** (`project/.venv`, a few GB). The complete
per-platform path list, env-var overrides, portable-mode note, and the steps to
remove the app binary itself are in
[docs/install/uninstall.md](uninstall.md).

**Linked issue:** [#1089](https://github.com/debpalash/VoiceStudio/issues/1089)
