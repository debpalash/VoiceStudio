"""Validated product identity and default ports from the repository manifest."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "app.json"


def _load() -> dict[str, Any]:
    """Read the repository-owned application manifest."""
    with _CONFIG_PATH.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise TypeError("config/app.json must contain an object")
    return value


def _required_string(value: object, key: str) -> str:
    """Return a required non-empty manifest string."""
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"config/app.json: {key} must be a non-empty string")
    return value.strip()


def _required_port(value: object, key: str) -> int:
    """Return a validated TCP port from the manifest."""
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise TypeError(
            f"config/app.json: ports.{key} must be an integer from 1 to 65535"
        )
    return value


def _validate_port_layout(backend: int, web: int, electron_dev: int) -> None:
    """Keep public defaults and Electron's derived proxy port collision-free."""
    configured = {backend, web, electron_dev}
    if len(configured) != 3:
        raise TypeError("config/app.json: default ports must be distinct")
    electron_proxy = electron_dev + 1
    if electron_proxy > 65535 or electron_proxy in configured:
        raise TypeError(
            "config/app.json: ports.electronDev + 1 is reserved for the private "
            "Electron proxy"
        )


_RAW = _load()
_PORTS = _RAW.get("ports")
if not isinstance(_PORTS, dict):
    raise TypeError("config/app.json: ports must be an object")

PRODUCT_NAME = _required_string(_RAW.get("productName"), "productName")
APP_ID = _required_string(_RAW.get("appId"), "appId")
DEFAULT_BACKEND_PORT = _required_port(_PORTS.get("backend"), "backend")
DEFAULT_WEB_PORT = _required_port(_PORTS.get("web"), "web")
DEFAULT_ELECTRON_DEV_PORT = _required_port(_PORTS.get("electronDev"), "electronDev")
_validate_port_layout(DEFAULT_BACKEND_PORT, DEFAULT_WEB_PORT, DEFAULT_ELECTRON_DEV_PORT)
