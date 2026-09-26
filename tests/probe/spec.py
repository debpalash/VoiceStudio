"""The hybrid spec engine.

Simple tests are declarative YAML (``*.probe.yaml``) — portable across projects
and, later, agent-authorable. Anything the schema can't express drops to a plain
pytest function that calls the same judge functions directly (the "escape
hatch"). Both paths share this one engine and the same :class:`JudgeResult`.

A spec separates three things deliberately:
  - ``steps``   — what the *Actor* does (HTTP call, browser action). Execution of
                  the Actor plugs in per-layer; this engine focuses on the Judge.
  - ``judge``   — deterministic, BLOCKING verdicts. A failure fails the test.
  - ``advisory``— non-blocking metrics (naturalness predictors, trends). These
                  are reported but NEVER gate, because they encode learned
                  opinion and fail out-of-domain.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class JudgeResult:
    """The atomic verdict. ``passed`` is True/False, or None when skipped
    (e.g. an optional backend isn't installed)."""

    name: str
    passed: bool | None
    detail: str = ""
    measured: Any = None
    advisory: bool = False

    @property
    def skipped(self) -> bool:
        return self.passed is None

    def __str__(self) -> str:
        tag = "SKIP" if self.skipped else ("PASS" if self.passed else "FAIL")
        lane = " (advisory)" if self.advisory else ""
        return f"[{tag}]{lane} {self.name}: {self.detail}"


@dataclass
class Spec:
    feature: str
    layer: str
    setup: dict = field(default_factory=dict)
    steps: list = field(default_factory=list)
    subject: Any = None  # default artifact for subject-taking judges (e.g. $.audio)
    checks: list = field(default_factory=list)
    advisory: list = field(default_factory=list)
    source: str | None = None


# ── registry ─────────────────────────────────────────────────────────────────


def _build_registry() -> dict[str, Callable[..., JudgeResult]]:
    """Map YAML judge keys → judge callables. Imported lazily to avoid the
    spec ⇄ judges circular import (judges import JudgeResult from here)."""
    from .judges import audio, coverage, dictation, dubbing, engine, http, i18n, speaker, transcription
    from .judges import web as web_judges

    return {
        # L4 media
        "artifact_exists": audio.artifact_exists,
        "decodes": audio.decodes,
        "sample_rate_eq": audio.sample_rate_eq,
        "duration_between": audio.duration_between,
        "not_silent": audio.not_silent,
        "not_clipping": audio.not_clipping,
        "no_nan": audio.no_nan,
        "asr_wer_below": transcription.asr_wer_below,
        "speaker_similarity_above": speaker.speaker_similarity_above,
        # L1/L5 http + filesystem
        "status_eq": http.status_eq,
        "json_has": http.json_has,
        "json_field_eq": http.json_field_eq,
        "responds_within_ms": http.responds_within_ms,
        "path_exists": http.path_exists,
        # L2 web (judges operate on a live page from the run context)
        "web_visible": web_judges.web_visible,
        "web_text_equals": web_judges.web_text_equals,
        "web_url_matches": web_judges.web_url_matches,
        # L4 dubbing
        "segments_duration_ratio": dubbing.segments_duration_ratio,
        "srt_well_formed": dubbing.srt_well_formed,
        "vtt_well_formed": dubbing.vtt_well_formed,
        "archive_has": dubbing.archive_has,
        "output_language_is": dubbing.output_language_is,
        # i18n / localization
        "locale_valid_json": i18n.locale_valid_json,
        "locale_no_orphan_keys": i18n.locale_no_orphan_keys,
        "locale_coverage": i18n.locale_coverage,
        # engine matrix
        "engines_present": engine.engines_present,
        "active_engine_available": engine.active_engine_available,
        "engine_available": engine.engine_available,
        "unavailable_engines_explained": engine.unavailable_engines_explained,
        # coverage critic
        "layers_have_specs": coverage.layers_have_specs,
        "coverage_report": coverage.coverage_report,
        # dictation (streaming-ASR WebSocket)
        "ws_endpoint_registered": dictation.ws_endpoint_registered,
        "ws_handshake_ok": dictation.ws_handshake_ok,
    }


JUDGE_REGISTRY: dict[str, Callable[..., JudgeResult]] = _build_registry()


# ── parsing ──────────────────────────────────────────────────────────────────


def _normalize_checks(raw: Any) -> list[tuple[str, Any]]:
    """Accept either ``[ "no_nan", {"sample_rate_eq": 44100} ]`` or a mapping,
    and return a list of (judge_name, args) pairs."""
    out: list[tuple[str, Any]] = []
    if raw is None:
        return out
    if isinstance(raw, dict):
        return [(k, v) for k, v in raw.items()]
    for item in raw:
        if isinstance(item, str):
            out.append((item, None))
        elif isinstance(item, dict) and len(item) == 1:
            (name, args), = item.items()
            out.append((name, args))
        else:
            raise ValueError(f"unparseable check entry: {item!r}")
    return out


def load_spec(path: str) -> Spec:
    import yaml

    with open(path, "r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    judge_block = doc.get("judge", {}) or {}
    # judge: can be a bare list of checks, or a mapping with subject + checks.
    if isinstance(judge_block, list):
        subject, checks_raw = None, judge_block
    else:
        subject = judge_block.get("subject")
        checks_raw = judge_block.get("checks", [])
    return Spec(
        feature=doc.get("feature", "<unnamed>"),
        layer=doc.get("layer", "<unknown>"),
        setup=doc.get("setup", {}) or {},
        steps=doc.get("steps", []) or [],
        subject=subject,
        checks=_normalize_checks(checks_raw),
        advisory=_normalize_checks(doc.get("advisory", [])),
        source=path,
    )


# ── execution (judge phase) ──────────────────────────────────────────────────


def _resolve(value: Any, context: dict) -> Any:
    """Resolve ``$.name`` references against the context dict, recursively."""
    if isinstance(value, str) and value.startswith("$."):
        key = value[2:]
        if key not in context:
            raise KeyError(f"spec references {value!r} but context has no {key!r}")
        return context[key]
    if isinstance(value, list):
        return [_resolve(v, context) for v in value]
    if isinstance(value, dict):
        return {k: _resolve(v, context) for k, v in value.items()}
    return value


def _bind_kwargs(fn: Callable, args: Any, subject: Any, backends: dict) -> dict:
    """Turn a check's args (scalar/list/dict) into kwargs for ``fn``, injecting
    the spec ``subject`` into a leading ``path`` param and wiring pluggable
    backends (transcriber/embedder) when the judge accepts them."""
    sig = inspect.signature(fn)
    params = list(sig.parameters)
    kwargs: dict[str, Any] = {}

    inject_path = "path" in params and subject is not None
    # Params the spec's own args fill positionally — never the auto-injected ones.
    skip = {"transcriber", "embedder"}
    if inject_path:
        skip.add("path")
    fillable = [p for p in params if p not in skip]

    if isinstance(args, dict):
        kwargs.update(args)
    elif isinstance(args, (list, tuple)):
        for name, val in zip(fillable, args):
            kwargs[name] = val
    elif args is not None:
        # scalar → first declared (non-injected) param
        if fillable:
            kwargs[fillable[0]] = args

    # Inject the subject artifact into `path` when the judge wants one.
    if inject_path and "path" not in kwargs:
        kwargs["path"] = subject
    # Wire optional backends only if the judge accepts them and the spec didn't.
    for backend_name in ("transcriber", "embedder"):
        if backend_name in params and backend_name not in kwargs and backend_name in backends:
            kwargs[backend_name] = backends[backend_name]
    return kwargs


def _run_one(name: str, args: Any, *, context: dict, subject: Any, backends: dict, advisory: bool) -> JudgeResult:
    if name not in JUDGE_REGISTRY:
        return JudgeResult(name=name, passed=False, advisory=advisory,
                           detail=f"unknown judge {name!r}; known: {sorted(JUDGE_REGISTRY)}")
    fn = JUDGE_REGISTRY[name]
    try:
        # Resolution can fail on a missing $.ref — that's a FAIL, not a crash.
        resolved_args = _resolve(args, context)
        resolved_subject = _resolve(subject, context) if subject is not None else None
        kwargs = _bind_kwargs(fn, resolved_args, resolved_subject, backends)
        result = fn(**kwargs)
    except Exception as exc:  # noqa: BLE001 — a judge that errors is a FAIL, not a crash
        return JudgeResult(name=name, passed=False, advisory=advisory, detail=f"judge errored: {exc}")
    result.advisory = advisory
    return result


def run_judges(
    spec: Spec,
    context: dict | None = None,
    *,
    backends: dict | None = None,
) -> list[JudgeResult]:
    """Run a spec's blocking ``checks`` and non-blocking ``advisory`` lane.

    ``context`` supplies values for ``$.`` references (e.g. the captured
    ``audio`` path produced by the Actor). ``backends`` injects pluggable
    transcriber/embedder instances (a FakeTranscriber in the harness's own
    tests; faster-whisper / Resemblyzer in real runs).
    """
    context = context or {}
    backends = backends or {}
    results: list[JudgeResult] = []
    for name, args in spec.checks:
        results.append(_run_one(name, args, context=context, subject=spec.subject,
                                backends=backends, advisory=False))
    for name, args in spec.advisory:
        results.append(_run_one(name, args, context=context, subject=spec.subject,
                                backends=backends, advisory=True))
    return results


def blocking_failures(results: list[JudgeResult]) -> list[JudgeResult]:
    """The verdict: only non-advisory, non-skipped, failed judges gate."""
    return [r for r in results if not r.advisory and r.passed is False]
