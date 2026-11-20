"""Model loads in ``tts_backend`` must pass the curated revision, not follow main.

``services/hf_revisions`` resolves every repository the product offers to a
reviewed commit SHA and its docstring requires download paths to call
``revision_for`` "instead of following ``main``". Two adapters in this module
loaded a curated repository without a revision, so a branch-name move upstream
would silently change what runs on a user's machine -- and one of them did it
with ``trust_remote_code=True``.

These are source-level assertions on purpose: loading the real models needs
multi-GB downloads and, for VoxCPM2, a CUDA/CPU torch stack. The behaviour that
matters here is *whether the pin is passed*, which is visible in the source.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from services.hf_revisions import CURATED_REVISIONS, revision_for
from services.tts_backend import _curated_revision_kwargs

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_TTS_BACKEND = _ROOT / "backend" / "services" / "tts_backend.py"

#: The two adapters that download by repository id.
CURATED_DEFAULTS = (
    ("OMNIVOICE_VOXCPM_MODEL", "openbmb/VoxCPM2"),
    ("OMNIVOICE_MOSS_TTS_MODEL", "OpenMOSS-Team/MOSS-TTS-Nano-100M"),
)


@pytest.mark.parametrize(("env_key", "repo_id"), CURATED_DEFAULTS)
def test_each_adapter_default_is_a_curated_repo(env_key, repo_id):
    assert repo_id in CURATED_REVISIONS, (
        f"{env_key} defaults to {repo_id!r}, which has no reviewed revision in "
        "config/model_licenses.json. Either curate it or stop presenting it as a "
        "shipped engine."
    )


@pytest.mark.parametrize(("_env_key", "repo_id"), CURATED_DEFAULTS)
def test_the_helper_returns_the_reviewed_revision(_env_key, repo_id):
    assert _curated_revision_kwargs(repo_id) == {"revision": revision_for(repo_id)}


def test_the_helper_leaves_a_non_curated_override_unpinned():
    # A user's own checkpoint id is an opt-in; absence of a pin is not an error.
    assert _curated_revision_kwargs("some-user/custom-checkpoint") == {}


def test_every_from_pretrained_in_this_module_passes_the_pin():
    source = _TTS_BACKEND.read_text(encoding="utf-8")
    calls = list(re.finditer(r"\.from_pretrained\(", source))
    assert calls, "no from_pretrained call found; the scan needs updating"
    offenders = []
    for match in calls:
        depth, index = 0, match.end() - 1
        while index < len(source):
            if source[index] == "(":
                depth += 1
            elif source[index] == ")":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        arguments = source[match.end() : index]
        if "_curated_revision_kwargs" not in arguments and "revision=" not in arguments:
            line = source[: match.start()].count("\n") + 1
            offenders.append(f"tts_backend.py:{line} -> {arguments.strip().splitlines()[0][:60]}")
    assert not offenders, (
        "These model loads pass no revision, so whichever commit the repository's "
        "default branch points at is what runs. Add "
        "`**_curated_revision_kwargs(checkpoint)`:\n" + "\n".join(offenders)
    )
