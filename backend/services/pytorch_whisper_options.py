"""Whisper requests must respect the loaded checkpoint's actual language support."""


def dependency_status():
    """Inventory is shallow; runtime readiness must prove the lazy pipeline import."""
    from services.performance_readiness import inventory_active, package_available

    if not package_available("transformers"):
        return False, "transformers is not installed"
    if inventory_active():
        return True, "ready"
    from services.performance_probe import isolated_asr_import_ready

    if not isolated_asr_import_ready("pytorch-whisper"):
        return False, "Transformers pipeline import could not be verified in its isolated process"
    return True, "ready"


def pipeline_import_ready():
    """Resolve Transformers' lazy pipeline in a child without loading weights."""
    from transformers import pipeline

    return callable(pipeline)


def english_only(pipe):
    """Recognize an English-only checkpoint from its loaded generation config."""
    model = getattr(pipe, "model", None)
    configuration = getattr(model, "generation_config", None)
    return getattr(configuration, "is_multilingual", None) is False


def generation_options(pipe, options):
    """English-only generation rejects even redundant English/task arguments."""
    if not english_only(pipe):
        return options
    if options.get("task", "transcribe") != "transcribe":
        raise ValueError("English-only Whisper checkpoints cannot translate; select a multilingual checkpoint")
    language = options.get("language")
    if language is not None and str(language).strip().lower() not in {"en", "english", "<|en|>"}:
        raise ValueError("English-only Whisper checkpoints only transcribe English; select a multilingual checkpoint")
    return {key: value for key, value in options.items() if key not in {"language", "task"}}
