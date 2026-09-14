"""#1931 — a removed torchaudio API must not take startup down.

torchaudio 2.9 removed ``set_audio_backend()``. soundfile has been the only
backend since 2.0, so the call was already a no-op — but unguarded it raises
AttributeError inside the ``ml_imports`` startup phase, and a failure there
takes the whole backend with it: the desktop app sits on "starting backend"
forever and /health stays 503.

The group that hits this is not hypothetical. #1931 came from an sm_120
(Blackwell) user whose torch import crashed on Windows and who fixed it by
moving to torch 2.9.1, which brings torchaudio 2.9 with it. Working around one
problem and then crashing on a line that does nothing is the whole bug.

The pinned torch is not missing sm_120 kernels: 2.8.0 from the cu128 index
lists sm_120 in get_arch_list(). CU128_ARCHS in tests/test_cuda_arch_compat.py
records the same list, captured verbatim from a real cu128 build in #1285.

Checked at the source level because reproducing it needs a real torchaudio 2.9
in the environment, which the pinned test env does not have. Guarding the
class, not the instance: any future call into a version-dependent torchaudio
attribute during ml_imports has to be defensive.
"""
import inspect
import pathlib
import re

_MAIN = pathlib.Path(__file__).resolve().parents[1] / "backend" / "main.py"


def test_the_legacy_backend_call_is_guarded():
    source = _MAIN.read_text(encoding="utf-8")
    calls = [
        line.strip()
        for line in source.splitlines()
        if "set_audio_backend(" in line and not line.strip().startswith("#")
    ]
    assert calls, "the call vanished — drop this test with it"
    for call in calls:
        assert call.startswith("torchaudio.set_audio_backend") or "hasattr" in call, call
    assert 'hasattr(torchaudio, "set_audio_backend")' in source, (
        "torchaudio.set_audio_backend() must be guarded: it does not exist in "
        "torchaudio 2.9+, and an AttributeError in ml_imports is a fatal "
        "startup failure rather than a degraded feature"
    )


def test_the_guard_actually_wraps_the_call():
    # A guard that sits somewhere else in the file would pass a naive substring
    # check while the real call stays unprotected.
    source = _MAIN.read_text(encoding="utf-8")
    pattern = re.compile(
        r'if hasattr\(torchaudio, "set_audio_backend"\):\s*\n\s+'
        r'torchaudio\.set_audio_backend\('
    )
    assert pattern.search(source), "the hasattr guard does not wrap the call"


def test_soundfile_is_importable_so_the_no_op_is_safe():
    # The call only ever selected soundfile. If that backend were missing,
    # dropping the call would be a behaviour change rather than a no-op.
    import soundfile  # noqa: F401

    assert inspect.ismodule(soundfile)
