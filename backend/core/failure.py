"""Shared pipeline-failure helper (plan-04 / #131).

Single source of truth for "what a failure looks like" so every emit site —
the TaskManager worker, the dub ingest pipeline, the dub/batch routers — produces
the same structured, **non-empty**, sanitized failure event instead of its own
ad-hoc ``str(e)`` (which is empty or cryptic for many exception types, and was
the root of the "extract: unknown error" reports in #122/#63).

Guarantees:
  - ``reason`` is ALWAYS non-empty (falls back to the exception class name).
  - ``detail`` / ``diagnostic`` are sanitized: HF tokens, ``*TOKEN*/*KEY*/*SECRET*``
    env values, and absolute home paths never leak (Constitution I).
  - the 5-class docs taxonomy is reused from ``core.error_docs_map`` (not
    duplicated) so the deeplink contract stays single-sourced.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from core import error_docs_map
from core.logging_filter import REDACTED, _HF_TOKEN_RE

# Env vars whose *name* implies a credential — their values are redacted.
_SECRET_NAME_RE = re.compile(r"(TOKEN|KEY|SECRET)", re.IGNORECASE)

#: An HTTP 401, not any number or identifier that contains 401.
#:
#: Digit-only boundaries are not enough: they reject `4012` and `1401` but
#: still accept `pytest-401`, `x401y` and `401.0`, so an error that mentions
#: Hugging Face anywhere and carries one of those elsewhere still classified
#: as an auth failure (CodeRabbit, #1427).
#:
#: The three guards, each for a family the others let through:
#:   `(?<![\w.-])`  identifier / path / dotted-version prefixes — `x401y`,
#:                  `pytest-401`, `v1.401`
#:   `(?![\w-])`    identifier and hyphen suffixes — `401k`, `401-retry`
#:   `(?!\.\d)`     dotted numerics — `401.0`, `401.25` (a trailing sentence
#:                  full stop is still fine: `status 401.`)
#:
#: Matches what a real status code looks like: `401`, `(401)`, `status 401:`,
#: `HTTP/1.1 401`.
_HTTP_401 = re.compile(r"(?<![\w.-])401(?![\w-])(?!\.\d)")
_REDACTED_VALUE = "***REDACTED***"

# One-line "what to do" per docs-taxonomy key. Keys mirror error_docs_map's
# taxonomy; the docs URL itself stays owned by error_docs_map.
_HINTS: dict[str, str] = {
    "PKG_RESOURCES_MISSING": "Run `uv pip install --reinstall 'setuptools>=75,<80'` in the backend venv (a plain install is skipped when setuptools' metadata is present but its pkg_resources files were removed by antivirus). Restart after.",
    "GATEKEEPER_QUARANTINE": "Clear the macOS quarantine flag (xattr -cr the app), then reopen.",
    "APPIMAGE_WEBKIT_WHITESCREEN": "Launch with WEBKIT_DISABLE_DMABUF_RENDERER=1 set.",
    "HF_AUTH_FAILED": "Set a valid HF_TOKEN in Settings → Hugging Face and retry.",
    "PYANNOTE_LICENSE_REQUIRED": "Accept the pyannote model licenses on Hugging Face, then retry.",
    "COMPUTE_TYPE_UNSUPPORTED": "Your GPU doesn't support float16 — VoiceStudio retried on int8. If transcription still fails, set OMNIVOICE/ASR_COMPUTE_TYPE=int8 or use CPU.",
    # Literal versions, not `--constraint deploy/torch-constraints.txt`:
    # desktop installs don't ship deploy/ (tauri.conf.json bundles only
    # pyproject/uv.lock/backend/omnivoice), so the file-based command would
    # fail with "file not found" for exactly the users most likely to need it
    # (greptile on #1377). tests/test_failure_classify.py pins these literals
    # to the constraint file so they cannot drift when the pins bump.
    "TRANSFORMERS_IMPORT": "Your transformers install is incomplete, or a package it loads models through (torchaudio, torchvision) is missing or mismatched with your torch — a torch/torchvision version mismatch fails with exactly this wording. Reinstall them together at the pinned versions (`uv pip install --python .venv --reinstall torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 transformers` in the project folder), then restart the backend. If only transcription is affected, switching ASR to faster-whisper (Settings → Models) also works around it.",
    "WINDOWS_APP_CONTROL_BLOCKED": "Windows refused to load a file VoiceStudio needs — an Application Control policy (Smart App Control, WDAC, or AppLocker) blocked it. On a personal PC: Windows Security → App & browser control → Smart App Control → Off (Windows only lets you turn it off once — re-enabling requires a Windows reset), then restart VoiceStudio. On a managed/work PC, ask IT to allow the VoiceStudio install folder.",
    "WINDOWS_PAGING_FILE_TOO_SMALL": "Windows ran out of virtual memory while mapping the model into memory — its paging file is smaller than the model needs. This is not the same as your RAM being full, and closing other apps usually won't fix it: Windows has to be allowed to back the mapping. Set a bigger paging file — Settings → System → About → Advanced system settings → Performance → Settings → Advanced → Virtual memory → Change: untick \"Automatically manage\", pick your system drive, choose \"Custom size\" and set both Initial and Maximum to at least 32768 MB (more than the model's size), then OK and restart Windows. A smaller/quantized engine (OmniVoice GGUF, Supertonic-3) also avoids the large mapping entirely.",
    "MEDIA_TOOL_MISSING": "VoiceStudio's media engine (ffmpeg/ffprobe) wasn't on the system path when a component went looking for it. Open Settings → Audio tools and use Download/Repair to fetch the bundled copy, then retry — a restart picks it up for everything. If you'd rather use a system install, install ffmpeg (macOS: `brew install ffmpeg`; Windows: `winget install Gyan.FFmpeg`; Linux: your package manager) and restart VoiceStudio, or point FFMPEG_PATH / OMNIVOICE_FFPROBE_PATH at the binaries in Settings.",
    "AUDIO_IO_FAILED": "An audio file couldn't be read or written at the OS level. Check the drive isn't full, that the output and temp folders exist and are writable, and that antivirus or OneDrive isn't locking them (add a VoiceStudio exclusion if you use one).",
    "VIDEO_DOWNLOAD_OS_ERROR": "The OS refused a file operation while saving the downloaded video — this is a disk/folder problem, not a network one, so retrying the same link won't help. The download is written to a job folder under your VoiceStudio data directory (Settings → Storage shows the path): check that drive isn't full, that the folder exists and is writable, and that antivirus or a cloud-sync client (OneDrive, Dropbox) isn't locking it — add a VoiceStudio exclusion if you use one. If your data directory sits on a synced or network drive, move it to a local one.",
    "OS_INVALID_ARGUMENT": "The OS rejected a file operation (Errno 22 / invalid argument) — in the transcribe path this is the temporary WAV write before ASR. It's almost always the temp directory: missing, read-only, on a full or removed drive, or blocked by antivirus. Check that your system TEMP/TMP folder exists and is writable and the drive has free space (add a VoiceStudio antivirus exclusion if you use one), then retry.",
    "SOCKS_PROXY_SUPPORT_MISSING": "A SOCKS proxy is configured in your environment (ALL_PROXY/HTTPS_PROXY=socks5://…) and the backend's HTTP client is missing SOCKS support. Newer VoiceStudio builds ship SOCKS support (the socksio package) — update the app. If you still see this, unset ALL_PROXY/HTTPS_PROXY for VoiceStudio, or run `uv pip install 'httpx[socks]'` in the backend venv, then restart.",
    "SSL_HANDSHAKE_FAILURE": "A corporate or antivirus proxy is intercepting HTTPS traffic and re-signing certificates with its own CA — your OS trusts that CA, but Python's bundled certifi CA list doesn't, so the TLS handshake fails even though the connection reached the server. Newer VoiceStudio builds trust the OS certificate store at startup (the truststore package), which should already fix this — update the app and retry. If you still see this, add an HTTPS-scanning exclusion for VoiceStudio/Python in your antivirus, or ask IT for the proxy's CA bundle and set SSL_CERT_FILE to it, then restart.",
    "UNSUPPORTED_VIDEO_URL": "This link isn't a directly downloadable video. Paste a direct video page (e.g. a youtube.com/watch?v=… or douyin.com/video/<id> link), not a share/profile/feed link — or download the file and drop it in directly.",
    "VIDEO_DRM_PROTECTED": "The video host only offered VoiceStudio a DRM-protected copy, which can't be downloaded. This is often not a property of the video itself — the host serves a different format set to different clients, and VoiceStudio already retried through every client it has. Try the link again in a minute, or download the video with a browser extension / the host's own download button and drop the file into Dubbing directly.",
    # #1301: distinct from SSL_HANDSHAKE_FAILURE. The handshake did not fail on
    # trust — the connection was CUT while TLS was in progress, so the certifi /
    # proxy-CA advice above would send the user to fix something that isn't
    # broken. Raw form is "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in
    # violation of protocol (_ssl.c:1016)", which means nothing to anyone.
    "TLS_CONNECTION_DROPPED": "The secure connection was cut off mid-transfer — the other end (or something between you and it) closed the socket during the TLS exchange. This is almost always transient: flaky Wi-Fi, a VPN reconnecting, a captive portal, or a download server dropping a long transfer. Retrying is safe: a partly-downloaded MODEL is picked up where it left off rather than started over. If it repeats every time, a VPN or an HTTPS-inspecting proxy is terminating long-lived connections — try without the VPN, or on another network.",
    "VIDEO_DOWNLOAD_NETWORK": "The connection to the video server dropped mid-download (often a transient CDN/network blip or a regional rate-limit). Just retry — VoiceStudio already cleaned up the partial download. If it keeps failing, check your network/VPN.",
    # #1347: the transformers pipeline fails to import a component it was
    # DOWNLOADING when the shared HTTP client closed underneath it (#880). The
    # message names AutoFeatureExtractor, so TRANSFORMERS_IMPORT matched and
    # told the reporter to reinstall transformers — advice that cannot work,
    # because nothing is wrong with their install. Checked first so the cause
    # wins over the symptom.
    "MODEL_DOWNLOAD_INTERRUPTED": "A model download was cut off mid-request, and the component it was fetching then failed to load. Nothing is wrong with your install — reinstalling won't help, and the partial download is resumed rather than restarted. Just retry. If it keeps happening, check your connection (and any VPN, proxy or HF mirror setting); if only transcription is affected, switching ASR to faster-whisper in Settings → Models avoids the pipeline that downloads this component.",
    "BROKEN_VENV": "The Python backend environment was moved or damaged. VoiceStudio rebuilds it automatically on the next launch; if it keeps failing, use Clean & Retry on the setup screen.",
    "MODEL_CACHE_CORRUPT": "The model cache had broken file links — snapshot entries that no longer point at their downloaded data (interrupted renames or antivirus interference can cause this). VoiceStudio repairs this automatically and retries the load once. If the error persists, quit VoiceStudio, delete the model's models--<org>--<name> folder inside the Hugging Face cache, and restart — the model re-downloads automatically.",
    # HF_MIRROR_UNREACHABLE has a DYNAMIC hint (it names the configured mirror)
    # — see hf_mirror_hint(); build_failure special-cases it.
}


# ── HF mirror connectivity (#874) ────────────────────────────────────────────
# When a non-default HF_ENDPOINT (a mirror, e.g. hf-mirror.com — set via
# Settings → Models → Hugging Face mirror) is configured and a model
# download/load fails with a connectivity error, the raw transformers/hf_hub
# message ("We couldn't connect to 'https://hf-mirror.com' to load the files…")
# gives the user no next step. This is the single classifier for that class,
# shared by every surface: build_failure() (model status, dub/task events),
# the global 500 handler (main.py — covers /generate and every other route
# that can leak a model-load error), and the model-install SSE
# (setup/download.py).

_OFFICIAL_HF_ENDPOINTS = {"https://huggingface.co", "https://hf.co"}

# Connectivity signatures across the layers an HF download failure surfaces
# from: transformers' wording, huggingface_hub errors, requests/urllib3, and
# raw socket/DNS failures (Linux/macOS/Windows variants).
_HF_CONNECTIVITY_SIGNATURES = (
    "couldn't connect to",             # transformers: "We couldn't connect to '<endpoint>' …"
    "could not connect to",
    "connection error",                # huggingface_hub / requests
    "connection refused",
    "connection reset",
    "connection aborted",
    "max retries exceeded",            # urllib3 via requests
    "failed to establish a new connection",
    "name or service not known",       # Linux DNS
    "temporary failure in name resolution",
    "nodename nor servname provided",  # macOS DNS
    "getaddrinfo failed",              # Windows DNS
    "timed out",
    "an error happened while trying to locate the file on the hub",  # LocalEntryNotFoundError
    "we cannot find the requested files",                            # LocalEntryNotFoundError
    # #1224: a TRUNCATED download — the server closed mid-body, so the client
    # got fewer bytes than Content-Length promised. httpx words it "peer closed
    # connection without sending complete message body"; urllib3/http.client
    # raise IncompleteRead. This is as transient as a refused connection and
    # must retry — a 4.6 GB model that dies at 4.0 GB used to abort the whole
    # install (and, on the reporter's 16 GB Mac, take the process with it).
    "peer closed connection",
    "incomplete message body",
    "incompleteread",
    "incomplete read",
    "connection broken",               # urllib3 ProtocolError wrapper
    "response ended prematurely",
)

# The failure must also be Hugging-Face-shaped — the configured endpoint/host
# named in the message, or HF-download wording — so a random socket error
# (e.g. a local LLM provider being down) doesn't get the mirror hint just
# because a mirror happens to be configured.
_HF_CONTEXT_MARKERS = (
    "huggingface",
    "hf_hub",
    "hf-hub",
    "load the files",          # transformers
    "cached files",            # transformers
    "the requested files",     # LocalEntryNotFoundError
    "locate the file on the hub",
    "snapshot_download",
)


def is_hf_connectivity_error(reason: Optional[str]) -> bool:
    """True when *reason* looks like a network/connectivity failure of an HF
    download (DNS, refused/reset connections, timeouts, hub locate errors).

    Signature match only — callers already in a download context (the model
    install worker, the cache auto-repair, the endpoint failover in
    services.endpoint_race) don't need the HF-context markers that
    ``hf_mirror_hint`` requires. Never raises."""
    low = (reason or "").lower()
    return any(sig in low for sig in _HF_CONNECTIVITY_SIGNATURES)


def configured_hf_mirror() -> str:
    """The non-default Hugging Face endpoint (mirror) in effect, or "".

    Same resolution the download paths use: ``HF_ENDPOINT`` env (what
    Settings → Models → Hugging Face mirror persists via user_env, and what
    the HF libraries read) with the ``hf_endpoint`` pref as fallback
    (mirrors setup/download.py's ``prefs.resolve``). Never raises.
    """
    ep = (os.environ.get("HF_ENDPOINT") or "").strip()
    if not ep:
        try:
            from core import prefs

            ep = str(prefs.get("hf_endpoint", "") or "").strip()
        except Exception:
            ep = ""
    ep = ep.rstrip("/")
    if not ep or ep.lower() in _OFFICIAL_HF_ENDPOINTS:
        return ""
    return ep


def hf_mirror_hint(reason: Optional[str]) -> str:
    """Actionable hint when ``reason`` is an HF-download connectivity failure
    and a non-default mirror endpoint is configured; "" otherwise.

    The hint names the configured mirror, says it may be down, points at the
    setting (Settings → Models → Hugging Face mirror; during first-run the
    wizard renders its own inline picker), and suggests the official endpoint
    when the model isn't cached yet. Model *downloads* pick up a mirror change
    immediately (PUT /hf-mirror updates os.environ and the download paths
    resolve the endpoint per call) — only transformers-side model *loads*,
    which read HF_ENDPOINT at import time, still need a restart, so restart is
    framed as the fallback when a retry still fails. Never raises.
    """
    mirror = configured_hf_mirror()
    if not mirror:
        return ""
    low = (reason or "").lower()
    if not is_hf_connectivity_error(low):
        return ""
    try:
        host = (urlsplit(mirror).netloc or "").lower()
    except Exception:
        host = ""
    if not (
        mirror.lower() in low
        or (host and host in low)
        or any(m in low for m in _HF_CONTEXT_MARKERS)
    ):
        return ""
    return (
        f"Your Hugging Face mirror is set to {mirror}, which couldn't be "
        "reached — the mirror may be down or blocked on your network. If the "
        'model isn\'t in your local cache yet, switch to "Hugging Face '
        '(official)" in Settings → Models → Hugging Face mirror — during '
        "first-run setup, use the mirror picker right on this screen — or "
        "wait for the mirror to recover, then retry: downloads pick up a "
        "mirror change immediately. If a retry still fails after switching, "
        "restart VoiceStudio."
    )


def append_hf_mirror_hint(text: str) -> str:
    """``"{text} — {hint}"`` when the mirror-connectivity class applies;
    ``text`` unchanged otherwise. For surfaces that hand a raw error string to
    the UI (the global 500 handler, the model-install SSE). Never raises."""
    try:
        hint = hf_mirror_hint(text)
    except Exception:
        return text
    return f"{text} — {hint}" if hint else text


# Classes whose hint is safe to attach on the CONTEXT-FREE surfaces (the
# global 500 handler in main.py, the model-install SSE in setup/download.py),
# where all we have is a raw error string with no stage. Only classes whose
# classify() trigger is unmistakable belong here — e.g. VIDEO_DOWNLOAD_NETWORK
# must NOT be added: its bare "timed out" trigger would stamp a "video server"
# hint on a model-load timeout that leaks through the 500 handler.
_CONTEXT_FREE_HINT_CLASSES = frozenset({
    "SOCKS_PROXY_SUPPORT_MISSING",
    "SSL_HANDSHAKE_FAILURE",
    # Its trigger is an exact OpenSSL string, so it cannot be confused with
    # another failure the way a bare "timed out" could.
    "TLS_CONNECTION_DROPPED",
    # Requires the exact httpx closed-client wording AND an import/transformers
    # term together, so it cannot fire on an unrelated failure (#1347).
    "MODEL_DOWNLOAD_INTERRUPTED",
    # #1334: triggered by WinError/os error 1455 or the literal "paging file is
    # too small" — both unmistakable. It reached /generate as a bare 500
    # carrying only the OS sentence, so the reporter had no way to know it was
    # a Windows virtual-memory setting rather than a connectivity problem, and
    # the detailed hint we already had for it never reached them.
    "WINDOWS_PAGING_FILE_TOO_SMALL",
})


def append_hint(text: str) -> str:
    """``"{text} — {hint}"`` for raw-string surfaces (the global 500 handler,
    the model-install SSE): the dynamic mirror hint (#874) when that class
    applies, else a context-free static class hint (#959). ``text`` unchanged
    otherwise — a no-op for every other error. Never raises."""
    try:
        hint = hf_mirror_hint(text)
        if not hint:
            topic = classify(text)
            if topic in _CONTEXT_FREE_HINT_CLASSES:
                hint = _HINTS.get(topic, "")
    except Exception:
        return text
    return f"{text} — {hint}" if hint else text


def classify(reason: str) -> str:
    """Map a failure reason to a docs-taxonomy key, or "" when unknown.

    Heuristic substring match — mirrors the frontend ``classifyError`` so the
    backend log / diagnostic names the same class the UI deeplink will use.
    """
    low = (reason or "").lower()
    if "pkg_resources" in low:
        return "PKG_RESOURCES_MISSING"
    if "quarantine" in low or "is damaged" in low or "gatekeeper" in low:
        return "GATEKEEPER_QUARANTINE"
    if "webkit" in low or "white screen" in low or "dmabuf" in low or "appimage" in low:
        return "APPIMAGE_WEBKIT_WHITESCREEN"
    if "pyannote" in low or ("gated" in low and "model" in low) or "accept the" in low:
        return "PYANNOTE_LICENSE_REQUIRED"
    # ASR robustness (#551 / #549): name the class so the no-segments toast is
    # actionable. Place before the generic returns so a compute-type/transformers
    # failure gets its hint rather than falling through to "".
    if "compute type" in low or "efficient float16" in low:
        return "COMPUTE_TYPE_UNSUPPORTED"
    # #763: a bare OS-level EINVAL ("[Errno 22] Invalid argument") while writing
    # the per-chunk temp WAV for transcription (tempfile.NamedTemporaryFile /
    # soundfile.write on the system temp dir) used to collapse into a dead-end
    # "produced no segments. [Errno 22] Invalid argument" toast with no next
    # step. errno 22 is EINVAL on every platform; in this path it's almost always
    # a temp dir that's missing, read-only, on a full/removed drive, or blocked
    # by antivirus. Name the class so build_failure attaches an actionable hint
    # instead of a raw errno. Matching the errno (not the generic "invalid
    # argument" wording) keeps this from mislabelling unrelated failures; the
    # transformers "errno 2" rule below is unaffected — it also requires the
    # transformers + site-packages markers, which this signature lacks.
    # #1225: the same errno raised by the DUB video download is a different
    # class with a different remedy — the failing directory is the job folder
    # under the VoiceStudio data dir, not the system temp dir. Checked first so
    # a download's errno 22 stops being handed the transcribe path's
    # "check your TEMP folder" hint, which sends the user to the wrong place.
    if is_os_write_refusal(reason) and any(
        marker in low for marker in _DOWNLOAD_CONTEXT_MARKERS
    ):
        return "VIDEO_DOWNLOAD_OS_ERROR"
    # #1256: a third-party library shelled out to `ffprobe`/`ffmpeg` BY NAME and
    # the OS had nothing to run. VoiceStudio's own code always resolves the
    # bundled sidecar explicitly, so this only ever comes from a dependency —
    # which meant it arrived with no class at all and the user was told the
    # engine "stopped with an error VoiceStudio doesn't recognize". Checked
    # before the generic errno-2 rules, which would otherwise claim it.
    if _is_missing_media_tool(low):
        return "MEDIA_TOOL_MISSING"
    # #1251: Windows refused to map the model because the PAGING FILE is too
    # small. The generate path already counted this as an OOM, but that hint
    # ("close other apps, use a lighter engine") is the wrong remedy — the
    # machine had 32 GB of RAM. Matched on the numeric code too, since the OS
    # translates the message text, and in both the Python (`[WinError 1455]`)
    # and Rust (`os error 1455`, from the safetensors mmap) spellings.
    if "1455" in low and ("winerror" in low or "os error" in low):
        return "WINDOWS_PAGING_FILE_TOO_SMALL"
    if "paging file is too small" in low:
        return "WINDOWS_PAGING_FILE_TOO_SMALL"
    if "errno 22" in low:
        return "OS_INVALID_ARGUMENT"
    # An HF cache whose snapshot entries don't resolve (dangling symlinks /
    # zero-byte stand-ins) or which is simply missing its weight shard.
    # model_manager self-heals this (delete broken entries → snapshot_download
    # → retry once); the class here covers the raw transformers wordings (any
    # load surface can leak them) and VoiceStudio's own repair messages, so the
    # user-facing error and the auto bug report name the class and its
    # automatic repair.
    if is_incomplete_cache_message(low) or "broken file link" in low:
        return "MODEL_CACHE_CORRUPT"
    # #1347: an import that failed because its DOWNLOAD died is a network
    # problem wearing an import problem's clothes. The reporter's message named
    # AutoFeatureExtractor *and* carried "Cannot send a request, as the client
    # has been closed" (#880) — TRANSFORMERS_IMPORT won on the former and told
    # them to reinstall transformers, which cannot fix a dropped connection.
    # Checked BEFORE the import rules so the cause beats the symptom.
    # Requires the closed-client wording itself, not merely "cannot send a
    # request" (CodeRabbit): the latter is generic enough to appear in an
    # unrelated failure that also mentions an import, and overriding
    # TRANSFORMERS_IMPORT there would replace correct reinstall advice with a
    # "just retry" that never succeeds — the same defect in the other
    # direction.
    if "client has been closed" in low and (
        "import" in low or "transformers" in low or "autofeatureextractor" in low
    ):
        return "MODEL_DOWNLOAD_INTERRUPTED"
    if (
        "could not import module" in low
        or "autofeatureextractor" in low
        # A corrupted/incomplete transformers install: a model load lazily
        # resolves a module file that's MISSING from site-packages (an
        # interrupted `uv sync`, antivirus removal, or a partial update), e.g.
        # `[Errno 2] No such file or directory:
        #  '.../site-packages/transformers/models/qwen3/modeling_qwen3.py'`.
        # That's a FileNotFoundError, not an ImportError, so the matches above
        # miss it and the user got a useless "try restarting". Substring-match
        # the package + the missing-file signal (separately, so it works on both
        # POSIX `/` and Windows `\` paths).
        or (
            ("no such file" in low or "errno 2" in low)
            and "transformers" in low
            and "site-packages" in low
        )
    ):
        return "TRANSFORMERS_IMPORT"
    # #959: httpx raises ImportError AT CLIENT CONSTRUCTION ("Using SOCKS
    # proxy, but the 'socksio' package is not installed. Make sure to install
    # httpx using `pip install httpx[socks]`.") when ALL_PROXY/HTTPS_PROXY is
    # socks5:// and socksio isn't importable. It surfaced from
    # huggingface_hub's get_session() inside model load — a bare 500 on
    # /generate with no next step. Checked BEFORE the HF-auth/mirror rules so
    # a message that also carries HF wording still names this class.
    if "socks proxy" in low or "socksio" in low:
        return "SOCKS_PROXY_SUPPORT_MISSING"
    # #976: a TLS handshake failing AFTER the TCP connection succeeds — the
    # signature of a corporate/antivirus proxy that TLS-inspects traffic and
    # re-signs certificates with a CA the OS trusts but Python's bundled
    # certifi list doesn't (a different failure mode from #984's TCP-level
    # "can't reach the host at all"). Requires "ssl" plus a handshake/cert-
    # verify marker so a generic connection error isn't mislabelled.
    # Check the dropped-connection shape FIRST: its text also contains "ssl",
    # and the handshake branch below would otherwise claim it and hand out
    # proxy/certifi advice for a socket that was simply cut (#1301).
    # Requires an "ssl" marker as well: "unexpected EOF" on its own is a phrase
    # a parser or an unrelated transport can also produce, and stamping the TLS
    # taxonomy on those would hand out VPN/proxy advice for something else
    # entirely. The OpenSSL text always carries the marker.
    if "ssl" in low and (
        "unexpected_eof_while_reading" in low
        or "eof occurred in violation of protocol" in low
    ):
        return "TLS_CONNECTION_DROPPED"
    if "ssl" in low and (
        "handshake" in low
        or "certificate verify failed" in low
        or "sslv3_alert" in low
        or "sslcertverificationerror" in low
    ):
        return "SSL_HANDSHAKE_FAILURE"
    # A bare 401 is no longer sufficient evidence on its own. It used to
    # satisfy BOTH halves of this condition, which made the `and` vacuous, so
    # any message containing those three digits — a path, a byte count, an id
    # — classified as an auth failure by itself. CI hit it when pytest's
    # numbered temp directory reached `pytest-401` and an audio-save error
    # came back as "set a valid HF_TOKEN". A real 401 always arrives with the
    # word Unauthorized or an HF URL beside it, so requiring that costs
    # nothing and closes the class.
    has_401 = _HTTP_401.search(low) is not None
    if ("huggingface" in low or "hf_token" in low or "unauthorized" in low) and (
        "token" in low or "auth" in low or has_401 or "unauthorized" in low
    ):
        return "HF_AUTH_FAILED"
    # #874: a model download that failed because the CONFIGURED HF mirror is
    # unreachable. Env-aware by design — the class only exists when a
    # non-default HF_ENDPOINT is configured. Checked BEFORE the video-download
    # network class so a model download's "timed out"/"connection reset"
    # names the mirror instead of the "video server".
    if hf_mirror_hint(reason):
        return "HF_MIRROR_UNREACHABLE"
    # Video download (#554/#536): a non-downloadable URL shape vs a transient
    # network drop — both previously surfaced as a bare yt-dlp string with no
    # next step. UNSUPPORTED first (more specific) so "Unable to download video:
    # Broken pipe" still classifies as a network blip.
    if "unsupported url" in low or "no video formats" in low or "is not a valid url" in low:
        return "UNSUPPORTED_VIDEO_URL"
    # #1254: reported as intermittent — the same URL failed, then succeeded on
    # a retry. That is a per-player-client format set, not real DRM, so the
    # download path now escalates the client the way it does for a 403. If
    # every client still says DRM, the video genuinely can't be fetched and the
    # user needs to hear that rather than retry a fourth time.
    if "drm protected" in low or "drm-protected" in low:
        return "VIDEO_DRM_PROTECTED"
    if (
        "broken pipe" in low
        or "connection reset" in low
        or "unable to download video" in low
        or "remote end closed" in low
        or "timed out" in low
    ):
        return "VIDEO_DOWNLOAD_NETWORK"
    # #1227: Windows Smart App Control / WDAC / AppLocker refused to load a
    # file the app needs. Matched on the numeric codes (locale-independent —
    # the OS translates the message text) plus the English policy phrase.
    if (
        "[winerror 4551]" in low
        or "[winerror 1260]" in low
        or "application control policy" in low
    ):
        return "WINDOWS_APP_CONTROL_BLOCKED"
    # #1221: libsndfile failed an OS-level audio read/write. Its own wording is
    # a bare "System error.", so match the library name — audio_io already
    # prefixes the target path and free space onto the write-path failures.
    # ``audio_io.AUDIO_WRITE_FAILED_MARKER`` (kept as a literal — core must not
    # import services). Matching the marker instead of generic wording like
    # "error opening" keeps a failed MODEL/config/archive open from being handed
    # the audio remedy, while still classifying the enriched write failures that
    # no longer carry the word "libsndfile" verbatim.
    if "libsndfile" in low or "writing the audio file failed" in low:
        return "AUDIO_IO_FAILED"
    # A relocated/corrupted venv whose interpreter can't bootstrap its stdlib —
    # the Rust self-heal rebuilds it; this names the class for the toast.
    if "no module named 'encodings'" in low:
        return "BROKEN_VENV"
    # #564: the interpreter starts fine but the backend can't import its OWN
    # `omnivoice` package (a venv missing the editable install). Same self-heal
    # class — Clean & Retry / the bootstrap repair rebuilds it. The trailing
    # quote keeps a legitimately-named `omnivoice_*` helper from matching.
    if "no module named 'omnivoice'" in low:
        return "BROKEN_VENV"
    return ""


# ffmpeg/ffprobe write a version banner to STDERR on every invocation, before
# doing any work. When a command fails we capture that stderr and it becomes
# the error message — so the user is shown the build configuration of their
# ffmpeg instead of what went wrong (#1309: a dub extract failure reported as
# "extract: ffmpeg version N-125781-gacf6b520c1-20260727 Copyright (c) …").
#
# The real diagnosis is always AFTER the banner. Strip the boilerplate so the
# message starts at the first line that is actually about this run.
_FFMPEG_BANNER_START = re.compile(r"^\s*(ffmpeg|ffprobe)\s+version\s", re.IGNORECASE)
_FFMPEG_BANNER_CONT = re.compile(
    r"^\s+(built with|configuration:|lib[a-z]+\s+\d)", re.IGNORECASE
)


def strip_ffmpeg_banner(text: Optional[str]) -> str:
    """Drop ffmpeg's version/configuration preamble from a captured stderr.

    Returns the text unchanged when no banner is present, and — importantly —
    when stripping would leave nothing: a message that is *only* a banner is
    unhelpful, but an empty one is worse.
    """
    if not text:
        return text or ""
    lines = str(text).splitlines()
    kept, in_banner = [], False
    for line in lines:
        if _FFMPEG_BANNER_START.match(line):
            in_banner = True
            continue
        if in_banner:
            # The banner is the version line plus its indented continuation
            # block; the first line that is not part of that ends it.
            if not line.strip() or _FFMPEG_BANNER_CONT.match(line):
                continue
            in_banner = False
        kept.append(line)
    out = "\n".join(kept).strip()
    return out or str(text).strip()


# Terminal escape sequences from a captured stderr (#1344). Command-line tools
# colourize their output whenever they think a terminal is attached, and the
# frozen backend's pipes are enough for several of them to decide that: yt-dlp
# reported "download: ^[[0;31mERROR:^[[0m [youtube] …" verbatim into the UI,
# where the escapes render as literal mojibake in the middle of the sentence.
#
# Stripping matters for more than looks: this text is also the input to the
# pattern matching below. ``classify()``'s patterns happen to match past the
# escape and survive it, but ``strip_ffmpeg_banner`` is anchored to the start
# of a line — a leading colour code hides "ffmpeg version " from it and
# quietly reinstates #1309 for any ffmpeg build that colours its output. Hence
# the strip runs FIRST, at the choke point every failure passes through.
#
# CSI (colour, cursor movement) plus OSC (window title / hyperlink), which
# yt-dlp and ffmpeg both emit and which carries its own terminator.
_ANSI_ESCAPE = re.compile(
    r"\x1B(?:\][^\x07\x1B]*(?:\x07|\x1B\\)|[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])"
)


def strip_ansi(text: Optional[str]) -> str:
    """Remove terminal escape sequences from a captured stderr/stdout.

    Returns the text unchanged when there are none, and empty when the input
    was nothing BUT escapes. Emptying it is correct rather than lossy:
    ``build_failure`` already falls back to the exception class name for an
    empty ``reason``, and a class name is a real answer where a run of escape
    bytes copied into ``reason``/``error``/``detail`` is not (CodeRabbit).
    """
    if not text:
        return text or ""
    return _ANSI_ESCAPE.sub("", str(text))


def sanitize(text: Optional[str]) -> str:
    """Redact secrets and strip the home path from a string.

    - HF tokens (reuses the regex from ``core.logging_filter``)
    - values of env vars whose name matches ``*TOKEN*/*KEY*/*SECRET*``
    - the user's absolute home directory → ``~``
    """
    if not text:
        return text or ""
    out = _HF_TOKEN_RE.sub(REDACTED, str(text))
    for name, val in os.environ.items():
        # Only redact substantial values so short/empty ones don't blank the text.
        if val and len(val) >= 6 and _SECRET_NAME_RE.search(name):
            out = out.replace(val, _REDACTED_VALUE)
    try:
        home = str(Path.home())
        if home and home in out:
            out = out.replace(home, "~")
    except Exception:
        # Best-effort: sanitize() must never raise (it runs on the failure path);
        # if home-dir resolution fails, leave the text as-is rather than throw.
        pass
    return out


def _env_summary() -> str:
    lines: list[str] = []
    try:
        lines.append(f"OS:      {platform.platform()}")
    except Exception:
        # Best-effort env summary — omit the OS line rather than fail diagnostics.
        pass
    lines.append(f"Python:  {sys.version.split()[0]}")
    try:
        import psutil  # already a runtime dep

        vm = psutil.virtual_memory()
        lines.append(f"CPU:     {os.cpu_count()} cores")
        lines.append(f"RAM:     {round(vm.total / 1024 ** 3, 1)} GB")
    except Exception:
        # Best-effort — omit CPU/RAM if psutil is unavailable or probing fails.
        pass
    # Only probe the GPU if torch is ALREADY imported — importing it here just
    # to build a diagnostic would add seconds to every failure (and to tests).
    torch = sys.modules.get("torch")
    if torch is not None:
        try:
            if torch.cuda.is_available():
                lines.append(f"GPU:     CUDA {torch.cuda.get_device_name(0)}")
            elif getattr(getattr(torch, "backends", None), "mps", None) and torch.backends.mps.is_available():
                lines.append("GPU:     MPS (Apple)")
            else:
                lines.append("GPU:     CPU only")
        except Exception:
            # Best-effort — omit the GPU line if torch probing raises.
            pass
    return "\n".join(lines)


def diagnostic(*, reason: str, error_class: str, stage: str) -> str:
    """A sanitized, copy-paste-friendly diagnostic block for a failed job."""
    block = (
        "VoiceStudio diagnostic\n"
        "--------------------\n"
        f"Stage:   {stage}\n"
        f"Error:   {error_class}\n"
        f"Reason:  {reason}\n"
        f"{_env_summary()}\n"
    )
    return sanitize(block)


# Signatures of the OS refusing a file operation. One list, because two
# consumers must agree: ``classify`` (which picks the class + hint) and
# ``dub_pipeline._with_target_facts`` (which decides whether to attach the
# destination). When they drifted, an ENOENT download classified as a disk
# problem but never got the folder named — the one fact that would have made
# the message actionable (#1225 review).
_OS_WRITE_REFUSAL_SIGNATURES = (
    "errno 22", "invalid argument",
    "errno 13", "permission denied",
    "errno 28", "no space left",
    "errno 2", "no such file or directory",
    "unable to open for writing",
    "unable to rename file",
)

# Wording that places a failure in the video-download path specifically.
_DOWNLOAD_CONTEXT_MARKERS = (
    "unable to download video",
    "unable to open for writing",
    "unable to rename file",
    "yt_dlp",
    "yt-dlp",
)


#: The media binaries a dependency may shell out to by bare name.
_MEDIA_TOOLS = ("ffprobe", "ffmpeg")


def _is_missing_media_tool(low: str) -> bool:
    """True when the failure is "the OS could not find ffprobe/ffmpeg" (#1256).

    Deliberately narrow: it must name the binary *as the thing that could not
    be found*, so a perfectly ordinary "ffmpeg failed: no such file or
    directory: /path/to/input.wav" — a missing INPUT, an entirely different
    problem — is not handed the "install the media engine" remedy.
    """
    if "no such file or directory" not in low and "[winerror 2]" not in low:
        return False
    # The name must appear UNQUALIFIED — quoted with no directory part, which
    # is how a bare-name spawn fails. A path that merely ends in the tool's
    # name ('/tmp/ffmpeg', '~/Movies/my-ffmpeg-export.mp4') is a missing FILE,
    # an entirely different problem that must not get the "repair your media
    # engine" remedy (#1256 review).
    return any(
        f"'{tool}'" in low or f'"{tool}"' in low
        for tool in _MEDIA_TOOLS
    )


#: transformers' two ways of saying "this snapshot has no weight shard".
#:
#: They come from different code paths and share no wording, so matching only
#: the first — which both the classifier and model_manager's self-heal used to
#: do — silently missed half the class (#1273):
#:
#:   hub load:   "<repo> does not appear to have a file named
#:                pytorch_model.bin or model.safetensors"
#:   local dir:  "Error no file named model.safetensors, or pytorch_model.bin,
#:                found in directory <path>"
#:
#: The second is what a load of a *subfolder* inside a cached snapshot raises
#: (e.g. `audio_tokenizer/`), which is exactly where an interrupted download
#: leaves a repo half-written. Both mean the same thing and both are repaired
#: the same way, so both must classify and both must trigger the heal.
_INCOMPLETE_CACHE_PHRASES = (
    "does not appear to have a file named",
    # Matched as two fragments: the file list between them varies with the
    # transformers version and the requested weight variant.
    ("error no file named", "found in directory"),
)


def is_incomplete_cache_message(text: str) -> bool:
    """True when *text* is transformers reporting a snapshot with no weights.

    Takes an already-lowercased string. Shared by :func:`classify` and
    model_manager's cache self-heal so the healer and the error message can
    never disagree about what an interrupted download looks like.
    """
    low = str(text).lower()
    for phrase in _INCOMPLETE_CACHE_PHRASES:
        if isinstance(phrase, tuple):
            if all(part in low for part in phrase):
                return True
        elif phrase in low:
            return True
    return False


def is_os_write_refusal(reason: Optional[str]) -> bool:
    """True when *reason* looks like the OS refusing a file operation (a full
    or removed drive, a read-only folder, an antivirus/cloud-sync lock) rather
    than a network or format failure. Signature match only; never raises."""
    low = (reason or "").lower()
    return any(sig in low for sig in _OS_WRITE_REFUSAL_SIGNATURES)


def describe_path_target(path: str) -> str:
    """Observable facts about where we were writing — "the folder does not
    exist", "the folder is not writable", "1,234 MB free on its drive".

    A bare OS error ("[Errno 22] Invalid argument", "System error.") names
    neither the target nor the reason, which is what makes those reports
    un-actionable (#1225). Attaching what we CAN see distinguishes a full
    drive from a removed one from an antivirus/OneDrive lock. Never raises —
    diagnosis must never replace the failure being diagnosed.
    """
    facts: list[str] = []
    try:
        directory = os.path.dirname(os.path.abspath(path)) or "."
        if not os.path.isdir(directory):
            facts.append("the folder does not exist")
        else:
            if not os.access(directory, os.W_OK):
                facts.append("the folder is not writable")
            try:
                free_mb = shutil.disk_usage(directory).free / (1024 ** 2)
                facts.append(f"{free_mb:,.0f} MB free on its drive")
            except OSError:
                facts.append("free space could not be read")
    except Exception:
        return ""
    return "; ".join(facts)


#: Exception types whose ``str()`` is a bare VALUE rather than a sentence, so
#: showing it alone tells the user nothing about what went wrong.
#: ``str(KeyError("mgw39lx3"))`` is ``"'mgw39lx3'"`` — the repr of the key.
_VALUE_ONLY_STR_EXCEPTIONS = (KeyError,)


def describe_exception(exc: BaseException) -> str:
    """``str(exc)`` in a form a human can act on.

    #1252/#1253: a dub ingest failed with the toast ``ingest: 'mgw39lx3'`` and
    nothing else — the entire user-facing reason was the repr of a dict key,
    because ``str(KeyError)`` does not mention that a lookup failed, or that it
    was an exception at all. The reporter's ``'mgw39lx3'`` was their own job id
    reflected back at them with no context.

    Naming the class is the floor, not the goal: a failure that reaches here at
    all is one nobody wrote a message for. It keeps the report diagnosable
    instead of cryptic while the specific path gets its own handling.
    """
    text = str(exc).strip()
    if not text:
        return type(exc).__name__
    if isinstance(exc, _VALUE_ONLY_STR_EXCEPTIONS):
        return f"{type(exc).__name__}: {text}"
    return text


def build_failure(
    exc_or_msg: Any,
    *,
    stage: str,
    context: Optional[dict] = None,
    include_diagnostic: bool = True,
) -> dict:
    """Build the structured failure fields (no ``type`` — caller/prep_event adds it).

    ``reason`` is guaranteed non-empty: ``str(exc)`` → exception class name.
    """
    if isinstance(exc_or_msg, BaseException):
        error_class = type(exc_or_msg).__name__
        raw = describe_exception(exc_or_msg)
    else:
        error_class = "Error"
        raw = str(exc_or_msg).strip() or "Unknown failure"

    # Normalise the captured text before anything reads it — both the
    # user-facing reason and classify() below, which would otherwise be
    # matching against a build configuration string (#1309) or against
    # terminal colour codes (#1344).
    # ANSI first: the banner matcher anchors on "ffmpeg version " at the start
    # of a line, which a leading colour code would push out of reach.
    raw = strip_ffmpeg_banner(strip_ansi(raw))
    reason = sanitize(raw) or error_class
    docs_topic = classify(raw)
    # HF_MIRROR_UNREACHABLE's hint is dynamic (it names the configured mirror)
    # so it can't live in the static _HINTS table.
    hint = hf_mirror_hint(raw) if docs_topic == "HF_MIRROR_UNREACHABLE" else _HINTS.get(docs_topic, "")
    fields: dict[str, Any] = {
        "reason": reason,
        "error": reason,  # backward-compat mirror for older frontends
        "error_class": error_class,
        "stage": stage,
        "hint": hint,
        "docs_topic": docs_topic,
        "docs_url": error_docs_map.ERROR_DOCS.get(docs_topic, ""),
        "detail": sanitize(raw),
    }
    if context:
        fields["context"] = {k: sanitize(str(v)) for k, v in context.items()}
    if include_diagnostic:
        fields["diagnostic"] = diagnostic(reason=reason, error_class=error_class, stage=stage)
    return fields


def build_failure_event(
    exc_or_msg: Any,
    *,
    stage: str,
    event_type: str = "error",
    context: Optional[dict] = None,
    include_diagnostic: bool = True,
) -> dict:
    """``build_failure`` plus a ``type`` key, for SSE event sites (tasks.py)."""
    return {
        "type": event_type,
        **build_failure(
            exc_or_msg, stage=stage, context=context, include_diagnostic=include_diagnostic
        ),
    }
