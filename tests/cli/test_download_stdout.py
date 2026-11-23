"""A truncated piped audio download must fail the CLI command."""

import io
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))


def test_incomplete_stdout_download_reports_failure(capsysbinary):
    from voicestudio_cli.errors import CLIError
    from voicestudio_cli.transport import download

    response = io.BytesIO(b"RIFF")
    response.headers = {"Content-Length": "14"}
    with pytest.raises(CLIError, match="Download ended before all bytes arrived"):
        download(response, "-")
    assert capsysbinary.readouterr().out == b"RIFF"
