"""A valid output name should not become invalid when staging its download."""

import io
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))


@pytest.mark.parametrize("overwrite", [False, True])
def test_download_preserves_long_destination_filename(tmp_path, overwrite):
    from voicestudio_cli.transport import download

    destination = tmp_path / ("a" * 249 + ".wav")
    try:
        destination.write_bytes(b"old")
    except OSError as error:
        pytest.skip(f"Test filesystem does not support this destination name: {error}")
    if not overwrite:
        destination.unlink()

    response = io.BytesIO(b"RIFF")
    response.headers = {"Content-Length": "4"}
    assert download(response, str(destination), overwrite=overwrite)["bytes"] == 4
    assert destination.read_bytes() == b"RIFF"
    assert list(tmp_path.iterdir()) == [destination]
