"""Resolve health labels without treating native timeout policy as device proof."""
from collections.abc import Callable
from logging import Logger


def _unverified_native_cpu(engine_id: str | None, profile: dict) -> str | None:
    """Keep unknown native execution distinct from explicit CPU selection."""
    if profile.get("runtime_device_verified") is not False or profile.get("runtime_backend"):
        return None
    if engine_id == "omnivoice-gguf":
        try:
            from engines.omnivoice_gguf.compute import cpu_selection_explicit

            if cpu_selection_explicit():
                return "cpu"
        except Exception:  # noqa: BLE001 - an invalid native policy cannot prove a device.
            pass
    return "unknown"


def probe_device(engine_id: str | None, torch_label: Callable[[], str], logger: Logger) -> str:
    """Use engine execution evidence, preserving the existing parent-Torch fallback."""
    try:
        from core.device_caps import detect_host_caps
        from services.engine_routing import runtime_compute_profile
        from services.tts_backend import get_backend_class

        caps = detect_host_caps()
        profile = runtime_compute_profile(get_backend_class(engine_id), caps)
        if profile.get("routing_status") != "unavailable":
            family = str(profile.get("effective_device") or "cpu")
            if family == "cpu":
                return _unverified_native_cpu(engine_id, profile) or "cpu"
            # HostCaps retains a discovery name that may belong to another GPU.
            host_name_matches = family == caps.family and all(
                detected in (family, "cpu") for detected in caps.available_families
            )
            name = profile.get("runtime_device_name") or (
                caps.device_name if host_name_matches else ""
            )
            return f"{family} ({name})" if name else family
    except Exception as exc:  # noqa: BLE001 - preserve the established fallback.
        logger.debug("health: engine compute profile unavailable (%s)", type(exc).__name__)
    return torch_label()
