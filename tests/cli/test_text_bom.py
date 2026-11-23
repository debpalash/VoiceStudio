"""UTF-8 request files saved by Windows editors may begin with a BOM."""

import io
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))


@pytest.mark.parametrize("stdin", [False, True])
def test_cli_reads_utf8_bom_in_json_and_script_input(stdin, tmp_path, monkeypatch):
    from voicestudio_cli.__main__ import document, read_text

    payload = '\ufeff{"text": "Hello"}'
    if stdin:
        monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
        source = "-"
    else:
        path = tmp_path / "request.json"
        path.write_text(payload, encoding="utf-8")
        source = str(path)
    assert document(source) == {"text": "Hello"}

    script = "\ufeffHello\ufeff there"
    if stdin:
        monkeypatch.setattr(sys, "stdin", io.StringIO(script))
    else:
        path.write_text(script, encoding="utf-8")
    assert read_text(source) == "Hello\ufeff there"
