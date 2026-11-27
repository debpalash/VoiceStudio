"""Windows serves transformers' CPU mmap reads with safetensors' pread backend (#4142)."""
import sys

import pytest


@pytest.fixture
def sw(monkeypatch):
    from omnivoice.utils import safetensors_windows

    monkeypatch.delenv(safetensors_windows.OPT_OUT_ENV, raising=False)
    yield safetensors_windows
    safetensors_windows.uninstall()


@pytest.fixture
def checkpoint(tmp_path):
    torch = pytest.importorskip("torch")
    from safetensors.torch import save_file

    path = tmp_path / "w.safetensors"
    save_file({"a": torch.arange(4.0)}, str(path))
    return str(path)


def _spy(monkeypatch):
    import safetensors

    calls = []
    real = safetensors.safe_open

    def spy(filename, framework, device="cpu", *args, **kwargs):
        calls.append(kwargs.get("backend"))
        return real(filename, framework, device, *args, **kwargs)

    monkeypatch.setattr(safetensors, "safe_open", spy)
    return calls, spy


def test_windows_cpu_mmap_reads_use_pread_everywhere_safe_open_was_bound(sw, monkeypatch, checkpoint):
    import types

    calls, spy = _spy(monkeypatch)
    early = types.ModuleType("bound_early")  # stands in for transformers.modeling_utils
    early.safe_open = spy
    monkeypatch.setitem(sys.modules, "bound_early", early)

    assert sw.install(platform="win32") is True
    with early.safe_open(checkpoint, framework="pt", device="cpu", backend="mmap") as f:
        assert f.get_tensor("a").tolist() == [0.0, 1.0, 2.0, 3.0]
    import safetensors

    with safetensors.safe_open(checkpoint, "pt") as f:
        f.keys()
    assert calls == ["pread", "pread"]
    assert sw.install(platform="win32") is True  # idempotent: no double wrap
    with safetensors.safe_open(checkpoint, "pt", backend="mmap") as f:
        f.keys()
    assert calls[-1] == "pread" and len(calls) == 3


def test_other_platforms_and_the_opt_out_keep_mmap(sw, monkeypatch):
    import safetensors

    original = safetensors.safe_open
    assert sw.install(platform="darwin") is False
    monkeypatch.setenv(sw.OPT_OUT_ENV, "0")
    assert sw.install(platform="win32") is False
    assert safetensors.safe_open is original


def test_an_older_safetensors_without_pread_still_loads(sw, monkeypatch):
    import safetensors

    calls = []

    def old_safe_open(filename, framework, device="cpu", **kwargs):
        calls.append(kwargs.get("backend"))
        if "backend" in kwargs:
            raise TypeError("safe_open() got an unexpected keyword argument 'backend'")
        return "opened"

    monkeypatch.setattr(safetensors, "safe_open", old_safe_open)
    assert sw.install(platform="win32") is True
    assert safetensors.safe_open("w.safetensors", "pt") == "opened"
    assert calls == ["pread", None]


def test_a_real_read_failure_is_not_masked(sw, monkeypatch):
    import safetensors

    def broken(filename, framework, device="cpu", **kwargs):
        raise OSError("disk read failed")

    monkeypatch.setattr(safetensors, "safe_open", broken)
    sw.install(platform="win32")
    with pytest.raises(OSError, match="disk read failed"):
        safetensors.safe_open("w.safetensors", "pt")


def test_gpu_reads_are_left_alone(sw, monkeypatch):
    import safetensors

    seen = []
    monkeypatch.setattr(safetensors, "safe_open", lambda f, fw, device="cpu", **k: seen.append((device, k)))
    sw.install(platform="win32")
    safetensors.safe_open("w.safetensors", "pt", device="cuda:0")
    safetensors.safe_open("w.safetensors", "pt", device="mps", backend="pread")
    assert seen == [("cuda:0", {}), ("mps", {"backend": "pread"})]


def test_a_none_device_counts_as_cpu(sw, monkeypatch, checkpoint):
    calls, _ = _spy(monkeypatch)
    sw.install(platform="win32")
    import safetensors

    with safetensors.safe_open(checkpoint, "pt", device=None, backend="mmap") as f:
        assert f.get_tensor("a").tolist() == [0.0, 1.0, 2.0, 3.0]
    assert calls == ["pread"]


def test_an_error_naming_a_backend_folder_is_not_mistaken_for_no_pread(sw, monkeypatch, caplog):
    import logging

    import safetensors

    def missing(filename, framework, device="cpu", **kwargs):
        raise FileNotFoundError(f"No such file: C:\\\\Users\\\\alice\\\\backend\\\\{filename}")

    monkeypatch.setattr(safetensors, "safe_open", missing)
    sw.install(platform="win32")
    with caplog.at_level(logging.INFO, logger="omnivoice.safetensors"):
        with pytest.raises(FileNotFoundError):
            safetensors.safe_open("w.safetensors", "pt")
    assert "alice" not in caplog.text
