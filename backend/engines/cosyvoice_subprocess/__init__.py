"""cosyvoice-subprocess: CosyVoice 3 from its own venv (one-click install).

The in-process engine needs CosyVoice importable from VoiceStudio's own
interpreter, which upstream's setup (its own Python 3.10 environment, pins
that conflict with the app's) never provides. The one-click installer clones a
reviewed CosyVoice commit and the Matcha-TTS code it vendors as a submodule
into ``DATA_DIR/engines/cosyvoice/``, builds a private venv (normally Python
3.10, or 3.12 for explicitly selected Windows HIP) from a trimmed
requirements list (``requirements.txt`` beside this file), downloads the
CosyVoice 3 weights, and this class runs the model there in a sidecar.

The engine id stays ``cosyvoice``. ``tts_backend._effective_backend_class``
resolves to this class once that venv exists, and to the in-process
``CosyVoiceBackend`` otherwise, so an existing source installation keeps
working as it did.
"""
from __future__ import annotations

import math
import os
import sys
from dataclasses import replace
from pathlib import Path

from services.expression import COSYVOICE_INLINE_TAGS
from services.subprocess_backend import SubprocessBackend

VENV_ENV_VAR = "OMNIVOICE_COSYVOICE_DIR"
#: Where the installer puts the CosyVoice 3 weights, inside the checkout.
MANAGED_MODEL_SUBDIR = "pretrained_models/Fun-CosyVoice3-0.5B"


def own_venv_python() -> "Path | None":
    """The venv the one-click installer made for CosyVoice, if any."""
    from services.sidecar_install import engine_venv_python

    return engine_venv_python(VENV_ENV_VAR)


def _runtime_device(caps, environment) -> str:
    cpu = (
        caps.requested_family == "cpu"
        or environment.get("OMNIVOICE_DEVICE", "").strip().lower() == "cpu"
        or environment.get("OMNIVOICE_TORCH_VARIANT", "").strip().lower() == "cpu"
    )
    return "cuda" if caps.family == "rocm" and not cpu else "cpu"


class CosyVoiceSubprocessBackend(SubprocessBackend):
    """CosyVoice in a killable sidecar running the engine's own venv."""

    id = "cosyvoice"
    display_name = "CosyVoice 3 (9 langs, zero-shot, instruct, Apache-2.0)"
    gpu_compat = ("cuda", "cpu")
    # instruct + reference → inference_instruct2 (emotion, dialect, pace).
    instruct_directs_delivery = True
    inline_tags = COSYVOICE_INLINE_TAGS
    _DEFAULT_SAMPLE_RATE = 24_000
    # The sidecar seeds its own torch RNG from the request (#3413): the
    # parent's torch.manual_seed never reaches another process.
    supports_generation_seed = True
    # Upstream's inference_vc: the source recording's speech tokens are kept
    # as they are and re-rendered with the reference voice, so timing and
    # rhythm stay the speaker's own (#3421).
    supports_voice_conversion = True
    #: Upstream's speech tokenizer refuses clips longer than 30 s; callers
    #: split longer sources (services.voice_conversion).
    max_vc_source_seconds = 30.0

    @classmethod
    def runtime_compute_profile(cls, caps) -> dict:
        environment = os.environ.copy()
        profile = super().runtime_compute_profile(caps)
        if sys.platform != "win32":
            return profile
        from services.sidecar_install import cosyvoice_rocm_verified

        if cosyvoice_rocm_verified():
            from services.engine_routing import resolve_routing

            compatibility = ("rocm", "cpu")
            profile["gpu_compat"] = compatibility
            routing_caps = replace(caps, family="cpu") if (
                caps.family == "rocm" and _runtime_device(caps, environment) == "cpu"
            ) else caps
            profile.update(resolve_routing(compatibility, routing_caps, profile["min_vram_gb"]))
        return profile

    def _managed_runtime_device(self, environment):
        if sys.platform != "win32":
            return None
        from services.sidecar_install import cosyvoice_rocm_verified

        if not cosyvoice_rocm_verified():
            return None
        from core.device_caps import detect_host_caps

        capabilities = detect_host_caps()
        return _runtime_device(capabilities, environment)

    def _child_environment(self) -> dict[str, str]:
        environment = super()._child_environment()
        environment.pop("VOICESTUDIO_COSYVOICE_ROCM_CHECKOUT", None)
        device = self._managed_runtime_device(environment)
        if device is not None:
            from core.windows_rocm.cosyvoice import child_environment

            environment["OMNIVOICE_DEVICE"] = device
            return child_environment(environment, Path(environment[VENV_ENV_VAR]))
        return environment

    def _spawn(self) -> None:
        environment = self._child_environment()
        device = environment.get("OMNIVOICE_DEVICE") if (
            "VOICESTUDIO_COSYVOICE_ROCM_CHECKOUT" in environment
        ) else None
        if self._proc is not None and self._proc.poll() is None:
            if getattr(self, "_cosyvoice_spawn_device", None) != device:
                current = self._proc
                self._send({"op": "shutdown"})
                self._reap_unusable_process(current, grace_period_s=3)
                if not self._retry_timeout_cleanup():
                    raise RuntimeError("CosyVoice is still stopping after a device change")
                self._proc = None
        super()._spawn(environment=environment)
        self._cosyvoice_spawn_device = device

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        if own_venv_python() is None:
            return False, (
                "cosyvoice package not installed. Install it from "
                "Model Catalogue → Engines."
            )
        return True, "ready"

    @classmethod
    def venv_python(cls) -> Path:
        py = own_venv_python()
        if py is None:
            raise RuntimeError(
                "CosyVoice's environment is missing. Reinstall it from "
                "Model Catalogue → Engines."
            )
        return py

    @classmethod
    def sidecar_script(cls) -> Path:
        return Path(__file__).resolve().parent / "main.py"

    @property
    def recv_timeout_s(self) -> float:
        # Loading the model and its text normalizers takes a while on a cold
        # start; the sidecar heartbeats progress frames meanwhile.
        try:
            v = float(os.environ.get("OMNIVOICE_COSYVOICE_RECV_TIMEOUT_S", "900"))
        except (TypeError, ValueError):
            return 900.0
        if not math.isfinite(v):  # reject inf/nan so the deadline can't be disabled
            return 900.0
        return max(30.0, v)

    @property
    def sample_rate(self) -> int:
        # The sidecar resamples to this rate if a model ever reports another.
        return self._DEFAULT_SAMPLE_RATE

    @property
    def supported_languages(self) -> list[str]:
        from services.tts_backend import CosyVoiceBackend

        return CosyVoiceBackend.supported_languages.fget(self)

    def convert_voice(self, source_audio: str, *, ref_audio: str, seed=None):
        msg = {"op": "convert", "source_audio": str(source_audio), "ref_audio": str(ref_audio)}
        if seed is not None:
            msg["seed"] = int(seed)
        return self._exchange(msg, deadline_text="")

    def model_identity(self) -> str:
        # v1/v2/v3 share the one "cosyvoice" id; the folder name tells them
        # apart, as it does for the in-process engine.
        model_dir = os.environ.get("OMNIVOICE_COSYVOICE_MODEL") or MANAGED_MODEL_SUBDIR
        return os.path.basename(os.path.normpath(model_dir))


__all__ = [
    "CosyVoiceSubprocessBackend",
    "MANAGED_MODEL_SUBDIR",
    "VENV_ENV_VAR",
    "own_venv_python",
]
