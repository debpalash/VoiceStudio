# Downloading models — speed & troubleshooting

VoiceStudio downloads models from the Hugging Face Hub on first use. This page
explains how downloads are made fast, how to read the progress, and what to do
on slow or restricted networks.

Unknown or user-managed cache layouts are allowed through so existing manual
engine installs remain compatible.

## Download backend: legacy LFS by default (accurate progress)

VoiceStudio ships `hf_xet` (Hugging Face's chunked, parallel, dedup transfer
backend — the IDM/uGet-style fast path), **but currently runs with Xet
disabled** (`HF_HUB_DISABLE_XET=1`, set by the app). Reason: Xet's transfer
reports progress out-of-band and bypasses the byte-level progress hook, so the
download UI couldn't show real bytes/speed. Until a proper Xet progress hook
lands, the app forces the **classic LFS path**, which streams through the
standard progress reporter and gives accurate downloaded/remaining/speed.

To keep that path **fast** despite Xet being off, the app runs a built-in
**multi-connection (segmented) downloader on by default**. It keeps up to eight
byte ranges in flight, caps each range at 16 MB, and records every completed
range in a resume manifest. After a dropped connection, only unfinished ranges
are fetched again: the install retries the accelerator and resumes that
manifest. An origin without range support remains a single stream within the
accelerator. A non-transient failure or the final attempt disables acceleration
for the rest of that install and lets plain `snapshot_download` take over. Adding a
free Hugging Face token (first-run setup, or Settings → Credentials) makes this
faster still — authenticated downloads get higher rate limits and fewer stalls.
To force the old single-stream path, set `OMNIVOICE_SEGMENTED_DOWNLOAD=0`.

State is reported at **Settings → About** / `GET /system/info`:

- `fast_download.xet_installed` — `hf_xet` present (true)
- `fast_download.xet_active` — whether Xet actually drives downloads (false by
  default, because of `HF_HUB_DISABLE_XET`)
- `GET /system/info` reports `fast_download.xet_enabled` only when Xet
  is *active*.

The backend logs one line at startup, e.g.
`downloads: Xet disabled → legacy LFS (hf_xet 1.4.2 installed=True)`.

### Re-enabling Xet (advanced, opt-in)

Power users who want Xet's speed and don't mind coarser progress can set
`HF_HUB_DISABLE_XET=0`. With Xet active, the overall bar advances by file and
snaps to the exact total on completion (per-file *byte* speed isn't shown,
which is exactly why it's off by default). Xet needs a 64-bit OS (all supported
VoiceStudio platforms).

## Reading the progress

When a download starts you'll see, in order:

1. **Resolving** — the app fetches the file list and computes an exact plan:
   total size, how much is already cached, and how much will actually
   download (shown before any bytes move).
2. **Downloading** — one overall bar with the file count (e.g. `3/7 files`),
   total size, and — on networks where per-byte progress is reported — live
   speed and ETA.
3. **Done** — the bar lands on 100% at the true total size.

> Note: the exact total and "already cached / to download" split are known
> up front (a pre-flight resolve), so remaining is accurate from the start.
> Live per-byte speed appears once a file is large enough to stream over
> several seconds; very small/fast files may jump straight to done. The bar
> always lands on the exact total at completion. (If Xet is re-enabled,
> progress becomes file-granular — see above.)

## Advanced / opt-in tuning

These apply to every platform identically. Set them as environment variables (or
via **Settings → API keys / environment**). The segmented accelerator is **on by
default** (set its var to `0` to disable); the rest default **off**.

| Setting | Env var | Effect |
|---|---|---|
| Segmented accelerator | `OMNIVOICE_SEGMENTED_DOWNLOAD=0` | **On by default** (see above). Set to `0` to force the old single-stream legacy-LFS download instead of the parallel byte-range one. |
| Max parallel files | `OMNIVOICE_DOWNLOAD_MAX_WORKERS` (default 8) | Files fetched at once. Xet already parallelises *within* a file, so raising this rarely helps and uses more memory. |
| High-performance mode | `HF_XET_HIGH_PERFORMANCE=1` | Maximum throughput. Needs lots of RAM and bandwidth — can **hurt** low-RAM machines. Leave off unless you have headroom. |
| Spinning-disk (HDD) | `HF_XET_RECONSTRUCT_WRITE_SEQUENTIALLY=1` | Sequential writes; avoids parallel-write thrash on HDDs. Leave off on SSD/NVMe. |

## Restricted networks / mirrors (e.g. China)

**Automatic (the default).** When no endpoint is explicitly configured,
VoiceStudio picks one for you: it probes `huggingface.co` and the community
mirror `hf-mirror.com` in parallel (short HTTPS reachability + latency
checks — no geo-IP lookups, no third-party services; your device
language/timezone only decides which endpoint is probed *first*), prefers the
official endpoint unless the mirror is decisively faster, and remembers the
winner. The decision is re-tested only on the first-run system check, after a
network-classified download failure (the failed download retries once on the
new winner), when it's more than 7 days old, or when you press **Test again**
in **Settings → Models → Hugging Face mirror**. Mirror integrity is a
non-issue: `huggingface_hub` verifies every download by checksum regardless
of endpoint. Opt out with `OMNIVOICE_HF_ENDPOINT_MODE=manual`.

This retry also applies to the model weights fetched by one-click engine
installation. It retries once only when a network failure selects a different
automatic endpoint; explicit endpoint choices stay fixed, and a full disk
stops the download immediately.

If an engine's live sidecar fails while fetching its first-use model weights,
an automatic endpoint change also restarts that sidecar before one retry, so
its Hub client reads the new endpoint. An unchanged endpoint or a non-network
engine failure does not restart it.

**Explicit (always wins).** To pin an endpoint yourself:

```
HF_ENDPOINT=https://hf-mirror.com
```

Set it in **Settings → Models → Hugging Face mirror** (quick-pick presets and
a custom URL — any explicit choice switches the panel to manual mode and is
**never** auto-switched), or as an environment variable before launching. On
first run, the setup wizard's network check reports which endpoint the
automatic selection picked, and still offers the mirror quick-pick when
nothing is reachable — the check is a warning, not a blocker, so an offline
or firewalled machine can still finish setup once models are available
(mirror, or manual download below). If a wizard download fails because the
**configured** mirror is unreachable, the same quick-pick (including
**Hugging Face (official)**) appears right next to the failed row — switching
applies to downloads **immediately** (no restart; only already-loaded engines
re-read the endpoint at startup), clears the retry cooldown, and retries the
failed download at once. Caveats:

- A mirror serves the **classic** download path, **not Xet** — you lose
  chunk-dedup and Xet's parallel fetch, but you gain reachability. On the
  classic path, per-byte speed/ETA **is** shown continuously.
- Russia and some networks have no official mirror; use a VPN/tunnel.
- A custom mirror URL must use `https://` (plain `http://` is accepted only
  for a local proxy on `localhost`/`127.0.0.1`) and must not contain a user
  name, password, query or fragment.
- Your Hugging Face token is sent only to Hugging Face, never to a mirror —
  including token checks and gated-access checks. Gated models (speaker
  diarization) therefore download from the official endpoint: in **Auto** mode
  they always do, even when automatic selection picked the mirror; with a
  mirror you chose yourself, the install fails with `HF_MIRROR_GATED` — switch
  to **Hugging Face (official)** to install them.

## Cancelling a download

The engine's **Weights** list in **Model Catalogue** lets you cancel an
in-flight install. The transfer stops at its next chunk (or before it starts,
if you cancel while it is still resolving; with the opt-in Xet transfer a file
already in progress may finish first), and the row shows *cancelling*
until cleanup finishes. Cleanup removes what that download wrote: partial
`*.incomplete` / `*.part` files, files it finished, and new snapshot entries.
Files that were already in the cache before you started (an earlier revision
or another model's files) are kept, so the model returns to its previous state.
On Windows, a file still held open is retried for a few seconds before it is
left in place. Cancelling also clears the failure cooldown, so you can start
the download again immediately.

## Troubleshooting

- **Stuck on "Resolving…"** — the Hub is slow to return metadata, or you're
  rate-limited without a token. Add a token (see
  [docs/setup/huggingface-token.md](setup/huggingface-token.md)) and retry.
- **Very slow / stalling** — try a mirror (above), or a wired connection.
  High-performance mode only helps if RAM and bandwidth are plentiful.
- **"download finished but no model weights were found"** — the download was
  interrupted and left a partial snapshot. Delete the model in
  the engine's **Weights** list in **Model Catalogue** and install it again.
- **Out of disk** — model sizes are shown in the catalog; free space or change
  the cache location with `HF_HOME` / `HF_HUB_CACHE`. An install also keeps
  2 GB free on the model disk after the download, so it needs the download
  size plus 2 GB; the warning in Model Catalogue shows both numbers.
