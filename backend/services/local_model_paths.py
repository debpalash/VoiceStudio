"""Validated, opt-in local checkpoints. Never write into model directories."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from core import prefs
from core.path_authorization import LocalModelSelection

PREF_KEY = "engine_local_model_paths"
SUPPORTED = {"tts": {"omnivoice", "mlx-audio"}}
ENV_KEYS = {"omnivoice": "OMNIVOICE_MODEL", "mlx-audio": "OMNIVOICE_MLX_AUDIO_MODEL"}


class LocalModelError(ValueError):
    pass


class EngineBusyError(LocalModelError):
    pass


def supported(family: str, engine_id: str) -> bool:
    return engine_id in SUPPORTED.get(family, set())


DISCOVERY_MAX_ENTRIES = 2000
DISCOVERY_TIMEOUT_SECONDS = 2.0
DISCOVERY_MAX_METADATA_BYTES = 1024 * 1024


class _DiscoveryIncomplete(Exception):
    def __init__(self, code: str, path: Path, message: str):
        self.warning = {"code": code, "path": str(path), "message": message}


def _check_deadline(budget: dict | None, path: Path) -> None:
    if budget is not None and time.monotonic() >= budget["deadline"]:
        raise _DiscoveryIncomplete("time_limit", path, "Discovery time limit reached; choose a smaller folder")


def _scan(root: Path, budget: dict):
    """Stream bounded entries, with one lookahead to distinguish exact exhaustion."""
    _check_deadline(budget, root)
    with os.scandir(root) as entries:
        for entry in entries:
            _check_deadline(budget, root)
            if budget["scanned"] >= DISCOVERY_MAX_ENTRIES:
                raise _DiscoveryIncomplete("scan_limit", root, "Discovery entry limit reached; choose a smaller folder")
            budget["scanned"] += 1
            yield entry


def _safe_asset(path: Path) -> Path:
    if path.is_symlink() or path.parent.is_symlink():
        raise LocalModelError("Symlinked checkpoint assets are not supported; copy the actual files into the checkpoint folder and select it again")
    return path


def _metadata(root: Path, name: str = "config.json", *, budget: dict | None = None) -> dict[str, Any]:
    if budget is not None and root.is_symlink():
        raise _DiscoveryIncomplete("symlink_directory", root, "Symlinked asset directories are skipped; copy actual assets into the checkpoint folder")
    _safe_asset(root / name)
    try:
        _check_deadline(budget, root)
        if budget is None:
            text = (root / name).read_text(encoding="utf-8")
        else:
            with (root / name).open("rb") as stream:
                raw = stream.read(DISCOVERY_MAX_METADATA_BYTES + 1)
            _check_deadline(budget, root)
            if len(raw) > DISCOVERY_MAX_METADATA_BYTES:
                raise _DiscoveryIncomplete("read_limit", root / name, "Metadata exceeds the discovery read limit; inspect this checkpoint separately")
            text = raw.decode("utf-8")
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except (OSError, UnicodeError, ValueError) as exc:
        if budget is not None and isinstance(exc, OSError) and not isinstance(exc, FileNotFoundError):
            raise
        raise LocalModelError(f"The local model metadata file {name} is missing or invalid") from exc
    raise LocalModelError(f"The local model metadata file {name} must be an object")


def _has_weights(root: Path, *, budget: dict | None = None) -> bool:
    # Match files actually consumed by the loaders, not random nested assets.
    files = None
    if budget is not None:
        files = budget["files"].get(root)
        if files is None:
            files = [Path(entry.path) for entry in _scan(root, budget)]
    indexes = (list(root.glob("*.safetensors.index.json")) + list(root.glob("pytorch_model.bin.index.json"))
               if files is None else [p for p in files if p.name.endswith(".safetensors.index.json") or p.name == "pytorch_model.bin.index.json"])
    if indexes:
        for index in indexes:
            mapping = _metadata(root, index.name, budget=budget).get("weight_map")
            if not isinstance(mapping, dict) or not mapping:
                return False
            for name in mapping.values():
                _check_deadline(budget, root)
                if isinstance(name, str) and Path(name).name == name:
                    _safe_asset(root / name)
                if (not isinstance(name, str) or Path(name).name != name
                        or not (root / name).is_file()
                        or (root / name).stat().st_size <= 0):
                    return False
        return True
    weights = (p for pattern in ("*.safetensors", "pytorch_model.bin") for p in root.glob(pattern)) if files is None else (p for p in files if p.name.endswith(".safetensors") or p.name == "pytorch_model.bin")
    weights = [_safe_asset(p) for p in weights]
    return any(p.is_file() and p.stat().st_size > 0 for p in weights)


def validate(family: str, engine_id: str, raw_path: str, *, _budget: dict | None = None) -> str:
    if not supported(family, engine_id):
        raise LocalModelError(f"Local model folders are not supported for {family}/{engine_id}")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise LocalModelError("Choose a local model folder, or clear the selection")
    try:
        if isinstance(raw_path, LocalModelSelection):
            raw_path.check()
            path = Path(raw_path)
        else:
            path = Path(os.path.realpath(os.path.expanduser(raw_path)))
        if not path.is_dir():
            raise LocalModelError("The selected local model folder does not exist")
        metadata = _metadata(path, budget=_budget)
        if engine_id == "omnivoice":
            if metadata.get("model_type") != "omnivoice" or not isinstance(metadata.get("llm_config"), dict):
                raise LocalModelError("This folder is not an OmniVoice checkpoint (expected model_type=omnivoice and llm_config)")
            asset = "audio_tokenizer"
        else:
            # The GUI accepts the verified quantized Qwen VoiceDesign layout.
            # A repository name containing 'mlx' does not prove conversion.
            quant = metadata.get("quantization")
            if (metadata.get("model_type") != "qwen3_tts"
                    or metadata.get("tts_model_type") != "voice_design"
                    or not isinstance(quant, dict)
                    or quant.get("bits") not in (2, 3, 4, 5, 6, 8)
                    or not isinstance(quant.get("group_size"), int)
                    or quant["group_size"] <= 0):
                raise LocalModelError("Choose a quantized MLX Qwen3-TTS VoiceDesign checkpoint, not native Qwen or Base weights")
            asset = "speech_tokenizer"
        if not _has_weights(path, budget=_budget):
            raise LocalModelError("The selected local model folder has missing or unsupported weight files")
        if not _safe_asset(path / "tokenizer.json").is_file():
            raise LocalModelError("The local checkpoint is missing tokenizer.json")
        _metadata(path / asset, budget=_budget)
        if not _has_weights(path / asset, budget=_budget):
            raise LocalModelError(f"The local checkpoint is missing {asset} weights")
        if engine_id == "omnivoice" and not _safe_asset(path / asset / "preprocessor_config.json").is_file():
            raise LocalModelError("The audio tokenizer is missing preprocessor_config.json")
        return str(path)
    except OSError as exc:
        if _budget is not None:
            raise
        raise LocalModelError("The local model folder could not be read") from exc


def get(family: str, engine_id: str) -> str | None:
    value = prefs.get(PREF_KEY, {})
    path = value.get(f"{family}/{engine_id}") if isinstance(value, dict) else None
    return path if isinstance(path, str) and path else None


def _validation(family: str, engine_id: str, path: str | None, *, budget: dict | None = None) -> dict[str, str]:
    if not path:
        return {"status": "unconfigured", "code": "no_local_model", "message": "No local model folder is configured"}
    try:
        validate(family, engine_id, path, _budget=budget)
    except LocalModelError as exc:
        message = str(exc)
        code = "invalid_files"
        if "VoiceDesign" in message or "not an OmniVoice" in message:
            code = "incompatible_architecture"
        elif "metadata" in message or "config.json" in message:
            code = "invalid_metadata"
        elif "weight" in message or "tokenizer" in message or "preprocessor" in message:
            code = "missing_assets"
        return {"status": "invalid", "code": code, "message": message}
    return {"status": "valid", "code": "complete", "message": "Checkpoint files match this engine"}


def _identity(path: str | None, *, budget: dict | None = None) -> dict[str, str | None]:
    if not path:
        return {"model_name": None, "model_type": None, "checkpoint_type": None}
    try:
        metadata = _metadata(Path(path), budget=budget)
    except LocalModelError:
        return {"model_name": Path(path).name, "model_type": None, "checkpoint_type": None}
    return {
        "model_name": str(metadata.get("_name_or_path") or metadata.get("name") or Path(path).name),
        "model_type": str(metadata.get("model_type")) if metadata.get("model_type") is not None else None,
        "checkpoint_type": str(metadata.get("tts_model_type")) if metadata.get("tts_model_type") is not None else None,
    }


def _loading_state(engine_id: str) -> dict[str, str]:
    """Observe resident weights/identity only; constructing a backend is not loading."""
    import sys

    tts = sys.modules.get("services.tts_backend")
    if tts is None:
        return {"status": "not_loaded"}
    try:
        cls = tts.get_backend_class(engine_id)
        with tts._ENGINE_CACHE_LOCK:
            instance = tts._ENGINE_INSTANCES.get(cls)
            if instance is None and tts._active_instance_id == engine_id:
                instance = tts._active_instance
            if engine_id == "mlx-audio":
                if instance is None:
                    return {"status": "not_loaded"}
                if type(instance) is not tts.MLXAudioBackend:
                    return {"status": "unknown"}
                if instance._model is None:
                    return {"status": "not_loaded"}
                actual = instance._model_id
                expected = tts.MLXAudioBackend.configured_identity()
            else:
                # model_manager owns the actual native singleton; backend objects
                # may remain cached after it was unloaded, or before a lazy load.
                mm = sys.modules.get("services.model_manager")
                if cls is not tts.OmniVoiceBackend:
                    return {"status": "unknown" if instance is not None else "not_loaded"}
                model = getattr(mm, "model", None)
                if model is None:
                    return {"status": "not_loaded"}
                actual = getattr(model, "_voicestudio_checkpoint", None)
                expected = (os.environ.get("OMNIVOICE_MODEL", "").strip()
                            or get("tts", "omnivoice") or getattr(mm, "_DEFAULT_OMNIVOICE_CHECKPOINT", None))
            if not isinstance(actual, str) or not actual or not isinstance(expected, str) or not expected:
                return {"status": "unknown"}
            if os.path.isabs(actual) and os.path.isabs(expected):
                actual, expected = os.path.realpath(actual), os.path.realpath(expected)
            return {"status": "loaded" if actual == expected else "not_loaded"}
    except Exception:  # Optional engines/identity failures must not break Settings.
        return {"status": "unknown"}


def state(family: str, engine_id: str) -> dict:
    if not supported(family, engine_id):
        raise LocalModelError(f"Local model folders are not supported for {family}/{engine_id}")
    env_key = ENV_KEYS[engine_id]
    override = os.environ.get(env_key, "").strip()
    path = get(family, engine_id)
    return {"family": family, "engine_id": engine_id, "path": path,
            **_identity(path), "validation": _validation(family, engine_id, path),
            "loading": _loading_state(engine_id),
            "environment_override": env_key if override else None,
            "effective_override": override or None, "restart_required": False}


def discover(family: str, engine_id: str, parent: str, limit: int = 100) -> dict:
    """Read only the selected root/children, bounded across all checkpoint scans.

    The deadline is cooperative: an individual OS read/stat cannot be interrupted.
    Sort only the bounded output, never materialize an unbounded directory.
    """
    if not supported(family, engine_id):
        raise LocalModelError(f"Local model folders are not supported for {family}/{engine_id}")
    if not isinstance(parent, str) or not parent.strip():
        raise LocalModelError("Choose a parent folder to discover local checkpoints")
    root = Path(os.path.realpath(os.path.expanduser(parent)))
    if not root.is_dir():
        raise LocalModelError("The selected discovery folder does not exist; choose an existing readable folder")
    budget = {"scanned": 0, "deadline": time.monotonic() + DISCOVERY_TIMEOUT_SECONDS, "files": {}}
    candidates, warnings = [], []
    cap = min(max(limit, 1), 100)

    def inspect(entry: Path):
        if len(candidates) >= cap:
            raise _DiscoveryIncomplete("candidate_limit", root, "More checkpoints may exist; choose a smaller folder")
        candidate = {"name": entry.name, "path": str(entry), "model_name": entry.name,
                     "model_type": None, "checkpoint_type": None}
        try:
            # Probe permissions even when config.json is missing; no recursive search.
            budget["files"][entry] = [Path(e.path) for e in _scan(entry, budget)]
            candidate.update(_identity(str(entry), budget=budget))
            candidate["validation"] = _validation(family, engine_id, str(entry), budget=budget)
        except OSError as exc:
            code = "permission_denied" if isinstance(exc, PermissionError) else "read_error"
            message = "Folder cannot be read; check permissions and choose it again" if code == "permission_denied" else "Folder could not be read; check that it is still available and try again"
            warnings.append({"code": code, "path": str(entry), "message": message})
            candidate["validation"] = {"status": "invalid", "code": code, "message": message}
        except _DiscoveryIncomplete as exc:
            candidate["validation"] = {"status": "invalid", "code": exc.warning["code"], "message": exc.warning["message"]}
            candidates.append(candidate)
            raise
        candidates.append(candidate)

    try:
        _check_deadline(budget, root)
        if (root / "config.json").is_file():
            inspect(root)
        for entry in _scan(root, budget):
            try:
                if entry.is_dir(follow_symlinks=False):
                    inspect(Path(entry.path))
            except OSError as exc:
                warnings.append({"code": "permission_denied" if isinstance(exc, PermissionError) else "read_error",
                                 "path": entry.path, "message": "Entry could not be read; check permissions and try again"})
    except _DiscoveryIncomplete as exc:
        warnings.append(exc.warning)
    except PermissionError as exc:
        raise LocalModelError("The selected discovery folder cannot be read (permission denied); check permissions and choose it again") from exc
    except OSError as exc:
        raise LocalModelError("The selected discovery folder could not be read; choose an available folder and try again") from exc
    candidates.sort(key=lambda item: item["name"].casefold())
    return {"parent": str(root), "candidates": candidates, "partial": bool(warnings),
            "limited": bool(warnings), "warnings": warnings, "scanned_entries": budget["scanned"]}


def set_path(family: str, engine_id: str, path: str) -> str:
    validated = validate(family, engine_id, path)
    # Recheck after validation, immediately before persistence. This is not an
    # fd-atomic load: a same-user writer can still mutate files after this check.
    if isinstance(path, LocalModelSelection):
        path.check()
    prefs.update_mapping(PREF_KEY, {f"{family}/{engine_id}": validated})
    return validated


def clear(family: str, engine_id: str) -> None:
    current = prefs.get(PREF_KEY, {})
    if isinstance(current, dict):
        current = dict(current)
        current.pop(f"{family}/{engine_id}", None)
        prefs.set_(PREF_KEY, current)


def change(family: str, engine_id: str, path: str | None) -> dict:
    """Serialize mutation with engine leases; busy never persists or unloads.

    The OmniVoice asyncio model lock also excludes direct generation and cold
    loads; the synchronous API's event-loop wrapper holds it across this call.
    """
    from services import tts_backend as tts
    cls = tts.get_backend_class(engine_id)
    with tts._ENGINE_CACHE_LOCK:
        if tts._ENGINE_IN_USE.get(cls):
            raise EngineBusyError("This engine is busy; wait for generation to finish")
        if path is None:
            clear(family, engine_id)
        else:
            set_path(family, engine_id, path)
        # Only evict this engine, never an unrelated active engine.
        inst = tts._ENGINE_INSTANCES.pop(cls, None)
        active = tts._active_instance if tts._active_instance_id == engine_id else None
        if active is not None:
            tts.reset_active_backend()
        if inst is not None and inst is not active:
            tts._retire_engine(inst)
        if engine_id == "omnivoice":
            from services import model_manager
            model_manager.unload_shared_model()
    return state(family, engine_id)
