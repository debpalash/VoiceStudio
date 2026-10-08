"""Which task parameters name files, shared by both ends of the worker wire.

The control plane stages every file-valued parameter as an artifact and sends
its id (``task_store.ensure_staged`` / ``codec.remote_params``); the worker
fetches the declared inputs and swaps each id for its local copy
(``executor._materialize_inputs``). Both halves must agree on the key list, so
it lives here, importable by the worker without pulling in control-plane code.

On the worker side the list is also a boundary: a value under one of these
keys that is not a declared input is a string the panel chose, and opening it
would read whatever file on the worker's disk it happens to name.
"""
from __future__ import annotations

from typing import Any, Iterator

INPUT_PARAM_KEYS: tuple[str, ...] = (
    "ref_audio",
    "reference_audio",
    "prompt_audio",
    "prompt_wav",
    "source_audio",
    "audio_path",
    "source_video",
    "video_path",
)

_INPUT_KEY_SET = frozenset(INPUT_PARAM_KEYS)


def input_values(params: Any) -> Iterator[Any]:
    """Every value stored under a file-valued key, at any depth.

    Nested rows matter: a dub carries ``segments[i]`` dicts, an audiobook
    ``voices[i]``, and a key the executor ignores today may be read tomorrow.
    A list under such a key yields its items, since that is how per-row
    references travel.
    """
    if isinstance(params, dict):
        for key, value in params.items():
            if key in _INPUT_KEY_SET:
                if isinstance(value, list):
                    yield from value
                else:
                    yield value
            else:
                yield from input_values(value)
    elif isinstance(params, list):
        for item in params:
            yield from input_values(item)


def undeclared_inputs(params: Any, declared: set[str]) -> list[Any]:
    """File-valued parameters that are neither empty nor a declared artifact id."""
    return [
        value
        for value in input_values(params)
        if value not in (None, "") and not (isinstance(value, str) and value in declared)
    ]


def rewrite_inputs(params: Any, local: dict[str, str]) -> Any:
    """Replace declared artifact ids under file-valued keys with local paths.

    Scoped to those keys on purpose: free text (a line to speak, an
    instruction) that happens to equal an artifact id must stay text rather
    than become a path on this machine.
    """
    if isinstance(params, dict):
        rewritten = {}
        for key, value in params.items():
            if key in _INPUT_KEY_SET:
                if isinstance(value, list):
                    rewritten[key] = [_local(item, local) for item in value]
                else:
                    rewritten[key] = _local(value, local)
            else:
                rewritten[key] = rewrite_inputs(value, local)
        return rewritten
    if isinstance(params, list):
        return [rewrite_inputs(item, local) for item in params]
    return params


def _local(value: Any, local: dict[str, str]) -> Any:
    return local.get(value, value) if isinstance(value, str) else value


__all__ = ["INPUT_PARAM_KEYS", "input_values", "rewrite_inputs", "undeclared_inputs"]
