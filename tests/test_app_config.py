"""The OSS identity and active default ports have one editable manifest."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_python_runtime_reads_the_central_app_config() -> None:
    from core import app_config

    config = json.loads((ROOT / "config/app.json").read_text(encoding="utf-8"))
    assert app_config.PRODUCT_NAME == config["productName"]
    assert app_config.APP_ID == config["appId"]
    assert (
        app_config.DEFAULT_BACKEND_PORT,
        app_config.DEFAULT_WEB_PORT,
        app_config.DEFAULT_ELECTRON_DEV_PORT,
    ) == (
        config["ports"]["backend"],
        config["ports"]["web"],
        config["ports"]["electronDev"],
    )


def test_python_rejects_invalid_electron_proxy_ports() -> None:
    from core.app_config import _validate_port_layout

    for ports in ((3900, 3901, 65535), (3900, 3903, 3902)):
        try:
            _validate_port_layout(*ports)
        except TypeError:
            continue
        raise AssertionError(f"accepted invalid port layout: {ports}")


def test_active_launchers_do_not_mirror_default_port_literals() -> None:
    active_files = (
        "electron/electron.vite.config.ts",
        "electron/vite.web.config.ts",
        "electron/src/main/backend.ts",
        "electron/src/main/dev-backend-proxy.ts",
        "electron/src/shared/utils/apiBase.ts",
        "scripts/dev-backend.mjs",
        "scripts/clear-dev-ports.mjs",
    )
    for relative in active_files:
        source = (ROOT / relative).read_text(encoding="utf-8")
        source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
        source = re.sub(r"//.*", "", source)
        for port in (3900, 3901, 3902, 3903):
            assert str(port) not in source, f"{relative} mirrors port {port}"
