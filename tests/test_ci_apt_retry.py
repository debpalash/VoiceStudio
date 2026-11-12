"""CI apt must go through .github/scripts/apt-retry.sh (transient DNS flakes)."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

GH = Path(__file__).resolve().parent.parent / ".github"
FILES = sorted([*GH.glob("workflows/**/*.yml"), *GH.glob("actions/**/*.yml")])
# apt-get subcommands that never touch the network
OFFLINE = re.compile(r"apt-get\s+(?:-\S+\s+)*(clean|autoclean|purge|remove|autoremove)\b")


SCRIPT = GH / "scripts" / "apt-retry.sh"
bash = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _run(tmp_path, args, script_body="", extra_env=None):
    """Run the helper with stubbed sudo/apt-get/getent on PATH."""
    stubs = tmp_path / "bin"
    stubs.mkdir(exist_ok=True)
    # Never touch real system files: tee/mkdir/systemctl outside tmp_path are no-ops.
    (stubs / "sudo").write_text(
        '#!/bin/sh\n'
        'case "$1" in\n'
        f'  tee) for a in "$@"; do last=$a; done; case "$last" in {tmp_path}/*) exec "$@";; *resolv*) cat >/dev/null; touch {tmp_path}/dns-switched; exit 0;; *) cat >/dev/null; exit 0;; esac;;\n'
        f'  systemctl) touch {tmp_path}/dns-switched; exit 0;;\n'
        '  mkdir) exit 0;;\n'
        '  *) exec "$@";;\n'
        'esac\n',
        encoding="utf-8",
    )
    (stubs / "sleep").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    # Always report a non-root user, so the helper routes every privileged write
    # through the sudo stub above even when the test suite itself runs as root.
    (stubs / "id").write_text('#!/bin/sh\n[ "$1" = "-u" ] && { echo 1000; exit 0; }\nexec /usr/bin/id "$@"\n', encoding="utf-8")
    for name, body in (script_body or {}).items():
        (stubs / name).write_text("#!/bin/bash\n" + body, encoding="utf-8")
    for f in stubs.iterdir():
        f.chmod(0o755)
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "APT_RETRY_DELAYS": "0 0 0",
        "APT_RETRY_HOSTS_FILE": str(tmp_path / "hosts"),
        "APT_RETRY_CONF_DIR": str(tmp_path / "conf"),
        "HOME": str(tmp_path),
        **(extra_env or {}),
    }
    return subprocess.run(
        ["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60
    )


def _flaky(tmp_path, fail_times):
    counter = tmp_path / "count"
    counter.write_text("0", encoding="utf-8")
    return (
        f'n=$(( $(cat {counter}) + 1 )); echo $n > {counter}; '
        f"[ $n -gt {fail_times} ]\n"
    ), counter


@bash
def test_retry_succeeds_after_transient_failures(tmp_path):
    body, counter = _flaky(tmp_path, 2)
    r = _run(tmp_path, ["retry", "flaky"], {"flaky": body, "getent": "exit 0\n"})
    assert r.returncode == 0, r.stderr
    assert counter.read_text(encoding="utf-8").strip() == "3"


@bash
def test_retry_gives_up_after_four_attempts(tmp_path):
    body, counter = _flaky(tmp_path, 99)
    r = _run(tmp_path, ["retry", "flaky"], {"flaky": body, "getent": "exit 0\n"})
    assert r.returncode == 1
    assert counter.read_text(encoding="utf-8").strip() == "4"
    assert "failed after 4 attempts" in r.stderr


@bash
def test_update_that_exits_zero_with_fetch_failures_is_retried(tmp_path):
    counter = tmp_path / "n"
    counter.write_text("0", encoding="utf-8")
    apt = (
        f'case "$*" in *update*) n=$(( $(cat {counter}) + 1 )); echo $n > {counter}; '
        'if [ $n -lt 2 ]; then echo "W: Failed to fetch http://x Temporary failure"; fi; exit 0;; '
        "*install*) exit 0;; esac\n"
    )
    r = _run(tmp_path, ["install", "pkg"], {"apt-get": apt, "getent": "exit 0\n"})
    assert r.returncode == 0, r.stderr
    assert counter.read_text(encoding="utf-8").strip() == "2"


@bash
def test_apt_conf_dropin_written(tmp_path):
    (tmp_path / "conf").mkdir()
    _run(tmp_path, ["retry", "true"], {"getent": "exit 0\n", "true": "exit 0\n"})
    conf = (tmp_path / "conf" / "80-ci-retries").read_text(encoding="utf-8")
    assert 'Acquire::Retries "5"' in conf


@bash
def test_unresolvable_mirrors_are_pinned_in_hosts(tmp_path):
    # getent fails until the hosts file contains the name; dig supplies an IP.
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n")
    getent = f'grep -q " $2$" {hosts}\n'
    dig = 'echo 192.0.2.7\n'
    r = _run(
        tmp_path,
        ["retry", "true"],
        {"getent": getent, "dig": dig, "true": "exit 0\n"},
        {"APT_RETRY_DNS_SETTLE": "0"},
    )
    assert r.returncode == 0, r.stderr
    text = hosts.read_text(encoding="utf-8")
    for h in ("azure.archive.ubuntu.com", "archive.ubuntu.com", "security.ubuntu.com", "ports.ubuntu.com"):
        assert f"192.0.2.7 {h}" in text
    assert "mirrors resolve after /etc/hosts pinning" in r.stderr


@bash
def test_resolvers_switch_alone_is_enough_when_it_works(tmp_path):
    # Names resolve only once the helper has switched resolvers (the sudo stub
    # records that), so the test fails if use_public_dns never runs.
    getent = f'[ -f {tmp_path}/dns-switched ] || exit 2\n'
    r = _run(tmp_path, ["retry", "true"], {"getent": getent, "true": "exit 0\n"},
             {"APT_RETRY_DNS_SETTLE": "0"})
    assert r.returncode == 0, r.stderr
    assert not (tmp_path / "hosts").exists()
    assert (tmp_path / "dns-switched").exists()


def test_no_direct_networked_apt():
    bad = []
    for f in FILES:
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(r"\bapt(-get)?\s+(?:-\S+(?:\s+[^-\s]\S*)?\s+)*(update|install|upgrade|dist-upgrade|full-upgrade)\b", code) or (
                "apt-get" in code and not OFFLINE.search(code)
            ):
                bad.append(f"{f.name}:{n}: {line.strip()}")
    assert not bad, "use .github/scripts/apt-retry.sh:\n" + "\n".join(bad)


def test_playwright_with_deps_not_unwrapped():
    for f in FILES:
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if "playwright" in line and "--with-deps" in line.split("#", 1)[0]:
                raise AssertionError(f"{f.name}:{n}: split into retried install-deps + install")
            if re.search(r"playwright\s+install(-deps)?\b", line.split("#", 1)[0]):
                assert "apt-retry.sh retry" in line, f"{f.name}:{n}: wrap in apt-retry.sh retry"


def test_helper_falls_back_to_public_dns_when_mirrors_do_not_resolve():
    # Runner DNS once failed for hours on the Ubuntu mirror names while public
    # resolvers answered them; the helper must switch resolvers, not only retry.
    script = (GH / "scripts" / "apt-retry.sh").read_text(encoding="utf-8")
    assert "getent hosts" in script and "azure.archive.ubuntu.com" in script
    assert "DNS=1.1.1.1 8.8.8.8" in script and "systemctl restart systemd-resolved" in script
    install = script.split("apt_install() {", 1)[1].split("}", 1)[0]
    assert install.lstrip().startswith("check_dns"), "check DNS before every install attempt"


@bash
def test_helper_takes_the_stubbed_sudo_path_even_as_root(tmp_path):
    # With the id stub, SUDO is always "sudo", so no real system file is written.
    probe = tmp_path / "probe.sh"
    probe.write_text('SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"; echo "SUDO=$SUDO"\n')
    stubs = tmp_path / "bin"
    _run(tmp_path, ["retry", "true"], {"getent": "exit 0\n", "true": "exit 0\n"})  # stubs only, no real DNS
    out = subprocess.run(["bash", str(probe)], env={"PATH": f"{stubs}:/usr/bin:/bin"},
                         capture_output=True, text=True, timeout=10).stdout
    assert out.strip() == "SUDO=sudo"


def test_direct_apt_pattern_catches_leading_options():
    pattern = re.compile(r"\bapt(-get)?\s+(?:-\S+(?:\s+[^-\s]\S*)?\s+)*(update|install|upgrade|dist-upgrade|full-upgrade)\b")
    for line in ("sudo apt -y install pkg", "apt-get -qq update", "apt install x", "sudo apt-get -o A=1 install y"):
        assert pattern.search(line), line
    assert not pattern.search("apt-get clean")


def _helper_fetch_steps():
    import yaml

    found = []
    for name in ("electron-release", "electron-build", "snap-store", "install-smoke", "build-omnivoice-tts"):
        doc = yaml.safe_load((GH / "workflows" / f"{name}.yml").read_text(encoding="utf-8"))
        steps = [
            (name, s.get("name", "?"), s["run"])
            for job in doc["jobs"].values()
            for s in job.get("steps", [])
            if "apt-retry.sh?ref=" in s.get("run", "")
        ]
        assert steps, f"{name}: no step fetches apt-retry.sh from the workflow commit"
        found.extend(steps)
    return found


def _run_step(tmp_path, run, with_helper):
    work = tmp_path / "work"
    (work / ".github" / "scripts").mkdir(parents=True)
    log = tmp_path / "helper-args"
    gh_log = tmp_path / "gh-args"
    fake = f'#!/bin/sh\necho "$@" > {log}\n'
    if with_helper:
        (work / ".github" / "scripts" / "apt-retry.sh").write_text(fake, encoding="utf-8")
    stubs = tmp_path / "bin"
    stubs.mkdir()
    # gh prints a fake helper that records its arguments.
    (stubs / "gh").write_text(
        f'#!/bin/sh\necho "$@" > {gh_log}\ncat <<\'EOS\'\n{fake}EOS\n', encoding="utf-8"
    )
    (stubs / "gh").chmod(0o755)
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_SHA": "abc123",
        "GH_TOKEN": "x",
    }
    r = subprocess.run(["bash", "-eo", "pipefail", "-c", run], cwd=work, env=env,
                       capture_output=True, text=True, timeout=30)
    return r, log, gh_log


@bash
@pytest.mark.parametrize("with_helper", [True, False], ids=["helper-present", "helper-missing"])
def test_workflows_that_build_older_refs_fetch_the_helper_when_missing(tmp_path, with_helper):
    # electron-release/-build, snap-store, install-smoke and build-omnivoice-tts can
    # check out an older tag that predates apt-retry.sh; each step must fetch it from
    # the workflow's own commit and still run `install` with its packages.
    for i, (name, step, run) in enumerate(_helper_fetch_steps()):
        case = tmp_path / f"case{i}"
        case.mkdir()
        expected = re.search(r'bash "\$helper" install ([^\n\\]+)', run)
        assert expected, f"{name}/{step}: helper is never run with `install`"
        pkgs = expected.group(1).split()
        r, log, gh_log = _run_step(case, run, with_helper)
        assert r.returncode == 0, f"{name}/{step}: {r.stderr}"
        got = log.read_text(encoding="utf-8").split()
        assert got[0] == "install" and got[1:] == pkgs, f"{name}/{step}: {got}"
        if with_helper:
            assert not gh_log.exists(), f"{name}/{step}: fetched despite local helper"
        else:
            args = gh_log.read_text(encoding="utf-8")
            assert "repos/owner/repo/contents/.github/scripts/apt-retry.sh?ref=abc123" in args, f"{name}/{step}: {args}"
