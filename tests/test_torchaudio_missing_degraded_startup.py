"""A torchaudio that cannot even import must degrade, not stop startup (#3150).

#1931 guarded ``set_audio_backend()`` against torchaudio 2.9, and this branch
already swallows the *import* failure in ``backend/main.py`` — but the required
generation import (``api.routers.generation`` → ``services.audio_io``) then ran
an UNGUARDED module-level ``import torchaudio``. That second failure escaped
``ml_imports`` and reached ``_deferred_startup``, which does ``os._exit(1)``:
the user still lost the backend, degraded startup was a promise the code did
not keep.

Checked at the source/import level: reproducing the real breakage needs a host
where torchaudio's shared libraries are absent, which CI does not have. Same
method as ``tests/test_torchaudio_legacy_backend_1931.py``.
"""
import ast
import pathlib
import subprocess
import sys

import pytest

_BACKEND = pathlib.Path(__file__).resolve().parents[1] / "backend"
_MAIN = _BACKEND / "main.py"
_AUDIO_IO = _BACKEND / "services" / "audio_io.py"


def test_audio_io_tolerates_a_broken_torchaudio():
    """Import services.audio_io with a poisoned torchaudio: must not raise.

    A meta-path finder that raises OSError for torchaudio (the real
    DLL-load failure) simulates the wheel that loads torch
    but fails on torchaudio's shared libraries. If the module still has a bare
    module-level ``import torchaudio`` without a guard, this raises and the
    test fails — fail-before, pass-after.
    """
    code = (
        "import sys, importlib.abc\n"
        "class _Block(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'torchaudio':\n"
        "            raise OSError('Could not find module libtorchaudio.pyd')\n"
        "sys.meta_path.insert(0, _Block())\n"
        "sys.path.insert(0, " + repr(str(_BACKEND)) + ")\n"
        "import services.audio_io as aio\n"
        "assert aio.torchaudio is None, (\n"
        "    'services.audio_io must degrade to torchaudio=None when the import fails')\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, f"audio_io crashed on a broken torchaudio: {proc.stderr[-2000:]}"
    assert "ok" in proc.stdout


def test_load_audio_and_save_fall_back_when_torchaudio_is_missing(tmp_path):
    """Round-trip real audio through the helpers with torchaudio unimportable."""
    code = (
        "import sys, importlib.abc\n"
        "class _Block(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'torchaudio':\n"
        "            raise OSError('Could not find module libtorchaudio.pyd')\n"
        "sys.meta_path.insert(0, _Block())\n"
        f"sys.path.insert(0, {str(_BACKEND)!r})\n"
        "import torch, soundfile as sf\n"
        "import services.audio_io as aio\n"
        "assert aio.torchaudio is None\n"
        "wave = torch.sin(torch.linspace(0, 100, 4000)).unsqueeze(0) * 0.5\n"
        f"target = {str(tmp_path / 'tone.wav')!r}\n"
        "aio._safe_torchaudio_save(target, wave, 16000, bits_per_sample=24)\n"
        "assert sf.info(target).subtype == 'PCM_24', sf.info(target).subtype\n"
        "loaded, rate = aio.load_audio(target)\n"
        "assert rate == 16000 and tuple(loaded.shape) == (1, 4000), (rate, loaded.shape)\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=300
    )
    assert proc.returncode == 0, "fallback round trip failed (exit %s)" % proc.returncode
    assert "ok" in proc.stdout


def test_no_module_level_import_torchaudio_in_api_routers():
    """api.routers.* load during ml_imports — no unguarded import there."""
    offenders = []
    for path in (_BACKEND / "api" / "routers").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                a.name == "torchaudio" for a in node.names
            ):
                offenders.append(path.name)
    assert not offenders, (
        f"module-level `import torchaudio` in api.routers: {offenders}. A "
        "failed load there reaches _deferred_startup and os._exit(1) — "
        "route audio through services.audio_io instead (#3150)."
    )


def _main_audio_import_guard():
    source = _MAIN.read_text(encoding="utf-8")
    tree = ast.parse(source)
    guarded_import = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.Try) and any(
            isinstance(statement, ast.Import)
            and any(alias.name == "torchaudio" for alias in statement.names)
            for statement in node.body
        )
    )
    return source, guarded_import


def test_import_failure_log_is_sanitized():
    """The ml_imports failure log must not embed the traceback or raw exc text."""
    source, guarded_import = _main_audio_import_guard()
    # The real handler: the guarded import inside the ml_imports try block,
    # not an early docstring mention of the same words.
    block = ast.get_source_segment(source, guarded_import)
    assert "logger.exception" not in block and ".exception(" not in block, (
        "the torchaudio import-failure handler must not use logger.exception: "
        "traceback text embeds absolute user-home paths and lands in "
        "backend.log / diagnostic bundles (CWE-532)"
    )
    assert "sanitize(" in block, "the failure detail must go through core.failure.sanitize"


@pytest.mark.parametrize("failure", [None, "preparation", "torchaudio", "both"])
def test_main_audio_guard_prepares_before_import_and_degrades(monkeypatch, caplog, failure):
    import builtins
    import logging
    from types import SimpleNamespace

    source, guarded_import = _main_audio_import_guard()
    preparation_guard = next(
        node for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Try) and any(
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Name)
            and statement.value.func.id == "prepare_audio_imports"
            for statement in node.body
        )
    )
    original_import = builtins.__import__
    events = []
    audio_module = SimpleNamespace()

    def prepare_audio():
        events.append("preparation")
        if failure in {"preparation", "both"}:
            raise OSError("compatibility preparation failed")

    def controlled_import(name, *args, **kwargs):
        if name == "core.windows_rocm.pipelines":
            return SimpleNamespace(prepare_audio_imports=prepare_audio)
        if name == "torchaudio":
            events.append("torchaudio")
            if failure in {"torchaudio", "both"}:
                raise OSError("native audio import failed")
            return audio_module
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", controlled_import)
    namespace = {"logging": logging}
    blocks = sorted([preparation_guard, guarded_import], key=lambda node: node.lineno)
    block = ast.Module(body=blocks, type_ignores=[])
    exec(compile(block, str(_MAIN), "exec"), namespace)
    assert events == ["preparation", "torchaudio"]
    import_failed = failure in {"torchaudio", "both"}
    assert namespace["torchaudio"] is (None if import_failed else audio_module)
    records = [record for record in caplog.records if record.name == "omnivoice.model"]
    assert len(records) == (1 if import_failed else 0)
    assert all(record.exc_info is None for record in records)
