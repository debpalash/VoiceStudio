"""The uninstall scripts' opt-in `app_uninstalled` ping — the contract, pinned.

- Sent ONLY when the user opted in: consent is read from the SAME prefs store
  the app writes (`prefs.json: analytics_enabled`), AND the backend-written
  `analytics_info.json` must exist (the backend only writes it while analytics
  is enabled and removes it on opt-out — its presence is itself consent-gated).
- Content-free: app version, OS name, random per-install id. Nothing else.
- Best-effort: 2s timeout, silent failure, never blocks the uninstall.
- Honest: exactly one console line when it sends; silence when not opted in.
- Never on dry-run: the ping lives behind the --yes / -Yes gate.

The bash script is exercised for real (fake $HOME + a curl shim that records
its argv); PowerShell isn't runnable on every dev/CI platform, so uninstall.ps1
is pinned by static contract checks against the same requirements.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SH = os.path.join(REPO, "scripts", "uninstall.sh")
PS1 = os.path.join(REPO, "scripts", "uninstall.ps1")

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"), reason="bash script is macOS/Linux-only"
)


def _run(tmp_path, *args, consented=True, with_info=True):
    """Run uninstall.sh in a throwaway HOME with a curl shim on PATH."""
    home = tmp_path / "home"
    data = tmp_path / "data"
    data.mkdir(parents=True)
    home.mkdir(parents=True)
    (data / "prefs.json").write_text(
        json.dumps({"analytics_enabled": bool(consented), "analytics_prompted": True})
    )
    if with_info:
        (data / "analytics_info.json").write_text(
            json.dumps(
                {
                    "token": "phc_test_token",
                    "host": "https://eu.i.posthog.com",
                    "distinct_id": "11111111-2222-3333-4444-555555555555",
                    "app_version": "0.3.23",
                    "platform": "macos",
                },
                indent=2,
            )
        )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl_log = tmp_path / "curl_args.txt"
    shim = bin_dir / "curl"
    shim.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$@" >> "{curl_log}"\nexit 0\n')
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)

    env = dict(os.environ)
    env.update(
        {
            "HOME": str(home),
            "OMNIVOICE_DATA_DIR": str(data),
            # Keep the shared-model-cache branch inert and inside the sandbox.
            "OMNIVOICE_CACHE_DIR": str(tmp_path / "models-nonexistent"),
            "PATH": f"{bin_dir}:{env.get('PATH', '')}",
        }
    )
    proc = subprocess.run(
        ["bash", SH, *args], capture_output=True, text=True, env=env, timeout=60
    )
    curl_args = curl_log.read_text(encoding="utf-8") if curl_log.exists() else ""
    return proc, curl_args


def test_bash_syntax_is_valid():
    subprocess.run(["bash", "-n", SH], check=True)


def test_consented_delete_sends_one_honest_ping(tmp_path):
    proc, curl = _run(tmp_path, "--yes")
    assert proc.returncode == 0, proc.stderr
    assert "Sending anonymous uninstall ping (you opted in to analytics)." in proc.stdout
    # One capture call, to the host from analytics_info.json, with the ping.
    assert "https://eu.i.posthog.com/capture/" in curl
    assert curl.count("app_uninstalled") == 1
    assert "phc_test_token" in curl
    assert "11111111-2222-3333-4444-555555555555" in curl
    assert '"app_version":"0.3.23"' in curl.replace(" ", "")
    # Best-effort timeout is on the call.
    assert re.search(r"^-m\n2$", curl, re.M)


def test_not_consented_sends_nothing_and_prints_nothing(tmp_path):
    proc, curl = _run(tmp_path, "--yes", consented=False)
    assert proc.returncode == 0, proc.stderr
    assert curl == ""
    assert "uninstall ping" not in proc.stdout.lower()


def test_missing_info_file_sends_nothing_even_if_prefs_claim_consent(tmp_path):
    """analytics_info.json only exists while the backend had consent AND a
    token — a hand-edited prefs.json alone must not produce a ping (there is
    no destination to send to)."""
    proc, curl = _run(tmp_path, "--yes", consented=True, with_info=False)
    assert proc.returncode == 0, proc.stderr
    assert curl == ""
    assert "uninstall ping" not in proc.stdout.lower()


def test_dry_run_never_pings_even_when_consented(tmp_path):
    proc, curl = _run(tmp_path)  # no --yes
    assert proc.returncode == 0, proc.stderr
    assert "DRY RUN" in proc.stdout
    assert curl == ""
    assert "uninstall ping" not in proc.stdout.lower()


def test_data_is_still_deleted_after_the_ping(tmp_path):
    _run(tmp_path, "--yes")
    assert not (tmp_path / "data").exists()


# ── uninstall.ps1: static contract checks (PowerShell isn't runnable here) ──


def _ps1_source() -> str:
    with open(PS1, encoding="utf-8") as f:
        return f.read()


def test_ps1_ping_is_consent_gated_and_best_effort():
    src = _ps1_source()
    # Consent gate: prefs.json's analytics_enabled AND the backend-written info file.
    assert "analytics_info.json" in src
    assert "prefs.json" in src
    assert re.search(r"analytics_enabled\s+-eq\s+\$true", src)
    # Best-effort: 2s timeout inside a try/catch.
    assert "-TimeoutSec 2" in src
    assert "try {" in src and "} catch {" in src
    # Honest single line, matching the bash script's wording.
    assert "Sending anonymous uninstall ping (you opted in to analytics)." in src
    # The ping only runs on an actual delete (-Yes) — after the dry-run exit.
    assert src.index("exit 0") < src.index("app_uninstalled")


def test_ps1_and_sh_carry_no_baked_token():
    """The scripts are generic: the token comes from the backend-written info
    file, never from a literal in the repo (the secret scanner would agree)."""
    for path in (SH, PS1):
        with open(path, encoding="utf-8") as f:
            src = f.read()
        assert not re.search(r"phc_[A-Za-z0-9]{16,}", src), path


def test_shellcheck_clean_if_available():
    """Run shellcheck when the tool exists (dev machines / CI images that have
    it); skip silently elsewhere — the bash -n + functional tests still run."""
    if not shutil.which("shellcheck"):
        pytest.skip("shellcheck not installed")
    proc = subprocess.run(
        ["shellcheck", "-S", "error", SH], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_shipped_powershell_scripts_with_non_ascii_carry_a_utf8_bom():
    """A BOM-less UTF-8 .ps1 is decoded with the ANSI code page by Windows
    PowerShell 5.1 — still the default shell on Windows 10/11.

    `uninstall.ps1` prints em-dashes in `Write-Host` output ("Model cache
    (Hugging Face weights — SHARED …)"), so without a BOM the user running the
    uninstaller sees mojibake in the very messages that explain what is about
    to be deleted. Pinned rather than left to review: the BOM is invisible in a
    diff, so an editor that "helpfully" strips it would go unnoticed until a
    Windows user reported garbled output (CodeRabbit, #1399).

    Scoped to `scripts/` — the vendored trees under `research/` are not ours.
    """
    scripts_dir = os.path.join(REPO, "scripts")
    offenders = []
    for name in sorted(os.listdir(scripts_dir)):
        if not name.endswith(".ps1"):
            continue
        raw = open(os.path.join(scripts_dir, name), "rb").read()
        if raw.startswith(b"\xef\xbb\xbf"):
            continue
        if any(ord(ch) > 127 for ch in raw.decode("utf-8")):
            offenders.append(name)
    assert not offenders, (
        f"these shipped PowerShell scripts contain non-ASCII text but have no "
        f"UTF-8 BOM, so Windows PowerShell 5.1 will mis-decode them: {offenders}"
    )
