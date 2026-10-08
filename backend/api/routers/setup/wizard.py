"""First-run wizard endpoints — status, preflight, and warmup.

Extracted from the monolithic ``setup.py``.

- ``GET /setup/status``       — missing-model gate for boot screen
- ``GET /setup/preflight``    — system health check (OS, RAM, disk, GPU, network —
  genuine user facts only; the media engine (ffmpeg/ffprobe/yt-dlp) is an
  internal concern that self-heals via ``services.media_tools``)
- ``POST /setup/warmup``      — background model pre-load
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform as _platform
import sys

from fastapi import APIRouter, Depends

from api.schemas import SetupStatusResponse, PreflightResponse
from core.device_caps import KERNEL_RISK_MARKER, is_windows_on_arm
# MIN_FREE_GB + disk_free_bytes are single-sourced in ``.models`` (the lowest
# module in the setup import graph) so the wizard gate, the /models header, and
# the per-install disk guard can't drift apart.
from .models import REQUIRED_MODELS, hf_cache_dir, is_cached, MIN_FREE_GB, disk_free_bytes
from core.browser_guard import reject_cross_site_get

logger = logging.getLogger("omnivoice.setup.wizard")
router = APIRouter()


def _disk_free_gb(path: str) -> float:
    """Free GB on the volume containing *path* (thin GB wrapper over the shared
    ``models.disk_free_bytes``, which walks up to the nearest existing ancestor
    for a not-yet-created path)."""
    return disk_free_bytes(path) / (1024 ** 3)


# ── Setup Status ───────────────────────────────────────────────────────────

@router.get("/setup/status", response_model=SetupStatusResponse)
def setup_status():
    """Snapshot the setup state so the client can pick its boot screen."""
    missing = [
        {"repo_id": rid, "label": label}
        for (rid, label) in REQUIRED_MODELS
        if not is_cached(rid)
    ]
    cache = hf_cache_dir()
    free_gb = _disk_free_gb(cache)
    return {
        "models_ready": len(missing) == 0,
        "missing": missing,
        "hf_cache_dir": cache,
        "disk_free_gb": round(free_gb, 2),
        "min_free_gb": MIN_FREE_GB,
        "enough_disk": free_gb >= MIN_FREE_GB,
    }


# ── Pre-flight System Check ───────────────────────────────────────────────

# CUDA 12.x minor-version compatibility floor (the bundled cu128 runtime runs on
# any 12.x-capable driver): R525.60.13 on Linux, R528.33 on Windows — see
# https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html
# Compared as full versions: a Windows 527.x driver is NOT enough (#2489).
_MIN_NVIDIA_DRIVER_LINUX = (525, 60, 13)
_MIN_NVIDIA_DRIVER_WINDOWS = (528, 33)


def _min_nvidia_driver(platform: str | None = None) -> tuple[int, ...]:
    return (
        _MIN_NVIDIA_DRIVER_WINDOWS if (platform or sys.platform) == "win32"
        else _MIN_NVIDIA_DRIVER_LINUX
    )


def _driver_tuple(driver: str | None) -> tuple[int, ...] | None:
    """Parsed driver version, or ``None`` when the metadata is missing or not a
    dotted number ("N/A", "[Not Supported]") - the floor check is skipped then
    rather than failing a working GPU."""
    parts = (driver or "").strip().split(".")
    if not parts or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)
_RAM_FAIL_GB = 8
_RAM_WARN_GB = 12
# Installed DIMMs never fully reach the OS: firmware, integrated graphics and
# kernel reservations shave off up to ~7% (an "8 GB" Windows laptop reports
# ~7.8 GB usable). Thresholds are compared with this allowance applied so the
# machines a threshold is meant to admit aren't blocked by that gap (#1618).
_RAM_RESERVED_ALLOWANCE = 0.93


def _run_cmd(args: list[str], timeout: float = 2.0) -> tuple[int, str]:
    """Run a subprocess synchronously with a short timeout."""
    import subprocess
    try:
        out = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False,
        )
        return out.returncode, out.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return -1, ""


def _amd_unusable_note() -> str:
    """Why an AMD card sits idle, per OS - and only options that really exist."""
    if sys.platform == "win32":
        return (
            "AMD GPU detected, but this install's PyTorch (the NVIDIA CUDA or "
            "CPU-only build) cannot drive it, and no ROCm build of the PyTorch "
            "version VoiceStudio ships exists for Windows. PyTorch "
            "engines run on the CPU; audio.cpp (Vulkan) can use the GPU - see "
            "Settings > Performance for the per-engine list."
        )
    return (
        "AMD GPU detected but torch was installed with CUDA wheels. Set "
        "OMNIVOICE_TORCH_VARIANT=rocm and re-run setup to install the ROCm "
        "build of PyTorch (docs/install/linux.md#amd-gpu-rocm)."
    )


def _detect_gpu() -> dict:
    """Best-effort detection of GPU vendor + driver + compute backend."""
    info = {
        "vendor": "none", "driver": None, "device_name": None,
        "backend": "cpu", "available": False, "notes": [],
    }

    # Apple Silicon → MPS
    if sys.platform == "darwin" and _platform.machine() == "arm64":
        info["vendor"] = "apple"
        info["backend"] = "mps"
        info["device_name"] = "Apple Silicon GPU (Metal)"
        try:
            import torch
            info["available"] = bool(torch.backends.mps.is_available())
        except Exception:
            info["available"] = False
        return info

    # NVIDIA
    rc, out = _run_cmd([
        "nvidia-smi",
        "--query-gpu=driver_version,name",
        "--format=csv,noheader",
    ])
    if rc == 0 and out.strip():
        line = out.strip().splitlines()[0]
        parts = [p.strip() for p in line.split(",")]
        driver = parts[0] if parts else None
        name = parts[1] if len(parts) > 1 else None
        info.update({"vendor": "nvidia", "driver": driver, "device_name": name})
        try:
            import torch
            info["available"] = bool(torch.cuda.is_available())
            info["backend"] = "cuda" if info["available"] else "cpu"
        except Exception:
            pass
        try:
            floor = _min_nvidia_driver()  # per host OS
            parsed = _driver_tuple(driver)
            if parsed is not None and parsed < floor:
                info["notes"].append(
                    f"NVIDIA driver {driver} below "
                    f"{'.'.join(map(str, floor))} required "
                    f"by the bundled CUDA 12.8 runtime — GPU will fail to launch "
                    f"kernels. Update drivers before dubbing."
                )
                info["available"] = False
        except Exception:
            pass
        return info

    # AMD. rocm-smi only exists where a ROCm userspace is installed - never on
    # a stock Windows box - so fall back to the OS adapter inventory (registry /
    # sysfs, no subprocess), which sees a Radeon whatever PyTorch can do with it.
    rc, out = _run_cmd(["rocm-smi", "--showproductname"])
    amd_name = out.strip().splitlines()[0][:120] if rc == 0 and out.strip() else None
    if amd_name is None:
        try:
            from core.gpu_inventory import detect_host_gpus

            amd_gpu = next((g for g in detect_host_gpus() if g.vendor == "amd"), None)
        except Exception:
            amd_gpu = None
        amd_name = amd_gpu.name[:120] if amd_gpu else None
    if amd_name is not None:
        info["vendor"] = "amd"
        info["device_name"] = amd_name
        try:
            import torch
            has_hip = getattr(torch.version, "hip", None) is not None
            if has_hip and torch.cuda.is_available():
                info["backend"] = "rocm"
                info["available"] = True
            else:
                info["backend"] = "cpu"
                info["notes"].append(_amd_unusable_note())
        except Exception:
            info["notes"].append("AMD GPU detected but torch not importable.")
        return info

    # Fallback — no nvidia-smi/rocm-smi but torch might still see CUDA
    # (common inside Docker containers with the NVIDIA runtime).
    try:
        import torch
        if torch.cuda.is_available():
            info["vendor"] = "unknown"
            info["backend"] = "cuda"
            info["available"] = True
            try:
                info["device_name"] = torch.cuda.get_device_name(0)
            except Exception:
                pass
            info["notes"].append(
                "torch.cuda.is_available() is True but no nvidia-smi/rocm-smi "
                "found — running through WSL or virtual GPU?"
            )
    except Exception:
        pass
    return info


def _probe_network(host: str = "huggingface.co", port: int = 443, timeout: float = 8.0) -> bool:
    """Tiny TCP connect test. 8s default — high-latency / China paths often exceed 2–3s."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _hf_endpoint_host() -> tuple[str, int]:
    """Host/port of the Hugging Face endpoint actually in effect.

    Mirror-aware: restricted-network users (e.g. behind the Great Firewall)
    point HF_ENDPOINT at a mirror via Settings → Network → Hugging Face
    mirror. Probing hardcoded huggingface.co would fail them even when their
    configured mirror works fine.
    """
    try:
        from core.failure import configured_hf_mirror
        mirror = configured_hf_mirror()
    except Exception:
        logger.warning("Configured Hugging Face endpoint could not be read")
        return "", 0
    if mirror:
        try:
            from urllib.parse import urlsplit
            u = urlsplit(mirror)
            if u.hostname:
                return u.hostname, u.port or (80 if u.scheme == "http" else 443)
        except Exception:
            logger.warning("Configured Hugging Face endpoint could not be parsed")
            return "", 0
        logger.warning("Configured Hugging Face endpoint has no host")
        return "", 0
    return "huggingface.co", 443


def _network_check() -> dict:
    """The preflight "network" check row — auto-race or explicit-endpoint probe.

    Auto mode (nothing explicitly configured): force a fresh endpoint race —
    preflight IS the connectivity health check, and the cached winner is what
    model downloads will use. Manual mode: probe exactly the configured
    endpoint (never auto-switch an explicit choice), keeping the mirror
    quick-pick affordance when the official endpoint is blocked.
    """
    auto_decision = None
    try:
        from services import endpoint_race
        if endpoint_race.mode() == "auto":
            auto_decision = endpoint_race.ensure_decision(force=True)
    except Exception as exc:  # the race must never break preflight
        logger.warning("preflight endpoint race failed: %s", exc)

    if auto_decision is not None:
        from urllib.parse import urlsplit
        from services.endpoint_race import CANONICAL_ENDPOINT

        picked = auto_decision["endpoint"]
        picked_host = urlsplit(picked).hostname or picked
        latency = auto_decision.get("latency_ms")
        latency_s = f" ({latency:.0f} ms)" if isinstance(latency, (int, float)) else ""
        results = {r["endpoint"]: r for r in auto_decision.get("results", [])}
        canonical_ok = bool(results.get(CANONICAL_ENDPOINT, {}).get("reachable"))
        mirror_reachable = any(
            r.get("reachable") for ep, r in results.items() if ep != CANONICAL_ENDPOINT
        )
        if auto_decision.get("reachable"):
            if picked == CANONICAL_ENDPOINT:
                detail = f"Reachable{latency_s}"
            elif not canonical_ok:
                detail = (
                    f"huggingface.co is unreachable on this network — using the "
                    f"community mirror {picked_host}{latency_s} for model "
                    "downloads. Downloads are checksum-verified by Hugging Face "
                    "regardless of endpoint; change anytime in Settings → "
                    "Models → Hugging Face mirror."
                )
            else:
                detail = (
                    f"Both endpoints reachable — {picked_host}{latency_s} "
                    "selected (decisively faster here). Change anytime in "
                    "Settings → Models → Hugging Face mirror."
                )
            status, fix = "pass", None
        else:
            status = "warn"
            detail = "No Hugging Face endpoint reachable"
            fix = (
                "Neither huggingface.co nor the hf-mirror.com community mirror "
                "responded — check internet connection, VPN, or firewall. You "
                "can continue — models already downloaded keep working "
                "offline; a custom mirror can be configured below."
            )
        return {
            "id": "network", "label": f"Network ({picked_host})",
            "status": status, "detail": detail, "fix": fix,
            # Frontend affordance hint: the wizard offers the mirror
            # quick-pick when the check didn't pass (PreflightCheck allows
            # extras). `endpoint` documents the auto pick for the UI.
            "mirror_reachable": mirror_reachable,
            "endpoint": picked,
        }

    # Manual mode (explicit endpoint) — probe exactly what the user chose.
    net_host, net_port = _hf_endpoint_host()
    if not net_host:
        return {
            "id": "network", "label": "Network (configured endpoint)",
            "status": "warn",
            "detail": "The configured Hugging Face endpoint could not be validated.",
            "fix": "Review the endpoint in Settings → Network, then re-check.",
            "mirror_reachable": False,
        }
    net_ok = _probe_network(net_host, net_port)
    mirror_reachable = False
    if not net_ok and net_host == "huggingface.co":
        # Official endpoint blocked — if the community mirror is reachable,
        # tell the user exactly which switch unblocks them.
        mirror_reachable = _probe_network("hf-mirror.com")
    if net_ok:
        net_fix = None
    elif mirror_reachable:
        net_fix = (
            "huggingface.co is blocked on this network, but the hf-mirror.com "
            "community mirror is reachable — apply it below and re-check. "
            "Model downloads will use the mirror immediately."
        )
    elif net_host != "huggingface.co":
        net_fix = (
            f"Your configured Hugging Face mirror ({net_host}) is unreachable "
            "— it may be down or blocked. Pick another mirror or the official "
            "endpoint below, or continue offline: models already downloaded "
            "keep working."
        )
    else:
        net_fix = (
            "Check internet connection, VPN, or corporate firewall whitelist "
            "for huggingface.co. You can continue — models already downloaded "
            "keep working offline; new downloads need a connection or a "
            "mirror (configurable below)."
        )
    return {
        "id": "network", "label": f"Network ({net_host})",
        "status": "pass" if net_ok else "warn",
        "detail": "Reachable" if net_ok else f"Unreachable on port {net_port}",
        "fix": net_fix,
        # Frontend affordance hint: the wizard offers the mirror quick-pick
        # when the endpoint is unreachable (PreflightCheck allows extras).
        "mirror_reachable": mirror_reachable,
    }


def _ram_gb() -> float:
    try:
        import psutil
        return psutil.virtual_memory().total / (1024 ** 3)
    except Exception:
        return 0.0


@router.get("/setup/preflight", response_model=PreflightResponse, dependencies=[Depends(reject_cross_site_get)])
def preflight():
    """One-shot system health check for the wizard."""
    checks: list[dict] = []

    # ── OS + arch
    arch = _platform.machine()
    os_ver = _platform.platform(terse=True)
    checks.append({
        "id": "os", "label": "Operating system", "status": "pass",
        "detail": f"{os_ver} ({arch})", "fix": None,
    })

    # ── Python runtime
    checks.append({
        "id": "python", "label": "Python runtime", "status": "pass",
        "detail": f"Python {sys.version.split()[0]}", "fix": None,
    })

    # ── RAM
    ram = _ram_gb()
    # Escape hatch (#1618): a preflight should inform, not brick setup —
    # OMNIVOICE_RAM_PREFLIGHT=0 downgrades the hard block to a warning for
    # users who accept the OOM risk. Same opt-out shape as
    # OMNIVOICE_ASR_VRAM_PREFLIGHT.
    ram_gate = os.environ.get(
        "OMNIVOICE_RAM_PREFLIGHT", "1"
    ).strip().lower() not in ("0", "false", "no")
    if ram == 0:
        ram_status, ram_detail, ram_fix = (
            "warn", "Could not detect system RAM.",
            "Install psutil in the backend environment or ignore this warning.",
        )
    elif ram < _RAM_FAIL_GB * _RAM_RESERVED_ALLOWANCE:
        ram_status, ram_detail, ram_fix = (
            "fail" if ram_gate else "warn",
            f"{ram:.1f} GB total (need ≥ {_RAM_FAIL_GB} GB)",
            "The app will OOM on first dub. Close other apps or upgrade RAM."
            if ram_gate else
            "RAM check disabled via OMNIVOICE_RAM_PREFLIGHT=0 — dubbing may "
            "OOM on this machine.",
        )
    elif ram < _RAM_WARN_GB * _RAM_RESERVED_ALLOWANCE:
        ram_status, ram_detail, ram_fix = (
            "warn", f"{ram:.1f} GB total ({_RAM_WARN_GB}+ GB recommended)",
            "Long videos may hit swap. Keep other apps closed during dubbing.",
        )
    else:
        ram_status, ram_detail, ram_fix = ("pass", f"{ram:.1f} GB total", None)
    checks.append({
        "id": "ram", "label": "System RAM", "status": ram_status,
        "detail": ram_detail, "fix": ram_fix,
    })

    # ── Disk free
    cache = hf_cache_dir()
    free = _disk_free_gb(cache)
    if free < MIN_FREE_GB:
        disk = {
            "status": "fail",
            "detail": f"{free:.1f} GB free at {cache} (need ≥ {MIN_FREE_GB} GB)",
            "fix": f"Free up disk space or set HF_HOME to a larger partition.",
        }
    else:
        disk = {"status": "pass", "detail": f"{free:.1f} GB free at {cache}", "fix": None}
    checks.append({"id": "disk", **{"label": "Disk space", **disk}})

    # ── HF cache writable
    try:
        os.makedirs(cache, exist_ok=True)
        writable = os.access(cache, os.W_OK)
    except Exception:
        writable = False
    checks.append({
        "id": "hf_cache_writable", "label": "HuggingFace cache writable",
        "status": "pass" if writable else "fail",
        "detail": cache,
        "fix": None if writable else
            f"Fix write permissions on {cache} or point HF_HOME elsewhere.",
    })

    # ── Intel-Mac platform gate (#2365): PyTorch ships no macOS x86_64
    # wheels, so the dependency set can never resolve there (see #889 and
    # docs/install/macos.md). Fail the preflight BEFORE any multi-GB
    # download so first-run setup shows this guidance instead of a raw
    # uv resolver error. Local inference stays unsupported; the Electron
    # UI can use a remote backend on another supported machine.
    #
    # Return immediately: _media_summary(auto_acquire=True) below would
    # otherwise start a background acquisition on this blocked path.
    if sys.platform == "darwin" and arch == "x86_64":
        checks.append({
            "id": "platform", "label": "Platform support", "status": "fail",
            "detail": "Intel Macs can't run the local AI backend "
                      "(PyTorch ships no macOS x86_64 wheels).",
            "fix": "Use a remote backend on a supported machine "
                   "(Apple Silicon, NVIDIA, or CPU-only Linux/Windows) — "
                   "see docs/install/macos.md. Local setup is disabled "
                   "on this host.",
        })
        gpu = _detect_gpu()
        return {
            "ok": False,
            "has_warnings": False,
            "checks": checks,
            "device": {
                "os": sys.platform,
                "arch": arch,
                "gpu_vendor": gpu["vendor"],
                "gpu_backend": gpu["backend"],
                "gpu_available": gpu["available"],
                "gpu_driver": gpu["driver"],
                "gpu_device_name": gpu["device_name"],
                "gpu_family": "cpu",
                "vram_gb": 0.0,
                "ram_gb": round(ram, 1),
                "disk_free_gb": round(free, 1),
            },
            "gpu_routing": None,
            "media_tools": None,
        }

    # ── Windows on ARM (Snapdragon X etc.): the Python runtime is the x64
    # build under Windows' emulation layer (PyTorch publishes no win_arm64
    # torchaudio/torchvision wheels), and no GPU/NPU is reachable from it.
    # Informational, never a blocker — the app is fully usable, just slower.
    if is_windows_on_arm():
        checks.append({
            "id": "arch", "label": "Windows on ARM", "status": "warn",
            "detail": "ARM64 Windows PC — the AI backend runs as x64 under "
                      "Windows emulation, CPU only (no GPU/NPU acceleration).",
            "fix": "Everything works but generation is slower. Prefer a "
                   "lightweight voice engine (KittenTTS, Supertonic-3, "
                   "PocketTTS) and a small Whisper model.",
        })

    # ── Media engine (ffmpeg/ffprobe/yt-dlp) — deliberately NOT a check row.
    # These are internal dependencies the app provisions for itself, not user
    # facts: when the resolution chain has no tier at all, preflight kicks the
    # bundled acquisition in the background and the wizard shows a quiet
    # progress line (a failure card only if that fails — with Retry / use a
    # system copy). yt-dlp is an importable locked module and never appears.
    # Power users manage all three in Settings → Audio tools.
    media_tools = None
    try:
        from services.media_tools import summary as _media_summary
        media_tools = _media_summary(auto_acquire=True)
    except Exception as exc:  # never break preflight on the media engine
        logger.warning("preflight media_tools summary failed: %s", exc)

    # ── GPU
    gpu = _detect_gpu()
    if gpu["vendor"] == "apple" and gpu["available"]:
        gpu_status, gpu_fix = "pass", None
        gpu_detail = f"{gpu['device_name']} — Metal (MPS) ready"
    elif gpu["vendor"] == "nvidia" and gpu["available"]:
        gpu_status, gpu_fix = "pass", None
        gpu_detail = f"{gpu['device_name']} (driver {gpu['driver']}) — CUDA ready"
    elif gpu["vendor"] == "nvidia" and not gpu["available"]:
        # Acceleration is optional: CPU wheels or an unusable driver must not
        # block CPU-capable engines. Active-engine compatibility is checked below.
        gpu_status = "warn"
        gpu_detail = (
            f"{gpu['device_name']} found but CUDA not usable "
            f"(driver {gpu['driver']}). " + " ".join(gpu["notes"])
        )
        gpu_fix = (
            f"Update NVIDIA drivers to ≥ R{'.'.join(map(str, _min_nvidia_driver()))} "
            "(https://www.nvidia.com/Download/index.aspx). Or run CPU-only "
            "by continuing past this step. CPU inference is slower; GPU-only "
            "engines remain unavailable."
        )
    elif gpu["vendor"] == "amd":
        gpu_status = "warn"
        gpu_detail = (
            f"{gpu['device_name']} — ROCm "
            + ("ready" if gpu["available"] else "not configured")
        )
        gpu_fix = (
            None if gpu["available"] else
            " ".join(gpu["notes"] or [_amd_unusable_note()])
            + " The app works on CPU otherwise (slower)."
        )
    elif gpu["available"]:
        # Fallback: torch.cuda works but nvidia-smi/rocm-smi absent (e.g. Docker)
        gpu_status, gpu_fix = "pass", None
        dev = gpu.get("device_name") or "GPU"
        gpu_detail = f"{dev} — CUDA ready (detected via PyTorch)"
        if gpu["notes"]:
            gpu_detail += f". {' '.join(gpu['notes'])}"
    else:
        gpu_status = "warn"
        gpu_detail = "No compatible GPU detected — running CPU-only."
        gpu_fix = (
            "Everything works on CPU, just ~10× slower than a GPU — integrated "
            "graphics are not used for AI. For the best CPU experience pick a "
            "lightweight voice engine (KittenTTS, Supertonic-3, PocketTTS) and a "
            "small Whisper model in the Model Catalogue. If you have an "
            "NVIDIA/AMD card, check drivers are installed."
        )
    checks.append({
        "id": "gpu", "label": "GPU acceleration",
        "status": gpu_status, "detail": gpu_detail, "fix": gpu_fix,
    })

    # ── GPU routing for the ACTIVE TTS engine (#21 — no silent CPU fallback).
    # Distinct from the hardware "gpu" check above: this asks "will the engine
    # the user actually selected use that GPU on this host?" Built from the same
    # canonical probe + resolver the Engine Compatibility Matrix uses.
    try:
        from services.tts_backend import gpu_routing_verdict
        gpu_routing = gpu_routing_verdict()
    except Exception as exc:  # never break preflight on a routing hiccup
        logger.warning("preflight gpu_routing failed: %s", exc)
        gpu_routing = None
    if gpu_routing:
        _rs = gpu_routing.get("routing_status")
        _eng = gpu_routing.get("engine") or "active engine"
        _dev = gpu_routing.get("effective_device") or "?"
        _why = gpu_routing.get("routing_reason")
        if _rs == "accelerated" and not _why:
            r_status, r_detail, r_fix = "pass", f"{_eng} → {_dev} (accelerated)", None
        elif _rs == "accelerated" and KERNEL_RISK_MARKER in (_why or ""):
            r_status, r_detail, r_fix = "warn", f"{_eng} → {_dev}: {_why}", (
                "GPU selected but may fail at kernel launch — update drivers / "
                "reinstall torch for this GPU architecture.")
        elif _rs == "accelerated":  # low-VRAM caveat — not a driver/arch issue
            r_status, r_detail, r_fix = "warn", f"{_eng} → {_dev}: {_why}", (
                "Unload other models before generating, keep the text short, "
                "or pick a lighter engine.")
        elif _rs == "cpu_fallback":
            r_status, r_detail, r_fix = "warn", (
                f"{_eng} runs on CPU here: {_why or 'no GPU path for this host'}"), (
                "Pick an engine that supports this host's GPU for a speedup, or "
                "continue on CPU (slower).")
        elif _rs == "cpu_only":
            r_status, r_detail, r_fix = "pass", f"{_eng} → cpu (no accelerator on this host)", None
        elif _rs == "unavailable":
            r_status, r_detail, r_fix = "fail", (
                f"{_eng} can't run on this host: {_why or 'needs a GPU this machine lacks'}"), (
                "Select an engine with a CPU path in Model Catalogue.")
        else:  # "none" / unknown
            r_status, r_detail, r_fix = "warn", "No active TTS engine resolved for routing.", (
                "Pick an engine in Model Catalogue.")
        checks.append({
            "id": "gpu_routing", "label": "Active engine routing",
            "status": r_status, "detail": r_detail, "fix": r_fix,
        })

    # ── Network — a dead network is a WARNING, not a blocker. The app is
    # local-first: already-downloaded models work offline, and a hard fail
    # here dead-ends restricted-network users (e.g. China, where
    # huggingface.co is blocked) on the very first screen — before they can
    # reach the mirror setting that fixes it. Model downloads surface their
    # own actionable errors.
    #
    # With NO explicit endpoint configured, preflight runs the automatic
    # endpoint race (services.endpoint_race): both the official endpoint and
    # the community mirror are probed, the winner is cached for downloads,
    # and the copy states the outcome honestly — so a blocked huggingface.co
    # no longer needs the user to find the mirror setting at all. An explicit
    # endpoint (Settings / HF_ENDPOINT / pref) keeps the single-endpoint
    # probe: the user's choice is never auto-switched.
    checks.append(_network_check())

    # Aggregate
    any_fail = any(c["status"] == "fail" for c in checks)
    any_warn = any(c["status"] == "warn" for c in checks)

    return {
        "ok": not any_fail,
        "has_warnings": any_warn,
        "checks": checks,
        "device": {
            "os": sys.platform,
            "arch": arch,
            "gpu_vendor": gpu["vendor"],
            "gpu_backend": gpu["backend"],
            "gpu_available": gpu["available"],
            "gpu_driver": gpu["driver"],
            "gpu_device_name": gpu["device_name"],
            # Canonical probe (distinguishes ROCm from CUDA):
            "gpu_family": (gpu_routing or {}).get("host_family", "cpu"),
            "vram_gb": (gpu_routing or {}).get("vram_gb", 0.0),
            "ram_gb": round(ram, 1),
            "disk_free_gb": round(free, 1),
        },
        "gpu_routing": gpu_routing,
        "media_tools": media_tools,
    }


# ── Warmup ─────────────────────────────────────────────────────────────────

@router.post("/setup/warmup")
async def setup_warmup():
    """Trigger a model load in the background so the first dub doesn't pay
    the cold-start tax."""
    loop = asyncio.get_running_loop()

    async def _do_warmup():
        try:
            from services.model_manager import get_model
            await get_model()
        except Exception as e:
            logger.warning("setup/warmup: model load failed: %s", e)

    loop.create_task(_do_warmup())
    return {"status": "warmup_started"}
