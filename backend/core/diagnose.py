"""Self-check diagnostics — answers "why doesn't it work on my machine?"

One pass over everything a working install needs: Python, compute device,
ffmpeg, HF token, disk, data-dir permissions, RAM, TTS engines, and (when
requested) network reachability of the HuggingFace hub. Surfaced two ways:

  - ``GET /system/diagnose`` (Settings > About -> "Run self-check")
  - ``python main.py --diagnose`` for headless installs / issue triage

Every ``detail``/``hint`` string is passed through ``core.scrub`` before it
leaves this module, so the report is safe to paste straight into a GitHub
issue — that's its whole purpose.

Check shape:

    {"id": str, "label": str, "status": "ok"|"warn"|"fail",
     "detail": str, "hint": Optional[str]}

``fail`` = the app cannot do its job (no disk, unwritable data dir).
``warn`` = degraded but usable (CPU-only, no HF token, hub unreachable).
"""
from __future__ import annotations

import importlib
import os
import platform
import shutil
import sys

from core.config import DATA_DIR
from core.device_caps import KERNEL_RISK_MARKER, UNUSABLE_GPU_MARKER
from core.scrub import scrub_text
from core.version import APP_VERSION

OK = "ok"
WARN = "warn"
FAIL = "fail"

# Below this much free disk the model cache can't even hold one engine.
_DISK_FAIL_GB = 2
_DISK_WARN_GB = 10
_RAM_WARN_GB = 8

_HUB_URL = "https://huggingface.co"
_HUB_TIMEOUT_S = 5


def _check(check_id: str, label: str, status: str, detail: str, hint: str | None = None) -> dict:
    return {
        "id": check_id,
        "label": label,
        "status": status,
        "detail": scrub_text(detail),
        "hint": scrub_text(hint) if hint else None,
    }


def _check_python() -> dict:
    return _check(
        "python", "Python runtime", OK,
        f"{sys.version.split()[0]} on {platform.platform()}",
    )


def _check_device() -> dict:
    try:
        from services.model_manager import get_best_device
        device = get_best_device()
    except Exception as e:
        return _check(
            "device", "Compute device", FAIL,
            f"device detection failed: {e}",
            "Reinstall may be needed - torch could not initialize.",
        )
    gpu_name = ""
    try:
        import torch
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
    except Exception:
        pass
    if device == "cpu":
        # A GPU the OS can see but this PyTorch build cannot drive is not "no
        # GPU": say which card and why, so the report is actionable.
        try:
            from core.device_caps import detect_host_caps

            unusable = next(
                (n for n in detect_host_caps().notes if UNUSABLE_GPU_MARKER in n), None,
            )
        except Exception:
            unusable = None
        if unusable:
            return _check(
                "device", "Compute device", WARN,
                f"cpu - {unusable}",
                "Settings > Performance > GPU acceleration lists which engines "
                "can use this GPU on this machine, and what would change that.",
            )
        return _check(
            "device", "Compute device", WARN,
            "cpu (no GPU acceleration detected)",
            "Generation will be slow. If this machine has a GPU, check CUDA/ROCm drivers (Linux/Windows) or that you're on Apple Silicon (macOS).",
        )
    detail = f"{device} ({gpu_name})" if gpu_name else device
    return _check("device", "Compute device", OK, detail)


def _check_ffmpeg() -> dict:
    """Media engine (ffmpeg + ffprobe) — an internal dependency the app
    bundles/acquires itself, so a failure here means the self-heal also has
    nothing to work with (and the hint says where the controls live)."""
    ffmpeg = ffprobe = None
    try:
        from services.ffmpeg_utils import find_ffmpeg, find_ffprobe
        ffmpeg = find_ffmpeg()
        ffprobe = find_ffprobe()
    except Exception:
        pass
    if ffmpeg and ffprobe:
        return _check("ffmpeg", "Media engine (ffmpeg)", OK,
                      f"ffmpeg: {ffmpeg}; ffprobe: {ffprobe}")
    if ffmpeg:
        return _check(
            "ffmpeg", "Media engine (ffmpeg)", WARN,
            f"ffmpeg: {ffmpeg}; ffprobe missing",
            "Media probing (Smart Fit, file inspection) is degraded. Open "
            "Settings > Audio tools and press Restore bundled to fetch the "
            "app's own ffprobe, or point it at a system copy there.",
        )
    return _check(
        "ffmpeg", "Media engine (ffmpeg)", FAIL,
        "no runnable ffmpeg in any tier (sidecar, bundled, system, custom)",
        "Dubbing and audio conversion are unavailable. The app normally "
        "provisions ffmpeg itself — open Settings > Audio tools and press "
        "Restore bundled (needs network once), or choose a system copy there.",
    )


def _check_hf_token() -> dict:
    # Presence only — the resolver never hands us the raw token and we
    # wouldn't print it anyway.
    try:
        from services import token_resolver
        present = token_resolver.resolve() is not None
    except Exception:
        present = False
    if present:
        return _check("hf_token", "HuggingFace token", OK, "configured")
    return _check(
        "hf_token", "HuggingFace token", WARN,
        "not set",
        "Downloads may be rate-limited and speaker diarization won't work. Set one in Settings > Credentials.",
    )


def _check_disk() -> dict:
    try:
        usage = shutil.disk_usage(DATA_DIR)
    except Exception as e:
        return _check("disk", "Disk space", WARN, f"could not stat {DATA_DIR}: {e}")
    free_gb = usage.free / (1024 ** 3)
    detail = f"{free_gb:.1f} GB free at {DATA_DIR}"
    if free_gb < _DISK_FAIL_GB:
        return _check(
            "disk", "Disk space", FAIL, detail,
            "Model downloads need several GB. Free up space or move OMNIVOICE_DATA_DIR to a larger volume.",
        )
    if free_gb < _DISK_WARN_GB:
        return _check(
            "disk", "Disk space", WARN, detail,
            "Engine model downloads can be 1-4 GB each; you may run out mid-download.",
        )
    return _check("disk", "Disk space", OK, detail)


def _check_data_dir() -> dict:
    probe = os.path.join(DATA_DIR, ".diagnose_write_probe")
    try:
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return _check("data_dir", "Data directory", OK, f"writable: {DATA_DIR}")
    except Exception as e:
        return _check(
            "data_dir", "Data directory", FAIL,
            f"not writable: {DATA_DIR} ({e})",
            "Voices, projects, and logs all live here. Fix permissions or point OMNIVOICE_DATA_DIR somewhere writable.",
        )


def _check_ram() -> dict:
    try:
        import psutil
        total_gb = psutil.virtual_memory().total / (1024 ** 3)
    except Exception as e:
        return _check("ram", "System memory", WARN, f"could not read: {e}")
    detail = f"{total_gb:.1f} GB total"
    if total_gb < _RAM_WARN_GB:
        return _check(
            "ram", "System memory", WARN, detail,
            "Large engines may swap or OOM below 8 GB. Prefer lighter engines and close other apps while generating.",
        )
    return _check("ram", "System memory", OK, detail)


def _check_engines() -> dict:
    try:
        from services.tts_backend import list_backends, active_backend_id
        backends = list_backends(include_hidden=True)
        active = active_backend_id()
    except Exception as e:
        return _check("engines", "TTS engines", WARN, f"could not enumerate: {e}")
    available = [b["id"] for b in backends if b.get("available")]
    detail = f"active: {active}; available: {', '.join(available) or 'none'}"
    active_row = next((b for b in backends if b.get("id") == active), None)
    if active_row is not None and not active_row.get("available"):
        reason = active_row.get("reason") or "unavailable"
        return _check(
            "engines", "TTS engines", FAIL,
            f"{detail} - active engine '{active}' is unavailable: {reason}",
            active_row.get("install_hint") or "Pick a different engine in Model Catalogue > Engines.",
        )
    if not available:
        return _check(
            "engines", "TTS engines", FAIL, detail,
            "No usable TTS engine. Install one from Model Catalogue > Engines.",
        )
    return _check("engines", "TTS engines", OK, detail)


def _check_gpu_routing() -> dict:
    """Routing verdict for the active TTS engine on THIS host (#21).

    Surfaces a CPU fallback / unavailable-GPU *before* a slow or failed synth —
    the no-silent-fallback contract. `cpu_only` on a no-GPU machine is the
    expected normal state and stays OK (never noise-warns)."""
    try:
        from services.tts_backend import gpu_routing_verdict
        v = gpu_routing_verdict()
    except Exception as e:
        return _check("gpu_routing", "GPU routing", WARN, f"could not resolve: {e}")

    status = v.get("routing_status")
    engine = v.get("engine") or "active engine"
    dev = v.get("effective_device") or "?"
    reason = v.get("routing_reason")
    host = v.get("host_family", "cpu")

    if status == "accelerated":
        if reason and KERNEL_RISK_MARKER in reason:  # driver/arch caveat — at risk
            return _check("gpu_routing", "GPU routing", WARN,
                          f"{engine} -> {dev}: {reason}",
                          "The GPU is selected but may fail at kernel launch — "
                          "update drivers / reinstall torch for this GPU arch.")
        if reason:  # low-VRAM caveat — not a driver/arch issue
            return _check("gpu_routing", "GPU routing", WARN,
                          f"{engine} -> {dev}: {reason}",
                          "Unload other models before generating, keep the text "
                          "short, or pick a lighter engine.")
        return _check("gpu_routing", "GPU routing", OK, f"{engine} -> {dev} (accelerated)")
    if status == "cpu_fallback":
        return _check("gpu_routing", "GPU routing", WARN,
                      f"{engine} runs on CPU: {reason or 'no GPU path for this host'}",
                      "Pick an engine that supports this host's GPU for a big speedup, "
                      "or continue on CPU (slower).")
    if status == "cpu_only":
        return _check("gpu_routing", "GPU routing", OK,
                      f"{engine} -> cpu (no accelerator on this host)")
    if status == "unavailable":
        return _check("gpu_routing", "GPU routing", FAIL,
                      f"{engine} can't run on this host: {reason or f'needs a GPU; host is {host}'}",
                      "Select an engine with a CPU path in Settings -> Engines.")
    # status == "none" / unknown — no active engine resolved.
    return _check("gpu_routing", "GPU routing", WARN,
                  "No active TTS engine resolved for routing.",
                  "Pick an engine in Settings -> Engines.")


_DEEP_TIMEOUT_S = 180


def _check_deep_synthesis() -> dict:
    """Actually load the active engine and synthesize a short utterance.

    Catches "installed but broken" — the most common issue category — which
    the presence checks above can't see. Opt-in only (?deep=true / --deep):
    it may cold-load the model (minutes + a multi-GB download on a fresh
    install), so it must never run on a casual Settings-page self-check.
    """
    try:
        from services.model_manager import get_model_status
        if get_model_status().get("status") == "loading":
            return _check(
                "deep_synth", "Deep synthesis", WARN,
                "skipped - a model load is already in progress",
                "Re-run once the current load finishes.",
            )
    except Exception:
        pass

    import concurrent.futures
    import time as _time

    def _synth():
        import services.model_manager as mm
        from services.tts_backend import get_active_tts_backend, active_backend_id
        backend = get_active_tts_backend(model=mm.model)
        wav = backend.generate("Diagnostics check, one two three.", num_step=4)
        return active_backend_id(), int(wav.shape[-1]) / max(1, backend.sample_rate)

    t0 = _time.perf_counter()
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        engine_id, audio_s = ex.submit(_synth).result(timeout=_DEEP_TIMEOUT_S)
    except concurrent.futures.TimeoutError:
        return _check(
            "deep_synth", "Deep synthesis", FAIL,
            f"timed out after {_DEEP_TIMEOUT_S}s - engine load or synthesis hung",
            "If this is a first run, the model may still be downloading - retry later. Otherwise check the backend log for where it stalled.",
        )
    except Exception as e:
        return _check(
            "deep_synth", "Deep synthesis", FAIL,
            f"active engine failed: {type(e).__name__}: {e}",
            "The engine is installed but not producing audio. The error above is the lead; Settings > Logs has the full trace.",
        )
    finally:
        # Never block the report on a hung worker; the thread is left to
        # finish (or hang) on its own — the timeout verdict already shipped.
        ex.shutdown(wait=False)
    elapsed = _time.perf_counter() - t0
    if audio_s <= 0:
        return _check(
            "deep_synth", "Deep synthesis", FAIL,
            f"engine '{engine_id}' returned empty audio in {elapsed:.1f}s",
            "Synthesis ran but produced no samples - engine output is broken.",
        )
    return _check(
        "deep_synth", "Deep synthesis", OK,
        f"engine '{engine_id}' produced {audio_s:.1f}s of audio in {elapsed:.1f}s",
    )


def _check_network() -> dict:
    # Any HTTP response — even a 4xx — proves the hub is reachable; that's
    # all model downloads need to get started. urllib honors HTTP(S)_PROXY.
    import urllib.request
    import urllib.error
    if not _HUB_URL.startswith("https://"):  # constant today; guard the sink anyway
        raise ValueError(f"hub URL must be https, got {_HUB_URL!r}")
    req = urllib.request.Request(_HUB_URL, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=_HUB_TIMEOUT_S):
            pass
        return _check("network", "HuggingFace hub", OK, f"{_HUB_URL} reachable")
    except urllib.error.HTTPError:
        return _check("network", "HuggingFace hub", OK, f"{_HUB_URL} reachable")
    except Exception as e:
        return _check(
            "network", "HuggingFace hub", WARN,
            f"{_HUB_URL} unreachable: {e}",
            "Model downloads will fail until this resolves. Behind a restricted network, set a proxy in Settings > General or configure a mirror via HF_ENDPOINT.",
        )


def run_diagnostics(include_network: bool = True, deep: bool = False) -> dict:
    """Run every check and return the structured report.

    ``include_network=False`` skips the hub probe — used by tests and by
    callers that need the report to come back instantly offline.
    ``deep=True`` additionally loads the active engine and synthesizes a
    short utterance (may take minutes on a cold install — opt-in only).
    """
    checks = [
        _check_python(),
        _check_device(),
        _check_ffmpeg(),
        _check_hf_token(),
        _check_disk(),
        _check_data_dir(),
        _check_ram(),
        _check_engines(),
        _check_gpu_routing(),
    ]
    if include_network:
        checks.append(_check_network())
    if deep:
        checks.append(_check_deep_synthesis())

    counts = {OK: 0, WARN: 0, FAIL: 0}
    for c in checks:
        counts[c["status"]] += 1
    engine_execution = []
    for family in ("tts", "asr"):
        active = "unknown"
        try:
            module = importlib.import_module(f"services.{family}_backend")
            active = module.active_backend_id()
            rows = (
                module.list_backends(include_hidden=True)
                if family == "tts"
                else module.list_backends()
            )
            row = next((item for item in rows if item.get("id") == active), None)
            if row is not None:
                engine_execution.append({
                    "family": family,
                    "engine_id": active,
                    **row["execution_evidence"],
                })
        except Exception:  # noqa: BLE001 - evidence must not break diagnostics
            # Preserve the other family's successful evidence and make this
            # collection failure explicit without exposing exception text.
            engine_execution.append({
                "family": family,
                "engine_id": active,
                "implementation_variant": None,
                "declared_device_families": [],
                "evidence_state": "collection_failed",
                "actual_execution_provider": None,
                "actual_execution_device": None,
                "gpu_name": None,
                "gpu_architecture": None,
                "precision_or_quantization": None,
                "cpu_fallback_reason": None,
                "cpu_fallback_stage": None,
                "parent_memory_observable": None,
                "runtime_versions": {},
            })

    return {
        "app_version": APP_VERSION,
        "platform": scrub_text(platform.platform()),
        "checks": checks,
        "engine_execution": engine_execution,
        "summary": {
            "ok": counts[FAIL] == 0,
            "passed": counts[OK],
            "warnings": counts[WARN],
            "failures": counts[FAIL],
        },
    }


def format_text(report: dict) -> str:
    """Human-readable rendering for `--diagnose` / pasting into an issue.

    ASCII-only on purpose — Windows consoles with legacy code pages must
    not choke on the output.
    """
    tag = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}
    lines = [
        f"VoiceStudio self-check - v{report['app_version']} on {report['platform']}",
        "",
    ]
    for c in report["checks"]:
        lines.append(f"{tag[c['status']]} {c['label']}: {c['detail']}")
        if c.get("hint"):
            lines.append(f"       hint: {c['hint']}")
    if report.get("engine_execution"):
        lines.append("")
        lines.append("Engine execution evidence:")
        for item in report["engine_execution"]:
            if item.get("actual_execution_provider"):
                provider = item["actual_execution_provider"]
            elif item.get("evidence_state") == "subprocess_loaded_provider_unreported":
                provider = "loaded child; provider not reported"
            else:
                provider = "not loaded"
            precision = item.get("precision_or_quantization") or "unknown"
            device = item.get("actual_execution_device") or "unknown"
            gpu = item.get("gpu_name") or "none"
            architecture = item.get("gpu_architecture") or "unknown"
            fallback_stage = item.get("cpu_fallback_stage") or "none"
            fallback_reason = item.get("cpu_fallback_reason") or "none"
            versions = ",".join(
                f"{name}={version}"
                for name, version in sorted(item.get("runtime_versions", {}).items())
            ) or "none"
            visible = "yes" if item.get("parent_memory_observable") else "no"
            lines.append(
                f"  {item['family']}:{item['engine_id']} provider={provider}; "
                f"device={device}; gpu={gpu}; architecture={architecture}; "
                f"precision={precision}; fallback-stage={fallback_stage}; "
                f"fallback-reason={fallback_reason}; runtimes={versions}; "
                f"evidence-state={item.get('evidence_state', 'unknown')}; "
                f"parent-memory-visible={visible}"
            )
    s = report["summary"]
    lines.append("")
    lines.append(
        f"{s['passed']} ok, {s['warnings']} warning(s), {s['failures']} failure(s) - "
        + ("looks healthy" if s["ok"] else "needs attention")
    )
    return "\n".join(lines)
