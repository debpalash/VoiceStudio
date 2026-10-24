"""#1156 — a missing/broken `mcp` SDK must degrade the backend, not kill it.

The backend crashed on startup with exit code 1 when `import mcp` failed:
`_ensure_mcp()` called `sys.exit(1)`, and `SystemExit` is a `BaseException`,
so main.py's best-effort `except Exception` around the /mcp mount never
caught it. These tests pin the whole class:

  * the library path raises `ImportError` (catchable), never `SystemExit`;
  * the mount helper contains even a stray `SystemExit` from the MCP
    integration layer (any dependency written as a CLI can call sys.exit —
    same class as #1133/#1143's exit containment at the engine boundary);
  * the "not installed" message no longer misdiagnoses a broken transitive
    import (e.g. pywin32 on Windows) as an absent package.

No `main` import here — the seams live in mcp_server so they run locally
(main-importing tests segfault on local torch/Triton, see test_mcp_mount).
"""
import os
import sys
from unittest import mock

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def _block_mcp_import():
    """Make `from mcp.server.fastmcp import FastMCP` raise ImportError,
    even when the real package is installed (None-entries in sys.modules
    halt the import machinery)."""
    blocked = {k: None for k in list(sys.modules) if k == "mcp" or k.startswith("mcp.")}
    blocked["mcp"] = None
    return mock.patch.dict(sys.modules, blocked)


def test_ensure_mcp_raises_importerror_not_systemexit():
    import mcp_server

    with _block_mcp_import():
        with pytest.raises(ImportError) as ei:
            mcp_server._ensure_mcp()
    # The message must name the real failure and the real remedy (repair /
    # uv sync), not just claim the package is missing.
    assert "sync" in str(ei.value).lower() or "repair" in str(ei.value).lower()


def test_mount_mcp_survives_missing_sdk():
    from fastapi import FastAPI

    import mcp_server

    app = FastAPI()
    with _block_mcp_import():
        assert mcp_server.mount_mcp(app) is False
    assert not [r for r in app.routes if getattr(r, "path", "") == "/mcp"]


def test_mount_mcp_contains_systemexit_from_integration_layer(monkeypatch):
    from fastapi import FastAPI

    import mcp_server

    def _exits():
        raise SystemExit(1)

    monkeypatch.setattr(mcp_server, "create_mcp_server", _exits)
    app = FastAPI()
    # Must return False — never propagate SystemExit into backend startup.
    assert mcp_server.mount_mcp(app) is False


def test_cli_entry_still_exits_nonzero_when_sdk_missing(monkeypatch):
    """The standalone `python -m backend.mcp_server` run SHOULD exit(1) on a
    missing SDK — only the embedded/library path must not."""
    import mcp_server

    monkeypatch.setattr(sys, "argv", ["mcp_server"])
    with _block_mcp_import():
        with pytest.raises(SystemExit) as ei:
            mcp_server.main()
    assert ei.value.code == 1


def test_cli_sse_listens_on_the_requested_port(monkeypatch):
    """`python -m backend.mcp_server --sse --port N` must start the SSE
    server on N. FastMCP.run() takes no `port`, so passing one crashed the
    CLI with a TypeError before anything listened."""
    pytest.importorskip("mcp.server.fastmcp")
    import mcp_server

    server = mcp_server.create_mcp_server()
    served = {}

    async def _fake_sse(mount_path=None):
        served["port"] = server.settings.port

    monkeypatch.setattr(server, "run_sse_async", _fake_sse)
    monkeypatch.setattr(mcp_server, "create_mcp_server", lambda: server)
    monkeypatch.setattr(sys, "argv", ["mcp_server", "--sse", "--port", "8799"])
    mcp_server.main()
    assert served == {"port": 8799}
