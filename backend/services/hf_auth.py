"""Which hosts may receive the Hugging Face token.

The token authorizes the user's Hugging Face account, so it is only ever sent
over HTTPS to Hugging Face itself. Mirrors (configured or picked by automatic
endpoint selection) and CDN redirect targets get no ``Authorization`` header.
"""
from __future__ import annotations

import threading
from typing import Optional, Union
from urllib.parse import urlsplit

HF_AUTH_HOSTS = ("huggingface.co", "hf.co")
# Account calls (whoami, access checks) always go here, never to HF_ENDPOINT.
CANONICAL_ENDPOINT = "https://huggingface.co"


def host_gets_auth(url: Optional[str]) -> bool:
    """True when ``url`` is an HTTPS Hugging Face URL."""
    try:
        parsed = urlsplit(url or "")
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False
    return parsed.scheme == "https" and (
        host in HF_AUTH_HOSTS or host.endswith(".huggingface.co")
    )


def hub_endpoint(endpoint: Optional[str]) -> str:
    """The endpoint huggingface_hub will contact for ``endpoint=``.

    ``None`` means the library default, which was fixed from ``HF_ENDPOINT``
    when huggingface_hub was imported and can differ from the current env.
    """
    if endpoint:
        return endpoint
    from huggingface_hub import constants

    return constants.ENDPOINT


def token_for_endpoint(
    endpoint: Optional[str], token: Optional[str]
) -> Union[str, None, bool]:
    """The ``token=`` value to hand huggingface_hub for ``endpoint``.

    Hugging Face hosts get ``token`` (``None`` keeps the library's own lookup).
    Any other host gets ``False``, which also stops huggingface_hub from
    sending a token it finds in ``HF_TOKEN`` or its token file.
    """
    if host_gets_auth(hub_endpoint(endpoint)):
        return token
    return False


def canonical_whoami(token: str) -> dict:
    """Validate ``token`` against Hugging Face itself.

    ``huggingface_hub.whoami`` and ``login`` contact the library default
    endpoint, which ``HF_ENDPOINT`` turns into a mirror; an explicit token is
    sent even with implicit tokens disabled. This call pins the endpoint.
    """
    from huggingface_hub import HfApi

    return HfApi(endpoint=CANONICAL_ENDPOINT).whoami(token=token)


def env_allows_token(environ) -> bool:
    """True unless ``environ['HF_ENDPOINT']`` names a non-Hugging Face host."""
    endpoint = (environ.get("HF_ENDPOINT") or "").strip()
    return not endpoint or host_gets_auth(endpoint)


_IMPLICIT_ENV = "HF_HUB_DISABLE_IMPLICIT_TOKEN"
# huggingface_hub reads these as true (case-insensitively, untrimmed); anything
# else, including "0" or " 1", leaves the implicit token on.
_TRUE_VALUES = frozenset({"1", "on", "yes", "true"})
# Mappings this module changed the variable in, with the value it replaced
# (None when unset), so a later call only ever restores what it changed: a
# value the user supplied, in the same or another mapping, is never touched.
# Bounded; ad-hoc mappings are short-lived.
_implicit_disabled_in: "dict[int, tuple[dict, Optional[str]]]" = {}
_IMPLICIT_TRACKED_MAX = 16
_implicit_lock = threading.Lock()


def implicit_token_disabled(value: object) -> bool:
    """Whether a ``HF_HUB_DISABLE_IMPLICIT_TOKEN`` value turns the implicit token off."""
    # No trimming: huggingface_hub compares ``value.upper()`` exactly, so " on " is false there.
    return str(value or "").lower() in _TRUE_VALUES


def apply_process_token_policy(environ: Optional[dict] = None) -> None:
    """Stop implicit token use when ``HF_ENDPOINT`` names a mirror.

    huggingface_hub, and every library built on it, sends the token from
    ``HF_TOKEN`` or its token file to whatever ``HF_ENDPOINT`` says. Setting
    ``HF_HUB_DISABLE_IMPLICIT_TOKEN`` stops that for engine processes that
    inherit the environment; the variable follows the current setting. The
    in-process endpoint is fixed when huggingface_hub is imported, so the
    in-process switch is only ever turned on, never back off.
    """
    import os
    import sys

    env = os.environ if environ is None else environ
    with _implicit_lock:
        if not env_allows_token(env):
            if not implicit_token_disabled(env.get(_IMPLICIT_ENV)):
                # An unset or false-valued setting ("0") would let a cached token reach the mirror.
                _implicit_disabled_in[id(env)] = (env, env.get(_IMPLICIT_ENV))
                env[_IMPLICIT_ENV] = "1"
                while len(_implicit_disabled_in) > _IMPLICIT_TRACKED_MAX:
                    _implicit_disabled_in.pop(next(iter(_implicit_disabled_in)))
        else:
            changed = _implicit_disabled_in.pop(id(env), None)
            if changed is not None and changed[0] is env:
                previous = changed[1]
                if previous is None:
                    env.pop(_IMPLICIT_ENV, None)
                else:
                    env[_IMPLICIT_ENV] = previous
    # Not imported yet: it will read both variables from the environment.
    constants = sys.modules.get("huggingface_hub.constants") if environ is None else None
    if constants is not None and not host_gets_auth(constants.ENDPOINT):
        constants.HF_HUB_DISABLE_IMPLICIT_TOKEN = True


__all__ = [
    "CANONICAL_ENDPOINT",
    "implicit_token_disabled",
    "HF_AUTH_HOSTS",
    "apply_process_token_policy",
    "canonical_whoami",
    "env_allows_token",
    "host_gets_auth",
    "hub_endpoint",
    "token_for_endpoint",
]
