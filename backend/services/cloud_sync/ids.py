"""Sync IDs: 16 Base62 characters from a CSPRNG (never uuid[:8])."""
from __future__ import annotations

import secrets
import string

_ALPHABET = string.ascii_letters + string.digits


def new_sync_id() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(16))
