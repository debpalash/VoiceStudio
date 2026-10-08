"""#2163: a gated model install must actually send the HF bearer token.

``token_resolver.resolve()`` returns a ``ResolvedToken`` *record*. Every
huggingface_hub entry point — and our own ``segmented_download`` — takes
``token: str | None``. The segmented accelerator handed the record straight
through, and neither consumer complains:

* ``build_hf_headers`` ignores a non-``str`` token and falls back to
  huggingface_hub's own ambient discovery, so a token held only in
  VoiceStudio's Settings produces **no** ``Authorization`` header at all and
  every gated file 401s;
* ``segmented_download`` interpolates it into ``f"Bearer {token}"``, sending a
  malformed header that also inlines the raw secret into the request.

Either way the accelerator 401s on the first file of a gated repo, is disabled
for the rest of the install, and logs a 401 that reads like the user's token or
license grant is at fault when it is neither.
"""
import importlib
import os
from types import SimpleNamespace

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest



@pytest.fixture
def download():
    return importlib.import_module("api.routers.setup.download")


GATED_REPO = "pyannote/speaker-diarization-3.1"
REVISION = "c" * 40


def _drive_segmented(download, monkeypatch, tmp_path, resolved):
    """Run ``_segmented_snapshot`` with every network seam mocked.

    Returns the token value each of the three consumers actually received.
    """
    import huggingface_hub
    from huggingface_hub import file_download as hf_file_download
    from services import segmented_download as sd_mod
    from services import token_resolver

    monkeypatch.setattr(token_resolver, "resolve", lambda *a, **k: resolved)
    monkeypatch.setattr(huggingface_hub.constants, "HF_HUB_CACHE", str(tmp_path))

    seen: dict = {}

    class _FakeApi:
        def __init__(self, *, endpoint=None, token=None):
            seen["hf_api"] = token

        def repo_info(self, repo_id, repo_type=None, revision=None):
            # Gated repos serve metadata unauthenticated and gate the file
            # bytes — which is why the 401 in #2163 lands on the first
            # resolve() call rather than here.
            return SimpleNamespace(
                sha=revision,
                siblings=[SimpleNamespace(rfilename="config.yaml")],
            )

    monkeypatch.setattr(huggingface_hub, "HfApi", _FakeApi)

    def _fake_metadata(url, token=None, **_kw):
        seen["file_metadata"] = token
        return SimpleNamespace(etag='"' + 'd' * 40 + '"', location=url, size=10)

    monkeypatch.setattr(hf_file_download, "get_hf_file_metadata", _fake_metadata)

    async def _fake_segmented(url, blob_path, *, token=None, **_kw):
        seen["segmented"] = token
        with open(blob_path, "wb") as fh:
            fh.write(b"config: ok")
        return blob_path

    monkeypatch.setattr(sd_mod, "segmented_download", _fake_segmented)

    download._segmented_snapshot(GATED_REPO, endpoint=None, revision=REVISION)
    return seen


def test_segmented_install_sends_the_bearer_string_to_every_consumer(
    download, monkeypatch, tmp_path
):
    from services.token_resolver import ResolvedToken
    seen = _drive_segmented(
        download,
        monkeypatch,
        tmp_path,
        ResolvedToken(token="hf_gatedsecret", source="app", username="tester"),
    )

    assert seen == {
        "hf_api": "hf_gatedsecret",
        "file_metadata": "hf_gatedsecret",
        "segmented": "hf_gatedsecret",
    }
    # The record itself must never cross the seam — that is the whole bug.
    for consumer, value in seen.items():
        assert isinstance(value, str), f"{consumer} received {type(value).__name__}"


def test_segmented_install_sends_no_token_when_none_resolves(
    download, monkeypatch, tmp_path
):
    # No token anywhere: every consumer must get a real None so huggingface_hub
    # treats the repo as anonymous, never the string "None".
    seen = _drive_segmented(download, monkeypatch, tmp_path, None)
    assert seen == {"hf_api": None, "file_metadata": None, "segmented": None}


def test_a_token_record_would_build_a_broken_authorization_header():
    """Why the unwrap matters, pinned at our own auth seam.

    ``_auth_headers`` is the function that turns the token into the header the
    segmented downloader sends. Given the bearer string it produces a valid
    header; given the record it produces a malformed one that also inlines the
    raw secret. This is the failure #2163 reported as a 401.
    """
    from services.token_resolver import ResolvedToken
    from services.segmented_download import _auth_headers

    url = "https://huggingface.co/pyannote/speaker-diarization-3.1/resolve/main/config.yaml"
    record = ResolvedToken(token="hf_gatedsecret", source="app", username="tester")

    assert _auth_headers(url, record.token) == {"Authorization": "Bearer hf_gatedsecret"}

    broken = _auth_headers(url, record)["Authorization"]
    assert broken != "Bearer hf_gatedsecret"
    assert "ResolvedToken" in broken
