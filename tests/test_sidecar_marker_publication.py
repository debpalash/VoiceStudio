"""Completion markers publish atomically without changing existing recipes."""

import errno
import importlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("engine_id", ["voxcpm2", "cosyvoice"])
@pytest.mark.parametrize("existing_marker", [False, True])
@pytest.mark.parametrize("failure", [None, "write", "replace"])
def test_verified_marker_publication_is_atomic_and_retryable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    engine_id: str,
    existing_marker: bool,
    failure: str | None,
) -> None:
    installer = importlib.import_module("services.sidecar_install")
    spec = installer.get_spec(engine_id)
    checkout = tmp_path / "GPU \u0141" / spec.checkout_dirname
    python = installer._venv_python(checkout / ".venv")
    python.parent.mkdir(parents=True)
    python.write_text("test interpreter; subprocess execution is mocked", encoding="utf-8")
    monkeypatch.setattr(installer, "managed_checkout", lambda current_spec: checkout)
    monkeypatch.setattr(installer, "_source_present", lambda current_spec, current_checkout: True)
    monkeypatch.setattr(installer, "_weights_present", lambda current_spec: True)
    monkeypatch.setenv(spec.env_var, str(checkout))
    monkeypatch.setattr(
        installer.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr=b"", stdout=b""),
    )

    marker = checkout / installer._INSTALL_COMPLETE_MARKER
    marker_text = f"{spec.probe_module}\n"
    if spec.install_revision:
        marker_text += f"{spec.install_revision}\n"
    expected_bytes = marker_text.replace("\n", os.linesep).encode("utf-8")
    previous_bytes = expected_bytes if existing_marker else None
    if previous_bytes is not None:
        marker.write_bytes(previous_bytes)
    observed_publications = []
    replace_attempts = []
    original_write = Path.write_text
    original_replace = Path.replace

    def write_marker(path: Path, text: str, *args, **kwargs) -> int:
        if path.parent == checkout and path.name.startswith(marker.name):
            original_write(path, "", *args, **kwargs)
            observed_publications.append((
                marker.read_bytes() if marker.is_file() else None,
                installer._healthy(spec),
                installer.engine_venv_python(spec.env_var),
            ))
            if failure == "write":
                raise OSError(errno.ENOSPC, "interrupted marker write")
        return original_write(path, text, *args, **kwargs)

    def replace_marker(path: Path, target: Path) -> Path:
        if target == marker:
            replace_attempts.append((path.parent, path.read_bytes()))
            if failure == "replace":
                raise PermissionError(errno.EACCES, "marker replacement denied")
        return original_replace(path, target)

    job = installer._new_job(engine_id)
    with monkeypatch.context() as publication_patch:
        publication_patch.setattr(Path, "write_text", write_marker)
        publication_patch.setattr(Path, "replace", replace_marker)
        if failure:
            message = "interrupted marker write" if failure == "write" else "marker replacement denied"
            with pytest.raises(OSError, match=message):
                installer._step_verify(spec, job)
        else:
            installer._step_verify(spec, job)

    assert observed_publications == [(
        previous_bytes,
        existing_marker,
        python if existing_marker else None,
    )]
    assert replace_attempts == ([] if failure == "write" else [(checkout, expected_bytes)])
    assert not list(checkout.glob(f"{marker.name}.*"))
    if failure:
        assert (marker.read_bytes() if marker.is_file() else None) == previous_bytes
        assert installer._healthy(spec) is existing_marker
        installer._step_verify(spec, job)

    assert marker.read_bytes() == expected_bytes
    assert installer._healthy(spec)
    assert installer.engine_venv_python(spec.env_var) == python
    assert not list(checkout.glob(f"{marker.name}.*"))
