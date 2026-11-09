from __future__ import annotations

import sys

import pytest

from utils import hf_progress


class _WriteFailure:
    def __init__(self, error):
        self.error = error
        self.label = "forwarded"

    def write(self, _text):
        raise self.error


@pytest.mark.parametrize(
    "error",
    [OSError("pipe closed"), UnicodeEncodeError("cp1252", "λ", 0, 1, "unsupported")],
)
def test_safe_file_wrapper_swallows_expected_write_failures(error):
    wrapped = hf_progress.SafeFileWrapper(_WriteFailure(error))
    assert wrapped.write("message") is None
    assert wrapped.label == "forwarded"
    assert wrapped._is_safe_wrapper is True


def test_safe_file_wrapper_allows_missing_flush():
    class WriteOnly:
        def write(self, text):
            return len(text)

    hf_progress.SafeFileWrapper(WriteOnly()).flush()


def test_safe_file_wrapper_does_not_hide_unrelated_errors():
    wrapped = hf_progress.SafeFileWrapper(_WriteFailure(RuntimeError("bug")))
    with pytest.raises(RuntimeError, match="bug"):
        wrapped.write("message")


class _FakeTqdm:
    fail_init = False
    fail_update = False
    fail_display = False
    fail_close = False

    @staticmethod
    def status_printer(_file):
        return lambda _text: None

    def __init__(self, *args, **kwargs):
        if self.fail_init:
            raise OSError("init output failed")
        self.desc = kwargs.get("desc")
        self.total = kwargs.get("total")
        self.n = 0
        self.unit = kwargs.get("unit")
        self.fp = kwargs.get("file")

    @property
    def format_dict(self):
        return {}

    def update(self, n=1):
        if self.fail_update:
            raise OSError("update output failed")
        self.n += n
        return True

    def display(self, msg=None, pos=None):
        if self.fail_display:
            raise OSError("display output failed")
        return (msg, pos)

    def close(self):
        if self.fail_close:
            raise OSError("close output failed")
        return "closed"


@pytest.fixture
def tracked(monkeypatch):
    import huggingface_hub.utils.tqdm  # noqa: F401

    module = sys.modules["huggingface_hub.utils.tqdm"]
    old_tqdm = module.tqdm
    old_installed = hf_progress._installed
    old_tracked = hf_progress._tracked_tqdm_class
    monkeypatch.setattr(module, "tqdm", _FakeTqdm)
    monkeypatch.setattr(hf_progress, "_installed", False)
    monkeypatch.setattr(hf_progress, "_tracked_tqdm_class", None)
    hf_progress.install()
    cls = hf_progress.tracked_tqdm_class()
    yield cls
    module.tqdm = old_tqdm
    hf_progress._installed = old_installed
    hf_progress._tracked_tqdm_class = old_tracked


def test_tracked_tqdm_emits_start_and_throttled_progress(tracked, monkeypatch):
    events = []
    times = iter([1.0, 1.2, 1.31])
    monkeypatch.setattr(hf_progress, "_emit", events.append)
    monkeypatch.setattr(hf_progress.time, "monotonic", lambda: next(times))

    bar = tracked(desc="weights.bin", total=10)
    bar.update(1)
    bar.update(1)

    assert events[0] == {
        "filename": "weights.bin", "downloaded": 0, "total": 10,
        "pct": 0.0, "phase": "start",
    }
    assert events[1]["phase"] == "progress"
    assert events[1]["downloaded"] == 2
    assert len(events) == 2


def test_tracked_tqdm_close_marks_only_positive_totals_complete(tracked, monkeypatch):
    calls = []
    positive = tracked(total=5)
    zero = tracked(total=0)
    monkeypatch.setattr(positive, "_feed_sink", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(zero, "_feed_sink", lambda *args, **kwargs: calls.append((args, kwargs)))
    positive.close()
    zero.close()
    assert calls == [((5, 5), {"complete": True})]


@pytest.mark.parametrize("operation", ["update", "display", "close"])
def test_tracked_tqdm_swallows_parent_output_errors(tracked, operation):
    setattr(_FakeTqdm, f"fail_{operation}", True)
    try:
        bar = tracked(total=0)
        getattr(bar, operation)()
    finally:
        setattr(_FakeTqdm, f"fail_{operation}", False)


def test_tracked_tqdm_swallows_parent_init_output_error(tracked):
    _FakeTqdm.fail_init = True
    try:
        tracked(desc="download", total=0)
    finally:
        _FakeTqdm.fail_init = False
