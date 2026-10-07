# Downloading models — speed & troubleshooting

VoiceStudio downloads models from the Hugging Face Hub on first use. This page
explains how downloads are made fast, how to read the progress, and what to do
on slow or restricted networks.

When a remote GPU is selected, the catalog is filtered and curated for that
worker's reported OS, architecture, and GPU backend—not for the control-plane
computer. Generation checks the worker's capability report before submitting a
job. If the required weights are positively known to be absent, VoiceStudio
shows “model not downloaded on &lt;worker&gt;” with a download action. The download
runs on that worker and refreshes its capabilities when it finishes; press
Generate again afterward (the interrupted job is not automatically resubmitted).
The same `POST /models/install` request targets either `local` or the selected
worker, and `/setup/download-stream` reports both with a `target` field. Progress
is tracked by `(target, repo_id)`, so simultaneous downloads of one model on two
machines remain separate. Workers receive only an opaque model identifier and
resolve the reviewed Hugging Face repository and pinned revision from their own
catalog.
Unknown or user-managed cache layouts are allowed through so existing manual
engine installs remain compatible.

Managed sidecar engines are intentionally excluded from remote installation.
Their current installer fetches mutable source before creating an editable
environment; install those directly on the worker until that source is pinned.

## Download backend: legacy LFS by default (accurate progress)

VoiceStudio ships `hf_xet` (Hugging Face's chunked, parallel, dedup transfer
backend — the IDM/uGet-style fast path), **but currently runs with Xet
disabled** (`HF_HUB_DISABLE_XET=1`, set by the app). Reason: Xet's transfer
reports progress out-of-band and bypasses the byte-level progress hook, so the
download UI couldn't show real bytes/speed. Until a proper Xet progress hook
lands, the app forces the **classic LFS path**, which streams through the
standard progress reporter and gives accurate downloaded/remaining/speed.

To keep that path **fast** despite Xet being off, the app runs a built-in
**multi-connection (segmented) downloader on by default** — it fetches each file
over parallel byte-ranges (IDM/uGet style), so the legacy-LFS path is no longer
single-stream. It reports real live speed/ETA and **falls back to the normal
download**, so it can never compromise a correct install.

Ranges are capped at 16 MB each and eight of them are in flight at a time, so a
completed range is committed to a resume manifest every few seconds. On a
connection that drops mid-transfer, only the ranges in flight are refetched: the
attempt is retried and the accelerator resumes from its manifest rather than
starting the file over. An origin that does not serve ranges at all is handled
inside the accelerator as a single stream, not as a failure.

The accelerator is disabled for the rest of the install — handing over to the
plain `snapshot_download` path — when it fails for a reason that is not
transient network trouble, and on the install's final attempt, so it can never
be the reason an install fails outright. Adding a
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

## Cancelling a download

the engine's **Weights** list in **Model Catalogue** lets you cancel an in-flight install. Cancellation stops
further retries and clears the failure cooldown so you can restart
immediately. A file that's already streaming finishes first — cancellation
takes effect at the next retry boundary.

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
  10 GB free on the model disk after the download, so it needs the download
  size plus 10 GB; the warning in Model Catalogue shows both numbers.
