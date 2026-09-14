"""alembic.ini must parse in every Windows ANSI code page.

alembic reads its ini with ``encoding="locale"`` (``alembic.util.compat.
read_config_parser``). On Python 3.11 that is the Windows ANSI code page even
with ``PYTHONUTF8=1`` — so on a Chinese, Japanese or Korean Windows the em
dashes in the ini's comments raised ``UnicodeDecodeError`` inside ``Config()``,
and every startup of a from-source install logged "alembic upgrade head
skipped" and never ran a migration or took the pre-migration backup.
"""
from __future__ import annotations

import configparser
from pathlib import Path

import pytest

_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


@pytest.mark.parametrize("code_page", ["cp932", "cp936", "cp949", "cp950", "cp1252"])
def test_alembic_ini_parses_in_the_windows_ansi_code_page(code_page):
    # The same parser alembic.config.Config builds: `here` is its default.
    parser = configparser.ConfigParser({"here": str(_INI.parent)})
    assert parser.read(_INI, encoding=code_page) == [str(_INI)]
    assert parser.get("alembic", "script_location").endswith("/backend/migrations")


def test_alembic_ini_is_ascii_so_no_locale_can_break_it():
    data = _INI.read_bytes()
    offenders = [
        f"line {n}: {line.decode('utf-8', 'replace').strip()}"
        for n, line in enumerate(data.splitlines(), start=1)
        if any(b > 0x7F for b in line)
    ]
    assert not offenders, (
        "alembic.ini is read in the locale code page, so it must stay ASCII:\n  "
        + "\n  ".join(offenders)
    )
