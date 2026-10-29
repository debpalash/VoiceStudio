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


class _GgufRig:
    """Wires a backend instance to throwaway model files and a fake runner."""

    def __init__(self, monkeypatch, root: Path, runner=None):
        from engines.omnivoice_gguf import backend as mod

        self.mod = mod
        self.root = root
        self.argvs: list = []
        self.instance = mod._make_backend_class()()
        weights, codec = root / "w-Q8_0.gguf", root / "c-Q8_0.gguf"
        weights.write_bytes(b"0")
        codec.write_bytes(b"0")
        monkeypatch.setattr(
            self.instance, "_resolve_quant_paths",
            lambda: (weights, codec, {"base": weights.name, "tokenizer": codec.name}),
        )
        exe = root / "stub-tts-exe"
        exe.write_bytes(b"\x7fELF" + b"\x00" * 16)
        exe.chmod(0o755)
        monkeypatch.setattr(mod, "_binary_path", lambda slug=None: exe)
        self._runner = runner

        def fake_run(argv, **_kw):
            self.argvs.append(list(argv))
            if self._runner:
                self._runner(list(argv))
            _write_3s_wav(Path(argv[argv.index("-o") + 1]))
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)

    def flag_value(self, flag):
        argv = self.argvs[-1]
        return argv[argv.index(flag) + 1]


def test_generate_translates_controls_into_flags(monkeypatch, tmp_path):
    rig = _GgufRig(monkeypatch, tmp_path)
    rig.instance.generate(
        "x", instruct="whispering", duration=1.75, seed=99,
        denoise=False, preprocess_prompt=False, chunk_duration=0, chunk_threshold=500,
    )
    assert rig.flag_value("--instruct") == "whispering"
    assert rig.flag_value("--duration") == "1.75"
    assert rig.flag_value("--seed") == "99"
    assert rig.flag_value("--chunk-duration") == "0.0"
    assert rig.flag_value("--chunk-threshold") == "500.0"
    assert {"--no-denoise", "--no-preprocess-prompt"} <= set(rig.argvs[-1])


def test_generate_without_controls_emits_no_extra_flags(monkeypatch, tmp_path):
    rig = _GgufRig(monkeypatch, tmp_path)
    rig.instance.generate("x")
    optional = {"--instruct", "--duration", "--seed", "--no-denoise", "--no-preprocess-prompt",
                "--chunk-duration", "--chunk-threshold", "--ref-text", "--ref-wav"}
    assert optional.isdisjoint(rig.argvs[-1])


def test_transcript_is_handed_over_as_a_file_then_deleted(monkeypatch, tmp_path):
    clip = tmp_path / "prompt.wav"
    _write_3s_wav(clip)
    seen = {}

    def peek(argv):
        path = Path(argv[argv.index("--ref-text") + 1])
        seen["path"], seen["body"] = path, path.read_bytes().decode("utf-8")

    rig = _GgufRig(monkeypatch, tmp_path, runner=peek)
    rig.instance.generate("x", ref_audio=str(clip), ref_text="caf\u00e9 au lait")
    assert seen["body"] == "caf\u00e9 au lait"
    assert not seen["path"].exists()


def test_transcript_without_ref_audio_is_ignored(monkeypatch, tmp_path):
    rig = _GgufRig(monkeypatch, tmp_path)
    rig.instance.generate("x", ref_text="orphan words")
    assert "--ref-text" not in rig.argvs[-1]


@pytest.mark.parametrize("how", ["runner_raises", "bad_ref_audio"])
def test_transcript_file_never_outlives_a_failed_call(monkeypatch, tmp_path, how):
    clip = tmp_path / "prompt.wav"
    _write_3s_wav(clip)
    staged: list = []
    real_mkstemp = tempfile.mkstemp

    def spy(*a, **kw):
        fd, name = real_mkstemp(*a, **kw)
        if kw.get("suffix") == ".transcript":
            staged.append(Path(name))
        return fd, name

    monkeypatch.setattr(tempfile, "mkstemp", spy)

    def explode(_argv):
        raise RuntimeError("runner failed")

    rig = _GgufRig(monkeypatch, tmp_path, runner=explode if how == "runner_raises" else None)
    if how == "runner_raises":
        with pytest.raises(RuntimeError):
            rig.instance.generate("x", ref_audio=str(clip), ref_text="abc")
    else:
        with pytest.raises(FileNotFoundError):
            rig.instance.generate("x", ref_audio="/nonexistent-root/p.wav", ref_text="abc")
    assert len(staged) == 1 and not staged[0].exists()


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


def _rig_without_exe(monkeypatch, root: Path, exe_bytes: bytes):
    from engines.omnivoice_gguf import backend as mod

    inst = mod._make_backend_class()()
    weights, codec = root / "m-Q8_0.gguf", root / "k-Q8_0.gguf"
    weights.write_bytes(b"0")
    codec.write_bytes(b"0")
    monkeypatch.setattr(
        inst, "_resolve_quant_paths",
        lambda: (weights, codec, {"base": weights.name, "tokenizer": codec.name}),
    )
    exe = root / "tts-candidate"
    exe.write_bytes(exe_bytes)
    exe.chmod(0o755)
    monkeypatch.setattr(mod, "_binary_path", lambda slug=None: exe)
    return inst


def test_empty_executable_is_refused_before_spawn(monkeypatch, tmp_path):
    from services.binary_preflight import InvalidBinaryError

    inst = _rig_without_exe(monkeypatch, tmp_path, b"")
    spawned = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: spawned.append(a))
    with pytest.raises(InvalidBinaryError) as err:
        inst.generate("say this")
    assert spawned == []
    text = str(err.value)
    assert "build-omnivoice-tts.sh" in text
    assert "Exec format error" not in text


def test_kernel_refusal_at_spawn_surfaces_as_typed_error(monkeypatch, tmp_path):
    import errno

    from services.binary_preflight import InvalidBinaryError

    inst = _rig_without_exe(monkeypatch, tmp_path, b"\x7fELF" + b"\x01" * 12)

    def refuse(*_a, **_k):
        raise OSError(errno.ENOEXEC, "wrong architecture")

    monkeypatch.setattr(subprocess, "run", refuse)
    with pytest.raises(InvalidBinaryError, match="build-omnivoice-tts.sh"):
        inst.generate("say this")


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


# ── #3655 — the CLI reads WAV containers only ────────────────────────────────


def _cli_wav_format(data: bytes) -> tuple[int, int]:
    """Return ``(audio_format, bits_per_sample)`` the way the runtime reads it.

    Mirrors ``read_wav_buf`` in the pinned ``omnivoice.cpp`` (``src/wav.h``):
    the container must start with ``RIFF``/``WAVE`` and the ``fmt `` chunk
    must declare a format the runtime decodes — PCM (1) at 16/24 bits or
    IEEE float (3) at 32 bits. Anything else makes the CLI exit with
    "[WAV] Not a valid WAV buffer".
    """
    assert len(data) >= 12, "too short to be a RIFF file"
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE", (
        "not a RIFF/WAVE container"
    )
    audio_format = 0
    bits = 0
    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        body = data[pos + 8:pos + 8 + size]
        if chunk_id == b"fmt " and len(body) >= 16:
            audio_format = int.from_bytes(body[0:2], "little")
            bits = int.from_bytes(body[14:16], "little")
            if audio_format == 0xFFFE and len(body) >= 40:
                # WAVE_FORMAT_EXTENSIBLE collapses to its sub-format.
                sub = int.from_bytes(body[24:26], "little")
                if sub in (1, 3):
                    audio_format = sub
        pos += 8 + size + (size & 1)
    assert audio_format in (1, 3), f"unsupported WAV format tag {audio_format}"
    if audio_format == 1:
        assert bits in (16, 24), f"unsupported PCM width {bits}"
    else:
        assert bits == 32, f"unsupported float width {bits}"
    return audio_format, bits


def _write_3s_ogg(out_path: Path, sr: int = 24_000) -> None:
    """Write a 3-second mono Ogg Vorbis clip — a non-WAV profile reference.

    The profile editor keeps the reference in whatever container the user
    uploaded, so an MP3/M4A/Ogg reference reaches the engine verbatim.
    """
    import math

    import numpy as np
    import soundfile as sf

    t = np.arange(sr * 3, dtype="float32") / sr
    sf.write(out_path, (0.05 * np.sin(2 * math.pi * 440 * t)).astype("float32"),
             sr, format="OGG")


class _PromptRig:
    """Backend wired to a stub runner that records the CLI argv.

    Unlike :class:`_GgufRig` this patches the engine's own spawn method
    instead of ``subprocess.run``, so a test that reaches ffmpeg (the
    fallback decoder) still runs the real subprocess.
    """

    def __init__(self, monkeypatch, tmp_path):
        from engines.omnivoice_gguf import backend as mod

        self.mod = mod
        self.argvs: list[list[str]] = []
        self.ref_bytes: bytes = b""
        self.instance = mod._make_backend_class()()
        weights, codec = tmp_path / "w.gguf", tmp_path / "c.gguf"
        weights.write_bytes(b"0")
        codec.write_bytes(b"0")
        monkeypatch.setattr(
            self.instance, "_resolve_quant_paths",
            lambda: (weights, codec, {"base": weights.name, "codec": codec.name}),
        )
        exe = tmp_path / "stub-tts-exe"
        exe.write_bytes(b"\x7fELF" + b"\x00" * 16)
        exe.chmod(0o755)
        monkeypatch.setattr(mod, "_binary_path", lambda slug=None: exe)

        def fake_run(argv, *, stdin_text):
            self.argvs.append(list(argv))
            # Read the prompt while it still exists: the converted copy is
            # deleted once the call returns.
            ref = Path(argv[argv.index("--ref-wav") + 1]) if "--ref-wav" in argv else None
            self.ref_bytes = ref.read_bytes() if ref is not None else b""
            _write_3s_wav(Path(argv[argv.index("-o") + 1]))

        monkeypatch.setattr(self.instance, "_run_subprocess", fake_run)

    def ref_wav(self) -> Path:
        argv = self.argvs[-1]
        return Path(argv[argv.index("--ref-wav") + 1])


def test_non_wav_reference_is_converted_before_it_reaches_the_cli(monkeypatch, tmp_path):
    """#3655 — a profile reference stored as a non-WAV container must not be
    handed to the CLI verbatim.

    The bundled runtime parses RIFF/WAVE only, so an MP3 (or Ogg, M4A, …)
    reference killed every generation with "[WAV] Not a valid WAV buffer".
    """
    reference = tmp_path / "profile-ref.ogg"
    _write_3s_ogg(reference)

    rig = _PromptRig(monkeypatch, tmp_path)
    rig.instance.generate("x", ref_audio=str(reference), ref_text="hello there")

    handed_over = rig.ref_wav()
    assert handed_over != reference.resolve(), "the raw profile clip was handed to the CLI"
    # The file the CLI receives must satisfy the runtime's WAV contract.
    _cli_wav_format(rig.ref_bytes)
    # The converted copy is transient.
    assert not handed_over.exists()
    # The stored profile clip is left untouched.
    assert reference.exists()


def test_wav_reference_is_handed_to_the_cli_unchanged(monkeypatch, tmp_path):
    """A WAV reference is already CLI-readable — it must not be re-encoded."""
    reference = tmp_path / "profile-ref.wav"
    _write_3s_wav(reference)

    rig = _PromptRig(monkeypatch, tmp_path)
    rig.instance.generate("x", ref_audio=str(reference), ref_text="hello there")

    assert rig.ref_wav() == reference.resolve()


def test_unreadable_reference_reports_an_actionable_error(monkeypatch, tmp_path):
    """A reference that cannot be decoded fails with a readable message
    instead of the CLI's "Not a valid WAV buffer"."""
    broken = tmp_path / "profile-ref.ogg"
    broken.write_bytes(b"this is not audio")

    rig = _PromptRig(monkeypatch, tmp_path)
    with pytest.raises(ValueError) as exc_info:
        rig.instance.generate("x", ref_audio=str(broken), ref_text="hello there")
    assert "reference" in str(exc_info.value).lower()
    assert not rig.argvs, "the CLI must not be spawned with an unusable reference"

def test_oversized_reference_is_refused_before_it_is_decoded(monkeypatch, tmp_path):
    """A reference whose decoded size does not fit the backend's budget is
    refused with an actionable message instead of being decoded.

    The upload path caps a reference at 128 MB on disk, but a lossy
    container decodes to far more than that, and the conversion decodes it
    in the backend process.
    """
    from engines.omnivoice_gguf import backend as gguf_backend

    reference = tmp_path / "profile-ref.ogg"
    _write_3s_ogg(reference)
    monkeypatch.setattr(
        gguf_backend,
        "_reference_decoded_bytes",
        lambda path: gguf_backend._MAX_REFERENCE_DECODED_BYTES + 1,
    )

    rig = _PromptRig(monkeypatch, tmp_path)
    with pytest.raises(ValueError) as exc_info:
        rig.instance.generate("x", ref_audio=str(reference), ref_text="hello there")
    assert "too long" in str(exc_info.value).lower()
    assert not rig.argvs, "the CLI must not be spawned with an oversized reference"


def test_reference_size_is_read_without_decoding(monkeypatch, tmp_path):
    """The size guard reads the clip's header, so nothing is decoded.

    A guard that decoded the clip to size it would defeat itself, so the
    sample-reading path is made to fail: the size must still come back.
    """
    import soundfile as sf

    from engines.omnivoice_gguf import backend as gguf_backend

    reference = tmp_path / "profile-ref.ogg"
    _write_3s_ogg(reference)

    def _explode(*_a, **_k):
        raise AssertionError("the reference must not be decoded to size it")

    monkeypatch.setattr(sf, "read", _explode)

    size = gguf_backend._reference_decoded_bytes(reference)
    assert size == pytest.approx(3 * 24_000 * 4, rel=0.01)


def test_reference_of_unknown_size_is_refused_before_it_is_decoded(monkeypatch, tmp_path):
    """A reference whose header cannot be read is refused, not decoded.

    An unbounded decode is exactly what the budget exists to prevent, so a
    clip of unknown size never reaches the decoder.
    """
    import services.audio_io as audio_io

    from engines.omnivoice_gguf import backend as gguf_backend

    unknown = tmp_path / "profile-ref.m4a"
    unknown.write_bytes(b"not a container libsndfile can size")

    decoded: list = []

    def _explode(*_a, **_k):
        decoded.append(True)
        raise AssertionError("an unsized reference must not be decoded")

    monkeypatch.setattr(audio_io, "load_audio", _explode)

    with pytest.raises(ValueError) as exc_info:
        gguf_backend._convert_reference_to_wav(unknown)
    assert not decoded, "an unsized reference must not be decoded"
    assert "could not be read" in str(exc_info.value)


def test_loader_size_limit_error_is_kept(monkeypatch, tmp_path):
    """The loader's own size-limit error stays as it is.

    It already says "use a shorter clip"; turning it into a re-upload
    hint would send the user after the wrong fix.
    """
    import services.audio_io as audio_io

    from engines.omnivoice_gguf import backend as gguf_backend

    reference = tmp_path / "profile-ref.ogg"
    _write_3s_ogg(reference)

    def _too_big(*_a, **_k):
        raise ValueError("Audio exceeds the decoding size limit; use a shorter clip.")

    monkeypatch.setattr(audio_io, "load_audio", _too_big)

    with pytest.raises(ValueError) as exc_info:
        gguf_backend._convert_reference_to_wav(reference)
    assert "shorter clip" in str(exc_info.value)
    assert "re-upload" not in str(exc_info.value)


def _fake_ffprobe(monkeypatch, payload: dict) -> None:
    """Make the engine's ffprobe probe report ``payload``.

    The probe's *result* is mocked rather than a stand-in binary: a
    shebang script is not launchable on Windows, and the probe is the
    only thing under test here.
    """
    def _fake_run(argv, **_kw):
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload).encode("utf-8"), b"")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setattr("services.ffmpeg_utils.find_ffprobe", lambda: "ffprobe")


def test_reference_size_falls_back_to_the_audio_stream(monkeypatch, tmp_path):
    """A container libsndfile cannot read is measured with ffprobe.

    The audio stream's own duration is used, not the container's: a video
    whose audio track is short is a short reference.
    """
    from engines.omnivoice_gguf import backend as gguf_backend

    reference = tmp_path / "profile-ref.m4a"
    reference.write_bytes(b"not a container libsndfile can read")

    _fake_ffprobe(monkeypatch, {
        "streams": [{"duration": "3.000000", "channels": 1, "sample_rate": 24000}],
    })

    size = gguf_backend._reference_decoded_bytes(reference)
    assert size == pytest.approx(3 * 24_000 * 4, rel=0.01)


def test_oversized_reference_is_refused_when_ffprobe_sizes_it(monkeypatch, tmp_path):
    """The limit applies to a clip ffprobe had to measure."""
    from engines.omnivoice_gguf import backend as gguf_backend

    reference = tmp_path / "profile-ref.m4a"
    reference.write_bytes(b"not a container libsndfile can read")

    # Two hours of 48 kHz stereo float32 is well past the budget.
    _fake_ffprobe(monkeypatch, {
        "streams": [{"duration": "7200.000000", "channels": 2, "sample_rate": 48000}],
    })

    with pytest.raises(ValueError) as exc_info:
        gguf_backend._convert_reference_to_wav(reference)
    assert "too long" in str(exc_info.value).lower()
