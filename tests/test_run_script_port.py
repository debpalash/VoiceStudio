"""``scripts/run.sh`` must validate OMNIVOICE_PORT before it builds a URL from it.

The launcher is the only consumer of that variable that interpolates it into a
string instead of parsing it as an integer: on WSL the URL is handed to
``powershell.exe -Command``, where a quote in the value would end the command
string. Every other consumer (``backend/main.py``, ``backend/docker_entrypoint.py``,
``backend/services/network_share.py``, the MCP shim, the CLI) already requires a
number, so a bad value fails there anyway -- with a confusing bind error rather
than one clear message.

This reads the script rather than executing it: the WSL branch needs a Windows
host and a real ``powershell.exe``, so a runtime test would be skipped exactly
where the change matters.
"""
from __future__ import annotations

import pathlib
import re

_RUN_SH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "run.sh"


def _source() -> str:
    return _RUN_SH.read_text(encoding="utf-8")


def test_port_is_validated_before_it_is_used():
    source = _source()
    assign = source.index('PORT="${OMNIVOICE_PORT-')
    guard = source.index('case "$PORT" in')
    export = source.index('export OMNIVOICE_PORT="$PORT"')
    assert assign < guard < export, (
        "the OMNIVOICE_PORT numeric guard must run between the assignment and the "
        "export, before the value can reach uvicorn or the browser URL"
    )
    assert "''|*[!0-9]*)" in source, "the guard must reject empty and non-numeric values"
    assert '[ "$PORT" -lt 1 ]' in source and '[ "$PORT" -gt 65535 ]' in source, (
        "the guard must also bound the value to a usable TCP port range"
    )


def test_wsl_browser_launch_keeps_the_url_out_of_the_command_text():
    source = _source()
    # Join shell line continuations so the invocation reads as one command.
    joined = source.replace("\\\n", " ")
    launches = [
        line for line in joined.splitlines() if "powershell.exe" in line and "have " not in line
    ]
    assert launches, "the WSL browser branch is gone; update this test with the new launcher"
    for line in launches:
        # `-Command` treats everything after it as more script, so a trailing
        # argument never reaches `$args`; the URL has to arrive through the
        # environment, which also keeps it out of the command text entirely.
        assert 'VOICESTUDIO_OPEN_URL="$URL"' in line, (
            "the URL must be handed to PowerShell through the environment:\n" + line
        )
        assert "$env:VOICESTUDIO_OPEN_URL" in line, (
            "the command must read the URL from the environment:\n" + line
        )
        # WSL forwards only the variables it is told to, so without this the Windows
        # process sees an empty value and the browser never opens. `/u` passes it
        # one way (WSL to Windows) and, unlike `/p`, leaves it unconverted.
        assert "WSLENV=" in line and "VOICESTUDIO_OPEN_URL/u" in line, (
            "the variable must be registered in WSLENV or Windows never receives it:\n" + line
        )
        assert "$args[" not in line, (
            "a trailing argument after -Command is read as script text, not as a value:\n" + line
        )
        assert not re.search(r"Start-Process\s+'", line), (
            "the URL is interpolated into the PowerShell command string again:\n" + line
        )
        assert "exit 0" in line, (
            "Start-Process without a follow-up command makes PowerShell wait for the "
            "opened browser to exit, which holds the launcher open:\n" + line
        )


def test_a_longer_digit_string_is_refused_before_the_arithmetic_comparison():
    """A long enough digit string overflows the comparison, so bound its length first."""
    source = _source()
    length_guard = source.index('"${#PORT}" -gt 5')
    range_check = source.index('[ "$PORT" -gt 65535 ]')
    assert length_guard < range_check, (
        "the length guard must run before the numeric range comparison, or a "
        "40-digit port reaches an arithmetic test that cannot represent it"
    )


def test_an_explicitly_empty_port_is_not_replaced_by_the_default():
    """`${VAR-}` keeps an empty value so the guard sees it; `${VAR:-}` would hide it."""
    source = _source()
    assert 'PORT="${OMNIVOICE_PORT-' in source
    assert 'PORT="${OMNIVOICE_PORT:-' not in source
