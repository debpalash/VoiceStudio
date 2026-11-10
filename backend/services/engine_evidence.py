"""Sanitized, reproducible execution evidence for TTS and ASR engines."""
from __future__ import annotations

import importlib.metadata
import platform
from typing import Any


def _version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _value(instance: object, *names: str) -> str | None:
    for name in names:
        try:
            value = getattr(instance, name, None)
            if value is not None and not callable(value):
                text = str(value).strip()
                if text and len(text) <= 80 and "/" not in text and "\\" not in text:
                    return text
        except Exception:
            continue
    return None


def runtime_versions(engine_id: str) -> dict[str, str]:
    """Relevant installed library versions, never paths or environment values."""
    names = {"python": platform.python_version()}
    candidates = ["torch"]
    low = engine_id.lower()
    if "faster" in low or "whisperx" in low:
        candidates.extend(["ctranslate2", "faster-whisper"])
    if "sherpa" in low or "moonshine" in low:
        candidates.append("onnxruntime")
    if "mlx" in low:
        candidates.append("mlx")
    for name in candidates:
        if (version := _version(name)) is not None:
            names[name] = version
    return names


def snapshot(
    *,
    engine_id: str,
    engine_cls: type,
    instance: object | None,
    routing: dict[str, Any],
    caps: object,
) -> dict[str, Any]:
    """Return fixed-shape evidence; actual fields stay null until an instance loads."""
    isolated = bool(
        getattr(engine_cls, "_is_subprocess_isolated", False)
        or getattr(engine_cls, "runs_out_of_process", False)
        or getattr(instance, "runs_out_of_process", False)
        or getattr(engine_cls, "runtime_runs_out_of_process", lambda: False)()
    )
    loaded = False
    probe_failed = False
    if instance is not None:
        try:
            contract = getattr(instance, "execution_evidence_loaded", False)
            loaded = bool(contract() if callable(contract) else contract)
        except Exception:  # noqa: BLE001 - third-party lifecycle descriptors may raise
            probe_failed = True

    actual_device = None
    provider = None
    precision = None
    if loaded:
        actual_device = _value(instance, "_device", "device", "execution_device")
        provider = _value(instance, "_provider", "provider", "execution_provider")
        precision = _value(
            instance, "_compute_type", "compute_type", "_dtype", "dtype", "quantization"
        )
        if provider is None and actual_device is not None:
            provider = actual_device

    runtime_fallback_reason = _value(instance, "_fallback_reason", "fallback_reason") if loaded else None
    runtime_fallback_stage = _value(instance, "_fallback_stage", "fallback_stage") if loaded else None
    status = routing.get("routing_status")
    fallback = status == "cpu_fallback" or runtime_fallback_reason is not None
    evidence_state = "not_loaded"
    if probe_failed:
        evidence_state = "probe_error"
    elif loaded:
        evidence_state = "loaded"
        if isolated and provider is None and actual_device is None:
            evidence_state = "subprocess_loaded_provider_unreported"
    from core.scrub import scrub_text

    runtime_device_name = routing.get("runtime_device_name")
    device_name = (
        getattr(caps, "device_name", "")
        if runtime_device_name is None
        else runtime_device_name
    )
    public_device_name = scrub_text(device_name)[:256]
    return {
        "implementation_variant": f"{engine_cls.__module__}.{engine_cls.__name__}",
        "declared_device_families": list(
            routing.get("gpu_compat", getattr(engine_cls, "gpu_compat", ("cpu",)))
        ),
        "evidence_state": evidence_state,
        "actual_execution_provider": provider,
        "actual_execution_device": actual_device,
        "gpu_name": public_device_name or None,
        "gpu_architecture": None
        if (
            routing.get("runtime_hardware_family")
            and not routing.get("runtime_device_verified")
        )
        else _gpu_architecture(
            routing.get("runtime_hardware_family")
            or getattr(caps, "family", "cpu")
        ),
        "runtime_vram_gb": routing.get("runtime_vram_gb"),
        "precision_or_quantization": precision,
        "cpu_fallback_reason": runtime_fallback_reason or (routing.get("routing_reason") if fallback else None),
        "cpu_fallback_stage": runtime_fallback_stage or ("routing_preflight" if fallback else None),
        "parent_memory_observable": not isolated,
        "runtime_versions": runtime_versions(engine_id),
    }


def _gpu_architecture(family: str) -> str | None:
    if family not in {"cuda", "rocm"}:
        return "apple-silicon" if family == "mps" else None
    try:
        import torch

        if family == "rocm":
            props = torch.cuda.get_device_properties(0)
            return str(getattr(props, "gcnArchName", "") or "") or None
        major, minor = torch.cuda.get_device_capability(0)
        return f"sm_{major}{minor}"
    except Exception:
        return None
