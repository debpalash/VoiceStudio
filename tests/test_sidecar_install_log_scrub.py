"""Install-job log lines must be redacted before they reach the UI and the log.

`_log` receives raw ``git``/``uv`` output. uv runs with an environment derived
from ``os.environ``, and a credential-bearing mirror or proxy URL is a sanctioned
configuration for restricted networks, so uv echoes ``https://user:token@host``
into a log the Model Catalogue install panel renders -- and that a user may paste
into a bug report. ``subprocess_backend`` already redacts sidecar stderr for the
same reason.
"""
from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "backend"))

import services.sidecar_install as sidecar_install  # noqa: E402


def _job() -> dict:
    return {"engine_id": "indextts2", "log": []}


def test_a_credential_bearing_url_is_redacted_from_the_job_log():
    job = _job()
    sidecar_install._log(
        job, "Collecting package from https://user:s3cr3t@mirror.example/simple/ ..."
    )
    assert "s3cr3t" not in "\n".join(job["log"]), job["log"]


def test_a_credential_bearing_url_is_redacted_from_the_emitted_record(caplog):
    job = _job()
    with caplog.at_level("INFO"):
        sidecar_install._log(job, "HTTP_PROXY=https://ops:hunter2@proxy.internal:8080")
    assert "hunter2" not in caplog.text


def test_a_home_path_is_redacted():
    job = _job()
    sidecar_install._log(job, "Installing from /home/someone/engines/indextts2")
    joined = "\n".join(job["log"])
    assert "/home/someone" not in joined, joined


def test_clean_lines_survive_and_keep_their_content():
    job = _job()
    sidecar_install._log(job, "Cloning https://github.com/IndexTeam/IndexTTS-2.git ...  ")
    assert job["log"] == ["Cloning https://github.com/IndexTeam/IndexTTS-2.git ..."]


def test_blank_lines_are_dropped():
    job = _job()
    sidecar_install._log(job, "   ")
    assert job["log"] == []


def test_redaction_never_raises_into_the_caller(monkeypatch):
    job = _job()
    import core.scrub as scrub

    def boom(_text: str) -> str:
        raise RuntimeError("redactor unavailable")

    monkeypatch.setattr(scrub, "scrub_text", boom)
    # The line is still recorded, unredacted, rather than losing the install log.
    sidecar_install._log(job, "uv pip install --python /venv/bin/python -e .")
    assert job["log"] == ["uv pip install --python /venv/bin/python -e ."]


def test_every_streamed_line_goes_through_log():
    """The drain thread must not append to the job log directly."""
    source = pathlib.Path(sidecar_install.__file__).read_text(encoding="utf-8")
    direct = [
        line
        for line in source.splitlines()
        if re.search(r'job\["log"\]\.append', line)
    ]
    assert len(direct) == 1, (
        "the job log is appended to outside `_log`, which bypasses redaction:\n"
        + "\n".join(direct)
    )
