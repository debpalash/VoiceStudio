"""Request paths select ASR through the *degrading loader*, not the raw
selector — a broken-but-probeable engine must not 500 a transcription.

#1185 added ``load_active_asr_backend`` (select + ``ensure_loaded`` + fall
through to the next healthy engine) precisely because ``is_available()`` is a
shallow probe. But only the dub preflight adopted it: five request paths kept
calling the pure selector ``get_active_asr_backend`` and handing the broken
engine straight to ``.transcribe()`` (#1512).

Observed on a hardened-kernel Linux host, where auto-detect picks whisperx
(``import whisperx`` succeeds) and its deep chain then dies with
``ImportError: libctranslate2-….so.4.4.0: cannot enable executable stack``:

    POST /v1/audio/transcriptions  → 500 (raw dlopen error to the client)
    POST /transcribe mode=accurate → 500
    POST /transcribe (fast)        → 200, because it routes through
                                     get_capture_asr_backend() instead

…with pytorch-whisper installed, healthy, and next in line the whole time.

Fail-before/pass-after: on pre-fix code the 500 tests fail (the broken engine
reaches ``.transcribe()``) and the guard test fails (the routers still name
``get_active_asr_backend``).

NOTE ON MODULE IDENTITY: nothing here may bind ``services.asr_backend`` at
import time. An earlier test in a combined run (tests/test_mcp_bindings.py)
purges ``api.*``/``services.*`` from ``sys.modules`` in its teardown, so a
collection-time alias points at a STALE pre-purge module object while the
router re-imports a fresh one — patches would land on the dead copy and the
real engine would run. The ``asr`` fixture below imports the routers FIRST and
then takes the module straight out of ``sys.modules``, so it always patches
exactly the object the handlers closed over. (Same hazard the
``asr_model_installed`` fixture in conftest.py documents.)
"""
from __future__ import annotations

import ast
import io
import re
import sys
import wave
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("asr_model_installed")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_ROUTERS = Path(__file__).resolve().parents[1] / "api" / "routers"
_BACKEND = Path(__file__).resolve().parents[1]

# The exact failure this regression is built from: an ImportError raised by the
# *deep* chain (dlopen of a CTranslate2 shared object), not a missing module.
_EXEC_STACK = (
    "libctranslate2-d3638643.so.4.4.0: cannot enable executable stack as "
    "shared object requires: Invalid argument"
)


def _tiny_wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 1600)
    return buf.getvalue()


@pytest.fixture
def asr(monkeypatch):
    """The live ``services.asr_backend`` module, hermetically configured.

    Imports the routers first so the module is (re)loaded through the same
    import that the handlers use, then reads it out of ``sys.modules`` — see
    the module docstring on why a collection-time alias is unsafe here.

    Hermetic selection: no env pin, no pref pin, no MPS → auto-detect goes
    whisperx-first, and shallow probes report ready regardless of what this
    host actually has installed.
    """
    from api.routers import capture, openai_compat  # noqa: F401  (loads services.*)

    ab = sys.modules["services.asr_backend"]

    monkeypatch.delenv("OMNIVOICE_ASR_BACKEND", raising=False)
    monkeypatch.setattr("core.prefs.get", lambda key, default=None: None)
    monkeypatch.setattr(ab, "_mps_available", lambda: False)
    for cls in (ab.WhisperXBackend, ab.FasterWhisperBackend):
        monkeypatch.setattr(cls, "is_available", classmethod(lambda cls: (True, "ready")))
    # The preflight is neutralized by `asr_model_installed`, but that fixture
    # resolves the module independently — re-assert it on the object the
    # routers actually hold so a purge can't leave a real preflight behind.
    monkeypatch.setattr(ab, "asr_model_missing_error", lambda **_kw: None)
    ab._DEEP_IMPORT_BROKEN.clear()
    ab._LAST_ERRORS.clear()
    yield ab
    ab._DEEP_IMPORT_BROKEN.clear()
    ab._LAST_ERRORS.clear()


@pytest.fixture
def broken_primary(asr, monkeypatch):
    """whisperx dies at load like the real ctranslate2 exec-stack failure;
    faster-whisper is healthy and returns a recognizable transcript."""
    def _die(self):
        raise ImportError(_EXEC_STACK)

    monkeypatch.setattr(asr.WhisperXBackend, "ensure_loaded", _die)
    monkeypatch.setattr(asr.FasterWhisperBackend, "ensure_loaded", lambda self: None)
    monkeypatch.setattr(
        asr.FasterWhisperBackend, "transcribe",
        lambda self, path, **kw: {
            "text": "degraded ok",
            "segments": [{"text": "degraded ok", "start": 0.0, "end": 0.1}],
            "language": "en",
        },
    )
    return asr


def _client(module_name: str, attr: str = "router") -> TestClient:
    from importlib import import_module

    app = FastAPI()
    app.include_router(getattr(import_module(module_name), attr))
    # Loopback peer so the routers' require_admin / require_consumer gates let
    # these handler tests through; remote denial is covered by the policy tests.
    return TestClient(app, client=("127.0.0.1", 50000))


def test_openai_transcriptions_degrades_past_broken_engine(broken_primary):
    """The bug, end to end: 500 + raw dlopen error before the fix."""
    client = _client("api.routers.openai_compat")  # carries its own /v1/audio prefix

    r = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("a.wav", _tiny_wav(), "audio/wav")},
    )

    assert r.status_code == 200, r.text
    assert r.json()["text"] == "degraded ok"
    # The broken engine is recorded so Settings → Engines can explain itself…
    assert "executable stack" in broken_primary._DEEP_IMPORT_BROKEN["whisperx"]
    # …and the raw loader error never reaches the client.
    assert "libctranslate2" not in r.text


def test_capture_accurate_mode_degrades_past_broken_engine(broken_primary):
    """`/transcribe mode=accurate` — the sibling 500 on the same host."""
    client = _client("api.routers.capture")

    r = client.post(
        "/transcribe",
        files={"audio": ("a.wav", _tiny_wav(), "audio/wav")},
        data={"mode": "accurate"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["text"].lower().startswith("degraded ok")
    assert "libctranslate2" not in r.text


def test_missing_weights_after_degrading_is_409_not_500(asr, monkeypatch):
    """Degrading onto an engine with no weights on disk is a typed 409 (the
    one-click download CTA), never a 500 — and never a silent multi-GB pull."""
    def _die(self):
        raise ImportError(_EXEC_STACK)

    monkeypatch.setattr(asr.WhisperXBackend, "ensure_loaded", _die)

    payload = {
        "error": "asr_model_missing",
        "missing_repo_id": "Systran/faster-whisper-large-v3",
    }
    calls = {"n": 0}

    def _missing(*a, **kw):
        # The *initial* preflight passes (the primary's weights exist); only
        # the re-selected fallback reports missing weights.
        calls["n"] += 1
        return None if calls["n"] == 1 else payload

    monkeypatch.setattr(asr, "asr_model_missing_error", _missing)

    client = _client("api.routers.openai_compat")
    r = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("a.wav", _tiny_wav(), "audio/wav")},
    )

    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "asr_model_missing"
    assert detail["missing_repo_id"] == "Systran/faster-whisper-large-v3"
    assert detail["message"]  # human-readable line for generic clients


# ── Recurrence guard ───────────────────────────────────────────────────────
# The bug is re-introduced by one import line, so assert the shape in CI
# rather than relying on review to catch the sixth call site.

# `get_active_asr_backend` is a legitimate *pure selector*, and exactly one
# module may call it. Approved entries are RELATIVE paths, not basenames: a
# basename match would exempt any future `<anything>/asr_backend.py` from the
# guard it exists to enforce (CodeRabbit, #1523).
_SELECTOR = "get_active_asr_backend"
_HOME_MODULE = "services/asr_backend.py"

# Relative path → why the raw selector (no ensure_loaded, no degradation) is
# right there. Add an entry only with that reason.
_SELECTOR_ALLOWED: dict[str, str] = {
    # (none — every call site outside the home module loads and transcribes)
}


def _selector_call_lines(source: str) -> list[int]:
    """Lines where THIS module's selector is called — resolved, not matched.

    Three ways to get this wrong, all of them seen in review:

    * a line regex misses ``import get_active_asr_backend as pick`` and fires
      on the name inside docstrings and comments (#1523);
    * matching any call named ``get_active_asr_backend`` also reports a local
      helper or an unrelated object's method that happens to share the name
      (CodeRabbit, #1524).

    So bindings are resolved first: a bare call counts only if the name was
    imported FROM services.asr_backend, and an attribute call only if it hangs
    off a module alias for it.
    """
    tree = ast.parse(source)

    home = _HOME_MODULE.removesuffix(".py").replace("/", ".")  # services.asr_backend
    tail = home.rsplit(".", 1)[-1]  # asr_backend

    functions: set[str] = set()  # names bound to the selector itself
    modules: set[str] = set()  # names bound to the module holding it
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == home or module.endswith(f".{tail}") or module == tail:
                for name in node.names:
                    if name.name == _SELECTOR:
                        functions.add(name.asname or name.name)
            elif module and home.startswith(f"{module}."):
                # from services import asr_backend
                for name in node.names:
                    if name.name == tail:
                        modules.add(name.asname or name.name)
        elif isinstance(node, ast.Import):
            for name in node.names:
                if name.name == home or name.name.endswith(f".{tail}"):
                    modules.add(name.asname or name.name.split(".")[0])

    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in functions:
            lines.append(node.lineno)
        elif (
            isinstance(func, ast.Attribute)
            and func.attr == _SELECTOR
            and isinstance(func.value, ast.Name)
            and func.value.id in modules
        ):
            lines.append(node.lineno)
    return sorted(lines)


def _offenders(paths, root):
    found = []
    for path in sorted(paths):
        rel = path.relative_to(root).as_posix()
        if rel == _HOME_MODULE or rel in _SELECTOR_ALLOWED:
            continue
        try:
            hits = _selector_call_lines(path.read_text(encoding="utf-8"))
        except SyntaxError:  # not ours to police here
            continue
        found.extend(f"{rel}:{line}" for line in hits)
    return found


@pytest.mark.parametrize(
    ("label", "source", "flagged"),
    [
        (
            "direct import",
            "from services.asr_backend import get_active_asr_backend\nget_active_asr_backend()\n",
            True,
        ),
        (
            "aliased import",
            "from services.asr_backend import get_active_asr_backend as pick\npick()\n",
            True,
        ),
        (
            "module attribute",
            "from services import asr_backend\nasr_backend.get_active_asr_backend()\n",
            True,
        ),
        # False positives teach people to add allowlist entries for code that
        # was never the bug — which is how a guard stops being believed.
        (
            "unrelated local function of the same name",
            "def get_active_asr_backend():\n    return 1\n\nget_active_asr_backend()\n",
            False,
        ),
        ("unrelated object method", "registry.get_active_asr_backend()\n", False),
        ('name only in a docstring', '\"\"\"once called get_active_asr_backend().\"\"\"\n', False),
    ],
)
def test_the_guard_resolves_the_selector_instead_of_matching_its_name(label, source, flagged):
    assert bool(_selector_call_lines(source)) is flagged, label


def test_routers_use_the_degrading_loader():
    offenders = _offenders(_ROUTERS.glob("*.py"), _BACKEND)

    assert not offenders, (
        "Request paths must select ASR via load_active_asr_backend() — the raw "
        "get_active_asr_backend() selector skips ensure_loaded() and the "
        "broken-engine fall-through, so an engine whose shallow is_available() "
        "probe passes but whose deep import chain is broken reaches "
        ".transcribe() and 500s the request (#1185, #1512).\n  "
        + "\n  ".join(offenders)
    )


def test_no_module_outside_asr_backend_calls_the_raw_selector():
    # Routers are where the bug was found, but not the only place it can live:
    # a service or engine module that transcribes on a request's behalf skips
    # ensure_loaded() just as thoroughly. Scan the whole backend, so the guard
    # cannot be sidestepped by moving the call one module down the stack.
    # (Broader scan contributed on #1519 — thanks @ahov520!)
    paths = (p for p in _BACKEND.rglob("*.py") if not p.relative_to(_BACKEND).as_posix().startswith("tests/"))
    offenders = _offenders(paths, _BACKEND)

    assert not offenders, (
        "Only services/asr_backend.py may call the raw get_active_asr_backend() "
        "selector — everywhere else must use load_active_asr_backend(), which "
        "runs ensure_loaded() and degrades past an engine whose deep import "
        "chain is broken (#1185, #1512, #1519).\n  " + "\n  ".join(offenders)
    )
