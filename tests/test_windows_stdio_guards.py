"""#1155 / #1153 — Windows stdio + console guards.

Two crash classes from the wild, one boundary:

* #1155: kittentts `print()`s the user's text; on Windows the backend's
  stdout defaults to cp1252, so Vietnamese/CJK/etc. raised
  UnicodeEncodeError — surfaced to the user as a bogus
  `400 Bad Request: 'charmap' codec can't encode character…`. The
  process-wide SafeFileWrapper only swallowed OSError (its EPIPE job), so
  the encode error sailed through. Guard both layers: stdio is reconfigured
  to UTF-8 at startup, and the wrapper also swallows UnicodeError (logs are
  best-effort; synthesis is not).

* #1153-class: the Intel Fortran runtime (MKL, under numpy/scipy) installs
  a console CTRL handler that aborts the whole backend with
  `forrtl: error (200): program aborting due to window-CLOSE event` when a
  console CLOSE event reaches the process. FOR_DISABLE_CONSOLE_CTRL_HANDLER=1
  disables that handler; main.py must set it before MKL can load. (The
  desktop shell also sets it — plus CREATE_NO_WINDOW — at spawn; this is
  the guard for `scripts/run.sh` / `python -m uvicorn` launches.)
"""
import io
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

VIETNAMESE = "Generating audio for text: xin chào, bạn khỏe không ả"  # noqa: RUF001


def test_safe_file_wrapper_swallows_unicode_encode_errors():
    from utils.hf_progress import SafeFileWrapper

    cp1252 = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    wrapped = SafeFileWrapper(cp1252)
    # The exact #1155 shape: user text with U+1EA3 hitting a charmap stream.
    wrapped.write(VIETNAMESE)  # must not raise
    wrapped.flush()


def test_main_sets_fortran_console_guard_and_utf8_stdio():
    import main  # noqa: F401  (import-time side effects are the contract)

    # forrtl error (200) guard — read by the Intel Fortran RTL at DLL init,
    # so it must be in the environment before torch/numpy import MKL.
    assert os.environ.get("FOR_DISABLE_CONSOLE_CTRL_HANDLER") == "1"

    # The stream under the EPIPE wrapper must be UTF-8 so no library print
    # of user text can ever hit a charmap codec (kittentts does exactly
    # that on every generate call).
    for stream in (sys.stdout, sys.stderr):
        fp = getattr(stream, "fp", stream)
        enc = (getattr(fp, "encoding", None) or "utf-8").lower().replace("-", "")
        assert enc == "utf8", f"stdio still on {enc}"


@pytest.mark.parametrize(
    ("platform", "dotenv_value", "expected"),
    [("win32", None, "1"), ("win32", "0", "0"), ("linux", None, None), ("darwin", None, None)],
)
def test_main_serial_weight_load_default_is_windows_only(monkeypatch, tmp_path, platform, dotenv_value, expected):
    """#3767/#3772/#4024: threaded weight loading faults on some Windows hosts; .env wins."""
    pytest.importorskip("dotenv")
    monkeypatch.setattr(sys, "platform", platform)
    # setenv records the original state (absent or set) so teardown restores it,
    # even though the code under test sets the variable again below.
    monkeypatch.setenv("HF_DEACTIVATE_ASYNC_LOAD", "unset-marker")
    monkeypatch.delenv("HF_DEACTIVATE_ASYNC_LOAD")
    monkeypatch.setenv("OMNIVOICE_DATA_DIR", str(tmp_path / "data"))
    # Never read the developer's saved settings file.
    monkeypatch.setenv("OMNIVOICE_ENV_FILE", str(tmp_path / "no-saved-settings.env"))
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"HF_DEACTIVATE_ASYNC_LOAD={dotenv_value}\n" if dotenv_value is not None else "", encoding="utf-8"
    )
    backend = Path(__file__).resolve().parents[1] / "backend"
    if str(backend) not in sys.path:
        monkeypatch.syspath_prepend(str(backend))
    from core.env_startup import load_startup_env

    main_src = (backend / "main.py").read_text("utf-8")
    # The default must be applied after .env loads and before transformers imports.
    marker = 'if sys.platform == "win32":\n    os.environ.setdefault("HF_DEACTIVATE_ASYNC_LOAD"'
    start = main_src.index(marker)
    assert main_src.index("_load_startup_env(_discovered_env") < start
    assert start < main_src.index("from services.hf_auth import")
    block = main_src[start : main_src.index("\n\n", start)]
    load_startup_env(str(env_file), str(env_file))
    exec(block, {"os": os, "sys": sys})
    assert os.environ.get("HF_DEACTIVATE_ASYNC_LOAD") == expected
