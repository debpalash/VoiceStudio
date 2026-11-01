"""pockettts: Kyutai PocketTTS as a crash-isolated, CPU-only TTS sidecar (#1306).

PocketTTS (kyutai-labs/pocket-tts, 100M params) is hired for the "fastest CPU
render / lowest latency" job, the row the engine-acceptance framework leaves
unheld: every CPU engine OmniVoice ships is either English-only or a quality
engine falling back to CPU. PocketTTS is the complementary opposite end of the
spectrum from the quality engines (omnivoice, IndexTTS, Supertonic-3): small,
fast, CPU-only, zero-shot cloning from a reference clip. Six languages
(en/fr/de/pt/it/es), one model per language, selected via the ``language``
kwarg. Measured ~8-9x real-time on an Apple M3 Pro (see
scripts/bench_engines_latency.py, PR #1322).

This engine runs PocketTTS in a child process via :class:`SubprocessBackend`,
mirroring engines/omnivoice_subprocess and engines/supertonic3. Crash isolation:
a wedged generate is hard-killed by the parent's watchdog, reclaiming the
child's memory, the thing an in-process engine structurally cannot do. CPU-only
by design (``gpu_compat = ("cpu",)``): Kyutai observes no GPU speedup for this
100M, batch-1 model.

Opt-in (Settings -> Engines, or ``OMNIVOICE_TTS_BACKEND=pockettts``); the
default ``omnivoice`` engine is unchanged, so existing users see no behaviour
change.

Licence: MIT (code) + CC-BY-4.0 (weights), both commercial-OK (cleared from
primary sources in #1306). The weights are gated on HuggingFace (an access
agreement plus an acceptable-use clause); the engine must surface that honestly
at first-run rather than failing inside a download (condition 6 of the #1306
acceptance). That preflight is built on top of this shape, not in it.

Streaming note: PocketTTS streams audio (``generate_audio_stream``), but this
batch sidecar returns one audio frame per synth, matching the SubprocessBackend
contract every other subprocess engine uses. A streaming-aware variant
(incremental audio frames) is a documented opportunity to recover PocketTTS's
~33 ms time-to-first-audio end-to-end; out of scope for this shape, raised on
the PR.
"""
from __future__ import annotations

import logging
import math
import os
import platform
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from services.subprocess_backend import SubprocessBackend

logger = logging.getLogger("omnivoice.engines.pockettts")

_VENV_ENV_VAR = "OMNIVOICE_POCKETTTS_DIR"


def _own_venv_python() -> "Path | None":
    """The venv the one-click installer made for this engine, if any."""
    from services.sidecar_install import engine_venv_python

    return engine_venv_python(_VENV_ENV_VAR)

if TYPE_CHECKING:
    import torch  # noqa: F401


class PocketTTSBackend(SubprocessBackend):
    """Kyutai PocketTTS in a killable, CPU-only sidecar process."""

    id = "pockettts"
    requires_hf_auth = True
    display_name = "PocketTTS (Kyutai, 6 langs, CPU-only, MIT/CC-BY-4.0)"
    # The sidecar protocol carries no quality knobs — explicit values 400 (#3654).
    supports_num_step = False
    supports_guidance_scale = False
    _DEFAULT_SAMPLE_RATE = 24_000
    # CPU-only by design (honest hardware reporting, like supertonic3): Kyutai
    # ships no CUDA/MPS path and reports no GPU speedup for this model.
    gpu_compat: tuple[str, ...] = ("cpu",)
    supports_cloning = True  # zero-shot clone from a reference clip
    # No instruct input: without a clip it speaks a preset voice, so a Voice
    # Design take would ignore every detail and the seed (#3413).
    supports_voice_design = False

    @classmethod
    def _platform_error(cls) -> str | None:
        if sys.platform == "darwin" and platform.machine().lower() == "x86_64":
            return (
                "PocketTTS is unavailable on Intel Macs because its required "
                "PyTorch version has no macOS x86_64 wheel."
            )
        return None

    @classmethod
    def _license_accepted(cls) -> bool:
        """Read acknowledgement fail-closed at every construction path."""
        try:
            from services import settings_store

            return bool(settings_store.get_license_accepted(cls.id))
        except Exception:
            logger.warning(
                "pockettts: license acknowledgement could not be read; "
                "treating as not accepted"
            )
            return False

    def __init__(self) -> None:
        # Availability probes are advisory. Construction is the shared
        # authorization boundary for HTTP, WebSocket, audiobook, and direct
        # backend selection, so none can reach gated weights before consent.
        if platform_error := self._platform_error():
            raise RuntimeError(platform_error)
        if not self._license_accepted():
            raise RuntimeError(
                "PocketTTS license not accepted. Review it in Model Catalogue."
            )
        super().__init__()

    def generate(self, *args, **kwargs):
        # Active backends are cached. Recheck at the synthesis chokepoint so a
        # later revocation takes effect without requiring process restart or
        # relying on every caller to evict its cached instance.
        if not self._license_accepted():
            raise RuntimeError(
                "PocketTTS license not accepted. Review it in Model Catalogue."
            )
        return super().generate(*args, **kwargs)

    def _validate_generate_authorization(self) -> None:
        # The preflight above rejects immediately when possible. This second
        # check runs under SubprocessBackend._lock, after any queued synthesis,
        # so revocation while waiting cannot reach the sidecar or return audio.
        if not self._license_accepted():
            raise RuntimeError(
                "PocketTTS license not accepted. Review it in Model Catalogue."
            )

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        if platform_error := cls._platform_error():
            return False, platform_error
        # Installed either into its own venv by the one-click installer, which
        # verified `import pocket_tts` there before saving the path, or into the
        # app's environment by `uv sync --extra pockettts`.
        if _own_venv_python() is None:
            try:
                import pocket_tts  # type: ignore[import-not-found]  # noqa: F401
            except Exception as e:
                return False, (
                    f"pocket_tts package not installed or failed to import ({e}). "
                    "Install it from Model Catalogue."
                )

        # The model repository has an additional gated-access agreement and
        # prohibited-use conditions beyond its CC-BY-4.0 license. Keep first
        # use behind an explicit local acknowledgement, matching the dialog.
        if not cls._license_accepted():
            return False, (
                "PocketTTS license not accepted. Open Model Catalogue → "
                "PocketTTS and review the MIT code license, CC-BY-4.0 model "
                "license, and gated-access conditions before enabling it."
            )
        return True, "ready (CPU-only)"

    @classmethod
    def shares_parent_torch(cls) -> bool:
        # The same check venv_python() makes: no venv of its own means the
        # parent interpreter runs it.
        return _own_venv_python() is None

    @classmethod
    def venv_python(cls) -> Path:
        # Its own venv when the one-click installer made one. Otherwise the
        # parent interpreter, where `uv sync --extra pockettts` installs it
        # (its deps sit happily at the parent's pins).
        return _own_venv_python() or Path(sys.executable)

    @classmethod
    def sidecar_script(cls) -> Path:
        return Path(__file__).resolve().parent / "main.py"

    @property
    def recv_timeout_s(self) -> float:
        # A cold load pulls gated weights (a 24-layer model can be hundreds of MB),
        # so allow a long recv deadline; the sidecar also heartbeats progress frames
        # during the download (main.py) to keep the watchdog armed.
        try:
            v = float(os.environ.get("OMNIVOICE_POCKETTTS_RECV_TIMEOUT_S", "600"))
        except (ValueError, TypeError):
            return 600.0
        if not math.isfinite(v):  # reject inf/nan so the deadline can't be disabled
            return 600.0
        return max(30.0, v)

    @property
    def sample_rate(self) -> int:
        return self._DEFAULT_SAMPLE_RATE

    @property
    def supported_languages(self) -> list[str]:
        # Protocol tag; six languages (en/fr/de/pt/it/es), one model per
        # language, selected via the language kwarg.
        return ["multi"]


__all__ = ["PocketTTSBackend"]
