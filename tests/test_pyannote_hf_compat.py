"""#167 — pyannote-audio 3.x passes the removed `use_auth_token` kwarg to
huggingface_hub.hf_hub_download (HF Hub 1.x only accepts `token`), breaking
diarization. Verify the compat shim translates the kwarg and that pyannote
actually binds the wrapped function."""
from services.model_manager import _ensure_pyannote_hf_token_compat


def test_shim_translates_use_auth_token_to_token(monkeypatch):
    import huggingface_hub

    seen = {}

    def fake(*args, token=None, **kwargs):
        # Mimic HF Hub 1.x: `use_auth_token` is no longer accepted.
        if "use_auth_token" in kwargs:
            raise TypeError(
                "hf_hub_download() got an unexpected keyword argument 'use_auth_token'"
            )
        seen["token"] = token
        return "downloaded"

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake, raising=False)
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake, raising=False)

    _ensure_pyannote_hf_token_compat()

    # The wrapped fn must translate the dead kwarg instead of raising.
    result = huggingface_hub.hf_hub_download(repo_id="r", filename="f", use_auth_token="secret")
    assert result == "downloaded"
    assert seen["token"] == "secret"


def test_shim_is_idempotent(monkeypatch):
    import huggingface_hub

    def fake(*args, token=None, **kwargs):
        return token

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake, raising=False)
    _ensure_pyannote_hf_token_compat()
    once = huggingface_hub.hf_hub_download
    _ensure_pyannote_hf_token_compat()
    twice = huggingface_hub.hf_hub_download
    assert once is twice  # not re-wrapped
    assert getattr(twice, "_ov_uat_shim", False) is True


def test_pyannote_binds_the_shim():
    """The real proof: after the shim, pyannote's own `hf_hub_download`
    reference translates `use_auth_token` rather than raising."""
    from services.pyannote_audio_compat import ensure_pyannote_audio_compat
    ensure_pyannote_audio_compat()
    _ensure_pyannote_hf_token_compat()
    import pyannote.audio.core.pipeline as pipeline
    assert getattr(pipeline.hf_hub_download, "_ov_uat_shim", False), (
        "pyannote.audio.core.pipeline.hf_hub_download is not the use_auth_token shim"
    )
