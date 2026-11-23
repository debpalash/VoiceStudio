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


def test_piped_utf8_bom_takes_precedence_over_the_console_code_page(monkeypatch):
    from voicestudio_cli.__main__ import document, read_text

    data = '\ufeff{"text": "Caf\u00e9"}'.encode("utf-8")
    with io.TextIOWrapper(io.BytesIO(data), encoding="cp1252") as pipe:
        monkeypatch.setattr(sys, "stdin", pipe)
        assert document("-") == {"text": "Caf\u00e9"}

    with io.TextIOWrapper(io.BytesIO(b"Caf\xe9"), encoding="cp1252") as pipe:
        monkeypatch.setattr(sys, "stdin", pipe)
        assert read_text("-") == "Caf\u00e9"


def test_piped_script_keeps_universal_newlines(monkeypatch):
    from voicestudio_cli.__main__ import read_text

    with io.TextIOWrapper(io.BytesIO(b"Hello\r\nWorld\r"), encoding="utf-8") as pipe:
        monkeypatch.setattr(sys, "stdin", pipe)
        assert read_text("-") == "Hello\nWorld\n"
