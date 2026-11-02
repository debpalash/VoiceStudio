"""Recurrence-hardening sweep — regression tests for the audit's gap list.

Each test pins one guard added after the full closed-issue-history audit:
error classes that were fixed but could still recur via an unguarded seam
(a bypassing client, a stale reinstall leftover, a cross-device move, an
OS-level OOM kill, a scaled-up request). See the PR body for the class map.
"""
from __future__ import annotations

import errno
import os

import pytest

os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

_FAKE_AUDIO = b"RIFF" + b"\x00" * 2000


# ── Class 3: instruct poisoning via clone-kind saves ─────────────────────────


@pytest.fixture()
def profiles_client(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_DATA_DIR", str(tmp_path))
    import importlib
    import core.config as _cfg
    importlib.reload(_cfg)
    import core.db as _db
    importlib.reload(_db)
    from api.routers import profiles as _profiles
    importlib.reload(_profiles)
    import main as _main
    importlib.reload(_main)
    _db.init_db()
    from fastapi.testclient import TestClient
    # No context manager: entering it runs the app lifespan, whose shutdown
    # tears down executors shared with other suites in a full run (the repo's
    # profile tests use the same lifespan-free pattern — "schema only").
    yield TestClient(_main.app, client=("127.0.0.1", 50002))


def test_clone_profile_save_sanitizes_instruct(profiles_client):
    """The 400-on-every-use class recurred THREE times via clients that
    bypassed the frontend filter; the server-side save heal was gated to
    design-kind. Clone-kind saves must be generation-safe too."""
    r = profiles_client.post("/profiles", data={
        "name": "poisoned clone",
        "kind": "clone",
        "instruct": "please read this in a very dramatic movie-trailer way!!",
    }, files={"ref_audio": ("ref.wav", _FAKE_AUDIO, "audio/wav")})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    from core.db import db_conn
    with db_conn() as conn:
        row = conn.execute(
            "SELECT instruct FROM voice_profiles WHERE id=?", (pid,)
        ).fetchone()
    persisted = row["instruct"] or ""
    from omnivoice.utils.voice_design import sanitize_instruct
    assert persisted == sanitize_instruct(persisted), (
        "persisted clone instruct is not validator-safe — the #550/#594 class is open again"
    )


# ── Class 5: reinstall inherits a stale env file ─────────────────────────────


def test_user_env_drops_unusable_path_keys(tmp_path, monkeypatch):
    """A reinstall that skipped uninstall inherits ~/.config/omnivoice/env
    verbatim — including a cache dir on an unplugged drive. Dead paths must be
    ignored for the run, not exported."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("a file, so a directory cannot be created beneath it")
    env_file = tmp_path / "env"
    env_file.write_text(
        f"OMNIVOICE_CACHE_DIR={blocker}/impossible/cache\n"
        f"OMNIVOICE_DATA_DIR={tmp_path}/fine\n"
    )
    monkeypatch.delenv("OMNIVOICE_CACHE_DIR", raising=False)
    monkeypatch.setenv("OMNIVOICE_DATA_DIR", "sentinel-overwritten-by-load")

    from core import user_env
    assert user_env.load_into_environ(str(env_file)) is True
    assert "OMNIVOICE_CACHE_DIR" not in os.environ, "dead path was exported anyway"
    assert os.environ["OMNIVOICE_DATA_DIR"] == f"{tmp_path}/fine"  # valid path honored
    assert os.path.isdir(f"{tmp_path}/fine")
    monkeypatch.delenv("OMNIVOICE_DATA_DIR", raising=False)


# ── Class 7: cross-device moves (Windows D:-drive class) ─────────────────────


def test_safe_replace_same_device(tmp_path):
    from utils.fsops import safe_replace
    src, dst = tmp_path / "a.txt", tmp_path / "b.txt"
    src.write_text("payload")
    dst.write_text("old")
    safe_replace(str(src), str(dst))
    assert dst.read_text(encoding="utf-8") == "payload" and not src.exists()


def test_safe_replace_falls_back_on_exdev(tmp_path, monkeypatch):
    """EXDEV (paths on different devices) must degrade to copy+replace, not
    surface as the [Errno 18/22] class Windows users reported."""
    from utils import fsops
    real_replace = os.replace
    calls = {"n": 0}

    def fake_replace(a, b):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_replace(a, b)

    monkeypatch.setattr(fsops.os, "replace", fake_replace)
    src, dst = tmp_path / "a.bin", tmp_path / "b.bin"
    src.write_bytes(b"x" * 4096)
    fsops.safe_replace(str(src), str(dst))
    assert dst.read_bytes() == b"x" * 4096
    assert not src.exists()
    assert calls["n"] == 2  # first raised EXDEV, second landed the temp copy


def test_safe_replace_propagates_real_errors(tmp_path, monkeypatch):
    from utils import fsops

    def fake_replace(a, b):
        raise OSError(errno.EACCES, "denied")

    monkeypatch.setattr(fsops.os, "replace", fake_replace)
    src = tmp_path / "a"
    src.write_text("x")
    with pytest.raises(OSError):
        fsops.safe_replace(str(src), str(tmp_path / "b"))


# ── Class 1: evict-then-load before a tight TTS load ─────────────────────────


def test_make_room_runs_only_when_memory_is_tight(monkeypatch):
    from services import model_manager as mm

    released = {"asr": 0, "vram": 0}
    monkeypatch.setattr(
        "services.memory_budget.available_memory",
        lambda: {"ram_available_gb": 3.0},  # below the 6 GB headroom
    )
    import services.asr_backend as ab
    monkeypatch.setattr(ab, "release_idle_capture_backend",
                        lambda idle_s: released.__setitem__("asr", released["asr"] + 1) or True)
    monkeypatch.setattr(mm, "free_vram", lambda: released.__setitem__("vram", released["vram"] + 1))

    mm._make_room_before_tts_load()
    assert released == {"asr": 1, "vram": 1}, "tight memory must trigger the reclaim"


def test_make_room_is_a_noop_with_headroom(monkeypatch):
    from services import model_manager as mm

    monkeypatch.setattr(
        "services.memory_budget.available_memory",
        lambda: {"ram_available_gb": 12.0},
    )
    called = []
    monkeypatch.setattr(mm, "free_vram", lambda: called.append(1))
    mm._make_room_before_tts_load()
    assert not called, "a roomy machine must pay nothing"


# ── Class 4 (503 wave): timeout scales with the request ──────────────────────


def test_generate_timeout_scales_with_text_length(monkeypatch):
    from api.routers import generation as g

    short = g._generate_timeout_s("hello world", execution_device="cuda")
    long = g._generate_timeout_s(
        "x" * 41_200, execution_device="cuda"
    )  # 40k chars past the free allowance
    assert short == pytest.approx(300.0)          # floor: the configured default
    assert long == pytest.approx(300.0 + 40_000 / 40.0)  # +1s per 40 chars


def test_generate_timeout_env_floor_respected(monkeypatch):
    import importlib
    monkeypatch.setenv("OMNIVOICE_GENERATE_TIMEOUT_S", "900")
    import services.model_manager as mm
    monkeypatch.setattr(mm, "GPU_JOB_TIMEOUT_S", 900.0)
    from api.routers import generation as g
    assert g._generate_timeout_s("short") == pytest.approx(900.0)


def test_user_env_drops_read_only_path(tmp_path, monkeypatch):
    """An existing directory on a read-only mount passes isdir but fails on
    first real use — validation must probe actual write capability."""
    ro = tmp_path / "readonly-cache"
    ro.mkdir()
    ro.chmod(0o500)
    # Verify the premise instead of assuming it. `chmod(0o500)` makes a
    # directory unwritable on POSIX; on Windows it only toggles a read-only
    # FILE attribute and does not stop a file being created inside, so the
    # scenario cannot be built there at all and the probe correctly reports
    # the directory as usable. Root (and anything holding CAP_DAC_OVERRIDE)
    # writes through the mode bits for the same reason. Probing for it covers
    # every such host, including ones no explicit check would name.
    try:
        probe = ro / ".writable-probe"
        probe.touch()
        probe.unlink()
    except OSError:
        pass  # genuinely unwritable — the test can do its work
    else:
        ro.chmod(0o700)
        pytest.skip("this host writes into a mode-0500 directory; no read-only path to test")
    env_file = tmp_path / "env"
    env_file.write_text(f"OMNIVOICE_CACHE_DIR={ro}\n")
    monkeypatch.delenv("OMNIVOICE_CACHE_DIR", raising=False)
    try:
        from core import user_env
        assert user_env.load_into_environ(str(env_file)) is True
        assert "OMNIVOICE_CACHE_DIR" not in os.environ, (
            "read-only path was kept — downloads would fail on first use"
        )
    finally:
        ro.chmod(0o700)


# ── #1133: a library's sys.exit() must not kill the backend ──────────────────


def test_pool_contains_system_exit(monkeypatch):
    """mlx-audio → misaki → spacy.cli.download() calls sys.exit(1) in-process
    when pip is missing (uv venvs ship none); SystemExit is not an Exception,
    so it rode the executor future into the event loop and uvicorn shut the
    whole backend down. The pool boundary must convert it to a normal error."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from services.model_manager import run_on_gpu_pool_guarded

    def _engine_code_that_exits():
        raise SystemExit(1)

    ex = ThreadPoolExecutor(max_workers=1)
    with pytest.raises(RuntimeError, match="SystemExit 1"):
        asyncio.run(run_on_gpu_pool_guarded(
            _engine_code_that_exits, what="TTS generate", executor=ex,
        ))
    # And the pool is still usable — the process (and executor) survived.
    assert asyncio.run(run_on_gpu_pool_guarded(
        lambda: "alive", what="probe", executor=ex,
    )) == "alive"


def test_transcribe_guard_contains_system_exit():
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from services.asr_backend import run_transcribe_guarded

    ex = ThreadPoolExecutor(max_workers=1)

    def _asr_code_that_exits():
        raise SystemExit(2)

    with pytest.raises(RuntimeError, match="SystemExit 2"):
        asyncio.run(run_transcribe_guarded(ex, _asr_code_that_exits, what="ASR"))


def test_managed_venv_ships_pip():
    """#1133 root trigger: uv-managed venvs ship no pip, but engine
    dependencies written as CLIs (spaCy's model downloader, invoked in-process
    by mlx-audio's phonemizer via misaki) shell out to `python -m pip`. pip is
    now a real project dependency so it survives the update drift-sync
    (anything installed ad-hoc would be stripped by `uv sync` on update,
    resurrecting the crash after every release)."""
    # Note: this inspects the interpreter running pytest — which in CI and
    # the packaged app IS the uv-synced managed venv, so it verifies the lock
    # produces these packages (review note on the original phrasing).
    import importlib.util
    assert importlib.util.find_spec("pip") is not None, (
        "pip missing from the managed venv — spaCy-style in-process "
        "downloaders will fail (and pre-#1143, kill the backend)"
    )
    assert importlib.util.find_spec("en_core_web_sm") is not None, (
        "the phonemizer's spaCy model is not bundled — first English "
        "MLX-Audio generation would trigger a raw GitHub download that "
        "bypasses the mirror system and fails offline"
    )
