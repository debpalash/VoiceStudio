"""One offline availability query, isolated from the API's model imports."""
import importlib
import json
from pathlib import Path
import sys

# Direct script execution keeps this worker out of the parent's import graph.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _asr_ready(asr, cls, probe=None):
    """Retain normal preflight and prove metadata-only CT2 probes can import."""
    if cls.id == "pytorch-whisper":
        from services.performance_readiness import inventory_only
        from services.pytorch_whisper_options import pipeline_import_ready

        return bool(inventory_only(probe or asr._probe_available)(cls) and pipeline_import_ready())
    if not (probe or asr._probe_available)(cls):
        return False
    if cls.id in {"faster-whisper", "faster-whisper-isolated"}:
        from core.windows_rocm.isolation_policy import isolation_required

        if isolation_required():
            from services.performance_probe import isolated_asr_import_ready

            return isolated_asr_import_ready()
        from core.windows_rocm.ct2 import prepare_import

        prepare_import()
        importlib.import_module("faster_whisper")
    return True


def check(family, engine, broken=()):
    """Import only the requested runtime, without constructing user models."""
    from core.windows_rocm.runtime import initialize_dll_search

    initialize_dll_search()
    if family == "asr-import":
        if engine == "pytorch-whisper":
            from services.pytorch_whisper_options import pipeline_import_ready

            return engine, pipeline_import_ready()
        if engine != "faster-whisper":
            return engine, False
        module = importlib.import_module("faster_whisper")
        return engine, callable(getattr(module, "WhisperModel", None))
    if family == "asr":
        from services import asr_backend as asr

        asr._DEEP_IMPORT_BROKEN.update({key: "parent preflight failed" for key in broken})
        if engine == "auto":
            original = asr._probe_available

            def native_probe(cls):
                """Apply native import checks within the normal hardware order."""
                try:
                    return _asr_ready(asr, cls, original)
                except Exception:
                    return False

            asr._probe_available = native_probe
            try:
                selected = asr._auto_detect()
                cls = asr._REGISTRY[selected] if selected in asr._REGISTRY else None
                return selected, bool(cls and native_probe(cls))
            finally:
                asr._probe_available = original
        cls = asr._REGISTRY[engine] if engine in asr._REGISTRY else None
        return engine, bool(cls and _asr_ready(asr, cls))
    if family == "tts":
        from services.tts_backend import get_backend_class

        return engine, bool(get_backend_class(engine).is_available()[0])
    if family == "translation":
        from services.translation_engines import is_ready

        return engine, bool(is_ready(engine, import_check=True))
    if family == "dictation":
        from services.sherpa_dictation import sherpa_available

        return engine, bool(sherpa_available()[0])
    return engine, False


def main():
    """Write a small identity-bound response after the native checks finish."""
    family, requested, broken_json, destination = sys.argv[1:]
    engine, ready = requested, False
    try:
        broken = json.loads(broken_json)
        if not isinstance(broken, list) or not all(isinstance(key, str) for key in broken):
            raise ValueError("Invalid failed-runtime inventory")
        engine, ready = check(family, requested, broken)
    except Exception:
        # No readiness proof was produced; the response remains unavailable.
        pass
    Path(destination).write_text(json.dumps({"schema": 1, "family": family,
        "requested": requested, "engine": engine, "ready": ready}), encoding="utf-8")


if __name__ == "__main__":
    main()
