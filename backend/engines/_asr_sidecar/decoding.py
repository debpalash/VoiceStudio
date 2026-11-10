"""Validated ASR wire options and lossless faster-whisper result normalization."""


def request_options(request):
    """Reject malformed or unknown per-request Whisper decoding settings."""
    options = request or {}
    if not isinstance(options, dict):
        raise ValueError("Invalid ASR request options")
    for key, value in options.items():
        if key == "language":
            ok = isinstance(value, str) and 0 < len(value) <= 32
        elif key == "initial_prompt":
            ok = isinstance(value, str) and len(value) <= 4096
        elif key == "temperature":
            ok = type(value) in (int, float) and 0 <= value <= 1
        elif key == "task":
            ok = value in ("transcribe", "translate")
        else:
            ok = False
        if not ok:
            raise ValueError("Invalid ASR request options")
    return options


def validate_configuration(configuration):
    """Validate the exact model, VAD and device contract sent by the proxy."""
    if configuration is None:
        return None
    if (not isinstance(configuration, dict) or set(configuration) != {"model", "vad_filter", "device"}
            or not isinstance(configuration.get("model"), str)
            or not 0 < len(configuration["model"]) <= 32768
            or "\x00" in configuration["model"]
            or type(configuration.get("vad_filter")) is not bool
            or configuration.get("device") not in {"cpu", "cuda"}):
        raise ValueError("Invalid ASR model configuration")
    return configuration


def transcribe(get_model, audio_path, word_timestamps, decode_options=None,
               request=None, configuration=None):
    """Run validated decoding and retain the normal backend's result fields."""
    options = decode_options or {}
    if not isinstance(options, dict) or any(
        key not in {"beam_size", "best_of"} or type(value) is not int or not 1 <= value <= 8
        for key, value in options.items()
    ):
        raise ValueError("Invalid ASR decoding options")
    extra = request_options(request)
    configuration = validate_configuration(configuration)
    model = get_model(configuration) if configuration is not None else get_model()
    if configuration is not None:
        extra["vad_filter"] = configuration["vad_filter"]
    segments, info = model.transcribe(
        audio_path, word_timestamps=word_timestamps, condition_on_previous_text=False,
        **options, **extra,
    )
    out = []
    for segment in segments:
        part = {"start": float(segment.start), "end": float(segment.end), "text": segment.text}
        if configuration is not None or (word_timestamps and getattr(segment, "words", None)):
            part["words"] = [
                {"word": word.word, "start": float(word.start), "end": float(word.end),
                 "probability": float(getattr(word, "probability", 0.0))}
                for word in (getattr(segment, "words", None) or [])
            ] if word_timestamps else []
        out.append(part)
    result = {"segments": out, "text": " ".join(part["text"].strip() for part in out).strip(),
              "language": getattr(info, "language", "unknown")}
    if configuration is not None:
        result.update(language_probability=info.language_probability, duration=info.duration)
    return result
