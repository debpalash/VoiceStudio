#!/usr/bin/env python3
"""Generate private app-intake build metadata; never print supplied values."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def valid_host(value):
    if not isinstance(value, str) or len(value.encode("utf-8")) > 1988 or re.search(r"[\x00-\x20]", value):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
        literal = parsed.netloc.rsplit(":", 1)[0] if port is not None else parsed.netloc
        return bool(parsed.hostname) and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment and parsed.path in ("", "/") and (
            parsed.scheme == "https" or parsed.scheme == "http" and literal.lower() in {"localhost", "127.0.0.1", "[::1]"}
        )
    except (ValueError, UnicodeError):
        return False


def generated_config(env=None):
    env = os.environ if env is None else env
    config = {"diagnostics": {"sessionPings": env.get("SUPERMETRY_DIAGNOSTICS_SESSION_PINGS") == "1"}}
    for name, key, pattern in (
        ("SUPERMETRY_APP_ID", "app_id", r"[a-z0-9]{6,32}"),
        ("SUPERMETRY_INGEST_KEY", "ingest_key", r"smk_[a-z2-7]{39}"),
    ):
        value = env.get(name, "").strip()
        if re.fullmatch(pattern, value):
            config[key] = value
    host = env.get("SUPERMETRY_APP_HOST", "").strip().rstrip("/")
    if valid_host(host):
        config["host"] = host
    return config


def write_config(path, env=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # O_TRUNC replaces metadata on every build, including credential-free builds.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(generated_config(env), stream, separators=(",", ":"))
        stream.write("\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "config/supermetry-app.generated.json")
    write_config(parser.parse_args().output)
