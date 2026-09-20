"""`desktop-prod:run` must not destroy the developer's data (#1333).

`scripts/desktop-prod.sh` exists to emulate a first install, so wiping is its
default: it removes the app data dir, the backend data dir (``~/.omnivoice`` —
the SQLite database, every voice profile, and all outputs), the Tauri logs and
the WebKit profile. `--keep-data` is the only thing that suppresses that.

`--skip-build` is an INDEPENDENT flag that only skips the cargo compile. The
package script advertised as a re-launch — its own header calls it "re-launch
last build (skip compile)" — passed `--skip-build` alone, so every "just run it
again" silently deleted the user's voice profiles and project database. That is
the gap this test closes: the wipe stays opt-out, but the *re-launch* aliases
have to opt out of it.

Mechanical on purpose (token-economy convention): a rule a reviewer would have
to remember belongs in a test, not in anyone's head.
"""
import importlib.util
import json
import os
import shutil
import subprocess

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG = os.path.join(_ROOT, "package.json")
_SH = os.path.join(_ROOT, "scripts", "desktop-prod.sh")
_APPIMAGE_PROCESSES = os.path.join(_ROOT, "scripts", "desktop_prod_processes.py")

def _supported_bash() -> str | None:
    """Return a native shell capable of executing desktop-prod.sh."""
    if os.name == "nt":
        roots = filter(
            None,
            (
                os.environ.get("ProgramFiles"),
                os.environ.get("ProgramFiles(x86)"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
            ),
        )
        for root in roots:
            for relative in (("Git", "bin", "bash.exe"), ("Git", "usr", "bin", "bash.exe")):
                candidate = os.path.join(root, *relative)
                if os.path.isfile(candidate):
                    return candidate

    candidate = shutil.which("bash")
    if candidate and not (os.name == "nt" and "\\system32\\" in candidate.lower()):
        return candidate
    return None


def test_desktop_prod_execution_does_not_require_posix_bin_bash():
    """The smoke seam must also collect on native Windows runners."""
    with open(__file__, encoding="utf-8") as fh:
        source = fh.read()
    posix_only_invocation = '["' + "/bin/" + 'bash", fixture_script'
    assert posix_only_invocation not in source


def _scripts() -> dict:
    with open(_PKG, encoding="utf-8") as fh:
        return json.load(fh)["scripts"]


def test_archived_tauri_has_no_root_package_entry_points():
    """The maintained root commands must never launch the archived shell."""
    scripts = _scripts()
    tauri_entries = [name for name in scripts if "tauri" in name]
    assert tauri_entries == [], f"archived Tauri commands remain active: {tauri_entries}"
    assert scripts["dev"] == "bun run --cwd electron dev"
    assert scripts["build"] == "bun run --cwd electron build"
    assert scripts["smoke-test"] == "node scripts/electron-smoke-test.mjs"


def test_skip_build_does_not_imply_keep_data_in_the_script():
    """The fix belongs in the package scripts, not in the flag parsing.

    Making `--skip-build` imply `--keep-data` inside desktop-prod.sh would take
    away a legitimate combination — a fresh-data run that skips the 1-3 min
    compile. Pin that the two stay independent so a later 'simplification'
    doesn't quietly remove it.
    """
    with open(_SH, encoding="utf-8") as fh:
        src = fh.read()
    assert "--skip-build)  SKIP_BUILD=true ;;" in src, (
        "desktop-prod.sh's --skip-build no longer sets only SKIP_BUILD; if it now "
        "also sets KEEP_DATA, the fresh-data-without-recompile combination is gone"
    )
    # And the wipe stays gated on KEEP_DATA alone.
    assert 'if [ "$KEEP_DATA" = false ]; then' in src


def test_running_instances_are_killed_regardless_of_data_policy():
    """Killing the live app must not be gated on the wipe (#1333 review).

    The app registers ``tauri_plugin_single_instance``, and its callback ignores
    the incoming argv — it just refocuses the window the RUNNING process already
    owns. Starting a second copy over a live one therefore does nothing visible:
    ``desktop-prod:run`` would refocus the OLD build instead of the one just
    compiled, and ``desktop-prod:run:pill`` would leave the user in studio mode
    with ``--pill`` silently discarded.

    That was previously masked: the kill lived inside the ``KEEP_DATA = false``
    branch, so every run happened to kill first *because* every run wiped.
    Adding ``--keep-data`` to the re-launch aliases removed the wipe and would
    have taken the kill with it.
    """
    with open(_SH, encoding="utf-8") as fh:
        lines = fh.read().splitlines()

    call_lines = [
        i for i, ln in enumerate(lines)
        if ln.strip() == "kill_running_instances"
    ]
    assert call_lines, "kill_running_instances is never called"

    guard = next(
        i for i, ln in enumerate(lines)
        if ln.strip() == 'if [ "$KEEP_DATA" = false ]; then'
    )
    assert any(i < guard for i in call_lines), (
        "kill_running_instances is only called inside the KEEP_DATA=false wipe "
        "branch, so a --keep-data run launches on top of the live app and "
        "single-instance just refocuses the old window (#1333)"
    )

def test_kill_is_scoped_to_this_checkouts_build():
    """The kill may not reach an installed /Applications copy (#1333 review).

    Now that ``kill_running_instances`` runs on EVERY invocation rather than
    only on wipe runs, its pattern matters in a way it did not before. A bare
    ``"VoiceStudio.app"`` matches the installed release app too, so a
    developer running ``desktop-prod:run`` while using the shipped app would
    have it killed underneath them — losing unsaved work in a session this
    script never started. Previously that was masked: the kill only ran when
    the developer had explicitly asked for a wipe.

    Scoping the pattern to ``${TAURI_DIR}/target/debug/`` keeps it to what this
    checkout built. ``pgrep -f`` sees the absolute path, of which the
    repo-relative prefix is a substring, and both launch shapes (raw binary and
    ``.app`` bundle) live under it.
    """
    with open(_SH, encoding="utf-8") as fh:
        src = fh.read()

    body = src.split("kill_running_instances() {", 1)[1].split("\n}", 1)[0]
    pgrep = next(
        ln.strip() for ln in body.splitlines()
        if "pgrep -f" in ln and not ln.strip().startswith("#")
    )
    assert "target/debug/" in pgrep, (
        "kill_running_instances' pgrep pattern is not scoped to this checkout's "
        f"build output, so it can match an installed app: {pgrep}"
    )
    assert "${APP_NAME}.app" not in pgrep, (
        "kill_running_instances matches any 'VoiceStudio.app', including "
        f"the installed one in /Applications: {pgrep}"
    )
    # The installed copy still has to be surfaced — single-instance keys on the
    # bundle id, so ignoring it silently swaps one confusing failure for another.
    assert "warn_installed_instance" in body, (
        "an installed instance is neither killed nor mentioned; single-instance "
        "will swallow the launch and the developer gets no explanation"
    )


def test_linux_extracted_appimage_and_backend_are_stopped_before_wipe(tmp_path):
    """Extraction hides the checkout path from argv, but APPIMAGE survives.

    Reproduce the production failure with a fake procfs: the Tauri process and
    backend inherit the same owned APPIMAGE, while a similarly named build from
    another checkout and an unrelated process must remain untouched.
    """
    build_root = tmp_path / "repo" / "frontend" / "src-tauri" / "target" / "debug"
    proc_root = tmp_path / "proc"

    def process(pid: int, *environment: str) -> None:
        process_dir = proc_root / str(pid)
        process_dir.mkdir(parents=True)
        (process_dir / "environ").write_bytes(
            ("\0".join(environment) + "\0").encode()
        )
        # field 22 (starttime) is index 19 after the closing comm parenthesis.
        (process_dir / "stat").write_text(
            f"{pid} (VoiceStudio worker) S " + " ".join(["0"] * 18 + [str(pid)])
        )

    owned = build_root / "bundle" / "appimage" / "VoiceStudio_0.4.2_amd64.AppImage"
    process(101, f"APPIMAGE={owned}", "HOME=/home/test")
    process(102, f"APPIMAGE={owned}", "ROLE=backend")
    process(
        201,
        f"APPIMAGE={build_root}-other/bundle/appimage/VoiceStudio_0.4.2_amd64.AppImage",
    )
    process(202, f"APPIMAGE={owned}.untrusted")
    process(203, "HOME=/home/test")

    spec = importlib.util.spec_from_file_location(
        "desktop_prod_processes", _APPIMAGE_PROCESSES
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    opened = []

    def fake_pidfd_open(pid: int, flags: int) -> int:
        opened.append((pid, flags))
        return os.open(os.devnull, os.O_RDONLY)

    owned = module.open_owned_processes(
        build_root, proc_root, pidfd_open=fake_pidfd_open
    )
    found = [str(pid) for pid, _ in owned]
    for _, pidfd in owned:
        os.close(pidfd)

    assert found == ["101", "102"]
    assert [pid for pid, _ in opened] == [101, 102, 201, 202, 203]


def test_linux_process_stop_executes_before_data_wipe(tmp_path):
    """Execute the shell with controlled commands and record the true order."""
    fixture_root = tmp_path / "repo"
    fixture_scripts = fixture_root / "scripts"
    fixture_scripts.mkdir(parents=True)
    fixture_script = fixture_scripts / "desktop-prod.sh"
    shutil.copy2(_SH, fixture_script)
    shutil.copy2(
        _APPIMAGE_PROCESSES,
        fixture_scripts / "desktop_prod_processes.py",
    )
    # Let the AppImage lookup complete normally, then exercise the intended
    # no-artifact launch failure without consulting this checkout's target/.
    (fixture_root / "frontend/src-tauri/target/debug/bundle/appimage").mkdir(
        parents=True
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    order_log = tmp_path / "order.log"
    backend_data = tmp_path / "home" / ".omnivoice"
    backend_data.mkdir(parents=True)
    marker = backend_data / "order-marker"
    marker.write_text("live backend data")

    fake_python = fake_bin / "python3"
    fake_python.write_text(
        "#!/bin/sh\n"
        '[ "$1" = scripts/desktop_prod_processes.py ] || exit 90\n'
        '[ -f "$ORDER_MARKER" ] || exit 91\n'
        'printf "stop-before-wipe\\n" >> "$ORDER_LOG"\n'
    )
    fake_python.chmod(0o755)
    fake_uname = fake_bin / "uname"
    fake_uname.write_text("#!/bin/sh\nprintf 'Linux\\n'\n")
    fake_uname.chmod(0o755)
    for command in ("pgrep", "lsof"):
        stub = fake_bin / command
        stub.write_text("#!/bin/sh\nexit 1\n")
        stub.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "XDG_DATA_HOME": str(tmp_path / "xdg"),
            "ORDER_LOG": str(order_log),
            "ORDER_MARKER": str(marker),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
        }
    )
    bash = _supported_bash()
    if bash is None:
        pytest.skip("desktop-prod.sh smoke requires Bash (for example Git Bash on Windows)")
    result = subprocess.run(
        [bash, fixture_script, "--skip-build"],  # noqa: S603
        cwd=fixture_root,
        env=env,
        capture_output=True,
        text=True,
    )

    # No build artifact exists in this fixture, so launch fails only after the
    # stop and wipe steps have both executed.
    assert result.returncode != 0
    assert order_log.read_text().splitlines() == ["stop-before-wipe"]
    assert not backend_data.exists()


def test_linux_pid_reuse_is_rejected_after_environment_read(tmp_path):
    build_root = tmp_path / "repo" / "frontend" / "src-tauri" / "target" / "debug"
    proc_root = tmp_path / "proc"
    process_dir = proc_root / "101"
    process_dir.mkdir(parents=True)
    owned = build_root / "bundle" / "appimage" / "VoiceStudio_0.4.2_amd64.AppImage"
    (process_dir / "environ").write_bytes(f"APPIMAGE={owned}\0".encode())

    spec = importlib.util.spec_from_file_location(
        "desktop_prod_processes_reuse", _APPIMAGE_PROCESSES
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    identities = iter(["old-start", "replacement-start"])
    opened_fds = []

    def fake_pidfd_open(pid: int, flags: int) -> int:
        fd = os.open(os.devnull, os.O_RDONLY)
        opened_fds.append(fd)
        return fd

    found = module.open_owned_processes(
        build_root,
        proc_root,
        pidfd_open=fake_pidfd_open,
        read_start_time=lambda _path: next(identities),
    )

    assert found == []
    for fd in opened_fds:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_reset_waits_for_killed_process_exit_or_fails(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "desktop_prod_processes_exit", _APPIMAGE_PROCESSES
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def run(exits_after_kill: bool) -> tuple[list[int] | None, list[int]]:
        fd = os.open(os.devnull, os.O_RDONLY)
        monkeypatch.setattr(module, "open_owned_processes", lambda _root: [(101, fd)])
        polls = iter([set(), {fd} if exits_after_kill else set()])
        monkeypatch.setattr(module, "_poll_exited", lambda *_args: next(polls))
        sent = []
        monkeypatch.setattr(module, "_signal_process", lambda _fd, sig: sent.append(sig))
        try:
            return module.stop_owned_processes(tmp_path), sent
        except RuntimeError:
            return None, sent

    result, sent = run(True)
    assert result == [101]
    assert sent == [module.signal.SIGTERM, module.signal.SIGKILL]

    result, sent = run(False)
    assert result is None
    assert sent == [module.signal.SIGTERM, module.signal.SIGKILL]
