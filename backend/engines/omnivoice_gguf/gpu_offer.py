"""One-time "use your GPU?" offer for AMD/Intel hosts whose PyTorch is CPU-only.

On a Radeon, Arc or Ryzen AI / Core Ultra host the CPU (or CUDA) PyTorch wheel
sees no GPU, while the bundled ``omnivoice-tts`` runtime can use it through
Vulkan. Rather than switching engines silently, the app asks once: it names the
GPU, the quant it would run and what has to be downloaded, and the user picks
**Use GPU** or **Not now**.

* Use GPU — the model licence is checked first (an unaccepted licence raises
  ``ModelLicenceNotAccepted`` so the app shows its licence dialog), then the TTS
  engine is set to ``omnivoice-gguf`` and the missing weight pair starts
  downloading, with progress in the quant picker.
* Not now — stays on the current engine; the answer is remembered and the offer
  is not shown again. Settings › Compute device keeps the same action.

Nothing is downloaded and no engine changes until the user chooses. The runtime
itself ships in the installers and is never downloaded: the offer is only made
when the bundled runtime with its Vulkan module is present and verified.
"""
from __future__ import annotations

import os
from typing import Optional

from core import prefs

PREF_KEY = "gguf_vulkan_offer"
ENGINE_ID = "omnivoice-gguf"
_DOWNLOAD_OK = ("started", "already_running", "already_installed")


def _answer() -> Optional[str]:
    """The stored answer, ``accepted`` / ``declined``, or None while unanswered."""
    value = prefs.get(PREF_KEY)
    return value if value in ("accepted", "declined") else None


def _engine_chosen() -> Optional[str]:
    """The TTS engine the user picked (env or Settings), or None."""
    return _env_engine() or prefs.get("tts_backend") or None


def _env_engine() -> Optional[str]:
    """``OMNIVOICE_TTS_BACKEND``: it wins over Settings, so while it names
    another engine accepting the offer could not switch anything."""
    return os.environ.get("OMNIVOICE_TTS_BACKEND", "").strip() or None


def _licence_accepted() -> bool:
    """Whether the GGUF model licence has already been accepted."""
    from .compute import _licence_accepted as accepted

    return accepted()


def offer_status() -> dict:
    """What the offer shows. ``show`` is true at most until the user answers."""
    from .compute import vulkan_eligible
    from .quants import quant_status

    env_engine = _env_engine()
    blocked = env_engine is not None and env_engine != ENGINE_ID
    eligible = vulkan_eligible() and not blocked
    chosen = _engine_chosen()
    answer = _answer()
    status: dict = {
        "eligible": eligible,
        "blocked_by_environment": blocked,
        "answer": answer,
        "active": chosen == ENGINE_ID,
        "show": bool(eligible and answer is None and chosen in (None, "omnivoice")),
        "licence_accepted": _licence_accepted() if eligible else False,
        "gpu_name": None, "device": None, "vendor": None, "shared_memory": False,
        "effective_vram_gb": None, "quant": None, "download_mb": 0,
    }
    if not eligible:
        return status
    q = quant_status()
    hw = q["hardware"]
    selected = next((o for o in q["options"] if o["quant"] == q["selected"]), None)
    status.update(
        gpu_name=hw["gpu_name"], device=hw["device"], vendor=hw["vendor"],
        shared_memory=hw["shared_memory"], effective_vram_gb=hw["effective_vram_gb"],
        quant=selected["tag"] if selected else None,
        # A pair generation can already load (an installed fallback) means
        # nothing has to be downloaded, the same rule generation follows.
        download_mb=0 if (selected is None or selected["installed"] or q["in_use"])
        else selected["approx_size_mb"],
    )
    return status


def respond(accept: bool) -> dict:
    """Record the user's answer; on accept switch the engine and fetch weights."""
    if not accept:
        prefs.set_(PREF_KEY, "declined")
        return {**offer_status(), "download": None}
    if not offer_status()["eligible"]:
        raise ValueError("GPU acceleration is not available on this machine")
    from .backend import _make_backend_class
    from services.tts_backend import ensure_engine_licence

    ensure_engine_licence(_make_backend_class())  # raises → licence dialog
    from .quants import quant_status, start_download

    # Generation accepts an installed pair when the recommended one is
    # missing, so an existing install is used rather than fetching another.
    status = quant_status()
    if status["in_use"]:
        download = {"status": "already_installed", "quant": status["in_use"]}
    else:
        # Switch only once the weights are on their way: a failed start
        # (offline, no network) must not leave the engine on missing weights.
        download = start_download(None)
    ok = download.get("status") in _DOWNLOAD_OK
    if download.get("status") == "already_running" and download.get("quant") != status["selected"]:
        # Another quant is downloading (picker Download): it would not give
        # the engine the weights it is about to need.
        ok = False
        download = {**download, "status": "busy"}
    if not ok:
        return {**offer_status(), "download": download}
    prefs.set_("tts_backend", ENGINE_ID)
    prefs.set_(PREF_KEY, "accepted")
    return {**offer_status(), "download": download}


__all__ = ["PREF_KEY", "offer_status", "respond"]
