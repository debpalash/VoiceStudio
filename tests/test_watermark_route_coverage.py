"""Structural guard: no synthesis call site can ship without provenance marking.

The #1169 class: watermark coverage grew call-site-by-call-site (three
separate ``embed_watermark`` calls) until a fourth producer —
``/v1/audio/speech`` — shipped synthetic audio with no mark at all, with EU AI
Act Art. 50(2) applying from 2026-08-02. The fix is ONE chokepoint,
``services.watermark.mark_synthetic``; this guard makes the chokepoint
structurally load-bearing:

1. Every backend module that invokes a TTS synthesis primitive must reference
   ``mark_synthetic`` — or sit in the justified allowlist below. A future
   audio route that synthesizes without marking fails here before it ships.
2. Known producers must KEEP their ``mark_synthetic`` call (deleting one fails).
3. ``embed_watermark`` may not be called outside ``services/watermark.py`` —
   new code physically can't bypass the chokepoint's logging/uniformity.
4. Allowlist entries must still match the synthesis pattern (no stale
   exemptions accumulating).

Behavioral (detect-on-response) coverage per route lives in
tests/test_synthetic_audio_watermark_1169.py.
"""
from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

import pytest
import torch

_BACKEND = Path(__file__).resolve().parents[1] / "backend"

#: Calls that produce a synthetic speech tensor. Matching any of these makes a
#: module a "producer" that must route its output through mark_synthetic.
_SYNTH_CALL = re.compile(
    r"\bbackend\.generate\("           # adapter-protocol engines
    r"|\b_run_inference\("             # OmniVoice model, generation.py primitive
    r"|\b_run_backend_inference\("
    r"|\bgenerate_with_cached_ref\("   # cached-reference OmniVoice path
    r"|\bsynthesize_chapter\("         # longform chapter assembly
)

#: Modules that legitimately touch synthesis primitives WITHOUT marking.
#: Every entry carries its justification; test_allowlist_is_not_stale keeps
#: the list honest.
_ALLOWED = {
    "api/routers/engines.py":
        "engine self-test: the synthesized audio is reduced to a sample count "
        "for a JSON verdict and never leaves the process",
    "services/tts_backend.py":
        "the engine-adapter layer itself — the seam BELOW the chokepoint; "
        "every caller marks the returned tensor",
    "services/audiobook.py":
        "pure chapter assembly (spans -> tensor); the audiobook router marks "
        "the assembled chapter at its single mark_synthetic call site",
}

#: Producers that must each keep a mark_synthetic call. (sonitranslate.py is
#: absent by design: its audio is synthesized inside the external sidecar and
#: never passes through our tensor stage — the gap is documented at the
#: /engines/sonitranslate/dub route, not silently ignored.)
_PRODUCERS = [
    "api/routers/generation.py",
    "api/routers/voice_convert.py",
    "api/routers/openai_compat.py",
    "api/routers/tts_stream.py",
    "api/routers/dub_generate.py",
    "api/routers/batch.py",
    "api/routers/audiobook.py",
    "api/routers/archetypes.py",
    "services/persona_bundle.py",
]


def _code_only(src: str) -> str:
    """``src`` with comments and string literals blanked to spaces.

    ee35d238 made a module a "producer" by *mentioning* ``backend.generate()``
    in a comment — prose can't synthesize audio. Only real call sites may
    match ``_SYNTH_CALL``, so blank every COMMENT/STRING token span (spaces,
    not deletion, to keep the layout the regexes were written against).
    Unparseable source falls back to the raw text — fail closed, a module we
    can't tokenize still gets scanned.

    f-strings stay conservative (Greptile P1 on #1564): on Python ≤3.11 the
    whole f-string — replacement expressions included — is ONE STRING token,
    so blanking it would let ``f"{backend.generate(t)}"`` evade the guard.
    f-prefixed strings are therefore kept raw there (a literal f-string
    *mentioning* a primitive false-positives toward the allowlist — fail
    closed). On 3.12+ (PEP 701) replacement code arrives as ordinary tokens
    and only the literal FSTRING_MIDDLE text is blanked.
    """
    fstring_middle = getattr(tokenize, "FSTRING_MIDDLE", None)
    lines = src.splitlines(keepends=True)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return src
    for tok in tokens:
        if tok.type == tokenize.STRING:
            prefix = tok.string.split(tok.string[-1], 1)[0].rstrip("\"'")
            if "f" in prefix.lower():
                continue  # pre-3.12 f-string: may contain executable code
        elif tok.type not in (tokenize.COMMENT, fstring_middle):
            continue
        (srow, scol), (erow, ecol) = tok.start, tok.end
        for row in range(srow - 1, erow):
            line = lines[row]
            lo = scol if row == srow - 1 else 0
            hi = ecol if row == erow - 1 else len(line.rstrip("\r\n"))
            lines[row] = line[:lo] + " " * (hi - lo) + line[hi:]
    return "".join(lines)


def _py_files():
    for sub in ("api", "services", "worker"):
        for p in sorted((_BACKEND / sub).rglob("*.py")):
            yield p.relative_to(_BACKEND).as_posix(), p.read_text(encoding="utf-8")


def test_every_synthesis_module_routes_through_mark_synthetic():
    offenders = []
    for rel, src in _py_files():
        if not _SYNTH_CALL.search(_code_only(src)):
            continue
        # The satisfying reference must be code too — a comment saying
        # "mark_synthetic" must not certify a module (CodeRabbit, #1564).
        if rel in _ALLOWED or "mark_synthetic" in _code_only(src):
            continue
        offenders.append(rel)
    assert not offenders, (
        "Modules synthesize audio but never reference the mark_synthetic "
        f"chokepoint (EU AI Act Art. 50(2), #1169): {offenders}\n"
        "Either mark the produced audio (services.watermark.mark_synthetic at "
        "the tensor stage, before encoding) or add a justified _ALLOWED entry."
    )


@pytest.mark.parametrize("rel", _PRODUCERS)
def test_known_producer_still_marks(rel):
    src = (_BACKEND / rel).read_text(encoding="utf-8")
    assert "mark_synthetic" in _code_only(src), (
        f"{rel} lost its mark_synthetic call — its synthetic audio would ship "
        "without the Art. 50(2) provenance mark (#1169)."
    )


def test_embed_watermark_not_called_outside_the_chokepoint():
    offenders = []
    for rel, src in _py_files():
        if rel == "services/watermark.py":
            continue
        if re.search(r"\bembed_watermark\(", src):
            offenders.append(rel)
    assert not offenders, (
        f"Direct embed_watermark() calls bypass the mark_synthetic chokepoint: "
        f"{offenders} (#1169 — call mark_synthetic instead)"
    )


def test_allowlist_is_not_stale():
    # _PRODUCERS entries need only exist (persona_bundle marks pre-existing
    # reference audio rather than calling a synthesis primitive); _ALLOWED
    # entries must additionally still match the pattern they're exempt from.
    for rel in list(_ALLOWED) + _PRODUCERS:
        p = _BACKEND / rel
        assert p.is_file(), f"watermark-coverage list names a missing file: {rel}"
    for rel in _ALLOWED:
        assert _SYNTH_CALL.search(_code_only((_BACKEND / rel).read_text(encoding="utf-8"))), (
            f"{rel} no longer matches a synthesis primitive — remove it from "
            "tests/test_watermark_route_coverage.py so the guard stays sharp."
        )


def test_prose_mentions_are_not_producers():
    """The ee35d238 regression: a comment (or log string / docstring) naming a
    synthesis primitive must not make a module a producer — only a call can."""
    prose = (
        "# A generic backend.generate() call accepts the same wire shape\n"
        'MSG = "route through generate_with_cached_ref(model) instead"\n'
        "def f():\n"
        '    """Docs may mention _run_inference( freely."""\n'
        "    return 1\n"
    )
    assert not _SYNTH_CALL.search(_code_only(prose))
    real = "def f(backend):\n    return backend.generate(text='hi')\n"
    assert _SYNTH_CALL.search(_code_only(real))
    # Greptile P1: a call inside an f-string replacement field is code and
    # must still be caught, on every supported Python (≤3.11 tokenizes the
    # whole f-string as one STRING; 3.12+ splits out the expression tokens).
    fstring_call = 'def f(backend):\n    return f"{backend.generate(text=\'hi\')}"\n'
    assert _SYNTH_CALL.search(_code_only(fstring_call))
    # ...and a comment claiming mark_synthetic must not certify a producer.
    assert "mark_synthetic" not in _code_only("# routes via mark_synthetic\nx = 1\n")


# ── mark_synthetic unit contract (delegation, not new policy) ────────────────


def test_mark_synthetic_delegates_and_respects_pref(monkeypatch):
    from services import watermark

    calls = []
    monkeypatch.setattr(watermark, "_audioseal_available", True)

    class _Gen:
        def __call__(self, audio, sample_rate, message=None):
            calls.append(audio.shape[-1])
            return audio * 2.0

    monkeypatch.setattr(watermark, "_generator", _Gen())
    wav = torch.full((1, 2400), 0.1)

    monkeypatch.setattr(watermark, "is_enabled", lambda: False)
    assert watermark.mark_synthetic(wav, 24000, context="t") is wav  # pref off → untouched
    assert watermark.mark_synthetic(wav, 24000, context="t", force=True) is not wav
    monkeypatch.setattr(watermark, "is_enabled", lambda: True)
    assert watermark.mark_synthetic(wav, 24000, context="t") is not wav
    assert calls == [1600, 1600]  # AudioSeal receives normalized 16 kHz input


def test_mark_synthetic_never_raises(monkeypatch):
    from services import watermark

    monkeypatch.setattr(watermark, "_audioseal_available", True)
    monkeypatch.setattr(watermark, "is_enabled", lambda: True)

    class _Boom:
        def __call__(self, *a, **k):
            raise RuntimeError("audioseal exploded (test)")

    monkeypatch.setattr(watermark, "_generator", _Boom())
    wav = torch.full((1, 2400), 0.1)
    # Degrade-don't-block: the original audio passes through unchanged.
    assert watermark.mark_synthetic(wav, 24000, context="t") is wav


def test_will_mark_requires_pref_and_availability(monkeypatch):
    from services import watermark

    monkeypatch.setattr(watermark, "is_enabled", lambda: True)
    monkeypatch.setattr(watermark, "_audioseal_available", True)
    assert watermark.will_mark() is True
    monkeypatch.setattr(watermark, "_audioseal_available", False)
    assert watermark.will_mark() is False
    monkeypatch.setattr(watermark, "_audioseal_available", True)
    monkeypatch.setattr(watermark, "is_enabled", lambda: False)
    assert watermark.will_mark() is False
