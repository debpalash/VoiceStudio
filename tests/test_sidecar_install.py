"""One-click sidecar-engine provisioner (services.sidecar_install).

The provisioner replaces IndexTTS-2's four manual terminal steps (clone,
venv, `uv pip install -e .`, set OMNIVOICE_INDEXTTS_DIR) with a resumable
background job. These tests run the job with git/uv/httpx/HF mocked and
cover: the happy path, the disk-space preflight, the git-absent tarball
fallback, partial-install repair, already-installed detection, uninstall
safety (never delete a user's own clone), and the router wiring.
"""
import io
import os
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

from services import sidecar_install as si


_GIB = 1024 ** 3


# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch, tmp_path):
    """Hermetic per-test state: managed root under tmp, no leaked jobs/env.

    Rebinds the module-level ``si`` to the LIVE ``services.sidecar_install``
    module: other suites purge ``sys.modules["services"]`` for DB isolation,
    so the object imported at collection time can differ from the one the
    engines router imports at call time — patching the stale copy would make
    the router tests order-dependent.
    """
    import importlib
    global si
    si = importlib.import_module("services.sidecar_install")
    monkeypatch.setattr(si, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(si, "_jobs", {})
    monkeypatch.delenv("OMNIVOICE_INDEXTTS_DIR", raising=False)
    monkeypatch.delenv("OMNIVOICE_FAKE_SIDE_DIR", raising=False)
    monkeypatch.delenv("OMNIVOICE_DESKTOP_CONTAINED", raising=False)
    # Set-then-delete: a bare delenv of an unset var records nothing to
    # restore, so a path an install test persists would leak into later
    # suites (an engine would then find a venv that no longer exists).
    for spec in si.SPECS.values():
        monkeypatch.setenv(spec.env_var, "")
        monkeypatch.delenv(spec.env_var)
    yield


def _mk_spec(**over) -> si.SidecarSpec:
    calls = over.pop("_calls", {})
    defaults = dict(
        engine_id="fake-side",
        display_name="Fake Sidecar",
        repo_url="https://github.com/example/fake-side.git",
        tarball_url="https://github.com/example/fake-side/archive/refs/heads/main.tar.gz",
        checkout_dirname="fake-side",
        env_var="OMNIVOICE_FAKE_SIDE_DIR",
        probe_module="fake_side.infer",
        weights_repo_id=None,
        required_bytes=1 * _GIB,
        invalidate=lambda: calls.setdefault("invalidated", 0) or calls.update(
            invalidated=calls.get("invalidated", 0) + 1
        ),
        installed_probe=lambda: False,
    )
    defaults.update(over)
    return si.SidecarSpec(**defaults)


def _fake_run_logged(created: list):
    """A _run_logged stand-in that fabricates git/uv side effects on disk."""

    def run(job, argv, *, timeout, env=None):
        created.append(argv)
        prog = os.path.basename(argv[0])
        if prog.startswith("git") and argv[1] == "clone":
            checkout = Path(argv[-1])
            checkout.mkdir(parents=True, exist_ok=True)
            (checkout / "pyproject.toml").write_text("[project]\nname='fake'\n")
        elif prog.startswith("uv") and argv[1] == "venv":
            venv = Path(argv[2])
            py = si._venv_python(venv)
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("#!fake python\n")
            if job["engine_id"] == "dots-tts":
                constraints = venv.parent / "constraints" / "recommended.txt"
                constraints.parent.mkdir(parents=True, exist_ok=True)
                constraints.write_text("six==1.17.0\n")
        # uv pip install: nothing to fabricate
        return 0

    return run


def _stub_verify_ok(monkeypatch):
    monkeypatch.setattr(
        si.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stderr=b"", stdout=b""),
    )


def _run(spec):
    job = si._new_job(spec.engine_id)
    si._run_install(spec, job)
    return job


def _step_states(job):
    return {s["id"]: s["state"] for s in job["steps"]}


# ── happy path ─────────────────────────────────────────────────────────────


def test_happy_path_installs_and_persists(monkeypatch):
    calls = {}
    prefs_written = {}
    spec = _mk_spec(_calls=calls)
    argvs = []
    monkeypatch.setattr(si, "_locate_uv", lambda: "/fake/uv")
    monkeypatch.setattr(si, "disk_free_bytes", lambda p: 100 * _GIB)
    monkeypatch.setattr(si.shutil, "which", lambda n: "/usr/bin/git" if n == "git" else None)
    monkeypatch.setattr(si, "_run_logged", _fake_run_logged(argvs))
    _stub_verify_ok(monkeypatch)
    monkeypatch.setattr("core.prefs.set_", lambda k, v: prefs_written.update({k: v}))

    job = _run(spec)

    assert job["state"] == "succeeded", (job["error"], list(job["log"]))
    states = _step_states(job)
    assert states["preflight"] == "done"
    assert states["fetch_source"] == "done"
    assert states["create_venv"] == "done"
    assert states["install_deps"] == "done"
    assert states["verify"] == "done"
    assert states["fetch_weights"] == "skipped"       # no weights_repo_id
    assert states["persist"] == "done"
    checkout = si.managed_checkout(spec)
    # Engine usable immediately: env var set in THIS process…
    assert os.environ["OMNIVOICE_FAKE_SIDE_DIR"] == str(checkout)
    # …and persisted for the next launch via the env.* prefs mechanism.
    assert prefs_written == {"env.OMNIVOICE_FAKE_SIDE_DIR": str(checkout)}
    # Memoised venv resolution invalidated so it re-probes without restart.
    assert calls.get("invalidated") == 1
    # uv pip install targeted the sidecar venv's own python (isolation
    # preserved — the parent app's env is never touched).
    pip = next(a for a in argvs if a[1:3] == ["pip", "install"])
    assert pip[pip.index("--python") + 1] == str(si._venv_python(checkout / ".venv"))
    assert pip[-1] == str(checkout)


def test_rerun_after_success_skips_completed_steps(monkeypatch):
    spec = _mk_spec()
    argvs = []
    monkeypatch.setattr(si, "_locate_uv", lambda: "/fake/uv")
    monkeypatch.setattr(si, "disk_free_bytes", lambda p: 100 * _GIB)
    monkeypatch.setattr(si.shutil, "which", lambda n: "/usr/bin/git" if n == "git" else None)
    monkeypatch.setattr(si, "_run_logged", _fake_run_logged(argvs))
    _stub_verify_ok(monkeypatch)
    monkeypatch.setattr("core.prefs.set_", lambda k, v: None)

    assert _run(spec)["state"] == "succeeded"
    argvs.clear()
    job2 = _run(spec)
    assert job2["state"] == "succeeded"
    # No re-clone, no re-venv; only the idempotent pip repair pass runs.
    assert all(a[1:3] == ["pip", "install"] for a in argvs), argvs
    states = _step_states(job2)
    assert states["fetch_source"] == "done" and states["create_venv"] == "done"


# ── disk-space preflight ───────────────────────────────────────────────────


def test_disk_space_preflight_fails_early_with_numbers(monkeypatch):
    spec = _mk_spec(required_bytes=10 * _GIB)
    monkeypatch.setattr(si, "_locate_uv", lambda: "/fake/uv")
    monkeypatch.setattr(si, "disk_free_bytes", lambda p: 2 * _GIB)

    job = _run(spec)

    assert job["state"] == "failed"
    assert _step_states(job)["preflight"] == "error"
    # Nothing after preflight ran.
    assert _step_states(job)["fetch_source"] == "pending"
    # The error names what's needed and what's free, so the user can act.
    assert "10.0 GB" in job["error"] and "2.0 GB" in job["error"]
    assert "disk space" in job["remediation"].lower() or "disk space" in job["error"].lower()


def test_disk_preflight_subtracts_partial_install(monkeypatch, tmp_path):
    # 1 GiB required, 0.9 GiB already on disk from a prior partial run →
    # only the remainder (+headroom) must fit, so a resume isn't blocked.
    spec = _mk_spec(required_bytes=1 * _GIB)
    root = si.managed_root(spec)
    checkout = si.managed_checkout(spec)
    root.mkdir(parents=True)
    monkeypatch.setattr(si, "_source_present", lambda _spec, _checkout: True)
    monkeypatch.setattr(
        si,
        "_dir_size_bytes",
        lambda path: int(0.9 * _GIB) if path == checkout else 0,
    )
    monkeypatch.setattr(si, "disk_free_bytes", lambda p: (si.MIN_FREE_GB + 1) * _GIB)
    assert si.disk_space_error(spec) is None


def test_disk_preflight_subtracts_partial_install_from_peak_requirement(monkeypatch):
    spec = _mk_spec(required_bytes=1 * _GIB, temporary_free_bytes=4 * _GIB)
    checkout = si.managed_checkout(spec)
    monkeypatch.setattr(si, "_source_present", lambda _spec, _checkout: True)
    monkeypatch.setattr(
        si,
        "_dir_size_bytes",
        lambda path: int(0.9 * _GIB) if path == checkout else 0,
    )
    monkeypatch.setattr(si, "disk_free_bytes", lambda _path: (si.MIN_FREE_GB + 2) * _GIB)
    assert "3.1 GB" in si.disk_space_error(spec)


def test_disk_preflight_does_not_credit_weights_against_dependency_peak(monkeypatch):
    spec = _mk_spec(
        required_bytes=12 * _GIB,
        temporary_free_bytes=12 * _GIB,
        weights_repo_id="Example/Weights",
    )
    checkout = si.managed_checkout(spec)
    weights = checkout / spec.weights_subdir
    monkeypatch.setattr(si, "_source_present", lambda _spec, _checkout: True)
    monkeypatch.setattr(
        si,
        "_dir_size_bytes",
        lambda path: 7 * _GIB if path == checkout else 6 * _GIB if path == weights else 0,
    )
    monkeypatch.setattr(si, "disk_free_bytes", lambda _path: (si.MIN_FREE_GB + 10) * _GIB)

    assert "11.0 GB" in si.disk_space_error(spec)


def test_disk_preflight_does_not_credit_checkout_that_fetch_will_delete(monkeypatch):
    spec = _mk_spec(required_bytes=4 * _GIB, source_revision="new-revision")
    checkout = si.managed_checkout(spec)
    checkout.mkdir(parents=True)
    (checkout / "pyproject.toml").write_text("[project]\nname='fake'\n")
    (checkout / si._SOURCE_REVISION_MARKER).write_text("stale-revision\n")
    monkeypatch.setattr(si, "_dir_size_bytes", lambda _path: 3 * _GIB)
    monkeypatch.setattr(si, "disk_free_bytes", lambda _path: (si.MIN_FREE_GB + 2) * _GIB)

    assert "4.0 GB" in si.disk_space_error(spec)


def test_missing_uv_is_actionable(monkeypatch):
    spec = _mk_spec()
    monkeypatch.setattr(si, "_locate_uv", lambda: None)
    job = _run(spec)
    assert job["state"] == "failed"
    assert "uv" in job["error"]
    assert "docs.astral.sh/uv" in job["remediation"]


# ── git-absent tarball fallback ────────────────────────────────────────────


def _tarball_bytes(root_name: str, with_pyproject: bool = True) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        if with_pyproject:
            data = b"[project]\nname='fake'\n"
            info = tarfile.TarInfo(f"{root_name}/pyproject.toml")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        data2 = b"print('hi')\n"
        info2 = tarfile.TarInfo(f"{root_name}/fake_side/__init__.py")
        info2.size = len(data2)
        tf.addfile(info2, io.BytesIO(data2))
    return buf.getvalue()


class _FakeStream:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    def iter_bytes(self):
        yield self._payload


def test_git_absent_falls_back_to_tarball(monkeypatch):
    spec = _mk_spec()
    urls = []
    monkeypatch.setattr(si.shutil, "which", lambda n: None)   # no git anywhere
    import httpx

    def fake_stream(method, url, **kw):
        urls.append((method, url))
        return _FakeStream(_tarball_bytes("fake-side-main"))

    monkeypatch.setattr(httpx, "stream", fake_stream)

    job = si._new_job(spec.engine_id)
    step = si._job_step(job, "fetch_source")
    step["state"] = "running"
    si._step_fetch_source(spec, job)

    assert urls == [("GET", spec.tarball_url)]
    checkout = si.managed_checkout(spec)
    assert (checkout / "pyproject.toml").is_file()
    assert (checkout / "fake_side" / "__init__.py").is_file()
    assert step["detail"] == "source tarball"


def test_git_failure_falls_back_to_tarball(monkeypatch):
    """A present-but-failing git (proxy block, DNS, …) must not dead-end."""
    spec = _mk_spec()
    monkeypatch.setattr(si.shutil, "which", lambda n: "/usr/bin/git" if n == "git" else None)
    monkeypatch.setattr(si, "_run_logged", lambda job, argv, timeout: 128)  # git exits 128
    import httpx
    monkeypatch.setattr(
        httpx, "stream",
        lambda m, u, **k: _FakeStream(_tarball_bytes("fake-side-main")),
    )
    job = si._new_job(spec.engine_id)
    si._job_step(job, "fetch_source")["state"] = "running"
    si._step_fetch_source(spec, job)
    assert (si.managed_checkout(spec) / "pyproject.toml").is_file()


def test_legacy_kill_fallback_uses_only_direct_stable_handle(monkeypatch):
    calls = {}
    monkeypatch.setattr(si.os, "name", "nt")
    monkeypatch.setattr(
        si.subprocess, "run",
        lambda argv, **kw: calls.setdefault("argv", argv) or SimpleNamespace(returncode=0),
    )
    proc = SimpleNamespace(pid=4242, kill=lambda: calls.setdefault("plain_kill", True))
    si._kill_tree(proc)
    assert calls == {"plain_kill": True}


def test_desktop_installer_needs_no_unmanaged_spawn_flags(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_DESKTOP_CONTAINED", "1")
    monkeypatch.setattr(si.os, "name", "posix")
    assert si._install_containment_kwargs() == {}


def test_standalone_installer_also_delegates_to_nested_owner(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_DESKTOP_CONTAINED", raising=False)
    monkeypatch.setattr(si.os, "name", "posix")
    assert si._install_containment_kwargs() == {}


def test_desktop_windows_timeout_never_taskkills_a_reusable_pid(monkeypatch):
    calls = {}
    monkeypatch.setenv("OMNIVOICE_DESKTOP_CONTAINED", "1")
    monkeypatch.setattr(si.os, "name", "nt")
    monkeypatch.setattr(
        si.subprocess,
        "run",
        lambda *args, **kwargs: calls.setdefault("taskkill", True),
    )
    proc = SimpleNamespace(pid=4242, kill=lambda: calls.setdefault("handle_kill", True))
    si._kill_tree(proc)
    assert calls == {"handle_kill": True}


def test_windows_job_timeout_waits_for_terminated_tree():
    events = []

    def timed_out_wait(timeout=None):
        events.append(("wait", timeout))
        raise subprocess.TimeoutExpired("operation.exe", timeout)

    child = SimpleNamespace(
        stdin=None,
        stdout=None,
        stderr=None,
        wait=timed_out_wait,
    )
    kernel = SimpleNamespace(
        TerminateJobObject=lambda job, code: events.append(("terminate", job, code)),
        CloseHandle=lambda job: events.append(("close", job)),
    )
    proc = si.WindowsJobPopen(child, 99, kernel)

    si._kill_tree(proc)

    assert events == [("terminate", 99, 1), ("close", 99), ("wait", 5)]


def test_desktop_installer_timeout_kills_nested_helper_before_it_can_mutate(
    monkeypatch, tmp_path
):
    """A timed-out uv/git root must not leave its pipe-owning helpers alive."""
    marker = tmp_path / "late-mutation"
    monkeypatch.setenv("OMNIVOICE_DESKTOP_CONTAINED", "1")
    drain_read, drain_write = os.pipe()
    monkeypatch.setenv("OMNIVOICE_DESKTOP_DRAIN_FD", str(drain_write))
    child_script = (
        "import os,time; time.sleep(1); "
        "open(os.environ['OMNIVOICE_TIMEOUT_MARKER'], 'w').write('bad')"
    )
    script = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {child_script!r}]); "
        "time.sleep(60)"
    )
    env = os.environ.copy()
    env["OMNIVOICE_TIMEOUT_MARKER"] = str(marker)
    job = si._new_job("fake-side")

    try:
        assert si._run_logged(job, [sys.executable, "-c", script], timeout=0.2, env=env) == -1
        time.sleep(1.2)
        assert not marker.exists()
    finally:
        os.close(drain_write)
        os.close(drain_read)


def test_backend_death_closes_control_pipe_and_kills_nested_operation(tmp_path):
    """Outer desktop teardown reaches an operation which owns a nested group."""
    marker = tmp_path / "mutation-after-backend-death"
    operation = (
        "import os,time; time.sleep(1); "
        "open(os.environ['OMNIVOICE_TIMEOUT_MARKER'], 'w').write('bad')"
    )
    backend = (
        "import os,sys,time; "
        "from core.contained_subprocess import spawn_owned; "
        f"spawn_owned([sys.executable, '-c', {operation!r}]); "
        "time.sleep(.2); os._exit(0)"
    )
    env = os.environ.copy()
    env["OMNIVOICE_DESKTOP_CONTAINED"] = "1"
    env["OMNIVOICE_TIMEOUT_MARKER"] = str(marker)
    env["PYTHONPATH"] = str(Path(__file__).parents[1] / "backend")

    run_kwargs = {}
    drain_fds = None
    if os.name == "posix":
        drain_fds = os.pipe()
        env["OMNIVOICE_DESKTOP_DRAIN_FD"] = str(drain_fds[1])
        run_kwargs["pass_fds"] = (drain_fds[1],)
    assert subprocess.run([sys.executable, "-c", backend], env=env, **run_kwargs).returncode == 0
    if drain_fds is not None:
        os.close(drain_fds[1])
        os.close(drain_fds[0])
    time.sleep(1.2)
    assert not marker.exists()


def test_safe_extract_members_blocks_tar_slip(tmp_path):
    """The pre-filter= fallback extractor must drop parent-dir escapes,
    absolute paths, and symlinks — mirroring extractall(filter='data')."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        good = tarfile.TarInfo("pkg/ok.txt")
        good.size = 2
        tf.addfile(good, io.BytesIO(b"ok"))
        evil = tarfile.TarInfo("../evil.txt")
        evil.size = 4
        tf.addfile(evil, io.BytesIO(b"pwnd"))
        absolute = tarfile.TarInfo("/abs.txt")
        absolute.size = 3
        tf.addfile(absolute, io.BytesIO(b"abs"))
        link = tarfile.TarInfo("pkg/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tf.addfile(link)
    buf.seek(0)
    dest = tmp_path / "sandbox" / "out"
    dest.mkdir(parents=True)
    with tarfile.open(fileobj=buf, mode="r:gz") as tf:
        si._safe_extract_members(tf, str(dest))
    assert (dest / "pkg" / "ok.txt").read_text() == "ok"
    assert not (tmp_path / "sandbox" / "evil.txt").exists()
    assert not (dest / "pkg" / "link").exists()


def test_tarball_without_pyproject_fails_with_remediation(monkeypatch):
    spec = _mk_spec()
    monkeypatch.setattr(si.shutil, "which", lambda n: None)
    import httpx
    monkeypatch.setattr(
        httpx, "stream",
        lambda m, u, **k: _FakeStream(_tarball_bytes("fake-side-main", with_pyproject=False)),
    )
    job = si._new_job(spec.engine_id)
    si._job_step(job, "fetch_source")["state"] = "running"
    with pytest.raises(si._StepError) as ei:
        si._step_fetch_source(spec, job)
    assert "pyproject.toml" in str(ei.value)
    assert spec.env_var in ei.value.remediation


# ── partial-install repair ─────────────────────────────────────────────────


def test_half_fetched_checkout_is_refetched(monkeypatch):
    """A checkout without pyproject.toml (killed mid-clone) is wiped and
    re-fetched instead of being trusted or corrupting the install."""
    spec = _mk_spec()
    checkout = si.managed_checkout(spec)
    (checkout / "leftover").mkdir(parents=True)
    (checkout / "leftover" / "junk.txt").write_text("stale")
    argvs = []
    monkeypatch.setattr(si.shutil, "which", lambda n: "/usr/bin/git" if n == "git" else None)
    monkeypatch.setattr(si, "_run_logged", _fake_run_logged(argvs))

    job = si._new_job(spec.engine_id)
    si._job_step(job, "fetch_source")["state"] = "running"
    si._step_fetch_source(spec, job)

    assert (checkout / "pyproject.toml").is_file()
    assert not (checkout / "leftover").exists()
    assert any(a[1] == "clone" for a in argvs)


def _write_weights(
    wdir: Path,
    *,
    complete: bool,
    repo_id: str = "Example/Weights",
    revision: str = "",
    config_name: str = "config.yaml",
) -> None:
    """Fabricate a weights dir; ``complete=True`` adds the completion marker
    the installer writes after snapshot_download returns."""
    wdir.mkdir(parents=True, exist_ok=True)
    (wdir / config_name).write_text("model: fake\n")
    (wdir / "weights.safetensors").write_bytes(b"\0" * (6 * 1024 * 1024))
    if complete:
        (wdir / si._WEIGHTS_COMPLETE_MARKER).write_text(
            f"{repo_id}\n{revision}\n",
        )


def test_partial_install_is_not_already_installed(monkeypatch):
    """venv present but weights missing → NOT healthy → a re-run repairs it
    instead of short-circuiting with already_installed."""
    spec = _mk_spec(weights_repo_id="Example/Weights")
    checkout = si.managed_checkout(spec)
    checkout.mkdir(parents=True, exist_ok=True)
    (checkout / "pyproject.toml").write_text("[project]\nname='fake'\n")
    py = si._venv_python(checkout / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("#!fake\n")
    assert si._healthy(spec) is False

    # Complete the weights (incl. the completion marker) and the install
    # (the marker the import probe writes) → healthy flips true.
    _write_weights(checkout / spec.weights_subdir, complete=True)
    assert si._healthy(spec) is False
    (checkout / si._INSTALL_COMPLETE_MARKER).write_text("x\n", encoding="utf-8")
    assert si._healthy(spec) is True


def test_interrupted_multishard_weights_are_not_healthy(monkeypatch):
    """Regression: a killed-mid-download weights dir can hold config.yaml +
    plausible shards, but WITHOUT the completion marker it must stay
    unhealthy so a re-run resumes the download instead of reporting
    already_installed and failing later at model-load time."""
    spec = _mk_spec(weights_repo_id="Example/Weights")
    checkout = si.managed_checkout(spec)
    checkout.mkdir(parents=True, exist_ok=True)
    (checkout / "pyproject.toml").write_text("[project]\nname='fake'\n")
    py = si._venv_python(checkout / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("#!fake\n")
    _write_weights(checkout / spec.weights_subdir, complete=False)
    assert si._weights_present(spec) is False
    assert si._healthy(spec) is False
    monkeypatch.setitem(si.SPECS, "fake-side", spec)
    monkeypatch.setattr(si, "_run_install", lambda s, j: None)
    assert si.start_install("fake-side")["status"] == "started"  # repairs, not skips


def test_weights_step_downloads_via_endpoint_autoselect(monkeypatch):
    """The weights download must ride snapshot_download with the endpoint
    from services.endpoint_race — never a hardcoded huggingface.co URL."""
    spec = _mk_spec(weights_repo_id="Example/Weights")
    wdir = si.managed_checkout(spec) / spec.weights_subdir
    seen = {}

    def fake_snapshot_download(**kwargs):
        seen.update(kwargs)
        Path(kwargs["local_dir"]).mkdir(parents=True, exist_ok=True)
        (Path(kwargs["local_dir"]) / "config.yaml").write_text("ok\n")
        (Path(kwargs["local_dir"]) / "w.safetensors").write_bytes(b"\0" * (6 * 1024 * 1024))

    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    from services import endpoint_race
    monkeypatch.setattr(endpoint_race, "effective_endpoint", lambda: "https://hf-mirror.example")
    monkeypatch.setattr("services.token_resolver.resolve", lambda: None)

    job = si._new_job(spec.engine_id)
    si._job_step(job, "fetch_weights")["state"] = "running"
    si._step_fetch_weights(spec, job)

    assert seen["repo_id"] == "Example/Weights"
    assert seen["endpoint"] == "https://hf-mirror.example"
    assert seen["local_dir"] == str(wdir)
    assert si._weights_present(spec)


def test_weights_download_sends_the_bearer_string_not_the_token_record(monkeypatch):
    """#2163: `token_resolver.resolve()` returns a ResolvedToken record, but
    snapshot_download takes `token: str | None` and silently ignores a non-str
    — falling back to huggingface_hub's own ambient token discovery. So gated
    engine weights 401 for a user whose token lives in VoiceStudio's Settings
    rather than HF's cache. Every other weights test here stubs resolve() to
    None, which is exactly why this went unnoticed."""
    from services.token_resolver import ResolvedToken

    spec = _mk_spec(weights_repo_id="Example/Gated")
    seen = {}

    def fake_snapshot_download(**kwargs):
        seen.update(kwargs)
        Path(kwargs["local_dir"]).mkdir(parents=True, exist_ok=True)
        (Path(kwargs["local_dir"]) / "config.yaml").write_text("ok\n")
        (Path(kwargs["local_dir"]) / "w.safetensors").write_bytes(b"\0" * (6 * 1024 * 1024))

    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    monkeypatch.setattr("services.endpoint_race.effective_endpoint", lambda: None)
    monkeypatch.setattr(
        "services.token_resolver.resolve",
        lambda: ResolvedToken(token="hf_gatedsecret", source="app", username="tester"),
    )

    job = si._new_job(spec.engine_id)
    si._job_step(job, "fetch_weights")["state"] = "running"
    si._step_fetch_weights(spec, job)

    assert seen["token"] == "hf_gatedsecret"
    assert isinstance(seen["token"], str)


def test_weights_download_sends_no_token_when_none_resolves(monkeypatch):
    # The other half of the contract: no token anywhere must reach
    # snapshot_download as a real None, never the string "None".
    spec = _mk_spec(weights_repo_id="Example/Open")
    seen = {}

    def fake_snapshot_download(**kwargs):
        seen.update(kwargs)
        Path(kwargs["local_dir"]).mkdir(parents=True, exist_ok=True)
        (Path(kwargs["local_dir"]) / "config.yaml").write_text("ok\n")
        (Path(kwargs["local_dir"]) / "w.safetensors").write_bytes(b"\0" * (6 * 1024 * 1024))

    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    monkeypatch.setattr("services.endpoint_race.effective_endpoint", lambda: None)
    monkeypatch.setattr("services.token_resolver.resolve", lambda: None)

    job = si._new_job(spec.engine_id)
    si._job_step(job, "fetch_weights")["state"] = "running"
    si._step_fetch_weights(spec, job)

    assert seen["token"] is None


def test_weights_revision_is_pinned_and_old_marker_forces_upgrade(monkeypatch):
    spec = _mk_spec(
        weights_repo_id="Example/Weights",
        weights_revision="a" * 40,
    )
    wdir = si.managed_checkout(spec) / spec.weights_subdir
    _write_weights(wdir, complete=True)  # legacy default-branch marker
    assert si._weights_present(spec) is False

    seen = {}

    def fake_snapshot_download(**kwargs):
        seen.update(kwargs)
        _write_weights(
            Path(kwargs["local_dir"]), complete=False,
            repo_id=kwargs["repo_id"], revision=kwargs["revision"],
        )

    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    monkeypatch.setattr("services.endpoint_race.effective_endpoint", lambda: None)
    monkeypatch.setattr("services.token_resolver.resolve", lambda: None)
    job = si._new_job(spec.engine_id)
    si._job_step(job, "fetch_weights")["state"] = "running"
    si._step_fetch_weights(spec, job)
    assert seen["revision"] == "a" * 40
    assert si._weights_present(spec) is True


def test_weights_step_skips_when_already_present(monkeypatch):
    spec = _mk_spec(weights_repo_id="Example/Weights")
    wdir = si.managed_checkout(spec) / spec.weights_subdir
    _write_weights(wdir, complete=True)
    import huggingface_hub

    def boom(**kw):  # pragma: no cover — must not be reached
        raise AssertionError("snapshot_download must not run when weights exist")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", boom)
    job = si._new_job(spec.engine_id)
    si._job_step(job, "fetch_weights")["state"] = "running"
    si._step_fetch_weights(spec, job)
    assert si._job_step(job, "fetch_weights")["state"] == "done"


# ── already-installed / already-running gating ────────────────────────────


def test_healthy_managed_install_reheals_lost_env_var(monkeypatch):
    """A complete managed install whose env var vanished (prefs.json wiped)
    is re-pointed by start_install instead of being reinstalled — and instead
    of returning already_installed while the engine stays unavailable."""
    spec = _mk_spec(weights_repo_id="Example/Weights")
    monkeypatch.setitem(si.SPECS, "fake-side", spec)
    checkout = si.managed_checkout(spec)
    checkout.mkdir(parents=True, exist_ok=True)
    (checkout / "pyproject.toml").write_text("[project]\nname='fake'\n")
    py = si._venv_python(checkout / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("#!fake\n")
    _write_weights(checkout / spec.weights_subdir, complete=True)
    (checkout / si._INSTALL_COMPLETE_MARKER).write_text("x\n", encoding="utf-8")
    prefs_written = {}
    monkeypatch.setattr("core.prefs.set_", lambda k, v: prefs_written.update({k: v}))
    assert "OMNIVOICE_FAKE_SIDE_DIR" not in os.environ

    res = si.start_install("fake-side")

    assert res["status"] == "already_installed"
    assert os.environ["OMNIVOICE_FAKE_SIDE_DIR"] == str(checkout)
    assert prefs_written == {"env.OMNIVOICE_FAKE_SIDE_DIR": str(checkout)}


def test_start_install_reports_already_installed_for_user_clone(monkeypatch):
    spec = _mk_spec(installed_probe=lambda: True)
    monkeypatch.setitem(si.SPECS, "fake-side", spec)
    monkeypatch.setenv("OMNIVOICE_FAKE_SIDE_DIR", "/home/user/own-clone")
    res = si.start_install("fake-side")
    assert res["status"] == "already_installed"


def test_start_install_reports_already_running(monkeypatch):
    spec = _mk_spec()
    monkeypatch.setitem(si.SPECS, "fake-side", spec)
    ran = threading.Event()
    monkeypatch.setattr(si, "_run_install", lambda s, j: ran.set())
    first = si.start_install("fake-side")
    assert first["status"] == "started"
    # Freeze the job as running to simulate the in-flight window.
    si._jobs["fake-side"]["state"] = "running"
    assert si.start_install("fake-side")["status"] == "already_running"
    assert ran.wait(5)


def test_start_install_unknown_engine_raises_keyerror():
    with pytest.raises(KeyError):
        si.start_install("definitely-not-an-engine")


def test_get_status_synthesizes_state_without_job(monkeypatch):
    spec = _mk_spec()
    monkeypatch.setitem(si.SPECS, "fake-side", spec)
    st = si.get_status("fake-side")
    assert st == {
        "engine_id": "fake-side",
        "installed": False,
        "managed": False,
        "install_dir": None,
        "job": None,
    }


# ── uninstall ──────────────────────────────────────────────────────────────


def test_uninstall_removes_managed_install_and_prefs(monkeypatch):
    spec = _mk_spec()
    monkeypatch.setitem(si.SPECS, "fake-side", spec)
    checkout = si.managed_checkout(spec)
    py = si._venv_python(checkout / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("#!fake\n")
    monkeypatch.setenv("OMNIVOICE_FAKE_SIDE_DIR", str(checkout))
    deleted = []
    monkeypatch.setattr("core.prefs.get", lambda k, d=None: str(checkout))
    monkeypatch.setattr("core.prefs.delete", lambda k: deleted.append(k))

    res = si.uninstall("fake-side")

    assert res["status"] == "uninstalled"
    assert not si.managed_root(spec).exists()
    assert "OMNIVOICE_FAKE_SIDE_DIR" not in os.environ
    assert deleted == ["env.OMNIVOICE_FAKE_SIDE_DIR"]


def test_uninstall_refuses_user_managed_clone(monkeypatch, tmp_path):
    spec = _mk_spec()
    monkeypatch.setitem(si.SPECS, "fake-side", spec)
    user_clone = tmp_path / "my-own-clone"
    user_clone.mkdir()
    monkeypatch.setenv("OMNIVOICE_FAKE_SIDE_DIR", str(user_clone))
    res = si.uninstall("fake-side")
    assert res["status"] == "not_managed"
    assert user_clone.exists()                      # never deleted
    assert os.environ["OMNIVOICE_FAKE_SIDE_DIR"] == str(user_clone)  # never cleared


def test_uninstall_clears_legacy_managed_preference(monkeypatch):
    spec = si.get_spec("indextts2")
    legacy = si.managed_root(spec) / "index-tts"
    legacy.mkdir(parents=True)
    monkeypatch.setenv(spec.env_var, str(legacy))
    deleted = []
    monkeypatch.setattr("core.prefs.get", lambda k, d=None: str(legacy))
    monkeypatch.setattr("core.prefs.delete", lambda k: deleted.append(k))

    assert si.uninstall(spec.engine_id)["status"] == "uninstalled"
    assert spec.env_var not in os.environ
    assert deleted == [f"env.{spec.env_var}"]


def test_uninstall_refuses_while_job_running(monkeypatch):
    spec = _mk_spec()
    monkeypatch.setitem(si.SPECS, "fake-side", spec)
    si._jobs["fake-side"] = si._new_job("fake-side")
    assert si.uninstall("fake-side")["status"] == "install_in_progress"


# ── indextts2 spec wiring (the engine this ships for) ─────────────────────


def test_indextts2_spec_matches_bootstrap_contract():
    spec = si.get_spec("indextts2")
    assert spec is not None
    # The env var must be the one engines/indextts/bootstrap.py actually
    # reads — anything else would install into a dir the engine never finds.
    assert spec.env_var == "OMNIVOICE_INDEXTTS_DIR"
    assert spec.probe_module == "indextts.infer_v2_5"
    # main.py loads from <dir>/checkpoints/, resolving whichever accepted
    # config name is on disk — the installer must put the weights there.
    assert spec.repo_ref == "indextts-2.5"
    assert spec.source_revision == "bf2e967fac7933197143b017a60820b1ad40c448"
    assert spec.source_required_path == "indextts/infer_v2_5.py"
    assert spec.weights_repo_id == "IndexTeam/IndexTTS-2.5"
    assert spec.weights_revision == "d0aa86e75bb6f3437f3831e95056fa72842d89ef"
    assert spec.weights_subdir == "checkpoints"
    assert spec.weights_config_names == ("config.yaml", "config_v2_5.yaml")
    assert spec.repo_url.endswith("index-tts.git")


def test_indextts25_health_accepts_either_config_name(monkeypatch):
    """#1611: this used to assert the OPPOSITE — that a checkout holding only
    config.yaml is unhealthy — which is what a clean IndexTeam/IndexTTS-2.5
    download actually produces, so every install failed. Both the upstream
    name and the hand-renamed one from the old workaround are healthy now;
    neither present is still a truncated download.
    """
    spec = si.get_spec("indextts2")
    monkeypatch.setenv(spec.env_var, str(si.managed_checkout(spec)))
    checkout = si.managed_checkout(spec)
    required = checkout / spec.source_required_path
    required.parent.mkdir(parents=True, exist_ok=True)
    required.write_text("class IndexTTS2: pass\n")
    (checkout / "pyproject.toml").write_text("[project]\nname='indextts'\n")
    (checkout / si._SOURCE_REVISION_MARKER).write_text(f"{spec.source_revision}\n")
    py = si._venv_python(checkout / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("#!fake\n")
    wdir = checkout / spec.weights_subdir
    _write_weights(
        wdir,
        complete=True,
        repo_id=spec.weights_repo_id,
        revision=spec.weights_revision,
    )
    # What a fresh upstream snapshot looks like.
    assert si._healthy(spec) is True
    # What the pre-fix workaround left behind.
    (wdir / "config.yaml").unlink()
    (wdir / "config_v2_5.yaml").write_text("model: fake\n")
    assert si._healthy(spec) is True
    # A genuinely truncated download is still caught.
    (wdir / "config_v2_5.yaml").unlink()
    assert si._healthy(spec) is False


def test_managed_indextts2_source_is_preserved_during_25_install(monkeypatch):
    spec = si.get_spec("indextts2")
    legacy = si.managed_root(spec) / "index-tts"
    legacy.mkdir(parents=True)
    (legacy / "pyproject.toml").write_text("[project]\nname='indextts'\n")
    (legacy / "old-v2-only.txt").write_text("old")
    monkeypatch.setenv(spec.env_var, str(legacy))
    checkout = si.managed_checkout(spec)
    argvs = []

    def fake_run(job, argv, *, timeout, env=None):
        argvs.append(argv)
        if argv[1] == "clone":
            checkout.mkdir(parents=True, exist_ok=True)
            (checkout / "pyproject.toml").write_text("[project]\nname='indextts'\n")
            required = checkout / spec.source_required_path
            required.parent.mkdir(parents=True, exist_ok=True)
            required.write_text("class IndexTTS2: pass\n")
        return 0

    monkeypatch.setattr(si.shutil, "which", lambda n: "/usr/bin/git")
    monkeypatch.setattr(si, "_run_logged", fake_run)
    job = si._new_job(spec.engine_id)
    si._job_step(job, "fetch_source")["state"] = "running"
    si._step_fetch_source(spec, job)
    clone = next(argv for argv in argvs if argv[1] == "clone")
    assert clone[clone.index("--branch") + 1] == "indextts-2.5"
    assert (legacy / "old-v2-only.txt").read_text() == "old"
    assert si._source_present(spec, checkout)


def test_indextts2_env_var_in_settings_allowlist():
    from api.routers.system import PERSISTENT_KEYS
    assert "OMNIVOICE_INDEXTTS_DIR" in PERSISTENT_KEYS


def test_list_backends_flags_indextts2_one_click():
    """Fail-before/pass-after: the Settings UI keys the Install button off
    this field — without it the engine stays a manual setup_snippet."""
    from services import tts_backend
    row = next(r for r in tts_backend.list_backends() if r["id"] == "indextts2")
    assert row["one_click_install"] is True
    other = next(r for r in tts_backend.list_backends() if r["id"] == "omnivoice")
    assert other["one_click_install"] is False


# ── router wiring ──────────────────────────────────────────────────────────


def test_sidecar_routes_never_shadow_literal_engine_routes():
    """Regression: the engines router registers BEFORE literal-path routers
    (e.g. sonitranslate), so a dynamic ``/engines/{engine_id}/install`` here
    would swallow ``POST /engines/sonitranslate/install``. The sidecar
    installer must keep its own literal namespace (/engines/sidecar/…)."""
    from api.routers import engines as engines_router
    install_paths = [
        r.path for r in engines_router.router.routes if "install" in r.path
    ]
    assert install_paths, "sidecar install routes missing"
    for p in install_paths:
        assert not p.startswith("/engines/{"), (
            f"{p} would shadow literal /engines/<x>/install routes registered later"
        )


def test_router_404s_engines_without_installer():
    from fastapi import HTTPException
    from api.routers import engines as engines_router
    with pytest.raises(HTTPException) as ei:
        engines_router.install_sidecar_engine("omnivoice")
    assert ei.value.status_code == 404
    with pytest.raises(HTTPException) as ei:
        engines_router.sidecar_install_status("omnivoice")
    assert ei.value.status_code == 404
    with pytest.raises(HTTPException) as ei:
        engines_router.uninstall_sidecar_engine("omnivoice")
    assert ei.value.status_code == 404


def test_router_uninstall_maps_refusals_to_http_errors(monkeypatch):
    from fastapi import HTTPException
    from api.routers import engines as engines_router
    spec = _mk_spec()
    monkeypatch.setitem(si.SPECS, "fake-side", spec)
    si._jobs["fake-side"] = si._new_job("fake-side")
    with pytest.raises(HTTPException) as ei:
        engines_router.uninstall_sidecar_engine("fake-side")
    assert ei.value.status_code == 409
    si._jobs.pop("fake-side")
    monkeypatch.setenv("OMNIVOICE_FAKE_SIDE_DIR", "/somewhere/else")
    with pytest.raises(HTTPException) as ei:
        engines_router.uninstall_sidecar_engine("fake-side")
    assert ei.value.status_code == 400


# ── isolation: switching engines can never corrupt another engine ─────────
#
# Every one-click engine owns DATA_DIR/engines/<id>/ — its checkout and its
# .venv — and switching the active engine only changes a pref. So going back
# to an engine that worked is safe for exactly as long as no install ever
# writes outside its own root. These tests pin that for every spec, including
# ones added later.

_ALL_SPEC_IDS = sorted(si.SPECS)
_PYTORCH_INDEX = "https://download.pytorch.org/whl/cu128"


def _capture_install_argvs(monkeypatch, family="cuda"):
    argvs = []
    monkeypatch.setattr(si, "_locate_uv", lambda: "/fake/uv")
    monkeypatch.setattr(si, "_host_family", lambda: family)
    monkeypatch.setattr(si, "_run_logged", _fake_run_logged(argvs))
    constraints = si.managed_checkout(si.get_spec("dots-tts")) / "constraints" / "recommended.txt"
    constraints.parent.mkdir(parents=True, exist_ok=True)
    constraints.write_text("six==1.17.0\n")
    return argvs


@pytest.mark.parametrize("engine_id", _ALL_SPEC_IDS)
def test_every_spec_installs_only_into_its_own_venv(monkeypatch, engine_id):
    spec = si.get_spec(engine_id)
    argvs = _capture_install_argvs(monkeypatch)
    job = si._new_job(engine_id)

    si._step_create_venv(spec, job)
    si._step_install_deps(spec, job)

    assert si.managed_root(spec) == Path(si.DATA_DIR) / "engines" / engine_id
    venv = si.managed_checkout(spec) / ".venv"
    venv_cmd = next(a for a in argvs if a[1] == "venv")
    assert venv_cmd[2] == str(venv)
    pip = next(a for a in argvs if a[1:3] == ["pip", "install"])
    # The interpreter uv installs into is this engine's venv — never the app's.
    assert pip[3:5] == ["--python", str(si._venv_python(venv))]
    for argv in argvs:
        assert sys.executable not in argv
        assert not any(sys.prefix in part for part in argv)


def test_managed_roots_never_overlap():
    roots = {eid: si.managed_root(si.get_spec(eid)) for eid in _ALL_SPEC_IDS}
    for a, ra in roots.items():
        for b, rb in roots.items():
            if a != b:
                assert ra != rb and ra not in rb.parents and rb not in ra.parents, (a, b)


def test_uninstalling_one_engine_leaves_every_other_engine_intact(monkeypatch):
    for eid in _ALL_SPEC_IDS:
        py = si._venv_python(si.managed_checkout(si.get_spec(eid)) / ".venv")
        py.parent.mkdir(parents=True)
        py.write_text("#!fake\n")
    monkeypatch.setattr("core.prefs.get", lambda k, d=None: None)
    monkeypatch.setattr("core.prefs.delete", lambda k: None)

    assert si.uninstall("moss-tts-v15")["status"] == "uninstalled"

    assert not si.managed_root(si.get_spec("moss-tts-v15")).exists()
    for eid in _ALL_SPEC_IDS:
        if eid != "moss-tts-v15":
            spec = si.get_spec(eid)
            assert si._venv_python(si.managed_checkout(spec) / ".venv").is_file(), eid


# ── per-engine install recipes ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("engine_id", "venv_args", "install_args", "env_var"),
    [
        ("moss-tts-v15", ["--python", "3.11"],
         ["-e", "{c}[torch-runtime]", "torchcodec===0.8.1"],
         "OMNIVOICE_MOSS_TTS_V15_DIR"),
        ("confucius4-tts", ["--python", "3.10"], ["-r", "{c}/requirements.txt"],
         "OMNIVOICE_CONFUCIUS4_TTS_DIR"),
        ("dots-tts", ["--python", "3.11"],
         ["-e", "{c}", "-c", "{uri}/constraints/recommended.txt"],
         "OMNIVOICE_DOTS_TTS_DIR"),
        # moss-tts-nano installs soundfile alongside the editable install so
        # torchaudio 2.7's I/O backend is present inside this engine's own
        # venv — upstream's pyproject pins torchaudio but no backend
        # (#2100). Without soundfile the engine's first load() crashes with
        # "Couldn't find appropriate backend to handle uri …".
        ("moss-tts-nano", ["--python", "3.11"],
         ["-e", "{c}", "soundfile"], "OMNIVOICE_MOSS_TTS_NANO_DIR"),
    ],
)
def test_new_specs_install_recipe(monkeypatch, engine_id, venv_args, install_args, env_var):
    """Engine recipes install into their own venv and expose the correct env var."""
    spec = si.get_spec(engine_id)
    # The env var must be the one the engine's own bootstrap reads, or the
    # install lands in a directory the engine never looks at.
    assert spec.env_var == env_var
    argvs = _capture_install_argvs(monkeypatch, family="cpu")
    job = si._new_job(engine_id)
    si._step_create_venv(spec, job)
    si._step_install_deps(spec, job)

    checkout = str(si.managed_checkout(spec))
    venv_cmd = next(a for a in argvs if a[1] == "venv")
    assert venv_cmd[3:] == venv_args
    pip = next(a for a in argvs if a[1:3] == ["pip", "install"])
    assert pip[5:] == [arg.replace("{c}", checkout).replace("{uri}", si.managed_checkout(spec).resolve().as_uri())
                      for arg in install_args]


def test_moss_tts_nano_probe_asserts_an_audio_backend_is_present(monkeypatch):
    """The moss-tts-nano verify step asserts torchaudio.list_audio_backends()
    is non-empty — catches a reinstall whose audio read path would crash at
    first generation (#2100). The probe must compile and reference the
    assertion so a future refactor cannot silently drop it.
    """
    spec = si.get_spec("moss-tts-nano")
    assert spec.probe_code, (
        "moss-tts-nano's verify probe must check torchaudio has a backend"
    )
    assert "list_audio_backends" in spec.probe_code
    # The assertion message is allowed to mention a concrete dependency
    # (soundfile / torchcodec) — that's user-facing guidance, not a probe
    # requirement. The probe itself must only check the outcome.
    assert "torchaudio.list_audio_backends()" in spec.probe_code
    # Compile-check, since probe_code runs through python -c on Windows.
    compile(spec.probe_code, "<probe>", "exec")


@pytest.mark.parametrize("family", ["cuda", "cpu", "rocm", "mps"])
@pytest.mark.parametrize("engine_id", _ALL_SPEC_IDS)
def test_cuda_index_is_added_only_for_cuda_pinned_specs_on_cuda_hosts(
    monkeypatch, engine_id, family
):
    from core.torch_indexes import UV_PIP_CU128_ARGS
    spec = si.get_spec(engine_id)
    argvs = _capture_install_argvs(monkeypatch, family=family)
    si._step_install_deps(spec, si._new_job(engine_id))
    pip = next(a for a in argvs if a[1:3] == ["pip", "install"])
    has_index = _PYTORCH_INDEX in pip
    assert has_index == (family == "cuda" and (spec.uses_cuda_index or bool(spec.torch_pins)))
    if has_index:
        i = pip.index("--extra-index-url")
        assert tuple(pip[i:i + len(UV_PIP_CU128_ARGS)]) == UV_PIP_CU128_ARGS


def test_torch_index_matches_the_apps_own_pytorch_cuda_index():
    """The sidecar index must be the one the app's own torch comes from."""
    import tomllib
    from core.torch_indexes import PYTORCH_CU128_INDEX_URL
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    indexes = tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["uv"]["index"]
    cuda = next(ix for ix in indexes if ix["name"] == "pytorch-cuda")
    assert PYTORCH_CU128_INDEX_URL == cuda["url"] == _PYTORCH_INDEX


@pytest.mark.parametrize("engine_id", _ALL_SPEC_IDS)
def test_verify_probe_runs_in_the_engines_venv_and_compiles(monkeypatch, engine_id):
    spec = si.get_spec(engine_id)
    # The venv, and so the checkout, exist by the time verify runs.
    si.managed_checkout(spec).mkdir(parents=True)
    ran = []

    def fake_run(argv, **kwargs):
        ran.append(argv)
        return SimpleNamespace(returncode=0, stderr=b"", stdout=b"")

    monkeypatch.setattr(si.subprocess, "run", fake_run)
    si._step_verify(spec, si._new_job(engine_id))
    checkout = si.managed_checkout(spec)
    assert ran[0][0] == str(si._venv_python(checkout / ".venv"))
    code = ran[0][2]
    compile(code, "<probe>", "exec")  # a Windows path must not break the literal
    assert "{checkout" not in code
    if engine_id == "confucius4-tts":
        assert repr(str(checkout)) in code


# ── host gates: no Install button that can only fail ─────────────────────


@pytest.mark.parametrize(
    ("family", "platform", "machine", "expected"),
    [
        ("cuda", "linux", "x86_64", {"moss-tts-v15", "dots-tts", "pockettts", "voxcpm2", "moss-tts-nano", "cosyvoice"}),
        ("cuda", "win32", "AMD64", {"moss-tts-v15", "pockettts", "voxcpm2", "moss-tts-nano", "cosyvoice"}),
        ("cpu", "win32", "AMD64", {"pockettts", "voxcpm2", "moss-tts-nano", "cosyvoice"}),
        ("mps", "darwin", "arm64", {"dots-tts", "pockettts", "voxcpm2", "moss-tts-nano", "cosyvoice"}),
        # Intel Mac: PyTorch publishes no build PocketTTS, VoxCPM2,
        # MOSS-TTS-Nano or CosyVoice can use.
        ("cpu", "darwin", "x86_64", {"dots-tts"}),
    ],
)
def test_installable_engine_ids_follow_the_host(monkeypatch, family, platform, machine, expected):
    import platform as platform_mod
    monkeypatch.setattr(si, "_host_family", lambda: family)
    monkeypatch.setattr(si.sys, "platform", platform)
    monkeypatch.setattr(platform_mod, "machine", lambda: machine)
    # Offered on every host: IndexTTS 2.5, Confucius4, Supertonic-3.
    expected = set(expected) | {"indextts2", "confucius4-tts", "supertonic3"}
    assert si.installable_engine_ids() == frozenset(expected)


def test_a_host_probe_that_raises_counts_as_unsupported():
    def boom():
        raise RuntimeError("probe exploded")

    spec = _mk_spec(host_supported=boom)
    ok, why = si.host_support(spec)
    assert not ok
    assert spec.docs_path in why and "exploded" not in why


def test_start_install_refuses_an_unsupported_host(monkeypatch):
    monkeypatch.setattr(si, "_host_family", lambda: "cpu")
    with pytest.raises(si.HostUnsupported, match="NVIDIA"):
        si.start_install("moss-tts-v15")
    assert "moss-tts-v15" not in si._jobs


def test_router_maps_unsupported_host_to_409(monkeypatch):
    from fastapi import HTTPException
    from api.routers import engines as engines_router
    monkeypatch.setattr(si.sys, "platform", "win32")
    with pytest.raises(HTTPException) as ei:
        engines_router.install_sidecar_engine("dots-tts")
    assert ei.value.status_code == 409
    assert "Windows" in ei.value.detail


def test_list_backends_offers_install_only_where_it_can_work(monkeypatch):
    from services import tts_backend
    monkeypatch.setattr(si, "_host_family", lambda: "cpu")
    monkeypatch.setattr(si.sys, "platform", "win32")
    rows = {r["id"]: r for r in tts_backend.list_backends()}
    assert rows["confucius4-tts"]["one_click_install"] is True
    assert rows["moss-tts-v15"]["one_click_install"] is False
    assert rows["dots-tts"]["one_click_install"] is False


# ── PyPI-package engines (Supertonic-3, PocketTTS) ─────────────────────────


@pytest.mark.parametrize(
    ("engine_id", "package", "env_var"),
    [
        ("supertonic3", "supertonic==1.3.1", "OMNIVOICE_SUPERTONIC3_DIR"),
        ("pockettts", "pocket-tts==2.1.0", "OMNIVOICE_POCKETTTS_DIR"),
    ],
)
def test_pypi_engines_install_the_apps_own_pin_without_fetching_source(
    monkeypatch, engine_id, package, env_var
):
    import tomllib
    spec = si.get_spec(engine_id)
    assert spec.env_var == env_var and not spec.has_source
    # The same pin as the app's optional extra, so the engine runs the same
    # wheel whether it was installed here or with `uv sync --extra`.
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    extras = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    assert package in {req.split(";")[0].strip() for reqs in extras.values() for req in reqs}

    monkeypatch.delenv(env_var, raising=False)
    argvs = _capture_install_argvs(monkeypatch, family="cpu")
    monkeypatch.setattr(si, "disk_free_bytes", lambda p: 100 * _GIB)
    monkeypatch.setattr(si.shutil, "which", lambda n: None)
    _stub_verify_ok(monkeypatch)
    monkeypatch.setattr("core.prefs.set_", lambda k, v: None)

    job = _run(spec)

    assert job["state"] == "succeeded", (job["error"], list(job["log"]))
    assert not any(os.path.basename(a[0]).startswith("git") for a in argvs)
    pip = next(a for a in argvs if a[1:3] == ["pip", "install"])
    assert pip[5] == package
    assert os.environ[env_var] == str(si.managed_checkout(spec))
    assert si._healthy(spec)


@pytest.mark.parametrize("family", ["cuda", "cpu", "rocm", "mps"])
def test_pockettts_installs_cpu_torch_on_every_host(monkeypatch, family):
    from core.torch_indexes import UV_PIP_CPU_ARGS
    argvs = _capture_install_argvs(monkeypatch, family=family)
    si._step_install_deps(si.get_spec("pockettts"), si._new_job("pockettts"))
    pip = next(a for a in argvs if a[1:3] == ["pip", "install"])
    i = pip.index("--extra-index-url")
    assert tuple(pip[i:i + len(UV_PIP_CPU_ARGS)]) == UV_PIP_CPU_ARGS
    assert pip.count("--extra-index-url") == 1


def test_an_extra_already_in_the_app_env_counts_as_installed(monkeypatch):
    """A `uv sync --extra supertonic` install keeps working and is never
    provisioned over."""
    import importlib.util as ilu
    monkeypatch.delenv("OMNIVOICE_SUPERTONIC3_DIR", raising=False)
    real = ilu.find_spec
    monkeypatch.setattr(
        ilu, "find_spec", lambda name, *a: object() if name == "supertonic" else real(name, *a)
    )
    assert si.start_install("supertonic3")["status"] == "already_installed"
    assert "supertonic3" not in si._jobs


def test_engine_venv_python_needs_a_real_interpreter(monkeypatch, tmp_path):
    monkeypatch.delenv("OMNIVOICE_FAKE_SIDE_DIR", raising=False)
    assert si.engine_venv_python("OMNIVOICE_FAKE_SIDE_DIR") is None
    monkeypatch.setenv("OMNIVOICE_FAKE_SIDE_DIR", str(tmp_path))
    assert si.engine_venv_python("OMNIVOICE_FAKE_SIDE_DIR") is None  # no venv yet
    py = si._venv_python(tmp_path / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("#!fake\n")
    # An interpreter without the marker is a failed or unfinished install.
    assert si.engine_venv_python("OMNIVOICE_FAKE_SIDE_DIR") is None
    (tmp_path / si._INSTALL_COMPLETE_MARKER).write_text("x\n", encoding="utf-8")
    assert si.engine_venv_python("OMNIVOICE_FAKE_SIDE_DIR") == py


# The root of each pinned upstream commit, as GitHub lists it (2026-09-10).
_UPSTREAM_ROOT_FILES = {
    "moss-tts-v15": ("pyproject.toml", "README.md", "LICENSE", "MANIFEST.in"),
    "confucius4-tts": ("requirements.txt", "setup.py", "README.md", "LICENSE", "server.py"),
    "dots-tts": ("pyproject.toml", "README.md", "LICENSE", "constraints/recommended.txt"),
    "moss-tts-nano": ("pyproject.toml", "moss_tts_nano_runtime.py", "requirements.txt",
                      "README.md", "LICENSE"),
    "cosyvoice": ("requirements.txt", "README.md", "LICENSE", "cosyvoice/cli/cosyvoice.py",
                  "asset/zero_shot_prompt.wav"),
}


@pytest.mark.parametrize("engine_id", sorted(_UPSTREAM_ROOT_FILES))
def test_a_real_upstream_layout_passes_source_validation(monkeypatch, engine_id):
    """Confucius4 has no pyproject.toml. Source validation demanded one of every
    checkout, so its install could never get past fetching the source."""
    spec = si.get_spec(engine_id)

    def fake_git(job, argv, *, timeout, env=None):
        if argv[1] == "clone":
            checkout = Path(argv[-1])
            for rel in _UPSTREAM_ROOT_FILES[engine_id]:
                (checkout / rel).parent.mkdir(parents=True, exist_ok=True)
                (checkout / rel).write_text("x\n")
        return 0

    def no_tarball(*args, **kwargs):
        pytest.fail("a valid clone fell back to the source tarball")

    monkeypatch.setattr(si.shutil, "which", lambda n: "/usr/bin/git" if n == "git" else None)
    monkeypatch.setattr(si, "_run_logged", fake_git)
    monkeypatch.setattr(si, "_fetch_tarball", no_tarball)
    # Submodule trees come from their own tarballs; not what this test covers.
    monkeypatch.setattr(si, "_ensure_extra_sources", lambda spec, job, checkout: None)

    job = si._new_job(engine_id)
    si._step_fetch_source(spec, job)

    assert si._job_step(job, "fetch_source")["detail"] == "git clone"
    assert si._source_present(spec, si.managed_checkout(spec))


def test_a_failed_dependency_install_is_repaired_not_reported_installed(monkeypatch):
    """A venv whose dependency install died halfway still has its interpreter.
    Counting that as installed made a retry answer already_installed, and the
    engine then failed at its first import."""
    spec = _mk_spec(repo_url="", tarball_url="", has_source=False)
    monkeypatch.setitem(si.SPECS, "fake-side", spec)
    monkeypatch.setattr(si, "_locate_uv", lambda: "/fake/uv")
    monkeypatch.setattr(si, "disk_free_bytes", lambda p: 100 * _GIB)
    monkeypatch.setattr("core.prefs.set_", lambda k, v: None)
    _stub_verify_ok(monkeypatch)
    argvs = []
    ok_run = _fake_run_logged(argvs)

    def pip_fails(job, argv, *, timeout, env=None):
        rc = ok_run(job, argv, timeout=timeout, env=env)
        return 1 if argv[1:3] == ["pip", "install"] else rc

    # A complete install is healthy.
    monkeypatch.setattr(si, "_run_logged", ok_run)
    assert _run(spec)["state"] == "succeeded"
    assert si._healthy(spec)

    # A reinstall whose dependency step fails is not, though the venv remains.
    monkeypatch.setattr(si, "_run_logged", pip_fails)
    assert _run(spec)["state"] == "failed"
    assert si._venv_python(si.managed_checkout(spec) / ".venv").is_file()
    assert not si._healthy(spec)

    # And the next run repairs it.
    monkeypatch.setattr(si, "_run_logged", ok_run)
    assert _run(spec)["state"] == "succeeded"
    assert si._healthy(spec)


@pytest.mark.parametrize(
    ("family", "platform", "suffix", "index"),
    [
        ("cuda", "win32", "+cu128", "https://download.pytorch.org/whl/cu128"),
        ("cuda", "linux", "+cu128", "https://download.pytorch.org/whl/cu128"),
        ("cpu", "win32", "+cpu", "https://download.pytorch.org/whl/cpu"),
        ("rocm", "linux", "+cpu", "https://download.pytorch.org/whl/cpu"),
        ("mps", "darwin", "", None),
    ],
)
def test_torch_pins_follow_the_host(monkeypatch, family, platform, suffix, index):
    """voxcpm leaves torch unpinned, and resolving it with the CUDA index
    paired PyPI's newest torch (CPU-only on Windows) with a CUDA torchaudio.
    The spec pins the pair; the host decides which build of it."""
    spec = si.get_spec("voxcpm2")
    assert spec.torch_pins
    argvs = _capture_install_argvs(monkeypatch, family=family)
    monkeypatch.setattr(si.sys, "platform", platform)

    si._step_install_deps(spec, si._new_job("voxcpm2"))

    pip = next(a for a in argvs if a[1:3] == ["pip", "install"])
    for pin in spec.torch_pins:
        assert f"{pin}{suffix}" in pip
    if index:
        assert pip.count("--extra-index-url") == 1
        assert pip[pip.index("--extra-index-url") + 1] == index
    else:
        assert "--extra-index-url" not in pip


# ── ROCm hosts take a ROCm torch, not the upstream's CUDA build (#2371) ────


def _capture_rocm_install(monkeypatch, family, variant=None):
    """Run indextts2's dependency step and return its `uv pip install` argv."""
    from core.torch_indexes import PYTORCH_ROCM_INDEX_URL

    if variant is None:
        monkeypatch.delenv("OMNIVOICE_TORCH_VARIANT", raising=False)
    else:
        monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", variant)
    monkeypatch.setattr(si.sys, "platform", "linux")
    argvs = _capture_install_argvs(monkeypatch, family=family)
    spec = si.get_spec("indextts2")
    si._step_install_deps(spec, si._new_job("indextts2"))
    pip = next(a for a in argvs if a[1:3] == ["pip", "install"])
    return pip, PYTORCH_ROCM_INDEX_URL


def test_rocm_host_installs_rocm_torch_for_indextts2(monkeypatch):
    """Fail-before/pass-after for #2371: the step used to run a bare
    `uv pip install -e <checkout>`, so upstream's `[tool.uv.sources]` routed
    torch to the cu128 index — a wheel that cannot see an AMD GPU, leaving
    the whole sidecar on CPU (~17x slower on the reporter's RX 6800 XT)."""
    from core.torch_indexes import PYTORCH_CU128_INDEX_URL

    pip, rocm_index = _capture_rocm_install(monkeypatch, family="rocm")
    # Upstream's cu128 tool.uv.sources must be ignored for torch to resolve.
    assert "--no-sources" in pip
    assert "torch==2.8.0+rocm6.4" in pip
    assert "torchaudio==2.8.0+rocm6.4" in pip
    i = pip.index("--extra-index-url")
    assert pip[i + 1] == rocm_index
    assert PYTORCH_CU128_INDEX_URL not in pip


def test_rocm_variant_env_opts_in_before_the_family_probe_reports_rocm(monkeypatch):
    """`OMNIVOICE_TORCH_VARIANT=rocm` is the documented opt-in; it can be set
    while the main venv swap has not landed yet (family still cpu)."""
    pip, _ = _capture_rocm_install(monkeypatch, family="cpu", variant=" ROCm ")
    assert "--no-sources" in pip
    assert "torch==2.8.0+rocm6.4" in pip


def test_rocm_torch_never_leaks_to_other_hosts(monkeypatch):
    """A CUDA host still resolves torch through upstream's own cu128 sources,
    and a CPU host keeps its existing install — ROCm args are ROCm-only."""
    from core.torch_indexes import PYTORCH_ROCM_INDEX_URL

    for family in ("cuda", "cpu"):
        pip, _ = _capture_rocm_install(monkeypatch, family=family)
        assert "--no-sources" not in pip
        assert PYTORCH_ROCM_INDEX_URL not in pip
        assert not any(a.startswith("torch==2.8.0+") for a in pip)


def test_rocm_args_never_reach_engines_that_did_not_opt_in(monkeypatch):
    """voxcpm2 pins its own torch pair and follows the host (+cpu on ROCm,
    per test_torch_pins_follow_the_host); opting indextts2 in must not
    change any other spec's install."""
    from core.torch_indexes import PYTORCH_ROCM_INDEX_URL

    monkeypatch.delenv("OMNIVOICE_TORCH_VARIANT", raising=False)
    monkeypatch.setattr(si.sys, "platform", "linux")
    argvs = _capture_install_argvs(monkeypatch, family="rocm")
    for engine_id in _ALL_SPEC_IDS:
        if engine_id == "indextts2":
            continue
        spec = si.get_spec(engine_id)
        if spec.uses_rocm_index:
            continue
        argvs.clear()
        si._step_install_deps(spec, si._new_job(engine_id))
        pip = next(a for a in argvs if a[1:3] == ["pip", "install"])
        assert "--no-sources" not in pip, engine_id
        assert PYTORCH_ROCM_INDEX_URL not in pip, engine_id


def test_rocm_index_env_is_the_index_uv_resolves_against(monkeypatch):
    """Fail-before/pass-after: `OMNIVOICE_TORCH_INDEX` derived the pin tag
    but the index appended to uv stayed the hard-coded public one — a
    mirror's pins looked up on someone else's server, bypassing the mirror
    a restricted network depends on."""
    mirror = "https://mirror.example.com/pytorch/rocm6.4"
    monkeypatch.setenv("OMNIVOICE_TORCH_INDEX", mirror)
    pip, public = _capture_rocm_install(monkeypatch, family="rocm")
    assert "--no-sources" in pip
    assert "torch==2.8.0+rocm6.4" in pip  # tag still from the mirror's tail
    assert mirror in pip
    assert public not in pip


# ── A pre-#2371 managed install is offered the repair on a ROCm host ───────

_CUDA_TORCH_VERSION_PY = (
    "from typing import Optional\n"
    "__version__ = '2.8.0+cu128'\n"
    "cuda: Optional[str] = '12.8'\n"
    "hip: Optional[str] = None\n"
)
_ROCM_TORCH_VERSION_PY = (
    "from typing import Optional\n"
    "__version__ = '2.8.0+rocm6.4'\n"
    "cuda: Optional[str] = None\n"
    "hip: Optional[str] = '6.4.43482'\n"
)


def _mk_complete_indextts2_install(monkeypatch, torch_version_py: str):
    """A COMPLETE managed indextts2 install whose venv's torch `version.py`
    says which build it carries."""
    monkeypatch.delenv("OMNIVOICE_INDEXTTS_DIR", raising=False)
    monkeypatch.delenv("OMNIVOICE_TORCH_VARIANT", raising=False)
    spec = si.get_spec("indextts2")
    checkout = si.managed_checkout(spec)
    (checkout / "indextts").mkdir(parents=True, exist_ok=True)
    (checkout / "pyproject.toml").write_text("[project]\nname='index-tts'\n")
    (checkout / spec.source_required_path).write_text("# fake infer\n")
    si._write_source_marker(spec, checkout)
    py = si._venv_python(checkout / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("#!fake\n")
    _write_weights(
        checkout / spec.weights_subdir,
        complete=True,
        repo_id=spec.weights_repo_id,
        revision=spec.weights_revision,
        config_name=spec.weights_config_names[0],
    )
    torch_dir = checkout / ".venv" / "lib" / "python3.11" / "site-packages" / "torch"
    torch_dir.mkdir(parents=True)
    (torch_dir / "version.py").write_text(torch_version_py)
    return spec


def test_rocm_host_offers_the_repair_when_the_venv_still_has_cuda_torch(monkeypatch):
    """Fail-before/pass-after for the existing-install half of #2371: by the
    marker rules this install is COMPLETE, so inventory reported installed
    and start_install short-circuited with already_installed — the user
    updates the app and the sidecar still runs on CPU while routing reports
    acceleration."""
    spec = _mk_complete_indextts2_install(monkeypatch, _CUDA_TORCH_VERSION_PY)
    monkeypatch.setattr(si.sys, "platform", "linux")
    monkeypatch.setattr(si, "_host_family", lambda: "rocm")
    assert si._rocm_index_url() is not None  # the gate's condition is live
    assert si._healthy(spec) is False        # fail-before: True (marker-less)


def test_rocm_host_keeps_a_rocm_torch_install_healthy(monkeypatch):
    """The repair offer must not loop: once the deps step has swapped in the
    ROCm build, the same install reads healthy again."""
    spec = _mk_complete_indextts2_install(monkeypatch, _ROCM_TORCH_VERSION_PY)
    monkeypatch.setattr(si.sys, "platform", "linux")
    monkeypatch.setattr(si, "_host_family", lambda: "rocm")
    assert si._healthy(spec) is True


def test_non_rocm_hosts_keep_installed_means_installed(monkeypatch):
    """The gate changes nothing where the recipe did not change: CUDA and
    CPU hosts (and the rocm opt-in turned off) keep the old verdict."""
    spec = _mk_complete_indextts2_install(monkeypatch, _CUDA_TORCH_VERSION_PY)
    monkeypatch.setattr(si.sys, "platform", "linux")
    for family in ("cuda", "cpu"):
        monkeypatch.setattr(si, "_host_family", lambda f=family: f)
        assert si._healthy(spec) is True
    # A rocm-family host with the opt-in machinery unable to select rocm
    # (probe failure degrades _host_family to "cpu" upstream) is covered by
    # the "cpu" case above; non-Linux never takes the rocm index at all.
    monkeypatch.setattr(si.sys, "platform", "win32")
    # Windows-layout interpreter now that _venv_python asks for one:
    checkout = si.managed_checkout(spec)
    win_py = si._venv_python(checkout / ".venv")
    win_py.parent.mkdir(parents=True, exist_ok=True)
    win_py.write_text("#!fake\n")
    monkeypatch.setattr(si, "_host_family", lambda: "rocm")
    assert si._rocm_index_url() is None
    assert si._healthy(spec) is True


# ── Submodule trees, partial weights, and optional post-install data ──────


def _submodule_tarball() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"x = 1\n"
        info = tarfile.TarInfo("Sub-abc123/sub/__init__.py")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_a_submodule_tree_is_fetched_at_its_pinned_revision(monkeypatch):
    """Neither a depth-1 clone nor GitHub's source tarball includes git
    submodules, and CosyVoice imports Matcha-TTS from one."""
    import httpx

    extra = si.ExtraSource(
        path="third_party/Sub", revision="abc123",
        tarball_url="https://example.test/sub.tar.gz", required_path="sub/__init__.py",
    )
    spec = _mk_spec(extra_sources=(extra,))
    urls = []

    def fake_stream(method, url, **kw):
        urls.append(url)
        return _FakeStream(_submodule_tarball() if url == extra.tarball_url
                           else _tarball_bytes("fake-side-main"))

    monkeypatch.setattr(si.shutil, "which", lambda n: None)
    monkeypatch.setattr(httpx, "stream", fake_stream)

    si._step_fetch_source(spec, si._new_job(spec.engine_id))
    sub = si.managed_checkout(spec) / "third_party" / "Sub"
    assert (sub / "sub" / "__init__.py").is_file()
    assert urls == [spec.tarball_url, extra.tarball_url]

    # Present at the pinned revision: nothing is fetched again.
    urls.clear()
    si._step_fetch_source(spec, si._new_job(spec.engine_id))
    assert urls == []

    # At another revision: only the submodule is fetched again.
    (sub / ".voicestudio_source_revision").write_text("old\n", encoding="utf-8")
    si._step_fetch_source(spec, si._new_job(spec.engine_id))
    assert urls == [extra.tarball_url]


def test_only_the_listed_weight_files_are_downloaded(monkeypatch):
    import huggingface_hub

    spec = _mk_spec(
        weights_repo_id="org/model", weights_revision="rev1", weights_subdir="w",
        weights_config_names=("m.yaml",), weights_allow_patterns=("m.yaml", "llm.pt"),
    )
    seen = {}

    def fake_snapshot(**kw):
        seen.update(kw)
        w = Path(kw["local_dir"])
        w.mkdir(parents=True, exist_ok=True)
        (w / "m.yaml").write_text("x")
        (w / "llm.pt").write_bytes(b"\0" * (6 * 1024 * 1024))

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot)
    monkeypatch.setattr("services.endpoint_race.effective_endpoint", lambda: None)
    monkeypatch.setattr("services.token_resolver.resolve", lambda: None)

    si._step_fetch_weights(spec, si._new_job(spec.engine_id))

    assert seen["allow_patterns"] == ["m.yaml", "llm.pt"]
    assert si._weights_present(spec)


def test_weights_from_an_earlier_run_do_not_prove_the_dependencies_finished():
    spec = _mk_spec(weights_repo_id="org/model", weights_revision="r", weights_subdir="w",
                    weights_config_names=("m.yaml",))
    checkout = si.managed_checkout(spec)
    py = si._venv_python(checkout / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("#!fake\n")
    (checkout / "pyproject.toml").write_text("[project]\n")
    w = checkout / "w"
    w.mkdir()
    (w / "m.yaml").write_text("x")
    (w / "llm.pt").write_bytes(b"\0" * (6 * 1024 * 1024))
    (w / si._WEIGHTS_COMPLETE_MARKER).write_text("org/model\nr\n0\n", encoding="utf-8")
    assert si._weights_present(spec)
    assert not si._healthy(spec)
    (checkout / si._INSTALL_COMPLETE_MARKER).write_text("x\n", encoding="utf-8")
    assert si._healthy(spec)
    # IndexTTS predates the marker and keeps its weights-only check.
    assert si.get_spec("indextts2").requires_install_marker is False


def _tarball_with_absolute_symlink() -> bytes:
    """Shaped like the pinned Matcha-TTS tarball: a package plus a `data` link
    to a folder on its author's machine."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"x = 1\n"
        info = tarfile.TarInfo("Matcha-abc/matcha/__init__.py")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("Matcha-abc/data")
        link.type = tarfile.SYMTYPE
        link.linkname = "/home/someone/Projects/Grad-TTS/data"
        tf.addfile(link)
    return buf.getvalue()


def test_a_tarball_with_an_absolute_link_still_extracts(monkeypatch, tmp_path):
    """The stdlib "data" filter raises AbsoluteLinkError on such a link, which
    aborted the Matcha-TTS fetch and with it the CosyVoice install."""
    import httpx

    monkeypatch.setattr(httpx, "stream", lambda method, url, **kw: _FakeStream(_tarball_with_absolute_symlink()))
    dest = tmp_path / "third_party" / "Matcha-TTS"
    dest.parent.mkdir(parents=True)
    job = si._new_job("fake-side")

    si._download_and_extract(job, "https://example.test/m.tar.gz", dest, tmp_path, "OMNIVOICE_FAKE_SIDE_DIR")

    assert (dest / "matcha" / "__init__.py").is_file()
    assert not (dest / "data").exists() and not (dest / "data").is_symlink()


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_a_failed_dependency_install_names_the_windows_path_limit(monkeypatch, platform):
    """openai-whisper (a CosyVoice dependency) builds from source, and under a
    long cache path its build fails on Windows' 260-character limit with a
    bare "No such file or directory"."""
    spec = _mk_spec()
    monkeypatch.setattr(si, "_locate_uv", lambda: "/fake/uv")
    monkeypatch.setattr(si, "_run_logged", lambda job, argv, *, timeout, env=None: 1)
    monkeypatch.setattr(si.sys, "platform", platform)
    with pytest.raises(si._StepError) as err:
        si._step_install_deps(spec, si._new_job(spec.engine_id))
    assert ("LongPathsEnabled" in err.value.remediation) == (platform == "win32")


@pytest.mark.parametrize('backends', [[], ['soundfile']])
def test_moss_audio_probe_survives_optimization(backends):
    """Missing audio support must fail even when Python assertions are disabled."""
    import types
    spec = si.get_spec('moss-tts-nano')
    runtime = types.ModuleType('moss_tts_nano_runtime')
    audio = types.ModuleType('torchaudio')
    audio.list_audio_backends = lambda: backends
    from unittest.mock import patch
    with patch.dict(sys.modules, moss_tts_nano_runtime=runtime, torchaudio=audio):
        probe = compile(spec.probe_code, '<probe>', 'exec', optimize=2)
        if backends:
            exec(probe, {})
        else:
            with pytest.raises(RuntimeError, match='I/O backend'):
                exec(probe, {})


def test_moss_existing_install_offers_dependency_repair(monkeypatch):
    """Old completion markers cannot hide missing audio dependencies."""
    spec = si.get_spec('moss-tts-nano')
    checkout = si.managed_checkout(spec)
    checkout.mkdir(parents=True)
    monkeypatch.setattr(si, '_source_present', lambda *_: True)
    py = si._venv_python(checkout / '.venv')
    py.parent.mkdir(parents=True)
    py.write_text('existing interpreter')
    weights = checkout / 'cached-model.bin'
    weights.write_bytes(b'existing weights')
    marker = checkout / si._INSTALL_COMPLETE_MARKER
    marker.write_text(spec.probe_module + '\n')
    assert not si.get_status(spec.engine_id)['installed']
    jobs = []
    monkeypatch.setattr(si.threading, 'Thread', lambda **kw: SimpleNamespace(start=lambda: jobs.append(kw)))
    monkeypatch.setattr(si, 'host_support', lambda _: (True, ''))
    assert si.start_install(spec.engine_id)['status'] == 'started'
    assert len(jobs) == 1
    monkeypatch.setattr(si.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout='3.11\n'))
    # Execute the repair transaction, including its real source/venv/deps/
    # verification/weights steps. Only external operations are stubbed.
    monkeypatch.setattr(si, '_step_preflight', lambda *_: None)
    monkeypatch.setattr(si, '_locate_uv', lambda: '/fake/uv')
    monkeypatch.setattr(si, '_persist', lambda *_: None)
    commands = []
    monkeypatch.setattr(si, '_run_logged', _fake_run_logged(commands))
    job = _run(spec)
    assert job['state'] == 'succeeded', job
    assert len(commands) == 1
    assert commands[0][1:3] == ['pip', 'install']
    assert 'soundfile' in commands[0]
    assert si._healthy(spec)
    assert weights.read_bytes() == b'existing weights'
    assert py.read_text() == 'existing interpreter'


@pytest.mark.parametrize('version', ['3.14', '3.11', '3.10'])
def test_indextts_repairs_only_incompatible_python(monkeypatch, version):
    spec = si.SPECS['indextts2']
    checkout = si.managed_checkout(spec)
    py = si._venv_python(checkout / '.venv')
    py.parent.mkdir(parents=True)
    py.write_text('old interpreter')
    weights = checkout / 'checkpoints' / 'model.bin'
    weights.parent.mkdir()
    weights.write_bytes(b'existing weights')
    source = checkout / 'pyproject.toml'
    source.write_text('existing source')
    marker = checkout / si._INSTALL_COMPLETE_MARKER
    marker.write_text('old success')
    calls = []
    monkeypatch.setattr(si, '_locate_uv', lambda: '/fake/uv')
    monkeypatch.setattr(si, '_run_logged', _fake_run_logged(calls))
    monkeypatch.setattr(si.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=version + '\n', stderr=''))
    si._step_create_venv(spec, si._new_job(spec.engine_id))
    if version == '3.14':
        assert calls and calls[0][-2:] == ['--python', '3.11']
        assert not marker.exists()
        assert py.read_text() != 'old interpreter'
    else:
        assert calls == []
        assert marker.exists()
        assert py.read_text() == 'old interpreter'
    assert weights.read_bytes() == b'existing weights'
    assert source.read_text() == 'existing source'


def test_all_sidecar_recipes_pin_python():
    for spec in si.SPECS.values():
        assert '--python' in spec.venv_args, spec.engine_id


def test_incompatible_linked_venv_is_not_deleted(monkeypatch, tmp_path):
    spec = _mk_spec(venv_args=('--python', '3.11'), compatible_python=('3.11',))
    checkout = si.managed_checkout(spec)
    checkout.mkdir(parents=True)
    external = tmp_path / 'external'
    py = si._venv_python(external)
    py.parent.mkdir(parents=True)
    py.write_text('user interpreter')
    try:
        (checkout / '.venv').symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip('host does not permit directory symlinks')
    monkeypatch.setattr(si.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=0, stdout='3.14\n', stderr=''))
    with pytest.raises(si._StepError, match='linked outside'):
        si._step_create_venv(spec, si._new_job(spec.engine_id))
    assert py.read_text() == 'user interpreter'
    assert (checkout / '.venv').is_symlink()


@pytest.mark.parametrize('failure', ['timeout', 'invalid', 'exit'])
def test_unproven_venv_is_not_destroyed(monkeypatch, failure):
    spec = _mk_spec(venv_args=('--python', '3.11'), compatible_python=('3.11',))
    py = si._venv_python(si.managed_checkout(spec) / '.venv')
    py.parent.mkdir(parents=True)
    py.write_text('user interpreter')

    def probe(*args, **kwargs):
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(args[0], 15)
        return SimpleNamespace(returncode=1 if failure == 'exit' else 0,
                               stdout='invalid', stderr='')

    monkeypatch.setattr(si.subprocess, 'run', probe)
    with pytest.raises(si._StepError, match='Could not check') as error:
        si._step_create_venv(spec, si._new_job(spec.engine_id))
    assert str(py) not in str(error.value)
    assert py.read_text() == 'user interpreter'


@pytest.mark.parametrize("version", ["3.10", "3.12"])
def test_unspecified_compatibility_does_not_rebuild_working_venv(monkeypatch, version):
    spec = _mk_spec(venv_args=("--python", "3.11"))
    monkeypatch.setattr(si.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=version, stderr=""))
    assert si._existing_venv_compatible(spec, Path("existing-python"))


@pytest.mark.parametrize('engine', ['cosyvoice', 'moss-tts-nano', 'moss-tts-v15'])
def test_runtime_rejects_old_managed_recipe_but_preserves_external_installs(monkeypatch, tmp_path, engine):
    """Recipe upgrades invalidate managed markers while preserving external venvs."""
    spec = si.SPECS[engine]
    checkout = si.managed_checkout(spec)
    py = si._venv_python(checkout / '.venv')
    py.parent.mkdir(parents=True)
    py.write_text('#!fake\n')
    marker = checkout / si._INSTALL_COMPLETE_MARKER
    marker.write_text(spec.probe_module + '\n')
    monkeypatch.setenv(spec.env_var, str(checkout))
    assert si.engine_venv_python(spec.env_var) is None
    assert py.exists() and marker.exists()
    marker.write_text(f'{spec.probe_module}\n{spec.install_revision}\n')
    assert si.engine_venv_python(spec.env_var) == py
    external = tmp_path / 'external'
    external_py = si._venv_python(external / '.venv')
    external_py.parent.mkdir(parents=True)
    external_py.write_text('#!fake\n')
    (external / si._INSTALL_COMPLETE_MARKER).write_text('external-version\n')
    monkeypatch.setenv(spec.env_var, str(external))
    assert si.engine_venv_python(spec.env_var) == external_py


def test_moss_decoder_recipe_upgrade_preserves_venv_and_cached_weights(monkeypatch, tmp_path):
    """An old managed MOSS install resumes dependencies instead of skipping repair."""
    spec = si.SPECS['moss-tts-v15']
    checkout = si.managed_checkout(spec)
    py = si._venv_python(checkout / '.venv')
    py.parent.mkdir(parents=True)
    py.write_text('#!existing-python\n')
    marker = checkout / si._INSTALL_COMPLETE_MARKER
    marker.write_text(spec.probe_module + '\n')
    weights = tmp_path / 'hf-cache' / 'model.safetensors'
    weights.parent.mkdir()
    weights.write_bytes(b'existing weights')
    monkeypatch.setattr(si, '_source_present', lambda *args: True)
    monkeypatch.setattr(si, '_existing_venv_compatible', lambda *args: True)
    monkeypatch.setattr(si, '_locate_uv', lambda: '/fake/uv')
    monkeypatch.setattr(si, '_host_family', lambda: 'cuda')
    commands = []
    monkeypatch.setattr(si, '_run_logged', lambda job, argv, **kw: commands.append(argv) or 0)
    monkeypatch.setattr(si.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0))

    assert not si._healthy(spec)
    job = si._new_job(spec.engine_id)
    si._step_create_venv(spec, job)
    si._step_install_deps(spec, job)
    assert not marker.exists()
    si._step_verify(spec, job)

    assert len(commands) == 1
    assert commands[0][1:3] == ['pip', 'install']
    assert commands[0][3:5] == ['--python', str(py)]
    assert 'torchcodec===0.8.1' in commands[0]
    assert py.read_text() == '#!existing-python\n'
    assert weights.read_bytes() == b'existing weights'
    assert si._healthy(spec)
    assert si._install_marker_valid(spec, checkout)


def test_corrupt_non_utf8_markers_read_as_absent_not_crash(monkeypatch):
    """#2634: a marker with undecodable bytes must mean 'not installed', not raise."""
    spec = _mk_spec(source_revision="rev", weights_repo_id="Example/Weights")
    checkout = si.managed_checkout(spec)
    checkout.mkdir(parents=True, exist_ok=True)
    (checkout / "pyproject.toml").write_text("[project]\nname='fake'\n")
    (checkout / si._SOURCE_REVISION_MARKER).write_bytes(b"\xff\xfe\x80rev")
    assert si._source_present(spec, checkout) is False

    wdir = checkout / spec.weights_subdir
    wdir.mkdir(parents=True, exist_ok=True)
    (wdir / si._WEIGHTS_COMPLETE_MARKER).write_bytes(b"\xff\xfe\x80")
    assert si._weights_present(spec) is False

    extra_dir = checkout / "extra"
    extra_dir.mkdir()
    (extra_dir / "needed.txt").write_text("x")
    (extra_dir / si._SOURCE_REVISION_MARKER).write_bytes(b"\xff\xfe\x80")
    extra = si.ExtraSource("extra", "rev", "https://example.invalid/x.tar.gz", "needed.txt")
    assert si._extra_source_present(extra, extra_dir) is False
