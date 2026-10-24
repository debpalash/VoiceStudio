# Engine venvs & disk usage

The Model Catalogue now exposes a structured disk breakdown before install:
model-weight download, package download, unique installed bytes, potentially
shared bytes, temporary free-space requirement, destination volume, and the
estimate confidence. Missing package or deduplication measurements are shown as
unknown rather than zero. Opening an installed engine's disk details measures
its model, environment, shared cache, and app-owned total separately. These
values come from `config/models.yaml` and the sidecar installer specification;
the UI does not maintain its own size table.

Most engines run in-process in VoiceStudio's main environment. A few
(**IndexTTS2**, **MOSS-TTS-v1.5**, **dots.tts**, and any engine whose
dependencies conflict with the parent's `torch`/`transformers` pins) run in a
**dedicated sidecar venv** so their pins can't break the rest of the app. Those
sidecars are where disk adds up — this page explains why, and how the on-disk
cost is kept down.

## Why a sidecar needs its own venv

IndexTTS2 pins `transformers<5`, but VoiceStudio requires `transformers>=5.3`.
You can't have both in one environment, so IndexTTS2 gets its own venv created
on first use (`uv venv` + `uv pip install`, see
`backend/engines/indextts/bootstrap.py`). The cost is a second copy of the
heavy ML stack — most of which is **torch + the bundled CUDA libraries**.

## How big is "a second torch"?

Measured (2026-06):

| Platform | torch CUDA wheel | + bundled CUDA libs |
|---|---|---|
| Linux (cu128) | ~0.83 GiB | several GiB of `nvidia-*` packages on top |
| Windows (cu128) | ~3.2 GiB (DLLs bundled in the wheel) | — |

So a sidecar that pins a **different** torch version than the parent is a
multi-GB add. A sidecar that pins the **same** torch + CUDA build shares almost
all of it (see below).

## uv dedupes identical wheels — for free, with one condition

uv installs packages by linking from a global wheel cache into each venv's
`site-packages`. The link mode:

- **macOS + Linux** — `clone` (copy-on-write reflink). N venvs that install the
  same wheel share the bytes until one is modified — effectively one copy on
  disk.
- **Windows** — `hardlink`. Same effect on a single volume.

**The one condition:** the cache and the venv must be on the **same
filesystem**. If `UV_CACHE_DIR` lives on a different drive than the engine
venvs, uv falls back to a full **copy** (no dedup, slower). Keep them together.

Dedup is **per identical wheel**. `torch==2.6.0+cu124` and `torch==2.8.0+cu128`
are different wheels → **zero** sharing → a full extra multi-GB copy. The single
biggest disk decision for a sidecar is therefore: **pin the same torch build as
the parent whenever the engine allows it.** When it doesn't (IndexTTS2's
`transformers<5` forces an older torch line), the second copy is the
unavoidable price of isolation — not a bug.

The opt-in #498 engines illustrate both sides: **dots.tts** pins
`torch==2.8.0` — the **same** build the parent constrains to — so it shares
almost all of torch with the main venv and only its `transformers==4.57` +
model deps are new. **MOSS-TTS-v1.5** pins `torch==2.9.1+cu128`, a **different**
build, so it pays a full extra multi-GB torch copy on CUDA hosts (the price of
running an 8B model whose stack pins `transformers==5.0`).

> On Linux, the `nvidia-*` CUDA packages are separate wheels, so even across
> *different* torch versions any `nvidia-*` whose pinned version happens to
> match is still shared. On Windows the CUDA DLLs live inside the one torch
> wheel, so nothing is shared across torch versions.

## Practical guidance

- Keep `UV_CACHE_DIR` and the engine venvs on one filesystem (the default —
  both under your home dir — already satisfies this). **The app enforces this
  automatically**: when the app env or the engine venvs live on a different
  volume than uv's default cache (D:-drive install, portable mode), every
  managed `uv` invocation gets `UV_CACHE_DIR` pointed at a cache next to the
  venvs (`<env root>/uv-cache`, `<data dir>/engines/.uv-cache`). An explicit
  `UV_CACHE_DIR` you set yourself always wins.
- On Linux ext4 (no reflink), `export UV_LINK_MODE=hardlink` guarantees dedup
  on any single filesystem; the default `clone` only dedupes on
  reflink-capable filesystems (XFS-with-reflink, btrfs, APFS).
- Reclaiming space: deleting a sidecar venv (`backend/engines/<id>/.venv/`)
  frees its unique files; shared cache bytes stay until `uv cache prune`.
- Forward-looking: PyTorch's experimental **wheel variants** (shipped in 2.8)
  will eventually let `uv install torch` auto-pick the right CUDA build, and
  uv already exposes `--torch-backend=auto`.

If uninstall reports that an engine could not be removed, close processes using
its files and check folder permissions, then retry. VoiceStudio keeps the
engine's saved location until deletion succeeds, so a locked file does not
make the remaining installation disappear from management.
The loaded-models panel reports each live sidecar's engine-compatible compute
device. CPU-only runtimes such as Supertonic-3 stay labelled CPU on CUDA and
Apple Silicon hosts; a failed device probe is shown as unknown rather than
assuming the child uses the host GPU.
