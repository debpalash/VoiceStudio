"""
TTS adapter interface — Phase 3.1 (ROADMAP.md).

A uniform protocol for every TTS engine. Today we ship:

    • OmniVoiceBackend — wraps the current k2-fsa/OmniVoice model. Zero
      behaviour change for existing callers.
    • VoxCPM2Backend   — thin stub that raises with a clear install hint
      until `pip install "voxcpm>=2.0.3"` is present and enabled.

Callers should use `get_active_tts_backend()` to pick the configured engine
instead of importing a specific class. The selection is controlled by the
`OMNIVOICE_TTS_BACKEND` env var (default: `"omnivoice"`).

The protocol deliberately stays narrow: `generate(...)` returns a 1-channel
tensor sampled at `sample_rate`. Streaming is left for a later pass — the
dub generator consumes whole segments today.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from contextlib import contextmanager
from typing import Optional

import torch

logger = logging.getLogger("omnivoice.tts")


# ── HF token leak mitigation (Plan 02-04, T-02-12) ─────────────────────────
#
# Token shape is ``hf_`` + 30+ alphanumeric chars per Hugging Face's own
# format. Any error / status string surfaced through the engines API gets
# scrubbed via :func:`_mask_hf_tokens` before serialization so that a
# backend whose ``is_available()`` interpolates ``HF_TOKEN`` into its
# failure message can't accidentally leak it to the frontend. Phase 1's
# ``HFTokenRedactor`` covers logging only — FastAPI response bodies do
# NOT run through the logging filter chain.
_HF_TOKEN_MASK_RE = re.compile(r"hf_[A-Za-z0-9]{30,}")
_HF_TOKEN_MASK = "hf_***REDACTED***"


def _mask_hf_tokens(value):
    """Return ``value`` with any HF-shaped token substring redacted.

    Non-string values pass through unchanged. Used inside
    :func:`list_backends` for the ``reason`` and ``last_error`` fields.
    """
    if not isinstance(value, str):
        return value
    return _HF_TOKEN_MASK_RE.sub(_HF_TOKEN_MASK, value)


def _available_hint(msg) -> Optional[str]:
    """Advisory text carried by an *available* engine's ``is_available()``
    message, or None when the message is a plain readiness echo.

    Convention (established by VoxCPM2's version-floor hint): an engine
    that is available but wants the user to know something returns
    ``(True, "ready — <advice>")``. This extracts ``<advice>`` so
    :func:`list_backends` can surface it — previously the whole message
    was dropped for available rows (``reason`` is None when ok), so
    upgrade hints never reached the UI. Plain "ready" / "ready (…)"
    messages yield None. Output is token-masked like ``reason``.
    """
    if not isinstance(msg, str):
        return None
    head, sep, advice = msg.partition(" — ")
    advice = advice.strip()
    if not sep or not advice or not head.strip().lower().startswith("ready"):
        return None
    return _mask_hf_tokens(advice)


# ── HF Hub closed-client recovery (#880) ────────────────────────────────────
#
# huggingface_hub ≥1.x shares ONE global httpx client across every download.
# If anything closes it mid-lifecycle, every later hub call — e.g. an engine's
# first-use model download inside the generate path — dies with httpx's
# "Cannot send a request, as the client has been closed". The client is
# recoverable: ``close_session()`` drops it and the next hub call builds a
# fresh one, so the correct handling is a single targeted retry, not a
# user-facing failure.


def _is_closed_client_error(e) -> bool:
    """True iff ``e`` (or anything in its __cause__/__context__ chain) is
    httpx's closed-client lifecycle error. Cycle-safe."""
    seen, stack = set(), [e]
    while stack:
        exc = stack.pop()
        if exc is None or id(exc) in seen:
            continue
        seen.add(id(exc))
        low = str(exc).lower()
        if "client has been closed" in low or "cannot send a request" in low:
            return True
        stack.append(exc.__cause__)
        stack.append(exc.__context__)
    return False


def _retry_once_with_fresh_hf_client(loader, what: str):
    """Run ``loader()`` — a model constructor that may download from the HF
    Hub on first use — retrying transient download failures.

    Two failure shapes are retried, with deliberately different budgets:

    * the httpx **closed-client** lifecycle error (#880) — retried exactly
      ONCE, after resetting the hub's shared session. It's a client-state bug,
      not a network condition: if a fresh session hits it again, repeating
      won't help, and #880 chose to surface it rather than loop.
    * any **transient download** failure ``core.failure
      .is_hf_connectivity_error`` recognises — refused/reset connections, DNS,
      timeouts, and (#1224) a truncated body ("peer closed connection without
      sending complete message body"). A multi-GB model that dies at 90% is
      the single most retry-worthy failure in the path, so this gets the full
      bounded budget. The HF cache is resumable (correctly-sized blobs are
      skipped by hash), so each retry continues rather than restarting.

    Anything unrecognised propagates untouched, where the generation error
    classifier labels it.
    """
    from core.failure import is_hf_connectivity_error

    attempts = max(1, _int_env("OMNIVOICE_MODEL_LOAD_RETRIES", 3))
    backoff = max(0.0, _float_env("OMNIVOICE_MODEL_LOAD_BACKOFF_S", 2.0))
    client_reset_used = False
    attempt = 0
    while True:
        try:
            return loader()
        except Exception as e:
            if _is_closed_client_error(e):
                if client_reset_used:
                    raise  # #880: single-shot — a second one is not transient
                client_reset_used = True
                logger.warning(
                    "%s: HF Hub httpx client was closed mid-download (%s); "
                    "retrying once with a fresh client.", what, e,
                )
                try:
                    from huggingface_hub.utils import close_session
                    close_session()
                except Exception:  # pragma: no cover — hub too old / renamed
                    logger.warning(
                        "%s: couldn't reset the HF Hub client; retrying anyway.",
                        what,
                    )
                # Deliberately does NOT consume a download attempt: the two
                # budgets are independent, and letting the session reset eat
                # one left a resumable multi-GB download a retry short of its
                # configured budget (#1224 review).
                continue  # immediate — nothing to back off from
            attempt += 1
            if not is_hf_connectivity_error(str(e)) or attempt >= attempts:
                raise
            logger.warning(
                "%s: model download failed (%s); retrying (attempt %d/%d). "
                "Already-downloaded files are reused.",
                what, e, attempt, attempts,
            )
            if backoff:
                import time as _time
                _time.sleep(backoff * attempt)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    # inf/nan parse fine and then poison the caller: `sleep(inf)` raises
    # OverflowError, turning a retryable download failure into an unrelated
    # crash that hides the original error (#1224 review).
    if value != value or value in (float("inf"), float("-inf")):
        return default
    return value


# ── Protocol ────────────────────────────────────────────────────────────────


class TTSInputError(ValueError):
    """The caller-supplied text can't be synthesized by the selected engine
    (empty / nothing speakable after cleanup). Subclasses ValueError so the
    native /generate route's existing ValueError→400 mapping applies;
    /v1/audio/speech maps it to 400 explicitly (#1173 class — these used to
    surface as opaque 500s like "need at least one array to concatenate")."""


class TTSBackend(ABC):
    """Every TTS engine exposes the same surface, regardless of vendor."""

    #: Unique id for config + UI (e.g. "omnivoice", "voxcpm2").
    id: str = "base"

    #: Human-readable name for the UI.
    display_name: str = "Base TTS"

    #: Output sample rate. May differ per engine (OmniVoice = 24k, VoxCPM2 = 48k).
    @property
    @abstractmethod
    def sample_rate(self) -> int: ...

    #: Languages the engine supports (ISO codes or "multi").
    @property
    @abstractmethod
    def supported_languages(self) -> list[str]: ...

    #: Whether this engine can actually run in the current environment.
    #: Callers use this to fail fast with a clear message instead of loading
    #: a backend that will blow up on first call.
    @classmethod
    @abstractmethod
    def is_available(cls) -> tuple[bool, str]:
        """Return (ok, message). message explains why not, if not."""

    #: Whether this engine supports voice design from a text description
    #: (e.g. "young female, warm tone, British accent") without reference audio.
    supports_voice_design: bool = False

    #: Whether this engine understands the graded-emotion generate kwargs
    #: (``emo_vector`` / ``emo_text`` + ``use_emo_text`` / ``emo_alpha``).
    #: Surfaced via ``list_backends()`` so UI surfaces (the Audiobook expressive
    #: panel, #1208) can show emotion controls ONLY for engines that apply them
    #: — no dead controls. Default False; IndexTTS2 overrides to True. Engines
    #: that don't set it still ignore the kwargs (every generate() takes **kw),
    #: so this is a discoverability hint, not an enforcement gate.
    supports_emotion: bool = False

    def ensure_ready(self) -> None:
        """Load model weights now (blocking), so callers can separate the
        LOAD budget from the GENERATE budget (#1033/#1037 class).

        Every adapter lazily loads inside ``generate()`` via a private
        ``_ensure_loaded()`` — which meant a cold first call spent its whole
        ``OMNIVOICE_GENERATE_TIMEOUT_S`` window (default 300s) downloading /
        loading weights and got killed with a misleading "too heavy for the
        available compute" error (measured in the wild on a fresh install:
        multi-GB checkpoint download, 0% GPU util, #1014). Routes call this
        first under the model-load budget (``OMNIVOICE_MODEL_LOAD_TIMEOUT``,
        default 1200s), then start the generate clock on an already-warm
        engine. Default implementation dispatches to the adapter's own
        ``_ensure_loaded`` when present; engines without lazy state no-op.
        Must be called on the GPU pool (it's blocking), same as generate.
        """
        loader = getattr(self, "_ensure_loaded", None)
        if callable(loader):
            loader()

    #: Whether this engine already emits mastered, studio-grade audio and should
    #: therefore skip the shared apply_mastering() chain (highpass + Compressor,
    #: tuned for OmniVoice's 24 kHz output). Studio engines like VoxCPM2 (native
    #: 48 kHz) set this True so their clean output isn't pumped. Loudness
    #: normalisation is applied regardless — it's a benign peak scale.
    applies_own_mastering: bool = False

    #: Whether this engine can clone an arbitrary voice from reference audio
    #: (`ref_audio=`), as opposed to only offering a fixed set of preset
    #: voices. Default True — most engines clone. Dub/batch gate on this
    #: (issue #312 class) before committing to a job that needs it, instead
    #: of silently falling back to OmniVoice or mis-cloning per segment.
    supports_cloning: bool = True

    #: GPU/accelerator targets the engine can run on. Surfaced via the
    #: Engine Compatibility Matrix (Plan 02-04 / ENGINE-06) so users can
    #: tell at a glance which engines will use their hardware. Defaults to
    #: CPU-only — subclasses override with the union of devices their
    #: implementation supports (cuda / mps / rocm / cpu). This is metadata,
    #: not enforced — actual device selection lives in the engine's loader.
    gpu_compat: tuple[str, ...] = ("cpu",)

    #: Approximate VRAM (GB) the engine needs to render comfortably on a
    #: dedicated GPU. Metadata, like ``gpu_compat`` — never enforced, because a
    #: hard refuse would block hosts that would actually cope (drivers page to
    #: system RAM, and a short input can fit where a long one won't).
    #:
    #: What it IS for: telling the user BEFORE they wait (#1226/#1222). Two
    #: users on 4 GB cards (GTX 1650 Ti, Quadro P2000) ran the `omnivoice`
    #: engine, waited out the full compute budget, and were told the job "was
    #: too heavy for the available compute" — after the fact, with no hint
    #: that their card was under-provisioned for the engine they'd picked.
    #: Routing showed a clean green "accelerated" the whole time, because
    #: family membership was the only thing anything checked.
    #:
    #: 0 means "no meaningful floor" (CPU-class engines) and never warns.
    min_vram_gb: float = 0.0

    #: True when generation allocates in ANOTHER process — a dedicated-venv
    #: sidecar (SubprocessBackend) or a spawned binary (omnivoice-gguf).
    #: Parent-process accelerator counters cannot see those allocations, so
    #: profilers/diagnostics must not attribute the parent's VRAM numbers to
    #: the engine. Duck-typed (attribute, not issubclass) for the same
    #: module-purge reason as `_is_subprocess_isolated`.
    runs_out_of_process: bool = False

    def model_identity(self) -> Optional[str]:
        """Which concrete model this backend would run, for adapter engines
        that host several very different models behind one backend id
        (mlx-audio, sherpa-onnx, cosyvoice). None means the engine id
        already names the model. Profilers and diagnostics use this to
        label results — without it, Kokoro-under-mlx and Dia-under-mlx
        rows are indistinguishable."""
        return None

    @abstractmethod
    def generate(
        self,
        text: str,
        *,
        ref_audio: Optional[str] = None,
        ref_text: Optional[str] = None,
        instruct: Optional[str] = None,
        language: Optional[str] = None,
        duration: Optional[float] = None,
        description: Optional[str] = None,
        num_step: int = 16,
        guidance_scale: float = 2.0,
        speed: float = 1.0,
        **extras,
    ) -> torch.Tensor:
        """Synthesize `text`. Returns a tensor of shape (1, n_samples).

        When `description` is provided and `ref_audio` is None, engines that
        support voice design will create a synthetic voice matching the
        description (e.g. "young female, warm, slight British accent").
        Engines that don't support this will ignore the parameter.
        """

    def generate_batch(
        self,
        texts: list[str],
        *,
        ref_audio=None,
        ref_text=None,
        instruct=None,
        language=None,
        duration=None,
        speed=1.0,
        **extras,
    ) -> list[torch.Tensor]:
        """Synthesize several utterances, preserving the single-item contract.

        Engines with a native batch forward pass override this method.  The
        default keeps every existing adapter correct while giving callers one
        stable seam and per-item keyword handling.
        """
        if not texts:
            return []

        def _item(value, index):
            return value[index] if isinstance(value, list) else value

        return [
            self.generate(
                text,
                ref_audio=_item(ref_audio, index),
                ref_text=_item(ref_text, index),
                instruct=_item(instruct, index),
                language=_item(language, index),
                duration=_item(duration, index),
                speed=_item(speed, index),
                **extras,
            )
            for index, text in enumerate(texts)
        ]

    # ── Lifecycle (Phase 2 will enforce per-engine overrides) ──────────────
    #
    # Today every backend lazily loads its weights on first `generate()` and
    # keeps them in VRAM for the lifetime of the process. Switching engines
    # in Settings therefore leaks the old engine's allocations until the
    # next process restart — measurable on multi-engine sessions on 8 GB
    # MPS Macs.
    #
    # `unload()` is the contract that lets the registry release an engine
    # before instantiating the next one. It is a default no-op on the ABC
    # so this commit does not break any of the 9 existing subclasses; Phase
    # 2 (engine isolation) overrides it per-engine and adds a CI gate that
    # fails when a subclass doesn't implement it.
    #
    # Contract for overriders:
    #   • Idempotent: calling unload() twice must not raise.
    #   • Synchronous: returns after VRAM is freed (or after best-effort
    #     `torch.cuda.empty_cache()` / `torch.mps.empty_cache()`).
    #   • Safe to call before the first generate(): a backend that never
    #     loaded has nothing to release.
    # Attribute(s) that hold this backend's heavy model, cleared by the default
    # unload(). Every in-process engine loads its weights lazily into one of
    # these in `_ensure_loaded()`; the next generate() re-runs that loader. An
    # engine that holds its model elsewhere (or nowhere — e.g. an external HTTP
    # server) overrides `unload()` or leaves these unset. OmniVoice overrides
    # entirely (it drives the shared model_manager singleton).
    _MODEL_ATTRS: tuple[str, ...] = ("_model", "_tts")

    def execution_evidence_loaded(self) -> bool:
        """Whether this instance has live model state worth reporting."""
        if self.runs_out_of_process:
            proc = getattr(self, "_proc", None)
            return proc is not None and proc.poll() is None
        return any(getattr(self, attr, None) is not None for attr in self._MODEL_ATTRS)

    def unload(self) -> None:
        """Release the heavy model this backend holds, and free device caches.

        Called by the registry on engine switch, by the single-active-engine
        eviction (services.engine_memory), and on app shutdown. Clears each of
        ``_MODEL_ATTRS`` that is set on this instance, then empties the device
        cache — so switching engines actually hands the memory back instead of
        leaving the old model resident until GC (the 16 GB-Mac OOM class). The
        next generate() lazily reloads. Idempotent and safe before first load:
        a backend that never loaded has every attr already None/absent.
        """
        freed = False
        for attr in self._MODEL_ATTRS:
            if getattr(self, attr, None) is not None:
                setattr(self, attr, None)
                freed = True
        if freed:
            try:
                from services.model_manager import free_vram

                free_vram()
            except Exception:  # noqa: BLE001 — unload must never raise (idempotent contract)
                pass
        return None


# ── OmniVoice adapter (the current default) ─────────────────────────────────


# ── Voice-clone prompt cache (#427) ──────────────────────────────────────────
# Every cloned generation re-encodes the reference audio from scratch — a fixed
# per-request latency that compounds on batch/long-form workloads reusing one
# voice. The OmniVoice model exposes create_voice_clone_prompt(ref) →
# VoiceClonePrompt + generate(voice_clone_prompt=) to do that encoding ONCE.
# We cache the prompt (bounded LRU, keyed by ref path + mtime + ref_text) so
# repeated generations with the same voice skip the encode. Bounded because a
# VoiceClonePrompt holds tensors (VRAM). Thread-safe (generation runs in a GPU
# thread pool). Best-effort: any miss/error falls back to the inline ref path,
# so output is never affected — this is a pure latency optimization.
_PROMPT_CACHE_MAX = 8
_prompt_cache: "OrderedDict[tuple, object]" = OrderedDict()
_prompt_cache_lock = threading.Lock()

# Disk layer under the in-memory LRU (upstream k2-fsa VoiceClonePrompt.save/
# load format). The in-memory cache dies with the process, so the first
# generation of every session re-encodes each voice (~0.4 s + an ASR pass when
# ref_text is missing). Encoded prompts are tiny (a (8, T) int token tensor +
# transcript), so we persist them and reload across restarts. Keyed by the
# same tuple as the memory cache — the ref file's mtime is inside the key, so
# an edited reference never matches a stale file; stale files age out via the
# mtime prune. Best-effort like the memory cache: any failure means "no disk
# hit / no disk write", never a failed generation. OMNIVOICE_PROMPT_DISK_CACHE=0
# disables the layer entirely.
_PROMPT_DISK_CACHE_MAX = 32


def _prompt_disk_dir():
    """Return the prompt-cache directory (created on first use), or None when
    the layer is disabled or the directory can't be created."""
    if os.environ.get("OMNIVOICE_PROMPT_DISK_CACHE", "1") == "0":
        return None
    try:
        from core.config import DATA_DIR

        path = os.path.join(str(DATA_DIR), "prompt_cache")
        os.makedirs(path, exist_ok=True)
        return path
    except Exception as e:  # noqa: BLE001 — cache layer must never break synthesis
        logger.debug("prompt disk cache unavailable: %s", e)
        return None


def _prompt_disk_path(cache_dir: str, key: tuple) -> str:
    import hashlib

    digest = hashlib.sha256(repr(key).encode("utf-8")).hexdigest()[:32]
    return os.path.join(cache_dir, f"{digest}.pt")


def _prompt_disk_load(key: tuple):
    """Load a persisted prompt for ``key``, or None. Never raises."""
    cache_dir = _prompt_disk_dir()
    if cache_dir is None:
        return None
    path = _prompt_disk_path(cache_dir, key)
    if not os.path.exists(path):
        return None
    try:
        from omnivoice.models.omnivoice import VoiceClonePrompt

        prompt = VoiceClonePrompt.load(path)
        # Freshen so the LRU prune (by mtime) keeps actively used voices.
        os.utime(path, None)
        return prompt
    except Exception as e:  # noqa: BLE001
        logger.warning("failed to load cached voice prompt %s: %s", path, e)
        try:
            os.remove(path)  # corrupt/incompatible file — don't retry it forever
        except OSError:
            pass
        return None


def _prompt_disk_save(key: tuple, prompt) -> None:
    """Persist ``prompt`` under ``key`` and prune old entries. Never raises."""
    cache_dir = _prompt_disk_dir()
    if cache_dir is None:
        return
    path = _prompt_disk_path(cache_dir, key)
    try:
        # Unique per write: two GPU-pool threads missing the same key must not
        # interleave writes into one tmp file (os.replace stays atomic).
        import uuid

        tmp = f"{path}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"
        prompt.save(tmp)
        os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001
        logger.warning("failed to persist voice prompt to %s: %s", path, e)
        return
    try:
        entries = [
            os.path.join(cache_dir, f)
            for f in os.listdir(cache_dir)
            if f.endswith(".pt")
        ]
        entries.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for old in entries[_PROMPT_DISK_CACHE_MAX:]:
            os.remove(old)
    except OSError as e:
        logger.debug("prompt disk cache prune skipped: %s", e)


def _clone_prompt_key(ref_audio: str, ref_text, preprocess_prompt: bool = True):
    try:
        mtime = os.path.getmtime(ref_audio)
    except OSError:
        mtime = 0.0
    # preprocess_prompt is part of the key: it changes the encoded prompt
    # (silence removal + trimming + ref-text punctuation, omnivoice.py:675/722),
    # so a False request must not be served a True-encoded prompt — or poison
    # the cache for the True callers. /generate never sets it (always the True
    # default); /v1/audio/speech exposes it.
    return (os.path.abspath(ref_audio), mtime, ref_text or "", bool(preprocess_prompt))


def _get_clone_prompt(
    model, ref_audio: str, ref_text, preprocess_prompt: bool = True, *,
    store: bool = True,
):
    """Return a cached/precomputed ``VoiceClonePrompt`` for
    (ref_audio, ref_text, preprocess_prompt), or ``None`` to fall back to the
    inline ref path.

    Raises only on a device OOM that survives a cache-drop retry (#1790): the
    inline path is the same allocation on the same device, so falling back to
    it after an OOM cannot succeed and has been observed taking the whole
    process down instead. Every other failure still falls back silently.

    ``store=False`` still *reads* the cache (a hit is free) but never inserts:
    it exists for single-use references — a dub's per-segment ref clips are each
    a distinct file used exactly once, and inserting a stream of them into an
    LRU of 8 evicts the per-speaker and locked-profile prompts that ARE reused.
    Every short segment falling back to its speaker ref then re-encodes it
    (~0.4 s each, measured). Scan-resistance, not a second cache policy.
    """
    try:
        key = _clone_prompt_key(ref_audio, ref_text, preprocess_prompt)
    except Exception:
        return None
    with _prompt_cache_lock:
        hit = _prompt_cache.get(key)
        if hit is not None:
            _prompt_cache.move_to_end(key)
            return hit
    # Memory miss → disk (survives restarts). A disk hit skips the encode AND
    # the ASR transcription pass a ref_text-less reference would trigger.
    prompt = _prompt_disk_load(key)
    if prompt is None:
        try:
            # Encode outside the lock (slow). Mirrors exactly what generate()
            # would do inline for this ref (omnivoice.py:964-978), so output is
            # identical.
            prompt = model.create_voice_clone_prompt(
                ref_audio, ref_text=ref_text, preprocess_prompt=preprocess_prompt
            )
        except Exception as e:  # noqa: BLE001 — fall back, never break synthesis
            # #1790/#1777: a GPU OOM is the one failure this fallback cannot
            # absorb. `generate()`'s inline ref path runs the SAME encode on the
            # SAME device — the docstring above says so, because producing
            # identical output is the point — so returning None after an OOM
            # guarantees a second OOM moments later, on a device with even less
            # headroom than the first attempt found. Both reporters' backends
            # then died with a Windows access violation (exit code
            # -1073741819) seconds after this exact log line, mid-generation on
            # a GPU that had just refused an 86 MiB allocation.
            #
            # An OOM here is also the most recoverable kind: the allocator is
            # typically holding reserved-but-unallocated blocks (#1790's own
            # log reports 90 MiB reserved against an 86 MiB request). Drop them
            # and try once more. If it still will not fit, raise — the failure
            # layer turns a device OOM into the actionable GPU_OOM message
            # ("close other GPU-heavy apps or unload models…"), which is a far
            # better answer than walking into a native fault.
            from core.failure import is_gpu_oom

            if is_gpu_oom(e):
                logger.warning(
                    "voice-clone prompt precompute hit a device OOM (%s) — "
                    "releasing allocator caches and retrying once", e,
                )
                try:
                    from services.model_manager import free_vram
                    free_vram()
                except Exception:  # noqa: BLE001 — reclaim is best-effort
                    logger.debug("VRAM reclaim before OOM retry failed", exc_info=True)
                prompt = model.create_voice_clone_prompt(
                    ref_audio, ref_text=ref_text, preprocess_prompt=preprocess_prompt
                )
            else:
                logger.warning(
                    "voice-clone prompt precompute failed; using inline ref: %s", e
                )
                return None
        if store:
            _prompt_disk_save(key, prompt)
    if not store:
        return prompt
    with _prompt_cache_lock:
        _prompt_cache[key] = prompt
        _prompt_cache.move_to_end(key)
        while len(_prompt_cache) > _PROMPT_CACHE_MAX:
            _prompt_cache.popitem(last=False)
    return prompt


def generate_with_cached_ref(model, *, ref_audio, ref_text, **gen_kw):
    """``model.generate()`` with the reference clip encoded once, not once per call.

    The native (non-adapter) callers of the OmniVoice model — ``/generate`` and its
    streaming twin, and the audiobook/long-form renderer — used to pass
    ``ref_audio=<path>`` straight through, so the codec encoder re-ran the reference
    on **every generate call**: once per chunk, per pause-span, and per audiobook
    segment, not merely once per request. The prompt cache below (#427/#473) existed
    the whole time but only ``OmniVoiceBackend`` (the adapter path) ever called it,
    and the default engine doesn't take that path.

    This is the one place that knows the rule, so it can't be re-broken piecemeal:
    ``voice_clone_prompt`` and ``ref_audio``/``ref_text`` are **mutually exclusive** —
    pass both and the model warns and ignores the latter (omnivoice.py:957).

    The cache is **best-effort, never load-bearing**: if the prompt can't be built,
    or the model rejects the one we built, we fall back to the inline reference and
    synthesize exactly as before. A latency optimization must never be able to turn
    a generation that would have succeeded into an error.
    """
    # cache_ref=False marks a single-use reference (a dub's per-segment clips):
    # look the cache up, but never insert — see _get_clone_prompt(store=). MUST
    # be popped: the model's generate() has an explicit signature and would
    # TypeError on an unknown kwarg.
    cache_ref = bool(gen_kw.pop("cache_ref", True))
    # Stays in gen_kw too: the model needs it on the inline branch, and it is inert
    # on the prompt branch (that prompt is already encoded).
    preprocess_prompt = bool(gen_kw.get("preprocess_prompt", True))
    prompt = (
        _get_clone_prompt(model, ref_audio, ref_text, preprocess_prompt, store=cache_ref)
        if ref_audio else None
    )
    if prompt is not None:
        try:
            return model.generate(voice_clone_prompt=prompt, **gen_kw)
        except Exception as e:  # noqa: BLE001 — fall back to the inline ref
            logger.warning("voice_clone_prompt generate failed; retrying inline ref: %s", e)
    return model.generate(ref_audio=ref_audio, ref_text=ref_text, **gen_kw)


def clear_clone_prompt_cache() -> None:
    """Drop all cached voice-clone prompts (frees their tensors). Called on model
    unload so a flush/engine-switch doesn't strand VRAM."""
    with _prompt_cache_lock:
        _prompt_cache.clear()


# NB: model_manager.release_tts_side_caches() calls clear_clone_prompt_cache()
# above whenever it drops the TTS model — the prompts belong to that model
# instance and an "unload" that leaves them behind isn't an unload (#1119). It
# reaches this module through sys.modules rather than importing it, so there is
# no import cycle and no import-time side effect here.


class OmniVoiceBackend(TTSBackend):
    """Wraps `omnivoice.models.omnivoice.OmniVoice`. Zero behaviour change.

    Loads lazily on the first `generate` call, mirrors the existing
    `services.model_manager.get_model()` flow: torch.compile on CUDA,
    fp16, ASR co-loaded.
    """

    id = "omnivoice"
    display_name = "VoiceStudio (k2-fsa/OmniVoice, 600+ languages)"
    gpu_compat = ("cuda", "rocm", "mps", "cpu")
    # Derived from the pool's own per-job budget (_GPU_VRAM_PER_JOB_GB = 5.0 in
    # model_manager, itself measured from the ~1.6 GB forward + autoregressive
    # decode and the co-loaded WhisperX on the clone path), plus room for the
    # resident weights. Below this the driver pages to system RAM and a render
    # that should take seconds runs for minutes — which is precisely what the
    # 4 GB reporters in #1226/#1222 hit. Deliberately the only engine with a
    # floor: the rest have no measured figure, and inventing one would put a
    # confident number in the UI that nothing backs.
    min_vram_gb = 6.0

    def __init__(self, model=None):
        # The live OmniVoice instance. Reuses the singleton owned by
        # model_manager so memory isn't doubled.
        self._model = model

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        try:
            import omnivoice.models.omnivoice  # noqa: F401
            return True, "ready"
        except Exception as e:
            return False, f"omnivoice package missing: {e}"

    @property
    def sample_rate(self) -> int:
        if self._model is None:
            return 24000  # canonical OmniVoice rate
        return getattr(self._model, "sampling_rate", 24000)

    @property
    def supported_languages(self) -> list[str]:
        # OmniVoice advertises 600+ zero-shot — `"multi"` is the honest tag.
        return ["multi"]

    def _ensure_loaded(self):
        if self._model is not None:
            return
        # Reuse model_manager's cached instance so we don't double-load.
        from services.model_manager import get_model
        import asyncio
        # Caller is sync; spin up a fresh loop if needed. get_running_loop()
        # raises only when *no* loop is running — that's the safe path where
        # we can bootstrap with asyncio.run().
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._model = asyncio.run(get_model())
            return
        raise RuntimeError(
            "OmniVoiceBackend.generate() called inside an async context without a pre-loaded model. "
            "Pass `model=await get_model()` to the constructor."
        )

    def generate(self, text, **kw) -> torch.Tensor:
        self._ensure_loaded()
        language = kw.get("language")
        ref_audio = kw.get("ref_audio")
        ref_text = kw.get("ref_text")
        gen_kw = dict(
            text=text,
            language=language if language and language != "Auto" else None,
            instruct=kw.get("instruct"),
            duration=kw.get("duration"),
            num_step=kw.get("num_step", 16),
            guidance_scale=kw.get("guidance_scale", 2.0),
            speed=kw.get("speed", 1.0),
            denoise=kw.get("denoise", True),
            postprocess_output=kw.get("postprocess_output", True),
        )
        # /v1/audio/speech exposes preprocess_prompt (openai_compat.py) and it
        # used to be dropped on the floor here — the API accepted it and gen_kw
        # never carried it, so it silently did nothing.
        gen_kw["preprocess_prompt"] = bool(kw.get("preprocess_prompt", True))
        # Single-use reference hint (dub per-segment clips) — see
        # generate_with_cached_ref, which pops it before the model sees it.
        gen_kw["cache_ref"] = bool(kw.get("cache_ref", True))
        # The cached-reference path lives in generate_with_cached_ref, shared with
        # the native callers. Deliberately NOT a second copy: this logic living in
        # one place here and a subtly different one there is exactly how the cache
        # came to be wired into the adapter and nowhere else.
        audios = generate_with_cached_ref(
            self._model, ref_audio=ref_audio, ref_text=ref_text, **gen_kw
        )
        return audios[0]

    def generate_batch(self, texts: list[str], **kw) -> list[torch.Tensor]:
        """Use OmniVoice's native variable-length batch generation.

        Batch callers pass per-item language, duration, speed and reference
        lists.  Reusable clone prompts are prepared once and handed to the
        model together; an incomplete prompt batch falls back to the proven
        single-item path instead of changing synthesis semantics.
        """
        self._ensure_loaded()
        if not texts:
            return []

        def _items(value):
            if isinstance(value, list):
                return value
            return [value] * len(texts)

        def _item_kwargs(index):
            return {
                key: value[index] if isinstance(value, list) else value
                for key, value in kw.items()
            }

        ref_audios = _items(kw.get("ref_audio"))
        ref_texts = _items(kw.get("ref_text"))
        cache_ref = bool(kw.get("cache_ref", True))
        preprocess_prompt = bool(kw.get("preprocess_prompt", True))
        prompts = []
        if any(ref_audios):
            for ref_audio, ref_text in zip(ref_audios, ref_texts):
                if not ref_audio:
                    prompts = []
                    break
                prompt = _get_clone_prompt(
                    self._model,
                    ref_audio,
                    ref_text,
                    preprocess_prompt,
                    store=cache_ref,
                )
                if prompt is None:
                    prompts = []
                    break
                prompts.append(prompt)

        if any(ref_audios) and len(prompts) != len(texts):
            return [self.generate(text, **_item_kwargs(i))
                    for i, text in enumerate(texts)]

        gen_kw = dict(
            language=kw.get("language"),
            instruct=kw.get("instruct"),
            duration=kw.get("duration"),
            speed=kw.get("speed", 1.0),
            denoise=kw.get("denoise", True),
            postprocess_output=kw.get("postprocess_output", True),
            num_step=kw.get("num_step", 16),
            guidance_scale=kw.get("guidance_scale", 2.0),
            preprocess_prompt=preprocess_prompt,
        )
        if prompts:
            gen_kw["voice_clone_prompt"] = prompts
        else:
            gen_kw["ref_audio"] = None
            gen_kw["ref_text"] = None
        return self._model.generate(text=texts, **gen_kw)

    def unload(self) -> None:
        """Release the OmniVoice model (MM2-02). OmniVoice shares the singleton
        owned by ``model_manager``, so dropping our local ref isn't enough — we
        clear the shared one and free GPU memory too. Idempotent and safe before
        the first generate(). Best-effort: assignment is GIL-atomic, so we don't
        take the async ``_model_lock`` from this sync path; the registry wraps
        this call in try/except so a race can never block an engine switch.

        Delegates to ``model_manager.unload_shared_model`` rather than clearing
        the singleton here: this path used to free the device caches *before*
        dropping the shared reference, which frees nothing, and it is the path
        the idle sweep on a headless worker node runs (#1495)."""
        self._model = None
        clear_clone_prompt_cache()  # #427: drop cached prompts so VRAM is freed
        try:
            import services.model_manager as mm
            mm.unload_shared_model()
        except Exception as exc:
            # The reference is already gone by the time anything in here can
            # raise — only the device-cache flush is left, and that failing is
            # a driver problem, not a stuck model. Saying "retry" would send
            # the user to repeat an unload that already happened.
            logger.warning("Shared voice model released, but the device cache flush failed")
            raise RuntimeError(
                "The voice model was released, but the GPU memory cache could not be flushed."
            ) from exc


# ── VoxCPM2 adapter (optional, scaffolded) ──────────────────────────────────

#: Minimum recommended `voxcpm` package version. 2.0.3 fixed an audio-quality
#: bug on Apple Silicon (low-precision dtypes on the MPS device produced
#: degraded output). A floor, NOT a pin: newer versions are fine, and an
#: already-installed older version keeps working — we only surface an upgrade
#: hint (is_available reason + load-time warning), never force a reinstall.
_VOXCPM_MIN_VERSION = "2.0.3"

#: Reference-clip cap for VoxCPM2 cloning (seconds). The `voxcpm` package no
#: longer trims reference audio internally, so an unbounded user clip would
#: condition the model on minutes of audio (slow, and past a point it stops
#: helping voice similarity). 30 s is a conservative upper bound.
_VOXCPM_REF_MAX_S = 30.0

#: Silence pad kept around the voiced region when trimming a reference clip —
#: a hard cut exactly at the first/last voiced sample clips consonant onsets.
_VOXCPM_REF_EDGE_PAD_S = 0.05


def _version_tuple(v: str) -> Optional[tuple[int, ...]]:
    """Parse the leading numeric components of a version string ("2.0.3" →
    (2, 0, 3), "2.1rc1" → (2, 1)). Returns None when nothing numeric parses —
    callers treat that as 'unknown, assume fine' rather than failing."""
    parts: list[int] = []
    for piece in v.split("."):
        digits = ""
        for ch in piece:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) if parts else None


def _voxcpm_installed_version() -> Optional[str]:
    """Installed `voxcpm` dist version, or None when undeterminable
    (not installed, or importable without package metadata)."""
    try:
        from importlib.metadata import version
        return version("voxcpm")
    except Exception:
        return None


def _voxcpm_upgrade_hint() -> Optional[str]:
    """Actionable upgrade hint when the installed `voxcpm` is older than
    :data:`_VOXCPM_MIN_VERSION`, else None. Never raises; an unparseable or
    unknown version yields None (don't nag users we can't be sure about)."""
    installed = _voxcpm_installed_version()
    if installed is None:
        return None
    have = _version_tuple(installed)
    want = _version_tuple(_VOXCPM_MIN_VERSION)
    if have is None or want is None or have >= want:
        return None
    return (
        f"installed voxcpm {installed} is older than {_VOXCPM_MIN_VERSION}, "
        "which fixed an audio-quality bug on Apple Silicon (low-precision "
        "dtypes on MPS). The engine still works, but upgrading is "
        'recommended: pip install --upgrade "voxcpm>=2.0.3"'
    )


# Prepared-reference cache: (abspath, mtime_ns, size) → prepared path (which
# may be the original path itself when no trim/cap applied). Keeps repeat
# generations from re-reading + re-writing the same clip, and keeps the temp
# dir from filling with one copy per generate() call.
_VOXCPM_REF_PREP_CACHE: dict[tuple, str] = {}


def _prepare_voxcpm_ref(path: str) -> str:
    """Prepare a cloning reference clip for VoxCPM2.

    The `voxcpm` package used to trim reference audio itself but no longer
    does — raw user clips reach the model unconditioned. This applies the
    minimal, conservative preparation the model expects:

      • trim leading/trailing near-silence (amplitude threshold at the same
        -50 dBFS floor `audio_dsp.normalize_audio` uses, with a small
        :data:`_VOXCPM_REF_EDGE_PAD_S` pad kept on each side), and
      • cap the reference at :data:`_VOXCPM_REF_MAX_S` seconds from the
        trimmed start.

    Returns a path to the prepared WAV. Deliberately non-destructive and
    fail-open: the ORIGINAL path is returned unchanged when the clip needs no
    meaningful trim/cap (short clean clips pass through untouched), when the
    whole clip sits below the silence floor (nothing to anchor a trim on), or
    when anything at all goes wrong — reference prep must never be the reason
    a generation fails.
    """
    try:
        import numpy as np
        import soundfile as sf

        abspath = os.path.abspath(path)
        st = os.stat(abspath)
        cache_key = (abspath, st.st_mtime_ns, st.st_size)
        cached = _VOXCPM_REF_PREP_CACHE.get(cache_key)
        if cached is not None and (cached == abspath or os.path.exists(cached)):
            return cached

        audio, sr = sf.read(abspath, dtype="float32", always_2d=True)  # (n, ch)
        n = audio.shape[0]
        if n == 0 or sr <= 0:
            return path

        # Silence floor: -50 dBFS, matching audio_dsp.normalize_audio. A clip
        # that never rises above it is left alone (fail-open, see docstring).
        floor = 10 ** (-50.0 / 20.0)
        envelope = np.abs(audio).max(axis=1)
        voiced = np.flatnonzero(envelope > floor)
        if voiced.size == 0:
            _VOXCPM_REF_PREP_CACHE[cache_key] = abspath
            return path

        pad = int(_VOXCPM_REF_EDGE_PAD_S * sr)
        start = max(0, int(voiced[0]) - pad)
        end = min(n, int(voiced[-1]) + 1 + pad)
        cap = int(_VOXCPM_REF_MAX_S * sr)
        end = min(end, start + cap)

        # No-op path: nothing meaningful to cut (>0.1 s total) — hand the
        # original file to the model byte-identical.
        if (start + (n - end)) <= int(0.1 * sr):
            _VOXCPM_REF_PREP_CACHE[cache_key] = abspath
            return path

        import tempfile
        fd, prepared = tempfile.mkstemp(prefix="voxcpm_ref_", suffix=".wav")
        os.close(fd)
        sf.write(prepared, audio[start:end], sr)
        _VOXCPM_REF_PREP_CACHE[cache_key] = prepared
        logger.info(
            "VoxCPM2: prepared reference clip %s → %s (%.2fs → %.2fs; "
            "silence trimmed, cap %.0fs)",
            path, prepared, n / sr, (end - start) / sr, _VOXCPM_REF_MAX_S,
        )
        return prepared
    except Exception as e:  # noqa: BLE001 — prep is best-effort by contract
        logger.warning(
            "VoxCPM2: reference-clip preparation failed for %s — using the "
            "raw clip: %s", path, e,
        )
        return path


class VoxCPM2Backend(TTSBackend):
    """OpenBMB VoxCPM2 wrapper — `pip install "voxcpm>=2.0.3"` required.

    Ships as a scaffold: the class loads and reports unavailability cleanly
    when the dep isn't installed, so Settings UI can gate the engine selector
    without a hard crash. When `voxcpm` is present, `generate()` delegates to
    the real model.

    Voice Design: VoxCPM2 uniquely supports creating voices from a text
    description (e.g. "young female, warm tone, British accent") without
    any reference audio. Pass `description=` without `ref_audio=` to use
    this mode.
    """

    id = "voxcpm2"
    display_name = "VoxCPM2 (30 langs, studio 48 kHz, voice design)"
    supports_voice_design = True
    applies_own_mastering = True  # native 48 kHz studio output — skip apply_mastering()
    gpu_compat = ("cuda", "mps", "cpu")

    def __init__(self):
        self._model = None

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        try:
            import voxcpm  # noqa: F401
        except ImportError:
            return False, (
                "voxcpm package not installed. Install with "
                '`pip install "voxcpm>=2.0.3"` '
                "(requires Python ≥3.10, PyTorch ≥2.5). CUDA ≥12 recommended "
                "for full speed; MPS (Apple Silicon) and CPU also supported."
            )
        # Version FLOOR, not pin: an older install still reports available
        # (no forced reinstall), but the reason carries the upgrade hint and
        # _ensure_loaded() logs it at load time.
        hint = _voxcpm_upgrade_hint()
        if hint:
            return True, f"ready — {hint}"
        return True, "ready"

    @property
    def sample_rate(self) -> int:
        return 48000

    @property
    def supported_languages(self) -> list[str]:
        # 30 langs per model card.
        return [
            "ar", "my", "zh", "da", "nl", "en", "fi", "fr", "de", "el",
            "he", "hi", "id", "it", "ja", "km", "ko", "lo", "ms", "no",
            "pl", "pt", "ru", "es", "sw", "sv", "tl", "th", "tr", "vi",
        ]

    def _ensure_loaded(self):
        if self._model is not None:
            return
        ok, msg = self.is_available()
        if not ok:
            raise RuntimeError(f"VoxCPM2 unavailable: {msg}")
        hint = _voxcpm_upgrade_hint()
        if hint:
            logger.warning("VoxCPM2: %s", hint)
        from voxcpm import VoxCPM  # type: ignore[import-not-found]
        checkpoint = os.environ.get("OMNIVOICE_VOXCPM_MODEL", "openbmb/VoxCPM2")
        logger.info("Loading VoxCPM2 from %s", checkpoint)
        # #1224: this first-use download is multi-GB. Unretried, a truncated
        # body at 90% aborted the load outright.
        self._model = _retry_once_with_fresh_hf_client(
            lambda: VoxCPM.from_pretrained(checkpoint, load_denoiser=False),
            "VoxCPM2",
        )

    def generate(self, text, **kw) -> torch.Tensor:
        self._ensure_loaded()
        import numpy as np

        ref_audio = kw.get("ref_audio")
        ref_text = kw.get("ref_text")
        description = kw.get("description")
        instruct = kw.get("instruct")

        # ── Voice Design mode: description-only, no reference audio ─────
        # VoxCPM2's `generate_from_description()` creates a synthetic voice
        # matching a natural-language description. This is the P0 feature
        # from the roadmap — text → voice without any audio sample.
        if description and not ref_audio:
            logger.info(
                "VoxCPM2: voice design mode — generating from description: %r",
                description[:80],
            )
            wav = self._model.generate(
                text=text,
                voice_description=description,
                cfg_value=kw.get("guidance_scale", 2.0),
                inference_timesteps=kw.get("num_step", 10),
            )
            return self._finalize(wav)

        # ── Standard clone / instruct mode ──────────────────────────────
        # Map our instruct prop onto VoxCPM2's inline "(instruct)prompt" prefix.
        # The reference clip is prepared first (edge-silence trim + length
        # cap) — the model no longer trims it internally, so a raw user clip
        # would condition generation on dead air. Fail-open: on any prep
        # problem the raw path is used, exactly as before.
        if ref_audio:
            ref_audio = _prepare_voxcpm_ref(ref_audio)
        prompt = text
        if instruct:
            prompt = f"({instruct}){text}"
        wav = self._model.generate(
            text=prompt,
            cfg_value=kw.get("guidance_scale", 2.0),
            inference_timesteps=kw.get("num_step", 10),
            reference_wav_path=ref_audio,
            prompt_wav_path=ref_audio if ref_text else None,
            prompt_text=ref_text,
        )
        return self._finalize(wav)

    def _finalize(self, wav) -> torch.Tensor:
        """Normalize model output to a (1, n) float tensor and apply the
        trailing-silence guard.

        The guard is a SILENCE trim only: generations often end with a long
        near-silent tail, which this cuts (keeping a short ~0.3 s natural
        tail). It deliberately does NOT attempt to detect or judge trailing
        *content* — an output that ends in audible audio, wanted or not,
        passes through unchanged, as does any output without a silent tail.
        """
        import numpy as np
        from services.audio_dsp import trim_trailing_silence

        if isinstance(wav, np.ndarray):
            wav = torch.from_numpy(wav).float()
        if wav.ndim == 1:
            wav = wav.unsqueeze(0)
        return trim_trailing_silence(wav, self.sample_rate)


# ── MOSS-TTS-Nano adapter (tiny, CPU-friendly, 20 langs) ────────────────────


# ── MOSS-TTS-Nano entry-point resolution (#1287) ────────────────────────────
# The upstream repo is installed straight from git (`pip install -e .`) with no
# pinned release, and the class it exports has changed. Rather than hard-import
# one name and fail at generate time, resolve among the names it has used and
# report honestly when none is present.
_MOSS_CLASS_NAMES = ("MossTTSNano", "MOSSTTSNano", "MossTTS", "MossTTSNanoForCausalLM")


def _moss_model_class(module):
    """The first known MOSS model class on ``module``, or None."""
    for name in _MOSS_CLASS_NAMES:
        cls = getattr(module, name, None)
        if cls is not None and hasattr(cls, "from_pretrained"):
            return cls
    return None


def _moss_candidate_exports(module):
    """Public names on ``module`` that look like a model class — so the error
    can say what IS there instead of only what is missing."""
    return [
        n
        for n in dir(module)
        if not n.startswith("_")
        and hasattr(getattr(module, n, None), "from_pretrained")
    ]


class MossTTSNanoBackend(TTSBackend):
    """OpenMOSS MOSS-TTS-Nano-100M — the low-resource / broad-language pick.

    100M-param autoregressive codec-LM. Runs realtime on a 4-core CPU (no GPU
    required), native 48 kHz stereo output, 20 languages, Apache-2.0. Fills
    two gaps in the existing lineup: the "runs on a fanless laptop" tier and
    the Arabic/Hebrew/Persian/Korean/Turkish coverage that OmniVoice's
    zero-shot does but VoxCPM2 + XTTS lean against.

    Ships as a scaffold — `is_available()` reports the missing install so the
    Settings picker gates the engine cleanly until the user opts in.
    """

    id = "moss-tts-nano"
    display_name = "MOSS-TTS-Nano (20 langs, CPU realtime, 48 kHz)"
    gpu_compat = ("cuda", "cpu")

    def __init__(self):
        self._model = None
        self._tokenizer = None

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        # Package isn't on PyPI — users install from the MOSS repo
        # (`pip install -e` of github.com/OpenMOSS/MOSS-TTS-Nano) or we load
        # the HF weights with `trust_remote_code=True`.
        try:
            import transformers  # noqa: F401
        except ImportError:
            return False, "transformers not installed"
        try:
            # MOSS ships its own package alongside the HF weights.
            import moss_tts_nano  # noqa: F401
        except ImportError:
            return False, (
                "moss_tts_nano package not installed. Install from "
                "https://github.com/OpenMOSS/MOSS-TTS-Nano "
                "(`pip install -e .`), then set OMNIVOICE_TTS_BACKEND=moss-tts-nano."
            )
        # Importing the MODULE is not enough (#1287). The user had the package
        # installed, so this reported "ready", they switched engine, and the
        # first generate died with `cannot import name 'MossTTSNano'` — the
        # upstream repo is unpinned and moves. An availability check that does
        # not verify the API it will actually call is a check that lies.
        if _moss_model_class(moss_tts_nano) is None:
            exported = ", ".join(_moss_candidate_exports(moss_tts_nano)) or "no model class"
            return False, (
                "moss_tts_nano is installed but does not expose a usable model "
                f"class (found: {exported}). MOSS-TTS-Nano is unpinned upstream and "
                "its entry point has changed before — pull the latest "
                "github.com/OpenMOSS/MOSS-TTS-Nano and re-run `pip install -e .`, "
                "or open an issue with the version you have so the name can be added."
            )
        return True, "ready"

    @property
    def sample_rate(self) -> int:
        return 48000  # native stereo 48 kHz

    @property
    def supported_languages(self) -> list[str]:
        return [
            "zh", "en", "de", "es", "fr", "ja", "it", "he", "ko", "ru",
            "fa", "ar", "pl", "pt", "cs", "da", "sv", "hu", "el", "tr",
        ]

    def _ensure_loaded(self):
        if self._model is not None:
            return
        ok, msg = self.is_available()
        if not ok:
            raise RuntimeError(f"MOSS-TTS-Nano unavailable: {msg}")
        import moss_tts_nano  # type: ignore[import-not-found]

        model_cls = _moss_model_class(moss_tts_nano)
        if model_cls is None:  # pragma: no cover - is_available() gates this
            raise RuntimeError(
                "moss_tts_nano exposes no usable model class; see Model Catalogue → Engines"
            )
        checkpoint = os.environ.get(
            "OMNIVOICE_MOSS_TTS_MODEL", "OpenMOSS-Team/MOSS-TTS-Nano"
        )
        logger.info("Loading MOSS-TTS-Nano from %s", checkpoint)
        self._model = _retry_once_with_fresh_hf_client(
            lambda: model_cls.from_pretrained(checkpoint, trust_remote_code=True),
            "MOSS-TTS-Nano",
        )

    def generate(self, text, **kw) -> torch.Tensor:
        self._ensure_loaded()
        import numpy as np
        ref_audio = kw.get("ref_audio")
        # MOSS is strictly reference-cloning: no instruct / speaker_id / speed.
        # We downgrade gracefully — extras are silently ignored so the common
        # call-site doesn't need to know which engine it's talking to.
        wav = self._model.generate(
            text=text,
            prompt_audio_path=ref_audio,
        )
        if isinstance(wav, np.ndarray):
            wav = torch.from_numpy(wav).float()
        if wav.ndim == 1:
            wav = wav.unsqueeze(0)
        elif wav.ndim == 2 and wav.shape[0] > 1:
            # Model emits stereo; downmix to mono for the dub mixer (which
            # treats TTS output as mono per segment). Cheap mean-channel mix.
            wav = wav.mean(dim=0, keepdim=True)
        return wav


# ── KittenTTS (lightweight English "Turbo" tier) ────────────────────────────


class KittenTTSBackend(TTSBackend):
    """KittenML/KittenTTS — 25-80 MB ONNX model, 8 preset voices, English only.

    Fills the ElevenLabs-Flash niche: when the caller just needs quick English
    narration (voiceover, demo reads, short phrases) with no reference sample.
    Runs CPU-realtime on any platform — no torch, no CUDA, no mlx. The
    trade-off vs OmniVoice is obvious:
      - No voice cloning (fixed preset voices)
      - English only
      - Much faster + much smaller install

    Preset voice is chosen via `extras["voice"]` (defaults to DEFAULT_VOICE,
    "expr-voice-2-f"). Any
    `ref_audio` / `instruct` / `language` arg is ignored with a log line so
    the common call-site doesn't need to know which engine it's talking to.
    """

    id = "kittentts"
    display_name = "KittenTTS (English, 8 preset voices, CPU realtime)"
    # KittenTTS ships as an ONNX CPU graph; no CUDA/MPS path today.
    gpu_compat = ("cpu",)
    supports_cloning = False  # fixed preset voices only; ref_audio is ignored

    PRESET_VOICES = [
        "expr-voice-2-m", "expr-voice-2-f",
        "expr-voice-3-m", "expr-voice-3-f",
        "expr-voice-4-m", "expr-voice-4-f",
        "expr-voice-5-m", "expr-voice-5-f",
    ]
    DEFAULT_VOICE = "expr-voice-2-f"

    def __init__(self):
        self._model = None

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        try:
            import kittentts  # noqa: F401
            return True, "ready"
        except ImportError as e:
            return False, f"kittentts not installed: {e}"

    @property
    def sample_rate(self) -> int:
        # KittenTTS emits 24 kHz mono per its ONNX model config.
        return 24000

    @property
    def supported_languages(self) -> list[str]:
        return ["en"]

    def _ensure_loaded(self):
        if self._model is not None:
            return
        from kittentts import KittenTTS
        checkpoint = os.environ.get(
            "OMNIVOICE_KITTENTTS_MODEL", "KittenML/kitten-tts-mini-0.8"
        )
        logger.info("Loading KittenTTS from %s", checkpoint)
        # #880: the first-use load downloads ~80 MB from the HF Hub inside the
        # generate path; if the hub's shared httpx client was closed
        # mid-lifecycle, retry once with a fresh client instead of failing
        # the whole generation.
        self._model = _retry_once_with_fresh_hf_client(
            lambda: KittenTTS(checkpoint), what="KittenTTS"
        )

    # #1173: the shipped ONNX graph's BERT front-end has a hard 512-token
    # positional cap (measured against kitten-tts-mini-0.8; exceeding it
    # aborts inference inside onnxruntime with the opaque
    # "Expand node … invalid expand shape" InvalidArgument). Upstream's
    # chunker caps chunks at 400 *text characters*, but token count is the
    # length of the *phonemized* string — espeak expands digits (and other
    # verbalized tokens) massively, so 110 chars of digits already
    # phonemize to ~1150 tokens. We pre-measure every chunk with the
    # model's own tokenizer and split oversized ones at word boundaries.
    _MAX_ONNX_TOKENS = 512

    def generate(self, text: str, **kw) -> torch.Tensor:
        import numpy as np
        self._ensure_loaded()

        language = kw.get("language")
        if language and language.lower() not in {"en", "english", "auto"}:
            logger.info(
                "KittenTTS is English-only; ignoring language=%r — "
                "use OmniVoice for multilingual synthesis.",
                language,
            )

        voice = kw.get("voice") or self.DEFAULT_VOICE
        if voice not in self.PRESET_VOICES:
            logger.info(
                "KittenTTS: unknown voice %r, falling back to %r. Valid: %s",
                voice, self.DEFAULT_VOICE, self.PRESET_VOICES,
            )
            voice = self.DEFAULT_VOICE

        speed = float(kw.get("speed", 1.0))
        wav_np = self._synthesize(text, voice, speed)
        if not isinstance(wav_np, np.ndarray):
            wav_np = np.asarray(wav_np)
        wav = torch.from_numpy(wav_np).float()
        if wav.ndim == 1:
            wav = wav.unsqueeze(0)
        elif wav.ndim == 2 and wav.shape[0] > 1:
            wav = wav.mean(dim=0, keepdim=True)
        return wav

    # ── #1173 input-shape hardening ────────────────────────────────────────
    #
    # KittenTTS.generate() defaults clean_text=False, so the engine's own
    # number-verbalizing preprocessor never ran through this adapter — and
    # the openai-compat route typically has no `language`, so the app-level
    # normalize_for_tts() skips numbers→words there too. Raw digits then
    # reached espeak, whose verbalization exploded past the ONNX graph's
    # 512-token cap ("invalid expand shape" 500). Empty / unspeakable input
    # crashed differently (np.concatenate on zero chunks). Both classes are
    # handled here, in the adapter, so every route benefits.

    def _synthesize(self, text: str, voice: str, speed: float):
        """Chunk-and-generate with the engine's own cleanup + a token-budget
        preflight per chunk. Falls back to the plain upstream call if the
        kittentts internals this relies on ever change shape."""
        import numpy as np

        onnx = getattr(self._model, "model", None)
        if not (
            onnx is not None
            and callable(getattr(onnx, "generate_single_chunk", None))
            and callable(getattr(onnx, "_prepare_inputs", None))
        ):  # pragma: no cover — future upstream refactor
            return self._model.generate(text, voice=voice, speed=speed,
                                        clean_text=True)

        try:
            from kittentts.onnx_model import chunk_text
        except ImportError:  # pragma: no cover — future upstream refactor
            # Same contract as the attribute guard above: if upstream moves
            # chunk_text, degrade to the plain call instead of a 500.
            return self._model.generate(text, voice=voice, speed=speed,
                                        clean_text=True)

        cleaned = text
        preprocessor = getattr(onnx, "preprocessor", None)
        if callable(preprocessor):
            # The engine's own cleaner (numbers→words etc.) — same pass
            # upstream applies with clean_text=True.
            cleaned = preprocessor(text)

        chunks: list[str] = []
        for chunk in chunk_text(cleaned):
            chunks.extend(self._split_to_token_budget(onnx, chunk, voice, speed))
        if not chunks:
            raise TTSInputError(
                "KittenTTS: the input contains no speakable text (empty or "
                "punctuation-only after cleanup) — send at least one word."
            )
        outs = [onnx.generate_single_chunk(c, voice, speed) for c in chunks]
        return np.concatenate(outs, axis=-1)

    def _split_to_token_budget(self, onnx, chunk: str, voice: str,
                               speed: float) -> list[str]:
        """Split ``chunk`` (at word boundaries, then mid-word as a last
        resort) until each piece phonemizes to ≤ _MAX_ONNX_TOKENS tokens,
        measured with the model's own tokenizer. Never raises — an
        unmeasurable chunk is passed through unchanged."""
        chunk = chunk.strip()
        if not chunk:
            return []
        try:
            n_tokens = onnx._prepare_inputs(chunk, voice, speed)[
                "input_ids"].shape[1]
        except Exception:  # pragma: no cover — measurement is best-effort
            return [chunk]
        if n_tokens <= self._MAX_ONNX_TOKENS:
            return [chunk]
        words = chunk.split()
        if len(words) > 1:
            mid = len(words) // 2
            left, right = " ".join(words[:mid]), " ".join(words[mid:])
        else:
            # Single monster token (e.g. a 500-digit number pre-cleanup) —
            # bisect the raw string; degraded prosody beats an ONNX abort.
            mid = max(1, len(chunk) // 2)
            left, right = chunk[:mid], chunk[mid:]
            if not left or not right:  # 1-char chunk that still overflows
                return [chunk]  # pragma: no cover — impossible in practice
        return (self._split_to_token_budget(onnx, left, voice, speed)
                + self._split_to_token_budget(onnx, right, voice, speed))


# ── MLX-Audio (mac-ARM engine multiplexer) ──────────────────────────────────


# #977: Kokoro's own ALIASES table (mlx_audio.tts.models.kokoro.pipeline) only
# recognizes ISO-ish tokens ("en", "es", "fr-fr", "pt-br", …) — it has no idea
# what a full language name is. OmniVoice's `language` kwarg is normally a
# full display name from frontend/src/languages.json (e.g. "Dutch",
# "Spanish"), forwarded verbatim by the frontend and by
# `OmniVoiceBackend.generate()`. Translate the subset Kokoro actually
# supports to the ISO token its own ALIASES expects; a caller that already
# passes an ISO code (or one of Kokoro's own single-letter codes) is
# resolved unchanged by `resolve_kokoro_lang_code()` below.
_KOKORO_ISO_BY_FULL_NAME = {
    "english": "en",
    "spanish": "es",
    "french": "fr",
    "hindi": "hi",
    "italian": "it",
    "portuguese": "pt",
    "japanese": "ja",
    "chinese": "zh",
}


def resolve_kokoro_lang_code(language: str) -> str:
    """Map a full language name / ISO code to Kokoro's single-letter
    `lang_code`, against the AUTHORITATIVE table read from the installed
    mlx-audio package (never a hardcoded guess — the vendored table is the
    only source of truth and can change across mlx-audio versions).

    Raises ``ValueError`` for anything Kokoro doesn't support, naming what
    it *does* support — instead of forwarding a bogus code into Kokoro's
    `assert lang_code in LANG_CODES`, which crashes with an unreadable
    ``(lang_code, LANG_CODES)`` tuple/dict repr (#977).
    """
    from mlx_audio.tts.models.kokoro.pipeline import ALIASES, LANG_CODES

    key = language.strip().lower()
    iso = _KOKORO_ISO_BY_FULL_NAME.get(key, key)
    code = ALIASES.get(iso, iso)
    if code not in LANG_CODES:
        supported = ", ".join(sorted(name.title() for name in _KOKORO_ISO_BY_FULL_NAME))
        raise ValueError(
            f"mlx-audio's Kokoro model (mlx-community/Kokoro-82M-bf16) doesn't "
            f"support language={language!r}. Kokoro supports: {supported}. "
            f"Pick one of those, leave language as 'Auto', or switch to a "
            f"multilingual engine (e.g. OmniVoice) for other languages."
        )
    return code


class MLXAudioBackend(TTSBackend):
    """Blaizzy/mlx-audio — Apple-Silicon-only wrapper over 14+ TTS engines
    (Kokoro, CSM, Dia, Qwen3-TTS, Chatterbox, MeloTTS, OuteTTS, Spark,
    Higgs-Audio, Voxtral, LongCat-AudioDiT, KugelAudio, MingOmni, Soprano).

    Exposed as a single backend with a `model_id` selector so the Settings
    UI can surface an engine picker within one adapter. The user switches
    models by setting `OMNIVOICE_MLX_AUDIO_MODEL` or picking from the UI —
    no code change per engine. Default is Kokoro (82M, multilingual, small).

    Availability: requires mlx (Apple Silicon only). Skipped entirely on
    Linux/Windows/mac-Intel; the dep is platform-gated in pyproject.toml.
    """

    id = "mlx-audio"
    display_name = "MLX-Audio (mac-ARM, 14+ engines: Kokoro, CSM, Dia, Qwen3, …)"
    # mlx is Apple-Silicon-only; CPU is the practical fallback when the
    # mlx framework is installed but the user lacks an Apple GPU.
    gpu_compat = ("mps", "cpu")

    # A curated subset surfaced by default — the full mlx-audio roster is
    # larger but these cover the useful tiers: small multilingual (Kokoro),
    # voice-clone (CSM), voice-design (Qwen3), European (Kugel), lightweight
    # VITS (MeloTTS). Users can point at any HF repo via OMNIVOICE_MLX_AUDIO_MODEL.
    CURATED_MODELS = {
        "kokoro":      "mlx-community/Kokoro-82M-bf16",
        "csm":         "mlx-community/csm-1b-8bit",
        "qwen3-tts":   "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-4bit",
        "dia":         "mlx-community/Dia-1.6B",
        "chatterbox":  "mlx-community/Chatterbox-TTS-4bit",
        "melotts":     "mlx-community/MeloTTS-English-v3-MLX",
        "outetts":     "mlx-community/Llama-OuteTTS-1.0-1B-4bit",
    }
    DEFAULT_MODEL_KEY = "kokoro"

    def __init__(self):
        self._model = None
        self._sr = 24000  # most mlx-audio engines emit 24 kHz mono
        # Env var > persisted UI choice (#981 — Model Catalogue → Engines curated-
        # model picker) > default. Mirrors active_backend_id()'s resolution
        # order exactly so power-users can still pin a model without the UI
        # silently undoing it.
        from core import prefs
        key = prefs.resolve(
            "mlx_audio_model_id",
            env="OMNIVOICE_MLX_AUDIO_MODEL",
            default=self.DEFAULT_MODEL_KEY,
        )
        # Accept either a curated key ("kokoro") or a full HF repo id
        # ("mlx-community/Kokoro-82M-bf16") — flexibility for power users.
        self._model_id = self.CURATED_MODELS.get(key, key)

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        # #390: gate on the shared platform check FIRST, before importing the
        # package. A stray mlx-audio wheel on Linux/Windows/mac-Intel must never
        # report available (and must never advertise a usable `mps` route).
        from core.device_caps import mlx_supported
        ok, why = mlx_supported()
        if not ok:
            return False, why
        try:
            import mlx_audio  # noqa: F401
            return True, "ready"
        # OSError/RuntimeError too: in a PyInstaller bundle mlx's native
        # dylib/metallib can fail to load even when the package imports —
        # report unavailable instead of crashing the registry scan (Wave 4.4).
        except (ImportError, OSError, RuntimeError) as e:
            return False, (
                f"mlx-audio unavailable: {e}. "
                "This backend is Apple Silicon only — available on mac-ARM dev "
                "installs; not shipped on Linux/Windows/mac-Intel."
            )

    @property
    def sample_rate(self) -> int:
        return self._sr

    def model_identity(self) -> Optional[str]:
        return self._model_id

    @property
    def supported_languages(self) -> list[str]:
        # Per-model; Kokoro supports 8, Qwen3 ~4, Kugel 24. Return "multi"
        # so the language picker doesn't gate by engine — each engine
        # silently ignores languages it doesn't know.
        return ["multi"]

    @property
    def supports_cloning(self) -> bool:
        """Model-dependent — this adapter multiplexes 7+ curated models and
        only some take a reference-audio speaker prompt. `generate()` passes
        `ref_audio` through when present (~kwargs below) but silently retries
        without it on a TypeError, so an engine picked for cloning that's
        actually running Kokoro/Qwen3-TTS/etc. would clone nothing. Of the
        curated set, only CSM (`mlx-community/csm-1b-8bit`) is confirmed to
        accept a reference prompt — default False for every other model,
        curated or user-supplied, until positively confirmed."""
        return self._model_id == self.CURATED_MODELS.get("csm")

    def _ensure_loaded(self):
        if self._model is not None:
            return
        from mlx_audio.tts.utils import load_model
        logger.info("Loading mlx-audio model %s", self._model_id)
        self._model = load_model(self._model_id)

    def _is_voice_design(self) -> bool:
        """Whether the loaded model builds a voice from a text description.

        Asks the model's own config — `tts_model_type`, the exact field
        mlx-audio branches on — so this cannot drift from the library's own
        behaviour. Falls back to the model id, which carries `VoiceDesign` by
        naming convention, when a config doesn't expose the field.
        """
        kind = getattr(getattr(self._model, "config", None), "tts_model_type", None)
        if kind:
            return kind == "voice_design"
        return "voicedesign" in (self._model_id or "").lower()

    def generate(self, text: str, **kw) -> torch.Tensor:
        import numpy as np
        self._ensure_loaded()

        voice     = kw.get("voice")
        ref_audio = kw.get("ref_audio")
        ref_text  = kw.get("ref_text")
        language  = kw.get("language")
        instruct  = kw.get("instruct")
        speed     = float(kw.get("speed", 1.0))

        # mlx-audio's generate(...) returns an iterator of result objects,
        # each with a .audio attribute. Different engines accept different
        # kwargs (voice for Kokoro, ref_audio for CSM, instruct for Qwen3)
        # — we pass them all and let the engine ignore what it doesn't use.
        kwargs = {"text": text, "speed": speed}
        if voice:     kwargs["voice"] = voice
        if ref_audio: kwargs["ref_audio"] = ref_audio
        # The comment above claimed instruct was passed "for Qwen3"; it never
        # was. The curated `qwen3-tts` model IS the VoiceDesign variant, which
        # mlx-audio refuses to run without one — so the engine was unusable no
        # matter what the user typed, and the reported failure was a bare
        # 400 quoting a library message (#1405).
        if instruct:
            kwargs["instruct"] = instruct
        elif self._is_voice_design():
            raise ValueError(
                "This model builds a voice from a written description, so it "
                "needs one — for example \"a warm, low-pitched British "
                "narrator\". Pick a designed voice (those carry a "
                "description), or choose a cloning model and supply a "
                "reference clip instead."
            )
        # CSM (sesame.py) only builds its cloning context when BOTH ref_audio
        # AND ref_text are present — with ref_text missing, its context list
        # stays empty and indexing into it raises an opaque
        # "IndexError: list index out of range" deep inside mlx-audio,
        # instead of ever attempting the clone. Community-diagnosed (#1012).
        if ref_audio and ref_text: kwargs["ref_text"] = ref_text
        if language and language != "Auto":
            if self._model_id == self.CURATED_MODELS.get("kokoro"):
                # Kokoro's vendored pipeline hard-asserts `lang_code` against
                # its own single-letter table — a bogus code crashes with an
                # unreadable AssertionError instead of failing cleanly
                # (#977). Resolve against the authoritative installed table
                # instead of guessing via `language[:2]`.
                kwargs["lang_code"] = resolve_kokoro_lang_code(language)
            else:
                # `lang_code`-as-2-letter-truncation is Kokoro's own
                # convention, not mlx-audio's in general — other curated
                # models either ignore unrecognized kwargs (CSM/Dia/OuteTTS
                # accept **kwargs and drop it) or expect something else
                # entirely (Qwen3-TTS's own docstring: "lang_code: Language
                # code (auto, chinese, english, etc.)" — a full name, not a
                # 2-letter code). Kokoro's strict validation doesn't apply to
                # them, so don't reject a language that's valid for whatever
                # model is actually active.
                kwargs["lang_code"] = language[:2].lower()

        pieces = []
        try:
            for result in self._model.generate(**kwargs):
                audio = getattr(result, "audio", result)
                if hasattr(audio, "numpy"):
                    audio = audio.numpy()
                pieces.append(np.asarray(audio, dtype=np.float32))
        except TypeError:
            # Some engines don't accept lang_code / ref_audio. Retry with
            # only the universal kwargs.
            pieces = []
            for result in self._model.generate(text=text, speed=speed):
                audio = getattr(result, "audio", result)
                if hasattr(audio, "numpy"):
                    audio = audio.numpy()
                pieces.append(np.asarray(audio, dtype=np.float32))

        if not pieces:
            raise RuntimeError(f"mlx-audio ({self._model_id}) produced no audio")
        wav_np = np.concatenate(pieces, axis=-1)
        wav = torch.from_numpy(wav_np).float()
        if wav.ndim == 1:
            wav = wav.unsqueeze(0)
        elif wav.ndim == 2 and wav.shape[0] > 1:
            wav = wav.mean(dim=0, keepdim=True)
        return wav


# ── CosyVoice adapter (Alibaba FunAudioLLM, Apache-2.0) ────────────────────


class CosyVoiceBackend(TTSBackend):
    """FunAudioLLM CosyVoice — multilingual zero-shot TTS (9 langs + 18 dialects).

    Supports v1 (300M), v2 (0.5B), and v3 (0.5B, latest). Installation is
    non-trivial (git clone --recursive + SoX) so we ship as an optional
    scaffold: ``is_available()`` reports the missing install cleanly.

    Set ``OMNIVOICE_COSYVOICE_MODEL`` to the pretrained model directory path
    (e.g. ``pretrained_models/Fun-CosyVoice3-0.5B``). The directory must
    contain the CosyVoice checkpoint files.

    Install:
        git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git
        cd CosyVoice && pip install -r requirements.txt
        # Ubuntu: sudo apt-get install sox libsox-dev
        # macOS:  brew install sox
    """

    id = "cosyvoice"
    display_name = "CosyVoice 3 (9 langs, zero-shot, instruct, Apache-2.0)"
    # CosyVoice's official inference path expects CUDA; CPU works but slow.
    # MPS support not verified upstream — flagged for Phase 6 confirmation.
    gpu_compat = ("cuda", "cpu")

    # CosyVoice language tags used for cross-lingual synthesis.
    LANG_TAGS = {
        "zh": "<|zh|>", "en": "<|en|>", "ja": "<|ja|>",
        "ko": "<|ko|>", "yue": "<|yue|>", "de": "<|de|>",
        "es": "<|es|>", "fr": "<|fr|>", "it": "<|it|>",
        "ru": "<|ru|>",
    }

    def __init__(self):
        self._model = None

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        try:
            from cosyvoice.cli.cosyvoice import AutoModel  # noqa: F401
            return True, "ready"
        except ImportError:
            return False, (
                "cosyvoice package not installed. Install from "
                "https://github.com/FunAudioLLM/CosyVoice "
                "(git clone --recursive + pip install -r requirements.txt + SoX). "
                "Then set OMNIVOICE_COSYVOICE_MODEL to your model directory."
            )

    @property
    def sample_rate(self) -> int:
        if self._model is not None:
            return self._model.sample_rate
        return 24000  # v3 default

    @property
    def supported_languages(self) -> list[str]:
        return ["zh", "en", "ja", "ko", "yue", "de", "es", "fr", "it", "ru"]

    @staticmethod
    def _resolved_model_dir() -> str:
        return os.environ.get(
            "OMNIVOICE_COSYVOICE_MODEL",
            "pretrained_models/Fun-CosyVoice3-0.5B",
        )

    def model_identity(self) -> Optional[str]:
        # v1/v2/v3 all live behind the one "cosyvoice" id — the directory
        # basename is the only thing that tells the models apart.
        return os.path.basename(os.path.normpath(self._resolved_model_dir()))

    def _ensure_loaded(self):
        if self._model is not None:
            return
        ok, msg = self.is_available()
        if not ok:
            raise RuntimeError(f"CosyVoice unavailable: {msg}")
        from cosyvoice.cli.cosyvoice import AutoModel  # type: ignore[import-not-found]
        model_dir = self._resolved_model_dir()
        logger.info("Loading CosyVoice from %s", model_dir)
        self._model = AutoModel(model_dir=model_dir)

    def generate(self, text: str, **kw) -> torch.Tensor:
        import numpy as np
        self._ensure_loaded()

        ref_audio = kw.get("ref_audio")
        ref_text = kw.get("ref_text")
        instruct = kw.get("instruct")
        language = kw.get("language")

        # Pick the right inference method based on what the caller provides:
        # 1. instruct + ref_audio → inference_instruct2 (emotion/dialect/speed)
        # 2. ref_audio + ref_text → inference_zero_shot (voice cloning)
        # 3. ref_audio only → inference_cross_lingual (with lang tag)
        # 4. nothing → inference_sft (built-in speakers, v1/SFT model only)
        pieces = []
        if instruct and ref_audio:
            # Instruct mode: "用四川话说<|endofprompt|>"
            if not instruct.endswith("<|endofprompt|>"):
                instruct = f"{instruct}<|endofprompt|>"
            results = self._model.inference_instruct2(
                text, instruct, ref_audio, stream=False,
            )
        elif ref_audio and ref_text:
            results = self._model.inference_zero_shot(
                text, ref_text, ref_audio, stream=False,
            )
        elif ref_audio:
            # Cross-lingual: prefix text with language tag if available.
            lang_tag = ""
            if language:
                full_lang = language.lower()
                lang_key = full_lang[:2] if len(full_lang) > 2 else full_lang
                lang_tag = self.LANG_TAGS.get(full_lang) or self.LANG_TAGS.get(lang_key, "")
            results = self._model.inference_cross_lingual(
                f"{lang_tag}{text}", ref_audio, stream=False,
            )
        else:
            # No ref audio — try SFT with first available speaker.
            spks = self._model.list_available_spks()
            spk = spks[0] if spks else "中文女"
            results = self._model.inference_sft(text, spk, stream=False)

        for chunk in results:
            wav = chunk.get("tts_speech")
            if wav is None:
                continue
            if isinstance(wav, np.ndarray):
                wav = torch.from_numpy(wav).float()
            if not isinstance(wav, torch.Tensor):
                wav = torch.tensor(wav, dtype=torch.float32)
            pieces.append(wav)

        if not pieces:
            raise RuntimeError("CosyVoice produced no audio")
        wav = torch.cat(pieces, dim=-1)
        if wav.ndim == 1:
            wav = wav.unsqueeze(0)
        return wav


# ── IndexTTS2 adapter ───────────────────────────────────────────────────────
#
# The concrete class lives in ``backend/engines/indextts/__init__.py`` so
# that ``services.tts_backend`` itself does NOT import
# ``services.subprocess_backend`` at module load time. That separation
# breaks the import cycle:
#
#     services.subprocess_backend  ──imports──>  services.tts_backend (TTSBackend)
#     services.tts_backend         ──exports──>  TTSBackend + registry
#     engines.indextts             ──imports──>  services.subprocess_backend
#                                  ──exports──>  IndexTTS2Backend
#
# The registry below resolves IndexTTS2Backend lazily via the
# ``_LAZY_REGISTRY`` indirection — see ``get_backend_class`` and
# ``list_backends``. This was driven by Plan 02-03 (Step 3); see
# ``engines/indextts/__init__.py`` for the actual class body.


# ``IndexTTS2Backend`` is re-exported from ``backend/engines/indextts``
# via the module-level ``__getattr__`` hook at the bottom of this file
# (PEP 562). Callers can still write::
#
#     from services.tts_backend import IndexTTS2Backend
#
# and they receive the same class object as ``engines.indextts.IndexTTS2Backend``.
# The deferred lookup is what breaks the
# ``services.subprocess_backend ↔ services.tts_backend`` cycle.


# ── GPT-SoVITS adapter (most popular voice cloning, 57k★) ──────────────────


class GPTSoVITSBackend(TTSBackend):
    """RVC-Boss GPT-SoVITS — the most popular open-source voice cloning system.

    57k GitHub stars, RTF 0.014 (10× faster than VoxCPM2). Supports zero-shot
    and few-shot voice cloning with excellent naturalness. Chinese, English,
    Japanese, Cantonese, Korean.

    GPT-SoVITS runs as a standalone API server (api_v2.py) because it doesn't
    ship a clean pip-installable package. This adapter connects to that server
    over HTTP. Start the server before using this backend:

        cd GPT-SoVITS
        python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml

    Set ``OMNIVOICE_GPTSOVITS_URL`` to the server URL (default: http://127.0.0.1:9880).

    License: MIT — fully permissive, commercial use OK.
    """

    id = "gpt-sovits"
    display_name = "GPT-SoVITS (5 langs, zero-shot, RTF 0.014, MIT)"
    # Server-side; whichever device GPT-SoVITS itself uses (CUDA preferred).
    gpu_compat = ("cuda", "cpu")

    def __init__(self):
        self._url = os.environ.get("OMNIVOICE_GPTSOVITS_URL", "http://127.0.0.1:9880")

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        # GPT-SoVITS runs as an external API server — check if it's reachable.
        from services.outbound_http import open_trusted_endpoint
        url = os.environ.get("OMNIVOICE_GPTSOVITS_URL", "http://127.0.0.1:9880")
        try:
            with open_trusted_endpoint(url, method="GET", timeout=2):
                pass
            return True, "ready (server reachable)"
        except Exception:
            return False, (
                f"GPT-SoVITS server not reachable at {url}. "
                "Start it with: python api_v2.py -a 127.0.0.1 -p 9880 "
                "-c GPT_SoVITS/configs/tts_infer.yaml"
            )

    @property
    def sample_rate(self) -> int:
        return 32000  # GPT-SoVITS outputs 32 kHz

    @property
    def supported_languages(self) -> list[str]:
        return ["zh", "en", "ja", "yue", "ko"]

    def generate(self, text: str, **kw) -> torch.Tensor:
        import urllib.parse
        from services.outbound_http import open_trusted_endpoint

        ref_audio = kw.get("ref_audio")
        ref_text = kw.get("ref_text", "")
        language = kw.get("language", "en")

        # Map language codes to GPT-SoVITS format
        lang_map = {
            "zh": "zh", "en": "en", "ja": "ja", "yue": "yue", "ko": "ko",
            "chinese": "zh", "english": "en", "japanese": "ja",
        }
        text_lang = lang_map.get(language.lower() if language else "en", "en")

        # Build request params
        params = {
            "text": text,
            "text_language": text_lang,
        }
        if ref_audio:
            params["refer_wav_path"] = ref_audio
            params["prompt_text"] = ref_text or ""
            params["prompt_language"] = text_lang

        speed = kw.get("speed", 1.0)
        if speed != 1.0:
            params["speed_factor"] = str(speed)

        query = urllib.parse.urlencode(params)
        try:
            with open_trusted_endpoint(
                self._url, method="POST", query=query, timeout=120
            ) as resp:
                audio_bytes = resp.read()
        except Exception as e:
            raise RuntimeError(
                f"GPT-SoVITS API call failed: {e}. "
                f"Ensure the server is running at {self._url}"
            )

        # Parse the WAV response
        import io
        import torchaudio
        wav, sr = torchaudio.load(io.BytesIO(audio_bytes))
        if sr != self.sample_rate:
            wav = torchaudio.functional.resample(wav, sr, self.sample_rate)
        if wav.ndim == 1:
            wav = wav.unsqueeze(0)
        elif wav.ndim == 2 and wav.shape[0] > 1:
            wav = wav.mean(dim=0, keepdim=True)
        return wav


# ── Sherpa-ONNX adapter (universal ONNX runtime, WASM-ready) ───────────────


class SherpaOnnxBackend(TTSBackend):
    """k2-fsa/sherpa-onnx — unified C++ ONNX runtime for TTS (and ASR).

    Sherpa-ONNX wraps 20+ TTS engines (VITS, MeloTTS, Piper, Kokoro, Matcha,
    CosyVoice, etc.) under a single runtime with pre-built wheels for:
      • Linux / Windows / macOS (x86 + ARM)
      • Android / iOS
      • WebAssembly (browser)

    This is the bridge to browser-based VoiceStudio: the same engine runs natively
    on desktop and compiles to WASM for the web UI.

    Install: pip install sherpa-onnx
    Models: download from https://github.com/k2-fsa/sherpa-onnx/releases

    Set ``OMNIVOICE_SHERPA_MODEL`` to the model directory path.
    """

    id = "sherpa-onnx"
    display_name = "Sherpa-ONNX (20+ engines, WASM-ready, universal runtime)"
    # Sherpa-ONNX uses the onnxruntime providers — CPU is the universal
    # baseline; CUDA provider is available on Linux/Windows installs.
    gpu_compat = ("cuda", "cpu")
    supports_cloning = False  # VITS speaker-id only; no ref_audio support

    def __init__(self):
        self._tts = None
        self._model_dir = os.environ.get("OMNIVOICE_SHERPA_MODEL", "")

    def model_identity(self) -> Optional[str]:
        model_dir = (self._model_dir or "").strip()
        return os.path.basename(os.path.normpath(model_dir)) if model_dir else None

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        try:
            import sherpa_onnx  # noqa: F401
        except ImportError as e:
            return False, (
                f"sherpa-onnx not installed: {e}. "
                "Install with: pip install sherpa-onnx. "
                "Download models from https://github.com/k2-fsa/sherpa-onnx/releases"
            )
        # #919: sherpa-onnx ships no bundled default model — it can only
        # synthesize once OMNIVOICE_SHERPA_MODEL points at a downloaded model
        # directory. Gate on it here (like the other path-configured opt-in
        # engines: Confucius4/dots/MOSS) so the picker marks it unavailable-
        # with-a-reason instead of letting a user select it, generate, and hit
        # a config error that used to be mislabeled as out-of-memory.
        model_dir = os.environ.get("OMNIVOICE_SHERPA_MODEL", "").strip()
        if not model_dir:
            return False, (
                "OMNIVOICE_SHERPA_MODEL not set. Point it to a sherpa-onnx TTS "
                "model directory (containing model.onnx + tokens.txt), then "
                "restart VoiceStudio. Download models from "
                "https://github.com/k2-fsa/sherpa-onnx/releases"
            )
        if not os.path.isfile(os.path.join(model_dir, "model.onnx")):
            return False, (
                f"No model.onnx in OMNIVOICE_SHERPA_MODEL ({model_dir}). Point "
                "it at a sherpa-onnx TTS model directory containing model.onnx "
                "+ tokens.txt. Download models from "
                "https://github.com/k2-fsa/sherpa-onnx/releases"
            )
        return True, "ready"

    @property
    def sample_rate(self) -> int:
        if self._tts is not None:
            return self._tts.sample_rate
        return 22050  # VITS default

    @property
    def supported_languages(self) -> list[str]:
        return ["multi"]  # depends on loaded model

    def _ensure_loaded(self):
        if self._tts is not None:
            return
        ok, msg = self.is_available()
        if not ok:
            raise RuntimeError(f"Sherpa-ONNX unavailable: {msg}")
        import sherpa_onnx

        if not self._model_dir:
            raise RuntimeError(
                "OMNIVOICE_SHERPA_MODEL not set. Point it to a sherpa-onnx "
                "TTS model directory (containing model.onnx + tokens.txt)."
            )

        # Auto-detect model type from directory contents
        model_onnx = os.path.join(self._model_dir, "model.onnx")
        tokens = os.path.join(self._model_dir, "tokens.txt")

        if not os.path.isfile(model_onnx):
            raise RuntimeError(
                f"No model.onnx found in {self._model_dir}. "
                "Download a model from https://github.com/k2-fsa/sherpa-onnx/releases"
            )

        logger.info("Loading sherpa-onnx TTS from %s", self._model_dir)
        tts_config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=model_onnx,
                    tokens=tokens,
                ),
            ),
        )
        self._tts = sherpa_onnx.OfflineTts(tts_config)

    def generate(self, text: str, **kw) -> torch.Tensor:
        import numpy as np
        self._ensure_loaded()

        speed = float(kw.get("speed", 1.0))
        # sherpa-onnx speaker ID (for multi-speaker VITS models)
        sid = int(kw.get("speaker_id", 0))

        audio = self._tts.generate(text, sid=sid, speed=speed)
        wav = np.array(audio.samples, dtype=np.float32)
        wav = torch.from_numpy(wav).unsqueeze(0)  # (1, n_samples)
        return wav


# ── Registry ────────────────────────────────────────────────────────────────


# ── Lazy registry entry for subprocess-isolated backends ──────────────────
#
# Backends that live in their own module (to avoid an import cycle with
# ``services.subprocess_backend``) register here as ``(module_path,
# attribute_name)``. ``_REGISTRY`` resolves the entry on first access via
# the descriptor below.

_LAZY_REGISTRY: dict[str, tuple[str, str]] = {
    "indextts2": ("engines.indextts", "IndexTTS2Backend"),
    # Phase 4 Plan 04-01 (GGUF-03): hardware-adaptive GGUF runtime wrapper.
    # Lazy so the import of services.tts_backend doesn't pull
    # huggingface_hub + soundfile transitively when callers only need
    # the in-process OmniVoice. Resolves on first attribute / item access.
    "omnivoice-gguf": ("engines.omnivoice_gguf", "OmniVoiceGGUFBackend"),
    # Phase 3 Plan 03-01 (TTS-01): Supertonic-3 lives in its own engine
    # package for the same import-cycle reason as IndexTTS2 (its backend
    # module imports services.subprocess_backend which in turn imports
    # this module for TTSBackend). The class is resolved on first
    # attribute access via the LazyRegistry below.
    "supertonic3": ("engines.supertonic3", "Supertonic3Backend"),
    # Issue #498: MOSS-TTS-v1.5 (8B) and dots.tts (2B) — both opt-in,
    # subprocess-isolated with their own venv because each pins a
    # transformers version that conflicts with the parent's >=5.3
    # (MOSS == 5.0.0, dots.tts == 4.57.0). Same dedicated-venv pattern as
    # IndexTTS2. Lazy for the same import-cycle reason as the entries above.
    "moss-tts-v15": ("engines.moss_tts_v15", "MossTTSV15Backend"),
    "dots-tts": ("engines.dots_tts", "DotsTTSBackend"),
    # The resident OmniVoice model in a crash-isolated sidecar (#730/#1190):
    # same model and quality as the in-process "omnivoice" engine, but a wedged
    # generate can be hard-killed to reclaim VRAM/device. Opt-in (the in-process
    # engine stays the default). Unlike the entries above it runs under the
    # parent interpreter (crash isolation, not dependency isolation).
    "omnivoice-subprocess": ("engines.omnivoice_subprocess", "OmniVoiceSubprocessBackend"),
    # Issue #1306: Kyutai PocketTTS, CPU-only, low-latency TTS hired for the
    # "fastest CPU render / lowest latency" job. Opt-in, subprocess-isolated
    # under the parent interpreter (crash isolation, not dependency isolation,
    # same as omnivoice-subprocess: pocket-tts deps sit at the parent's pins).
    "pockettts": ("engines.pockettts", "PocketTTSBackend"),
    # Issue #590: Confucius4-TTS (netease-youdao) — LLM-based, 14-language
    # cross-lingual zero-shot cloning, Apache-2.0. Opt-in + subprocess-isolated
    # (own Python 3.10 venv) like the entries above. Validated end-to-end
    # 2026-07-02 (CPU, Apple Silicon; 22.05 kHz output). Gated behind
    # OMNIVOICE_CONFUCIUS4_TTS_DIR so it's inert until enabled.
    "confucius4-tts": ("engines.confucius4", "Confucius4Backend"),
}


class _LazyRegistry(dict):
    """A dict that resolves selected keys via a deferred import.

    Keys in ``_LAZY_REGISTRY`` are not present in ``self`` until first
    access; ``__getitem__`` / ``__contains__`` / iteration all import
    them on demand. Everything else behaves like a normal dict — the
    registry-sandbox fixture in
    ``tests/backend/services/test_tts_backend_registry.py`` still gets
    snapshot semantics because once a lazy key is resolved it's stored
    in self exactly like a non-lazy key.
    """

    def __contains__(self, key) -> bool:  # noqa: D401
        return dict.__contains__(self, key) or key in _LAZY_REGISTRY

    def __getitem__(self, key):
        if dict.__contains__(self, key):
            return dict.__getitem__(self, key)
        if key in _LAZY_REGISTRY:
            mod_path, attr = _LAZY_REGISTRY[key]
            import importlib

            cls = getattr(importlib.import_module(mod_path), attr)
            self[key] = cls
            return cls
        raise KeyError(key)

    def __iter__(self):
        # Yield resolved keys first, then any lazy keys that haven't been
        # resolved yet. Resolving inside __iter__ would trigger a side
        # effect on every list_backends() call — we keep iteration light
        # and let the caller's __getitem__ trigger the import.
        seen: set[str] = set()
        # Snapshot the live keys before yielding. A concurrent thread's lazy
        # __getitem__ inserts into self (self[key] = cls), and list_backends()
        # runs in a FastAPI threadpool — so holding a *live* dict iterator open
        # across the per-engine is_available() probes would raise
        # "dictionary changed size during iteration". list() consumes the
        # iterator atomically under the GIL, closing that window.
        for k in list(dict.__iter__(self)):
            seen.add(k)
            yield k
        for k in _LAZY_REGISTRY:
            if k not in seen:
                yield k

    def items(self):
        for k in self:
            yield k, self[k]

    def keys(self):
        return list(iter(self))

    def values(self):
        return [self[k] for k in self]


_REGISTRY: dict[str, type[TTSBackend]] = _LazyRegistry({
    "omnivoice":     OmniVoiceBackend,
    "cosyvoice":     CosyVoiceBackend,
    "kittentts":     KittenTTSBackend,
    "mlx-audio":     MLXAudioBackend,
    "voxcpm2":       VoxCPM2Backend,
    "moss-tts-nano": MossTTSNanoBackend,
    # "indextts2": resolved lazily via _LAZY_REGISTRY -> engines.indextts
    "gpt-sovits":    GPTSoVITSBackend,
    "sherpa-onnx":   SherpaOnnxBackend,
})


# ── ENGINE-06 last-error cache ─────────────────────────────────────────────
#
# Populated by `list_backends()` whenever a backend's `is_available()`
# returns ok=False or raises an exception. Cleared per-id when the same
# backend reports ok=True. Surfaced via the `last_error` field on each
# registry entry so the Compat Matrix UI (Plan 02-04) can show the most
# recent failure even between calls — and prove which engine is the source
# of a hung Settings panel.
_LAST_ERRORS: dict[str, str] = {}



# Short install hints surfaced as tooltips on the Model Catalogue → Engines UI.
# Helps users understand what pip package to install and where.
_INSTALL_HINTS: dict[str, str] = {
    "omnivoice":     "pip install omnivoice  (bundled — no extra install needed)",
    "omnivoice-subprocess": "No extra install; uses the host OmniVoice install. Opt in with OMNIVOICE_TTS_BACKEND=omnivoice-subprocess (same model in a killable sidecar, for unattended reliability).",
    "cosyvoice":     "git clone --recursive FunAudioLLM/CosyVoice + pip install -r requirements.txt + SoX",
    "kittentts":     "pip install kittentts  (ONNX, CPU-only, ~80 MB)",
    "mlx-audio":     "pip install mlx-audio  (Apple Silicon only)",
    "voxcpm2":       'pip install "voxcpm>=2.0.3"  (floor: 2.0.3 fixed Apple-Silicon audio quality; CPU/MPS supported, CUDA recommended for speed)',
    "moss-tts-nano": "git clone OpenMOSS/MOSS-TTS-Nano && pip install -e .  (not on PyPI)",
    "indextts2":     "git clone --branch indextts-2.5 https://github.com/index-tts/index-tts.git && cd index-tts && uv venv .venv && uv pip install --python .venv/bin/python -e .  (Windows: .venv\\Scripts\\python.exe; NOT uv sync --all-extras)",
    "gpt-sovits":    "External API server — start api_v2.py on port 9880",
    "sherpa-onnx":   "pip install sherpa-onnx  (universal ONNX runtime, WASM-ready)",
    "omnivoice-gguf":"Bundled — runs the C++ omnivoice-tts binary in bin/. Quants download lazily from Serveurperso/OmniVoice-GGUF on first generate.",
    "supertonic3":   "uv sync --extra supertonic  (CPU-only ONNX, 31 langs, ~400 MB model on first use; OpenRAIL-M model license)",
    "pockettts":     "uv sync --extra pockettts  (Kyutai, CPU-only, ~100 MB model on first use; MIT code + CC-BY-4.0 weights; HF-gated, review terms and set HF_TOKEN)",
    "moss-tts-v15":  "git clone OpenMOSS/MOSS-TTS + set OMNIVOICE_MOSS_TTS_V15_DIR  (own venv, transformers==5.0; 8B, ~16 GB weights; CUDA/CPU, no MPS; Apache-2.0)",
    "dots-tts":      "git clone rednote-hilab/dots.tts + set OMNIVOICE_DOTS_TTS_DIR  (own venv, transformers==4.57; 2B, ~9 GB weights; CUDA/CPU, Linux/macOS only — no Windows; Apache-2.0)",
    "confucius4-tts":"git clone netease-youdao/Confucius4-TTS + set OMNIVOICE_CONFUCIUS4_TTS_DIR  (own Python 3.10 venv; 14-lang cross-lingual zero-shot clone; ~5 GB weights auto-download; CUDA/CPU, no MPS; Apache-2.0)",
}


# Copy-paste-ready setup line for opt-in engines gated behind a filesystem-path
# env var (issue #498 / #590). The install_hint tells users a var exists; this
# is the *exact* `export VAR=...` line to run, so they don't have to reconstruct
# it from the docs. Surfaced verbatim in the Compat Matrix's "Why unavailable?"
# disclosure with a Copy button. Single-sourced here so it can't drift from the
# var each engine's is_available() actually reads. bash/zsh form (the dominant
# clone-and-run workflow for these engines; dots.tts is *nix-only anyway).
_SETUP_SNIPPETS: dict[str, str] = {
    "indextts2":      "export OMNIVOICE_INDEXTTS_DIR=/path/to/index-tts",
    "moss-tts-v15":   "export OMNIVOICE_MOSS_TTS_V15_DIR=/path/to/MOSS-TTS",
    "dots-tts":       "export OMNIVOICE_DOTS_TTS_DIR=/path/to/dots.tts",
    "confucius4-tts": "export OMNIVOICE_CONFUCIUS4_TTS_DIR=/path/to/Confucius4-TTS",
    # #919: sherpa-onnx gates on a downloaded model dir (model.onnx + tokens.txt).
    "sherpa-onnx":    "export OMNIVOICE_SHERPA_MODEL=/path/to/sherpa-onnx-model",
}


# Short, readable labels for mlx-audio's curated models (#981) — surfaced in
# the Model Catalogue → Engines model picker so users see more than a bare key.
# Single-sourced here rather than on MLXAudioBackend.CURATED_MODELS itself so
# the class dict stays a plain key → repo-id map (what __init__ needs).
_MLX_AUDIO_MODEL_LABELS: dict[str, str] = {
    "kokoro":     "Kokoro (default, fast)",
    "csm":        "CSM (voice cloning)",
    "qwen3-tts":  "Qwen3-TTS (voice design)",
    "dia":        "Dia",
    "chatterbox": "Chatterbox",
    "melotts":    "MeloTTS (lightweight)",
    "outetts":    "OuteTTS",
}


def _sidecar_installable_ids() -> frozenset[str]:
    """Engine ids with a one-click sidecar installer. Deferred import — the
    installer module is tiny, but keeping the import inside the function
    means a broken/absent installer can never take the engine picker down.

    All current sidecar SPECS are TTS engines, so only this registry carries
    ``one_click_install``; the first non-TTS sidecar engine will need the same
    field plumbed into asr_backend/llm_backend.list_backends and the Install
    button into their matrix rows.
    """
    try:
        from services.sidecar_install import SPECS
        return frozenset(SPECS)
    except Exception:  # pragma: no cover — defensive only
        return frozenset()


def list_backends() -> list[dict]:
    """Enumerate every registered backend with its availability state.

    Per-entry shape (ENGINE-05 + ENGINE-06):

        {
          "id":             str,
          "display_name":   str,
          "available":      bool,
          "reason":         Optional[str],          # message when not available
          "hint":           Optional[str],          # advice when available-but-has-advice
                                                    #   (is_available "ready — <advice>" convention;
                                                    #   e.g. VoxCPM2's >=2.0.3 upgrade hint)
          "install_hint":   Optional[str],
          "setup_snippet":  Optional[str],          # exact `export VAR=...` for path-gated opt-in engines
          "one_click_install": bool,                # services.sidecar_install can provision it in-app
          "last_error":     Optional[str],          # cached most-recent failure
          "isolation_mode": "in-process" | "subprocess",
          "gpu_compat":     list[str],              # subset of {cuda, rocm, mps, xpu, cpu}
          "supports_cloning": Optional[bool],       # True/False from the class attr; None when
                                                    #   model-dependent (property, e.g. mlx-audio)
          "effective_device": str,                  # device this engine uses on THIS host
          "routing_status": "accelerated" | "cpu_fallback" | "cpu_only" | "unavailable",
          "routing_reason": Optional[str],          # scrubbed; null when none
        }

    Guarantees (ENGINE-05): a backend whose `is_available()` raises does
    NOT prevent the list from returning. The exception is captured into
    the `reason`/`last_error` fields for that one entry and every other
    backend is still listed normally.

    Security (Plan 02-04 / T-02-12): any HF-shaped token substring in
    ``reason`` or ``last_error`` is redacted before the entry is
    serialized — :func:`_mask_hf_tokens`. The frontend can render these
    fields verbatim without leaking credentials.
    """
    # Detect subprocess-isolated backends via a duck-typed marker rather
    # than `issubclass(cls, SubprocessBackend)`. Test fixtures (e.g. the
    # token_resolver suite) purge `sys.modules["services"]` between tests
    # for DB isolation, which produces a re-imported SubprocessBackend
    # class object that no longer == the one this test's subclasses closed
    # over. The marker attribute is set on SubprocessBackend itself, so
    # subclasses inherit it through any re-import path.
    # Routing is host-aware but the host caps are constant per process, so probe
    # ONCE here and resolve each engine's effective device against the same caps.
    from core.device_caps import detect_host_caps
    from services.engine_disk_usage import disk_summary_for
    from services.engine_evidence import snapshot as execution_snapshot
    from services.engine_routing import routing_fields
    caps = detect_host_caps()
    installable = _sidecar_installable_ids()

    out: list[dict] = []
    for bid, cls in _REGISTRY.items():
        cls = _effective_backend_class(bid, cls, caps.family)
        try:
            ok, msg = cls.is_available()
        except Exception:
            ok = False
            msg = "Availability probe failed; check the backend log."
            logger.warning("list_backends: availability probe failed for registered backend %s", bid)
        if ok:
            _LAST_ERRORS.pop(bid, None)
        else:
            # Mask any HF token inside the failure message BEFORE it lands
            # in the in-memory cache — otherwise a later list_backends()
            # call would re-surface the unmasked string.
            _LAST_ERRORS[bid] = _mask_hf_tokens(msg)
        # ENGINE-06 isolation_mode: duck-typed marker for SubprocessBackend
        # subclasses (see services.subprocess_backend.SubprocessBackend).
        if getattr(cls, "_is_subprocess_isolated", False):
            isolation = "subprocess"
        else:
            isolation = "in-process"
        gpu_compat = getattr(cls, "gpu_compat", ("cpu",))
        # Cloning capability: same descriptor guard as
        # cloning_capable_engine_ids() — a class-level getattr on a *property*
        # (mlx-audio: capability depends on the picked model) returns the
        # descriptor, not a bool, so report None (= model-dependent) there
        # instead of an always-truthy false positive.
        _clone = getattr(cls, "supports_cloning", True)
        routing = routing_fields(gpu_compat, caps, getattr(cls, "min_vram_gb", 0.0))
        loaded_instance = None
        if _active_instance_id == bid:
            loaded_instance = _active_instance
        if loaded_instance is None:
            loaded_instance = _ENGINE_INSTANCES.get(cls)
        out.append({
            "id": bid,
            "display_name": cls.display_name,
            "available": ok,
            "reason": None if ok else _mask_hf_tokens(msg),
            # Available-but-has-advice (e.g. VoxCPM2's ">=2.0.3 recommended"
            # upgrade hint). None unless ok and the message carries advice.
            "hint": _available_hint(msg) if ok else None,
            "supports_cloning": _clone if isinstance(_clone, bool) else None,
            # Graded-emotion capability (#1208) — drives the Audiobook emotion
            # panel's engine gate. Class attr, defaults False.
            "supports_emotion": bool(getattr(cls, "supports_emotion", False)),
            "install_hint": _INSTALL_HINTS.get(bid),
            # Exact `export VAR=...` line for path-gated opt-in engines, or None.
            "setup_snippet": _SETUP_SNIPPETS.get(bid),
            # True when services.sidecar_install can provision this engine
            # in-app (Settings renders an Install button instead of leading
            # with the manual setup snippet).
            "one_click_install": bid in installable,
            "disk_usage": disk_summary_for(bid),
            "last_error": _LAST_ERRORS.get(bid),
            "isolation_mode": isolation,
            "gpu_compat": list(gpu_compat),
            # effective_device / routing_status / routing_reason (scrubbed):
            "min_vram_gb": getattr(cls, "min_vram_gb", 0.0) or None,
            # effective_device / routing_status / routing_reason (scrubbed);
            # the reason now also carries the under-provisioned-GPU caveat.
            **routing,
            "execution_evidence": execution_snapshot(
                engine_id=bid,
                engine_cls=cls,
                instance=loaded_instance,
                routing=routing,
                caps=caps,
            ),
        })
        # #981: mlx-audio multiplexes 7+ curated models behind one backend id
        # — surface the roster + the currently-active pick so Settings can
        # render a model picker instead of always defaulting to Kokoro.
        # mlx-audio ONLY; every other backend loads a single fixed model.
        if bid == "mlx-audio":
            from core import prefs
            active_model = prefs.resolve(
                "mlx_audio_model_id",
                env="OMNIVOICE_MLX_AUDIO_MODEL",
                default=cls.DEFAULT_MODEL_KEY,
            )
            out[-1]["curated_models"] = [
                {"key": key, "label": _MLX_AUDIO_MODEL_LABELS.get(key, key), "repo_id": repo_id}
                for key, repo_id in cls.CURATED_MODELS.items()
            ]
            out[-1]["active_model_id"] = active_model
    return out


def _effective_backend_class(
    backend_id: str,
    backend_cls: type[TTSBackend],
    host_family: str | None = None,
) -> type[TTSBackend]:
    """Resolve host-specific containment without changing the configured id."""
    if backend_id != "omnivoice":
        return backend_cls
    if host_family is None:
        from core.device_caps import detect_host_caps

        host_family = detect_host_caps().family
    if host_family != "mps":
        return backend_cls
    from engines.omnivoice_subprocess import OmniVoiceMPSSubprocessBackend

    return OmniVoiceMPSSubprocessBackend


def get_backend_class(backend_id: str) -> type[TTSBackend]:
    if backend_id not in _REGISTRY:
        raise ValueError(f"Unknown TTS backend: {backend_id!r}. Known: {list(_REGISTRY)}")
    return _effective_backend_class(backend_id, _REGISTRY[backend_id])


def cloning_capable_engine_ids() -> list[str]:
    """Engine ids that support reference-audio voice cloning — used to build
    an actionable error when the active engine can't (dub/batch gating).

    Iterates the same registry ``list_backends()`` uses, via ``.items()`` so
    lazy entries resolve through ``_LazyRegistry``'s snapshot-safe iteration
    (see ``_LazyRegistry.__iter__``) exactly like every other registry scan
    in this module.

    A class-level ``getattr`` on a *property* returns the descriptor object
    itself (always truthy) rather than its computed value — so a
    model-dependent adapter like ``MLXAudioBackend`` (only some of its 7+
    curated models can clone) would always show up here regardless of which
    model is actually configured. Excluded rather than falsely recommended:
    ``isinstance(..., bool)`` is False for a descriptor, True for a plain
    class attribute.
    """
    return [
        bid for bid, cls in _REGISTRY.items()
        if isinstance((v := getattr(cls, "supports_cloning", True)), bool) and v
    ]


def active_routing() -> dict | None:
    """Routing verdict for the currently-active TTS engine, or ``None`` if it
    can't be determined (no engine / probe failure).

    Derived from :func:`list_backends` so the verdict is byte-identical to what
    the Engine Compatibility Matrix shows for the same engine. Consumed by
    ``/setup/preflight`` and ``/system/diagnose`` to surface a GPU-routing
    verdict for the active engine (no silent CPU fallback). Never raises.
    """
    try:
        active = active_backend_id()
        for b in list_backends():
            if b.get("id") == active:
                return {
                    "engine": active,
                    "available": b.get("available"),
                    "effective_device": b.get("effective_device"),
                    "routing_status": b.get("routing_status"),
                    "routing_reason": b.get("routing_reason"),
                }
    except Exception:
        # Routing is advisory — never let a probe/registry hiccup break the
        # caller (preflight/diagnose must stay responsive — local-first).
        return None
    return None


def gpu_routing_verdict() -> dict:
    """The GpuRouting payload (see api.schemas.GpuRouting) for the active TTS
    engine + this host's compute summary. Used by ``/setup/preflight`` and
    ``/system/diagnose``. Never raises — degrades to a host-only verdict with
    ``routing_status:"none"`` if the active engine can't be resolved."""
    from core.device_caps import detect_host_caps
    try:
        caps = detect_host_caps()
        host_family, vram_gb = caps.family, round(caps.vram_gb, 1)
    except Exception:
        host_family, vram_gb = "cpu", 0.0
    r = active_routing()
    if not r:
        return {
            "engine": None, "effective_device": None,
            "routing_status": "none", "routing_reason": None,
            "host_family": host_family, "vram_gb": vram_gb,
        }
    return {
        "engine": r.get("engine"),
        "effective_device": r.get("effective_device"),
        "routing_status": r.get("routing_status"),
        "routing_reason": r.get("routing_reason"),
        "host_family": host_family, "vram_gb": vram_gb,
    }


def active_backend_id() -> str:
    # Env var > persisted UI choice > default. Env wins so power-users can
    # pin a backend without the Settings picker silently undoing it.
    from core import prefs
    return prefs.resolve("tts_backend", env="OMNIVOICE_TTS_BACKEND", default="omnivoice")


# Cached active backend instance + its id (MM2-01). Without this, every call
# built a fresh instance and the previous engine's VRAM/sidecar leaked until GC
# — measurable when switching engines on an 8 GB MPS Mac (root cause behind the
# #278 comment thread). We now keep one instance per configured backend id and
# call the outgoing engine's unload() before switching.
_active_instance: "TTSBackend | None" = None
_active_instance_id: "str | None" = None
# mlx-audio multiplexes 7+ curated models behind one backend id — a model-only
# switch (same "mlx-audio" id, different curated model) must also invalidate
# the cache, or picking a different model in Settings has no effect until the
# app restarts (#981). Only meaningful when _active_instance_id == "mlx-audio".
_active_mlx_model_key: "str | None" = None


def reset_active_backend() -> None:
    """Unload + clear the cached active backend. For app shutdown and tests.
    Idempotent and best-effort — a raising unload() never propagates."""
    global _active_instance, _active_instance_id, _active_mlx_model_key
    inst = _active_instance
    _active_instance = None
    _active_instance_id = None
    _active_mlx_model_key = None
    if inst is not None:
        try:
            inst.unload()
        except Exception as exc:  # noqa: BLE001
            logger.warning("reset_active_backend: %s.unload() raised: %s",
                           type(inst).__name__, exc)


def get_active_tts_backend(*, model=None) -> TTSBackend:
    """Return the configured backend, reusing a cached instance and releasing
    the previous engine on a switch (MM2-01).

    Rule: the cache tracks the configured backend id. Switching id always
    unload()s the outgoing instance first. For OmniVoice with an explicit
    ``model=`` (caller already holds a loaded model), we return a fresh view
    over the shared singleton rather than caching it — but a switch *away from*
    a different engine still triggers that engine's unload().

    For mlx-audio specifically, the backend id alone doesn't capture *which*
    curated model is loaded (#981) — so we also track the resolved model key
    and treat a model-only change as a switch, reusing the exact same
    unload-and-reconstruct path as an id switch.
    """
    global _active_instance, _active_instance_id, _active_mlx_model_key
    bid = active_backend_id()

    mlx_model_key = None
    if bid == "mlx-audio":
        from core import prefs
        mlx_model_key = prefs.resolve(
            "mlx_audio_model_id",
            env="OMNIVOICE_MLX_AUDIO_MODEL",
            default=MLXAudioBackend.DEFAULT_MODEL_KEY,
        )

    # Switching engines (or, for mlx-audio, switching curated models): release
    # the outgoing one first. Best-effort so a bad unload() can never block
    # the switch.
    switching = _active_instance is not None and (
        _active_instance_id != bid
        or (bid == "mlx-audio" and mlx_model_key != _active_mlx_model_key)
    )
    if switching:
        try:
            _active_instance.unload()
        except Exception as exc:  # noqa: BLE001
            logger.warning("engine switch: %s.unload() raised: %s",
                           type(_active_instance).__name__, exc)
        _active_instance = None
        _active_instance_id = None
        _active_mlx_model_key = None

    cls = get_backend_class(bid)
    if cls is OmniVoiceBackend and model is not None:
        # Per-call view over the already-loaded shared singleton; don't cache it
        # (the model lifecycle is owned by model_manager), but the switch above
        # already released any *different* previous engine.
        return OmniVoiceBackend(model=model)

    if _active_instance is None or _active_instance_id != bid:
        _active_instance = OmniVoiceBackend(model=model) if cls is OmniVoiceBackend else cls()
        _active_instance_id = bid
        _active_mlx_model_key = mlx_model_key
    return _active_instance


# ── Shared engine-instance cache ──────────────────────────────────────────
#
# One instance per engine class for the lifetime of the process. It lived in
# ``api/routers/engines.py`` until the worker needed it too: a worker executing
# a remote assignment must reuse the same warm engine the local generate path
# uses, and importing an API router from ``worker/`` would invert the layering
# (``worker/executor.py`` is a translator over ``services/``). The router now
# aliases this dict, so every consumer that already reaches for
# ``engines._ENGINE_INSTANCES`` — engine_memory's eviction, model_lifecycle's
# inventory and unload — keeps operating on the one true cache.
#
# Keyed by CLASS, not by engine id, because registry-sandbox tests rebind ids
# transiently; ``get_engine_instance_for`` resolves an id through
# ``get_backend_class`` so callers can key by id without the cache doing so.
_ENGINE_INSTANCES: dict[type, object] = {}
_ENGINE_CACHE_LOCK = threading.RLock()

# Last use, on the monotonic clock — a wall clock would make an NTP step or a
# laptop resume look like a ten-minute idle and unload a model mid-job.
_ENGINE_LAST_USED: dict[type, float] = {}

# How many jobs are inside an engine right now. A long generation touches the
# cache once at the start, so on elapsed time alone a 40-minute dub looks
# exactly like an abandoned model — and the sweep would unload it out from
# under the thread rendering it.
_ENGINE_IN_USE: dict[type, int] = {}

def _idle_seconds_from_env(name: str, default: float, *, floor: float) -> float:
    """Read a tunable idle duration, ignoring anything unusable.

    These exist so the ten-minute behaviour can be observed in a minute during
    testing instead of a coffee break. A bad value must not change behaviour
    silently, and must never reach zero: a zero threshold unloads an engine the
    instant it goes idle, which on a busy machine means reloading it for every
    request.
    """
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Ignoring %s=%r: not a number.", name, raw)
        return default
    if value < floor:
        logger.warning("Ignoring %s=%s: below the %ss floor.", name, value, floor)
        return default
    return value


#: How long an engine may sit unused before its weights are handed back.
#: Override with OMNIVOICE_ENGINE_IDLE_UNLOAD_SECONDS (testing).
ENGINE_IDLE_UNLOAD_SECONDS = _idle_seconds_from_env(
    "OMNIVOICE_ENGINE_IDLE_UNLOAD_SECONDS", 600.0, floor=5.0
)


@contextmanager
def engine_in_use(instance, *, now: Optional[float] = None):
    """Hold an engine against the idle sweep for the duration of one job.

    Leaving on the exit stamp rather than the entry one makes "idle" mean
    "idle since the work finished", which is the only reading under which the
    ten-minute window measures what it claims to.
    """
    cls = type(instance)
    with _ENGINE_CACHE_LOCK:
        _ENGINE_IN_USE[cls] = _ENGINE_IN_USE.get(cls, 0) + 1
    try:
        yield instance
    finally:
        with _ENGINE_CACHE_LOCK:
            remaining = _ENGINE_IN_USE.get(cls, 1) - 1
            if remaining > 0:
                _ENGINE_IN_USE[cls] = remaining
            else:
                _ENGINE_IN_USE.pop(cls, None)
            _ENGINE_LAST_USED[cls] = time.monotonic() if now is None else float(now)


def get_engine_instance(cls, *, now: Optional[float] = None):
    """Return the cached singleton instance of ``cls``, creating it once.

    ``SubprocessBackend.__init__`` registers an atexit shutdown hook, so
    re-instantiating per call would leak handler entries — and on real engines,
    an extra sidecar process the first time the lock is acquired. One instance
    per process is the right move.
    """
    with _ENGINE_CACHE_LOCK:
        inst = _ENGINE_INSTANCES.get(cls)
        if inst is None:
            inst = cls()
            _ENGINE_INSTANCES[cls] = inst
        _ENGINE_LAST_USED[cls] = time.monotonic() if now is None else float(now)
        return inst


def get_engine_instance_for(engine_id: str, *, now: Optional[float] = None):
    """Cached instance of the TTS engine registered under ``engine_id``.

    Deliberately NOT :func:`get_active_tts_backend`: that resolves
    ``active_backend_id()``, i.e. *this machine's* Settings preference. On a
    remote worker that would run whatever the worker's owner happens to prefer
    while the control plane's slots, breaker history and result metadata are
    keyed to the engine it believes ran — wrong audio, silently.
    """
    return get_engine_instance(get_backend_class(engine_id), now=now)


def release_idle_engines(
    idle_seconds: float = ENGINE_IDLE_UNLOAD_SECONDS,
    *,
    now: Optional[float] = None,
) -> list[str]:
    """Unload and drop every cached engine unused for ``idle_seconds``.

    Least-recently-used first, so a sweep cut short by a raising ``unload()``
    has already freed the coldest engine. Never raises: a stuck unload must not
    take down the loop that called it. Returns the engine ids released.
    """
    stamp = time.monotonic() if now is None else float(now)
    pending: list[tuple[str, object]] = []
    with _ENGINE_CACHE_LOCK:
        # Entries other consumers popped straight out of the cache
        # (engine_memory's eviction, model_lifecycle's unload) would otherwise
        # pin a stale class.
        for cls in [c for c in _ENGINE_LAST_USED if c not in _ENGINE_INSTANCES]:
            _ENGINE_LAST_USED.pop(cls, None)
        coldest_first = sorted(
            _ENGINE_INSTANCES, key=lambda c: _ENGINE_LAST_USED.get(c, 0.0)
        )
        for cls in coldest_first:
            if _ENGINE_IN_USE.get(cls):
                continue
            # An instance put here by some other path has no timestamp; start
            # its clock now rather than leaving it resident forever.
            last_used = _ENGINE_LAST_USED.setdefault(cls, stamp)
            if stamp - last_used < idle_seconds:
                continue
            inst = _ENGINE_INSTANCES.pop(cls, None)
            _ENGINE_LAST_USED.pop(cls, None)
            if inst is not None:
                pending.append((getattr(cls, "id", cls.__name__), inst))

    released: list[str] = []
    for engine_id, inst in pending:
        try:
            inst.unload()
        except Exception as exc:  # noqa: BLE001
            logger.warning("idle unload: %s.unload() raised: %s", engine_id, exc)
        released.append(engine_id)
    if released:
        logger.info("Released %d idle engine(s): %s", len(released), ", ".join(released))
    return released


# ── Shared generation-time engine resolution (issue #312 class) ───────────
#
# dub_generate.py and batch.py used to call services.model_manager.get_model()
# directly, hardcoding OmniVoice regardless of the engine selected in
# Model Catalogue → Engines — a SILENT fallback: pick VoxCPM2, dub anyway with
# OmniVoice, no error. This is the single resolution path both routers now
# call instead, mirroring generation.py's /generate resolution (engine id →
# is_available() → routing gate) plus a voice-cloning capability gate that
# /generate doesn't need (OmniVoice's native path always clones).


async def resolve_generation_backend(
    *, require_cloning: bool = False, cloning_purpose: str = "dubbing",
) -> TTSBackend:
    """Resolve + validate the active TTS engine for a generation call.

    Returns the live backend instance (:func:`get_active_tts_backend`) —
    cached, and properly unload()ed on an engine switch. Raises ``ValueError``
    with an actionable message (never silently falls back to OmniVoice) when:

      * the configured engine id is unknown (bad env var / stale pref),
      * the engine reports itself unavailable (``is_available()``),
      * the engine needs an accelerator this host lacks and has no CPU path
        (``routing_status == "unavailable"``),
      * ``require_cloning`` is True and the resolved backend can't clone
        from reference audio (``supports_cloning`` False) — checked on the
        live *instance*, not the class, so a model-dependent adapter like
        MLX-Audio (Kokoro vs. CSM) is judged by what's actually loaded.
    """
    engine_id = active_backend_id()
    try:
        backend_cls = get_backend_class(engine_id)
    except ValueError as e:
        raise ValueError(
            f"Active TTS engine '{engine_id}' is not a recognized backend ({e}). "
            "Check Model Catalogue → Engines or the OMNIVOICE_TTS_BACKEND env var."
        ) from e

    try:
        ok, msg = backend_cls.is_available()
    except Exception as exc:  # noqa: BLE001 — surface as an actionable ValueError
        ok, msg = False, f"{type(exc).__name__}: {exc}"
    if not ok:
        raise ValueError(f"TTS engine '{engine_id}' is not available: {_mask_hf_tokens(msg)}")

    from core.device_caps import detect_host_caps
    from services.engine_routing import resolve_routing
    routing = resolve_routing(
        getattr(backend_cls, "gpu_compat", ("cpu",)), detect_host_caps(),
        getattr(backend_cls, "min_vram_gb", 0.0),
    )
    if routing["routing_status"] == "unavailable":
        raise ValueError(routing["routing_reason"])

    _model = None
    if backend_cls is OmniVoiceBackend:
        # OmniVoice needs its model pre-loaded before construction: called
        # from an async context, OmniVoiceBackend._ensure_loaded() refuses to
        # bootstrap its own event loop (see its docstring) — same reason
        # generation.py's /generate special-cases this backend.
        from services.model_manager import get_model
        _model = await get_model()
    backend = get_active_tts_backend(model=_model)

    if require_cloning and not getattr(backend, "supports_cloning", True):
        raise ValueError(
            f"The active TTS engine '{engine_id}' doesn't support voice cloning, "
            f"so {cloning_purpose} can't preserve speaker voices. Switch to one "
            f"of: {', '.join(cloning_capable_engine_ids())} in "
            "Model Catalogue → Engines, or use OmniVoice for this job."
        )

    return backend


# ── PEP 562 lazy attribute re-export ───────────────────────────────────────
#
# Allows ``from services.tts_backend import IndexTTS2Backend`` to keep
# working even though the class itself lives in ``engines.indextts``.
# Triggers the engines.indextts import on first attribute access, which
# is after this module has finished loading — so no import cycle.

def __getattr__(name: str):  # pragma: no cover - exercised via tests
    if name in _LAZY_REGISTRY:
        return _REGISTRY[name if name in _REGISTRY else None]
    if name == "IndexTTS2Backend":
        return _REGISTRY["indextts2"]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
