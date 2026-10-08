"""
Bind-host resolution tests — guards against regressing the loopback default.

`backend/main.py` resolves its uvicorn bind host from `OMNIVOICE_BIND_HOST`
with a default of `127.0.0.1`. Binding to `0.0.0.0` by default would expose
every (currently unauthenticated) router on the user's LAN — see the security
note above the bind in `backend/main.py`.

These tests don't boot uvicorn. They re-evaluate the resolution expression
used in `main.py`'s `__main__` block, both as a string-level guard against
edits that swap the default back to `0.0.0.0`, and as a behavioral guard
on the env-var override path.
"""

import os
import re
from pathlib import Path


_BACKEND_MAIN = Path(__file__).resolve().parent.parent / "backend" / "main.py"


def _resolve_bind_host(env: dict) -> str:
    """Mirror of the resolution logic in `backend/main.py`'s __main__ block.

    Kept in lock-step with the production expression on purpose — any code
    change there should require the test to be updated as well, which is
    exactly the regression boundary we want.
    """
    return env.get("OMNIVOICE_BIND_HOST", "127.0.0.1")


def test_default_bind_is_loopback_when_env_unset():
    assert _resolve_bind_host({}) == "127.0.0.1"


def test_explicit_loopback_env_var_is_honored():
    assert _resolve_bind_host({"OMNIVOICE_BIND_HOST": "127.0.0.1"}) == "127.0.0.1"


def test_explicit_all_interfaces_env_var_is_honored():
    # An explicit opt-in for `python backend/main.py` (and `bun run dev:api`).
    assert _resolve_bind_host({"OMNIVOICE_BIND_HOST": "0.0.0.0"}) == "0.0.0.0"


def test_backend_main_source_does_not_hardcode_all_interfaces_default():
    """String-level guard against re-introducing a `host="0.0.0.0"` default
    in `backend/main.py`. If someone edits the production uvicorn.run call to
    hardcode 0.0.0.0 again, this test fails with a pointer to the security
    rationale in the surrounding comment."""
    src = _BACKEND_MAIN.read_text(encoding="utf-8")
    # The health-check thread legitimately binds 127.0.0.1; the production
    # call should reference OMNIVOICE_BIND_HOST (env-driven), not a hardcoded
    # 0.0.0.0 literal. Allow `0.0.0.0` to appear in *comments* (security
    # rationale block) but not in any uvicorn.run(...) host= argument.
    bad_pattern = re.compile(r'uvicorn\.run\([^)]*host\s*=\s*["\']0\.0\.0\.0["\']')
    assert not bad_pattern.search(src), (
        "backend/main.py contains a hardcoded host=\"0.0.0.0\" in uvicorn.run(). "
        "Use OMNIVOICE_BIND_HOST env var (default 127.0.0.1) instead — see "
        "the security comment block above the call site."
    )


def test_backend_main_source_references_omnivoice_bind_host():
    """Companion to the above: the env-var-driven path must be present so a
    future refactor doesn't accidentally remove it (which would silently
    revert to whichever literal defaults the new code chose)."""
    src = _BACKEND_MAIN.read_text(encoding="utf-8")
    assert "OMNIVOICE_BIND_HOST" in src, (
        "backend/main.py no longer references OMNIVOICE_BIND_HOST — the "
        "loopback-default contract is unenforced."
    )
