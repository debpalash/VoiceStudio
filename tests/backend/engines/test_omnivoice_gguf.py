"""Unit + integration tests for backend/engines/omnivoice_gguf (Plan 04-01).

Behavioural coverage matches the plan's <verify> bullets:

  * quant_map.json round-trips through json.load(), schema_version is 1,
    every compute class has base+tokenizer keys with valid filenames,
    and _meta.source_commit_sha / _meta.runtime_commit_sha are real
    40-char hex SHAs.
  * is_available() returns (False, reason) when the binary is missing.
  * is_available() returns (False, reason) on SHA-256 mismatch against
    bin/checksums.sha256.
  * probe_load() raises subprocess.TimeoutExpired when the binary hangs
    past the timeout.
  * generate() with a stubbed subprocess that writes a known 3-second
    24 kHz WAV returns a torch.Tensor of shape (1, 72000).
  * select_default_engine() returns "omnivoice-gguf" when probe succeeds
    and "omnivoice" when it fails (mocked).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


QUANT_FILENAME_RE = re.compile(
    r"omnivoice-(base|tokenizer)-(Q4_K_M|Q8_0|BF16|F32)\.gguf"
)


def test_quant_map_valid():
    """GGUF-02 — quant_map.json is well-formed and pinned to real SHAs."""
    from engines.omnivoice_gguf.backend import _PKG_DIR  # type: ignore[attr-defined]

    quant_map_path = _PKG_DIR / "quant_map.json"
    assert quant_map_path.is_file(), quant_map_path

    # utf-8 named for the same reason the backend names it: a bare open()
    # decodes in the locale code page, which cannot read this file's em dashes
    # on a Chinese, Japanese or Korean Windows
    # (tests/test_repo_data_locale_decoding.py).
    with quant_map_path.open(encoding="utf-8") as f:
        data = json.load(f)

    meta = data.get("_meta")
    assert isinstance(meta, dict), "quant_map.json must have _meta block"
    assert meta["schema_version"] == 1
    assert meta["source_model"] == "Serveurperso/OmniVoice-GGUF"
    assert meta["runtime"] == "omnivoice.cpp"

    for key in ("source_commit_sha", "runtime_commit_sha"):
        sha = meta[key]
        assert isinstance(sha, str)
        assert re.fullmatch(r"[0-9a-fA-F]{40}", sha), (
            f"{key} must be a 40-char hex SHA (ADR requires real pin); got {sha!r}"
        )

    for compute_class in ("cpu", "low-vram", "mid-vram", "high-vram"):
        assert compute_class in data, f"missing compute class: {compute_class}"
        entry = data[compute_class]
        assert isinstance(entry, dict)
        for k in ("base", "tokenizer"):
            assert k in entry
            assert QUANT_FILENAME_RE.fullmatch(entry[k]), (
                f"{compute_class}.{k} = {entry[k]!r} doesn't match quant filename pattern"
            )

    # Any _extras override-only entries (e.g. F32) must follow the same
    # filename pattern so the allow-list stays defensible against
    # T-04-05 freeform paths.
    if "_extras" in data:
        for sub_key, sub_entry in data["_extras"].items():
            if not isinstance(sub_entry, dict) or "base" not in sub_entry:
                continue
            for k in ("base", "tokenizer"):
                assert QUANT_FILENAME_RE.fullmatch(sub_entry[k]), (
                    f"_extras.{sub_key}.{k} = {sub_entry[k]!r} doesn't match pattern"
                )


def test_is_available_when_binary_missing(monkeypatch, tmp_path):
    """GGUF-03 — engine reports unavailable cleanly when the binary file
    is absent on this platform."""
    from engines.omnivoice_gguf import backend as gguf_backend

    cls = gguf_backend._make_backend_class()

    # Point _binary_path at a non-existent file.
    monkeypatch.setattr(
        gguf_backend,
        "_binary_path",
        lambda slug=None: tmp_path / "does-not-exist",
    )
    ok, reason = cls.is_available()
    assert ok is False
    assert "missing" in reason.lower()


def test_is_available_when_sha_mismatch(monkeypatch, tmp_path):
    """T-04-01 — bundled-binary tampering detection."""
    from engines.omnivoice_gguf import backend as gguf_backend

    cls = gguf_backend._make_backend_class()

    # Create a fake binary on disk with known contents (valid ELF magic so
    # the #1172 executable preflight passes and the checksum check is what
    # gets exercised).
    fake_bin = tmp_path / "omnivoice-tts-linux-x86_64"
    fake_bin.write_bytes(b"\x7fELFhello world")
    monkeypatch.setattr(gguf_backend, "_binary_path", lambda slug=None: fake_bin)

    # Force the manifest loader to return a WRONG checksum.
    monkeypatch.setattr(
        gguf_backend,
        "_load_checksum_manifest",
        lambda: {"omnivoice-tts-linux-x86_64": "0" * 64},
    )
    # macOS quarantine detection is no-op on non-darwin; on darwin we
    # also need to short-circuit it for this test.
    monkeypatch.setattr(gguf_backend, "_is_macos_quarantined", lambda p: False)

    ok, reason = cls.is_available()
    assert ok is False
    assert "checksum mismatch" in reason.lower()


@pytest.fixture
def cached_weights(monkeypatch, tmp_path):
    """Install the pinned GGUF pair in a throwaway HuggingFace cache.

    Readiness covers the weights as well as the binary, so a test that
    expects "ready" has to supply the pair it claims to have — the
    engine loads only from the local cache and never downloads.
    """
    from huggingface_hub import constants

    from engines.omnivoice_gguf.backend import _load_quant_map

    quant_map = _load_quant_map()
    revision = quant_map["_meta"]["source_commit_sha"]
    entry = quant_map["cpu"]
    cache = tmp_path / "hub"
    folder = cache / "models--Serveurperso--OmniVoice-GGUF" / "snapshots" / revision
    folder.mkdir(parents=True)
    for name in (entry["base"], entry["tokenizer"]):
        (folder / name).write_bytes(b"gguf-weights")
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(cache))
    # A clean compute environment: an ambient CPU pin or native backend
    # would change which quant the engine looks for.
    for variable in ("OMNIVOICE_DEVICE", "OMNIVOICE_GGUF_BACKEND",
                     "OMNIVOICE_GGUF_DEVICE", "GGML_BACKEND"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("OMNIVOICE_DEVICE", "auto")
    return cache


def test_is_available_passes_when_manifest_absent(monkeypatch, tmp_path, cached_weights):
    """When checksums.sha256 is missing we still report ready — the manifest
    is an integrity check, not a hard requirement (binaries are bundled
    via the installer, not by `pip`)."""
    from engines.omnivoice_gguf import backend as gguf_backend

    cls = gguf_backend._make_backend_class()

    fake_bin = tmp_path / "omnivoice-tts-linux-x86_64"
    fake_bin.write_bytes(b"\x7fELF-real-enough")
    monkeypatch.setattr(gguf_backend, "_binary_path", lambda slug=None: fake_bin)
    monkeypatch.setattr(gguf_backend, "_load_checksum_manifest", lambda: {})
    monkeypatch.setattr(gguf_backend, "_is_macos_quarantined", lambda p: False)

    ok, reason = cls.is_available()
    assert ok is True, reason


def test_probe_load_timeout(monkeypatch, tmp_path):
    """T-04-06 — a hung binary surfaces as subprocess.TimeoutExpired."""
    from engines.omnivoice_gguf import backend as gguf_backend

    cls = gguf_backend._make_backend_class()

    fake_bin = tmp_path / "omnivoice-tts-linux-x86_64"
    fake_bin.write_bytes(b"\x7fELF-real-enough")
    monkeypatch.setattr(gguf_backend, "_binary_path", lambda slug=None: fake_bin)
    monkeypatch.setattr(gguf_backend, "_load_checksum_manifest", lambda: {})
    monkeypatch.setattr(gguf_backend, "_is_macos_quarantined", lambda p: False)

    def _hang(*a, **kw):
        raise subprocess.TimeoutExpired(cmd=a[0] if a else "x", timeout=0.1)

    monkeypatch.setattr(subprocess, "run", _hang)

    with pytest.raises(subprocess.TimeoutExpired):
        cls.probe_load(timeout=0.1)


def test_probe_load_filenotfound(monkeypatch, tmp_path):
    """A missing binary surfaces as RuntimeError from is_available
    (not FileNotFoundError) because is_available short-circuits first."""
    from engines.omnivoice_gguf import backend as gguf_backend

    cls = gguf_backend._make_backend_class()
    monkeypatch.setattr(
        gguf_backend,
        "_binary_path",
        lambda slug=None: tmp_path / "missing",
    )
    with pytest.raises(RuntimeError):
        cls.probe_load(timeout=1.0)


def _write_3s_wav(out_path: Path, sr: int = 24_000) -> None:
    """Write a 3-second 24 kHz mono WAV of low-amplitude sine to ``out_path``."""
    import math
    import struct
    import wave

    n = sr * 3  # 3 seconds
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # 16-bit PCM
        w.setframerate(sr)
        for i in range(n):
            sample = int(1000 * math.sin(2 * math.pi * 440 * i / sr))
            w.writeframesraw(struct.pack("<h", sample))


def test_generate_returns_tensor_from_stub_wav(monkeypatch, tmp_path):
    """T-04 happy path: a stubbed subprocess writes a 3-second 24 kHz WAV
    to the `-o` path, the backend reads it back as a (1, 72000) float32
    tensor."""
    from engines.omnivoice_gguf import backend as gguf_backend
    import torch

    cls = gguf_backend._make_backend_class()
    backend = cls()

    # Avoid touching huggingface_hub — fake the path resolver.
    fake_base = tmp_path / "omnivoice-base-Q8_0.gguf"
    fake_tok = tmp_path / "omnivoice-tokenizer-Q8_0.gguf"
    fake_base.write_bytes(b"x")
    fake_tok.write_bytes(b"x")
    monkeypatch.setattr(
        backend,
        "_resolve_quant_paths",
        lambda: (fake_base, fake_tok, {"base": "omnivoice-base-Q8_0.gguf",
                                       "tokenizer": "omnivoice-tokenizer-Q8_0.gguf"}),
    )

    # Stub the binary path itself — we won't spawn anything.
    # The #1172 pre-exec preflight reads the binary, so the stub must exist
    # on disk with a real executable magic.
    fake_tts = tmp_path / "fake-omnivoice-tts"
    fake_tts.write_bytes(b"\x7fELF-real-enough")
    monkeypatch.setattr(gguf_backend, "_binary_path", lambda slug=None: fake_tts)

    # Stub subprocess.run: extract the `-o` arg from argv, write a real
    # 3-second WAV to it, return success.
    captured: dict = {}

    def _stub_run(argv, *, input=None, text=None, capture_output=None,
                  timeout=None, check=None, **_kw):
        # Find the -o arg and write a WAV there.
        for i, a in enumerate(argv):
            if a == "-o":
                _write_3s_wav(Path(argv[i + 1]))
                break
        captured["argv"] = argv
        captured["stdin"] = input
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _stub_run)

    tensor = backend.generate("hello world")
    assert isinstance(tensor, torch.Tensor)
    assert tensor.shape == (1, 72_000), tensor.shape
    assert tensor.dtype == torch.float32

    # Sanity-check the argv shape — uses --model, --codec, -o, no shell.
    argv = captured["argv"]
    assert "--model" in argv
    assert "--codec" in argv
    assert "-o" in argv
    assert captured["stdin"] == "hello world"


def test_generate_forwards_control_arguments(monkeypatch, tmp_path):
    """The GGUF binary exposes quality/prosody controls; the adapter must not
    silently drop them."""
    from engines.omnivoice_gguf import backend as gguf_backend

    cls = gguf_backend._make_backend_class()
    backend = cls()

    fake_base = tmp_path / "omnivoice-base-Q8_0.gguf"
    fake_tok = tmp_path / "omnivoice-tokenizer-Q8_0.gguf"
    fake_base.write_bytes(b"x")
    fake_tok.write_bytes(b"x")
    monkeypatch.setattr(
        backend,
        "_resolve_quant_paths",
        lambda: (fake_base, fake_tok, {"base": "omnivoice-base-Q8_0.gguf",
                                       "tokenizer": "omnivoice-tokenizer-Q8_0.gguf"}),
    )
    # The #1172 pre-exec preflight reads the binary, so the stub must exist
    # on disk with a real executable magic.
    fake_tts = tmp_path / "fake-omnivoice-tts"
    fake_tts.write_bytes(b"\x7fELF-real-enough")
    monkeypatch.setattr(gguf_backend, "_binary_path", lambda slug=None: fake_tts)

    captured: dict = {}

    def _stub_run(argv, *, input=None, text=None, capture_output=None,
                  timeout=None, check=None, **_kw):
        for i, a in enumerate(argv):
            if a == "-o":
                _write_3s_wav(Path(argv[i + 1]))
                break
        captured["argv"] = argv
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _stub_run)

    backend.generate(
        "hello world",
        instruct="calm narrator",
        duration=3.2,
        seed=1234,
        denoise=False,
        preprocess_prompt=False,
        chunk_duration=0,
        chunk_threshold=999,
    )

    argv = captured["argv"]
    assert argv[argv.index("--instruct") + 1] == "calm narrator"
    assert argv[argv.index("--duration") + 1] == "3.2"
    assert argv[argv.index("--seed") + 1] == "1234"
    assert "--no-denoise" in argv
    assert "--no-preprocess-prompt" in argv
    assert argv[argv.index("--chunk-duration") + 1] == "0.0"
    assert argv[argv.index("--chunk-threshold") + 1] == "999.0"


def test_generate_passes_ref_text_as_temporary_file(monkeypatch, tmp_path):
    from engines.omnivoice_gguf import backend as gguf_backend

    cls = gguf_backend._make_backend_class()
    backend = cls()
    fake_base = tmp_path / "omnivoice-base-Q8_0.gguf"
    fake_tok = tmp_path / "omnivoice-tokenizer-Q8_0.gguf"
    fake_ref = tmp_path / "reference.wav"
    fake_base.write_bytes(b"x")
    fake_tok.write_bytes(b"x")
    fake_ref.write_bytes(b"x")
    monkeypatch.setattr(
        backend,
        "_resolve_quant_paths",
        lambda: (fake_base, fake_tok, {"base": fake_base.name,
                                       "tokenizer": fake_tok.name}),
    )
    # The #1172 pre-exec preflight reads the binary, so the stub must exist
    # on disk with a real executable magic.
    fake_tts = tmp_path / "fake-omnivoice-tts"
    fake_tts.write_bytes(b"\x7fELF-real-enough")
    monkeypatch.setattr(gguf_backend, "_binary_path", lambda slug=None: fake_tts)

    captured: dict = {}

    def _stub_run(argv, *, input=None, text=None, capture_output=None,
                  timeout=None, check=None, **_kw):
        ref_text_path = Path(argv[argv.index("--ref-text") + 1])
        captured["ref_text_path"] = ref_text_path
        captured["ref_text"] = ref_text_path.read_text(encoding="utf-8")
        for i, arg in enumerate(argv):
            if arg == "-o":
                _write_3s_wav(Path(argv[i + 1]))
                break
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _stub_run)

    backend.generate(
        "hello world",
        ref_audio=str(fake_ref),
        ref_text="reference transcript",
    )

    assert captured["ref_text"] == "reference transcript"
    assert not captured["ref_text_path"].exists()


def test_generate_blocks_freeform_ref_audio(monkeypatch, tmp_path):
    """T-04-02 — ref_audio path that doesn't exist is rejected before
    spawning the subprocess."""
    from engines.omnivoice_gguf import backend as gguf_backend

    cls = gguf_backend._make_backend_class()
    backend = cls()

    fake_base = tmp_path / "b.gguf"
    fake_tok = tmp_path / "t.gguf"
    fake_base.write_bytes(b"x")
    fake_tok.write_bytes(b"x")
    monkeypatch.setattr(
        backend,
        "_resolve_quant_paths",
        lambda: (fake_base, fake_tok, {"base": "b", "tokenizer": "t"}),
    )
    monkeypatch.setattr(
        gguf_backend, "_binary_path", lambda slug=None: tmp_path / "fake",
    )

    # subprocess.run would never be called if validation works; if it
    # does run we'd FAIL.
    def _explode(*a, **kw):
        raise AssertionError("subprocess.run should not have been called")

    monkeypatch.setattr(subprocess, "run", _explode)

    with pytest.raises(FileNotFoundError):
        backend.generate("hi", ref_audio="/etc/shadow")


def test_select_default_returns_gguf_on_success(monkeypatch):
    """GGUF-05 happy path — probe + load succeed → "omnivoice-gguf" wins."""
    from engines.omnivoice_gguf import backend as gguf_backend

    cls = gguf_backend._make_backend_class()
    monkeypatch.setattr(cls, "is_available", classmethod(lambda c: (True, "ready")))
    monkeypatch.setattr(cls, "probe_load", classmethod(lambda c, **k: None))

    assert gguf_backend.select_default_engine() == "omnivoice-gguf"


def test_select_default_falls_back_on_probe_failure(monkeypatch):
    """GGUF-05 fallback — probe failure → in-process "omnivoice"."""
    from engines.omnivoice_gguf import backend as gguf_backend

    cls = gguf_backend._make_backend_class()
    monkeypatch.setattr(cls, "is_available", classmethod(lambda c: (True, "ready")))

    def _boom(c, **k):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(cls, "probe_load", classmethod(_boom))
    assert gguf_backend.select_default_engine() == "omnivoice"


def test_select_default_falls_back_when_unavailable(monkeypatch):
    """GGUF-05 fallback — is_available False → in-process "omnivoice"."""
    from engines.omnivoice_gguf import backend as gguf_backend

    cls = gguf_backend._make_backend_class()
    monkeypatch.setattr(
        cls,
        "is_available",
        classmethod(lambda c: (False, "binary missing")),
    )
    assert gguf_backend.select_default_engine() == "omnivoice"


def test_registry_resolves_omnivoice_gguf_lazily():
    """The _LAZY_REGISTRY plumbing should produce the backend class on
    first `_REGISTRY["omnivoice-gguf"]` access without forcing the
    huggingface_hub / torch chain at module-load time."""
    from services.tts_backend import _REGISTRY, _LAZY_REGISTRY

    assert "omnivoice-gguf" in _LAZY_REGISTRY
    cls = _REGISTRY["omnivoice-gguf"]
    assert cls.id == "omnivoice-gguf"
    assert cls.display_name == "OmniVoice (GGUF, hardware-adaptive)"


def test_no_shell_true_in_engine_code():
    """Plan 04-01 verification: no `shell=True` token in the engine
    package's executable source — only in docstring prose that explains
    why we don't use it.

    Use a Python tokenizer so the check is robust against the multiple
    docstrings that legitimately mention the term in prose (e.g.
    "Never uses ``shell=True``").
    """
    import io
    import tokenize

    from engines.omnivoice_gguf.backend import _PKG_DIR

    for path in _PKG_DIR.rglob("*.py"):
        # Python source is UTF-8, and it is re-encoded as UTF-8 on the next
        # line — reading it in the locale code page never made sense.
        source = path.read_text(encoding="utf-8")
        toks = tokenize.tokenize(io.BytesIO(source.encode("utf-8")).readline)
        prev_name: str | None = None
        prev_op: str | None = None
        for tok in toks:
            if tok.type == tokenize.NAME and tok.string == "shell":
                prev_name = "shell"
                continue
            if prev_name == "shell" and tok.type == tokenize.OP and tok.string == "=":
                prev_op = "="
                prev_name = None
                continue
            if prev_op == "=" and tok.type == tokenize.NAME and tok.string == "True":
                raise AssertionError(
                    f"shell=True found at {path}:{tok.start[0]}"
                )
            prev_name = None
            prev_op = None


# ── #1172: zero-byte placeholder / invalid binary preflight ────────────────


def test_is_available_rejects_zero_byte_placeholder(monkeypatch, tmp_path):
    """#1172 — a source checkout ships 0-byte placeholders in bin/ and no
    checksums.sha256; is_available() used to bless them as (True, "ready")
    and the request died at spawn with '[Errno 8] Exec format error'."""
    from engines.omnivoice_gguf import backend as gguf_backend

    cls = gguf_backend._make_backend_class()

    placeholder = tmp_path / "omnivoice-tts-darwin-arm64"
    placeholder.write_bytes(b"")
    placeholder.chmod(0o755)  # mode of the shipped placeholder in the report
    monkeypatch.setattr(gguf_backend, "_binary_path", lambda slug=None: placeholder)
    monkeypatch.setattr(gguf_backend, "_load_checksum_manifest", lambda: {})
    monkeypatch.setattr(gguf_backend, "_is_macos_quarantined", lambda p: False)

    ok, reason = cls.is_available()
    assert ok is False
    # Actionable: names the build script and the placeholder cause.
    assert "build-omnivoice-tts.sh" in reason
    assert "placeholder" in reason.lower() or "empty" in reason.lower()


def test_is_available_rejects_garbage_binary(monkeypatch, tmp_path):
    """#1172 class — non-empty but not a real executable (truncated
    download / HTML error page / text file) must also be rejected."""
    from engines.omnivoice_gguf import backend as gguf_backend

    cls = gguf_backend._make_backend_class()

    fake_bin = tmp_path / "omnivoice-tts-linux-x86_64"
    fake_bin.write_bytes(b"<html>503 Service Unavailable</html>")
    monkeypatch.setattr(gguf_backend, "_binary_path", lambda slug=None: fake_bin)
    monkeypatch.setattr(gguf_backend, "_load_checksum_manifest", lambda: {})
    monkeypatch.setattr(gguf_backend, "_is_macos_quarantined", lambda p: False)

    ok, reason = cls.is_available()
    assert ok is False
    assert "not a usable executable" in reason


@pytest.mark.skipif(
    os.name == "nt",
    reason=(
        "POSIX exec bits do not exist on Windows: os.access(path, os.X_OK) is "
        "true for any file that exists, so the assertion below can never fail "
        "there and the whole module errored on a Windows checkout instead."
    ),
)
def test_is_available_does_not_chmod_placeholder(monkeypatch, tmp_path):
    """The #437 exec-bit self-heal must never bless a placeholder: with no
    manifest present, no SHA check confirmed the file, so chmod +x on an
    invalid binary just converts a clean skip into an exec-time crash."""
    from engines.omnivoice_gguf import backend as gguf_backend

    cls = gguf_backend._make_backend_class()

    placeholder = tmp_path / "omnivoice-tts-linux-x86_64"
    placeholder.write_bytes(b"")
    placeholder.chmod(0o644)
    monkeypatch.setattr(gguf_backend, "_binary_path", lambda slug=None: placeholder)
    monkeypatch.setattr(gguf_backend, "_load_checksum_manifest", lambda: {})
    monkeypatch.setattr(gguf_backend, "_is_macos_quarantined", lambda p: False)

    ok, _reason = cls.is_available()
    assert ok is False
    assert not os.access(placeholder, os.X_OK), (
        "is_available() chmod +x'd an invalid placeholder binary"
    )


def test_generate_zero_byte_binary_raises_typed_actionable_error(
    monkeypatch, tmp_path
):
    """#1172 end-to-end at the engine layer: generate() against a 0-byte
    binary raises InvalidBinaryError (typed, actionable) BEFORE exec —
    never the raw OSError '[Errno 8] Exec format error'."""
    from engines.omnivoice_gguf import backend as gguf_backend
    from services.binary_preflight import InvalidBinaryError

    cls = gguf_backend._make_backend_class()
    backend = cls()

    fake_base = tmp_path / "omnivoice-base-Q8_0.gguf"
    fake_tok = tmp_path / "omnivoice-tokenizer-Q8_0.gguf"
    fake_base.write_bytes(b"x")
    fake_tok.write_bytes(b"x")
    monkeypatch.setattr(
        backend,
        "_resolve_quant_paths",
        lambda: (fake_base, fake_tok, {"base": fake_base.name,
                                       "tokenizer": fake_tok.name}),
    )
    placeholder = tmp_path / "omnivoice-tts-darwin-arm64"
    placeholder.write_bytes(b"")
    placeholder.chmod(0o755)
    monkeypatch.setattr(gguf_backend, "_binary_path", lambda slug=None: placeholder)

    def _explode(*a, **kw):
        raise AssertionError("subprocess.run must not be called for a placeholder")

    monkeypatch.setattr(subprocess, "run", _explode)

    with pytest.raises(InvalidBinaryError) as exc_info:
        backend.generate("hello")
    msg = str(exc_info.value)
    assert "Exec format error" not in msg
    assert "build-omnivoice-tts.sh" in msg


def test_generate_enoexec_at_spawn_becomes_typed_error(monkeypatch, tmp_path):
    """#1172 TOCTOU leg: the file passes preflight but the OS still refuses
    to exec it (wrong arch) — the OSError is converted to the same typed,
    actionable InvalidBinaryError."""
    import errno

    from engines.omnivoice_gguf import backend as gguf_backend
    from services.binary_preflight import InvalidBinaryError

    cls = gguf_backend._make_backend_class()
    backend = cls()

    fake_base = tmp_path / "omnivoice-base-Q8_0.gguf"
    fake_tok = tmp_path / "omnivoice-tokenizer-Q8_0.gguf"
    fake_base.write_bytes(b"x")
    fake_tok.write_bytes(b"x")
    monkeypatch.setattr(
        backend,
        "_resolve_quant_paths",
        lambda: (fake_base, fake_tok, {"base": fake_base.name,
                                       "tokenizer": fake_tok.name}),
    )
    fake_tts = tmp_path / "omnivoice-tts-linux-x86_64"
    fake_tts.write_bytes(b"\x7fELF-wrong-arch")
    monkeypatch.setattr(gguf_backend, "_binary_path", lambda slug=None: fake_tts)

    def _enoexec(argv, **kw):
        raise OSError(errno.ENOEXEC, "Exec format error", argv[0])

    monkeypatch.setattr(subprocess, "run", _enoexec)

    with pytest.raises(InvalidBinaryError) as exc_info:
        backend.generate("hello")
    assert "build-omnivoice-tts.sh" in str(exc_info.value)


def test_select_default_falls_back_on_zero_byte_placeholder(monkeypatch, tmp_path):
    """#1172 integration — with the real is_available() logic and a 0-byte
    placeholder, the default-engine resolver must pick the in-process
    fallback (and never raise)."""
    from engines.omnivoice_gguf import backend as gguf_backend

    placeholder = tmp_path / "omnivoice-tts-darwin-arm64"
    placeholder.write_bytes(b"")
    placeholder.chmod(0o755)
    monkeypatch.setattr(gguf_backend, "_binary_path", lambda slug=None: placeholder)
    monkeypatch.setattr(gguf_backend, "_load_checksum_manifest", lambda: {})
    monkeypatch.setattr(gguf_backend, "_is_macos_quarantined", lambda p: False)

    assert gguf_backend.select_default_engine() == "omnivoice"
