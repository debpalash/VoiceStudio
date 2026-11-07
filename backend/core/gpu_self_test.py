"""Run real work on the GPU once, in a process that is allowed to die (#3804).

Every other check reads metadata, and a GPU can pass all of them and still fail
its first real work: `torch.cuda.is_available()` was True on gfx1034 while the
first matmul died with "invalid device function". A failure here sends the loader
to the CPU, so the app keeps working, and the warning says why.

Only a result that says something about the GPU counts. A timeout, a process that
could not load torch, or a GPU busy with another program is inconclusive and changes
nothing. A pass is remembered until the app or torch changes. A failure is not, so
updating the driver and restarting is enough to clear it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
from dataclasses import dataclass

from core.config import DATA_DIR
from core.version import APP_VERSION

logger = logging.getLogger("omnivoice.gpu_self_test")

RESULT_PATH = os.path.join(DATA_DIR, "gpu_self_test.json")
TIMEOUT_S = 90

STARTED_MARK = "VOICESTUDIO_GPU_TEST_STARTED"
PASSED_MARK = "VOICESTUDIO_GPU_TEST_PASSED"
NO_GPU_MARK = "VOICESTUDIO_GPU_TEST_NO_GPU"
FAILED_MARK = "VOICESTUDIO_GPU_TEST_FAILED="

SCRIPT = f"""
import sys
import torch
print({STARTED_MARK!r}, flush=True)
if not torch.cuda.is_available():
    print({NO_GPU_MARK!r}, flush=True)
    sys.exit(0)
try:
    a = torch.ones((64, 64), device="cuda")
    if round((a @ a).sum().item()) != 64 ** 3:
        raise RuntimeError("the GPU returned a wrong result")
except Exception as exc:
    message = (str(exc).splitlines() or [""])[0][:300]
    print({FAILED_MARK!r} + type(exc).__name__ + ": " + message, flush=True)
    sys.exit(1)
print({PASSED_MARK!r}, flush=True)
"""


@dataclass(frozen=True)
class Verdict:
    status: str  # passed | failed | no_gpu | inconclusive | skipped
    detail: str = ""


_thread: threading.Thread | None = None
_verdict: Verdict | None = None


def _user_chose_device() -> bool:
    force = os.environ.get("OMNIVOICE_FORCE_CUDA", "").strip().lower()
    if force in {"1", "true", "yes", "on"}:
        return True
    from core.device_caps import requested_device_override

    return requested_device_override() != "auto"


def _nothing_to_test(torch) -> bool:
    version = getattr(torch, "version", None)
    hip = getattr(version, "hip", None)
    if not hip and not getattr(version, "cuda", None):
        return True  # A CPU or Apple build.
    # Its Python needs a DLL search path first, and setup runs its own smoke test.
    return bool(hip) and sys.platform == "win32"


def _gpu_identity() -> str:
    """Which GPUs and driver, without touching the GPU in this process. Windows has no
    such cheap read, so a pass there lasts until the app or torch changes."""
    parts: list[str] = []
    try:
        from core.device_caps import _kfd_gpu_architectures

        parts.extend(sorted(_kfd_gpu_architectures()))
    except Exception:  # noqa: BLE001 - identity only narrows how long a pass counts
        logger.debug("could not list AMD GPUs", exc_info=True)
    try:
        with open("/proc/driver/nvidia/version", encoding="ascii", errors="replace") as f:
            parts.append(f.readline().strip())
    except OSError:
        logger.debug("no NVIDIA driver version to read")
    return ",".join(parts)


def _build_key(torch) -> str:
    """Everything that decides which kernels run on which card."""
    return "|".join(
        (
            APP_VERSION,
            torch.__version__,
            os.environ.get("CUDA_VISIBLE_DEVICES", ""),
            os.environ.get("HSA_OVERRIDE_GFX_VERSION", ""),
            _gpu_identity(),
        )
    )


def _remembered_pass(key: str) -> bool:
    try:
        with open(RESULT_PATH, encoding="utf-8") as f:
            saved = json.load(f)
        return saved.get("key") == key and saved.get("status") == "passed"
    except (OSError, ValueError, AttributeError):
        return False


def _remember(key: str, status: str) -> None:
    try:
        temporary = f"{RESULT_PATH}.{os.getpid()}.tmp"
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump({"key": key, "status": status}, f)
        os.replace(temporary, RESULT_PATH)
    except OSError:
        logger.debug("could not save the GPU self-test result", exc_info=True)


def _run_child() -> Verdict:
    try:
        result = subprocess.run(
            [sys.executable, "-c", SCRIPT],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=TIMEOUT_S,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return Verdict("inconclusive", type(exc).__name__)
    lines = (result.stdout or "").splitlines()
    if STARTED_MARK not in lines:
        return Verdict("inconclusive", "torch did not load in the test process")
    if PASSED_MARK in lines:
        return Verdict("passed")
    if NO_GPU_MARK in lines:
        return Verdict("no_gpu")
    failure = next((line for line in lines if line.startswith(FAILED_MARK)), None)
    if failure is not None:
        detail = failure[len(FAILED_MARK):]
        if "out of memory" in detail.lower():
            return Verdict("inconclusive", "the GPU is busy")
        return Verdict("failed", detail)
    if result.returncode != 0:
        return Verdict("failed", f"the test process crashed (exit code {result.returncode})")
    return Verdict("inconclusive", "the test process gave no result")


def evaluate(torch) -> Verdict:
    """Never raises. `torch` must be imported already; the GPU is only used in the child."""
    if _nothing_to_test(torch) or _user_chose_device():
        return Verdict("skipped")
    key = _build_key(torch)
    if _remembered_pass(key):
        return Verdict("passed")
    verdict = _run_child()
    if verdict.status == "passed":
        _remember(key, verdict.status)
    logger.info("GPU self-test: %s %s", verdict.status, verdict.detail)
    return verdict


def start(torch) -> None:
    """Run in the background: a slow machine must not spend its startup budget here."""
    global _thread

    def work() -> None:
        global _verdict
        try:
            _verdict = evaluate(torch)
        except Exception as exc:  # noqa: BLE001 - this check must never break startup
            _verdict = Verdict("inconclusive", type(exc).__name__)

    _thread = threading.Thread(target=work, name="gpu_self_test", daemon=True)
    _thread.start()


def pending() -> bool:
    """A test is still running, so `failure_warning` has no answer yet."""
    return _thread is not None and _thread.is_alive()


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def failure_warning() -> str | None:
    """Why the GPU can't be used, or None. Waits for a test still running, because this
    decides where a model loads and a wrong answer can crash the app. Not on the event
    loop, where waiting would stop every request, health checks included."""
    if _thread is not None and not _on_event_loop():
        _thread.join(TIMEOUT_S + 5)
    if _verdict is None or _verdict.status != "failed":
        return None
    from core.scrub import scrub_text

    return (
        f"The GPU failed a self-test ({scrub_text(_verdict.detail)}), so VoiceStudio is "
        "using the CPU, which is slower. Update your GPU driver and restart the app. "
        "To try the GPU anyway, set OMNIVOICE_FORCE_CUDA=1."
    )
