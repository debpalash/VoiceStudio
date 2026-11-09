"""Language refusals caused by a profile's saved language say so; bearer token unwrapping."""
import pytest

from api.routers import generation as gen

KOKORO_TEXT = (
    "Kokoro model (mlx-community/Kokoro-82M) doesn't support language='Persian'. "
    "Kokoro supports: Chinese, English, French. Pick one of those, leave language "
    "as 'Auto', or switch to a multilingual engine."
)


class _Row(dict):
    pass


def _row(language, **over):
    base = dict(kind="clone", instruct=None, is_locked=0, ref_audio_path=None,
                locked_audio_path=None, ref_text=None, seed=None, vd_states=None,
                language=language)
    base.update(over)
    return _Row(base)


@pytest.mark.parametrize("request_lang,profile_lang,want,flag", [
    (None, "Persian", "Persian", True),
    ("French", "Persian", "French", False),
    (None, None, None, False),
    (None, "", None, False),
    (None, "Auto", None, False),
    ("Auto", "Persian", None, False),
    ("auto", "Persian", None, False),
    (" AUTO ", "Persian", None, False),
])
def test_language_provenance_flag(request_lang, profile_lang, want, flag):
    out = gen._resolve_profile_conditioning(_row(profile_lang), language=request_lang)
    assert out["language"] == want
    assert out["language_from_profile"] is flag


def test_rejection_detection():
    assert gen._is_language_rejection(KOKORO_TEXT)
    assert gen._is_language_rejection("Unsupported language: bn")
    assert gen._is_language_rejection("INVALID LANGUAGE CODE x")
    assert not gen._is_language_rejection("Reference clip is shorter than 3 seconds.")


class _Engine:
    display_name = "MLX Audio"


def test_self_describing_message_is_returned_as_is():
    err = ValueError(KOKORO_TEXT)
    assert gen._language_rejection_or(err, _Engine(), "Persian") is err


def test_unrelated_error_is_returned_as_is():
    err = ValueError("boom")
    assert gen._language_rejection_or(err, _Engine(), "Persian") is err


@pytest.mark.parametrize("msg", [
    "Invalid language code. Supported languages: ar (Arabic), da (Danish)",
    "Unsupported language: bn",
])
def test_generic_rejection_is_wrapped_and_unwrappable(msg):
    err = ValueError(msg)
    new = gen._language_rejection_or(err, _Engine(), "bn")
    assert new is not err
    assert "MLX Audio" in str(new)
    assert gen._root_language_error(new) is err
    assert gen._root_language_error(err) is err


def test_profile_detail_wording():
    text = gen._profile_language_rejection_detail(ValueError(KOKORO_TEXT), "Persian")
    assert "voice profile" in text.lower()
    assert "Persian" in text
    assert "does not override" in text
    assert text.count("switch engine in Model Catalogue") == 1
    assert text.count("Engine's own message:") == 1
    assert "Kokoro supports:" in text


def test_http_error_shapes():
    exc = ValueError("Invalid language code. Supported languages: ar (Arabic)")
    wrapped = gen._language_rejection_or(exc, _Engine(), "Persian")
    plain = gen._language_rejection_http_error(wrapped, "Persian", from_profile=False)
    assert plain.status_code == 400 and plain.detail == str(wrapped)
    prof = gen._language_rejection_http_error(wrapped, "Persian", from_profile=True)
    assert prof.status_code == 400
    assert prof.detail["code"] == "profile_language_rejected"
    assert prof.detail["language"] == "Persian"
    msg = prof.detail["message"]
    assert msg.count("Engine's own message:") == 1
    assert msg.count("Invalid language code") == 1


def test_stream_payload_for_profile():
    p = gen._language_rejection_payload(ValueError(KOKORO_TEXT), "Persian", from_profile=True)
    assert p["code"] == "profile_language_rejected"
    assert p["language"] == "Persian"
    assert p["terminal"] is True
    assert p["retryable"] is False


# --- bearer token unwrapping in the segmented snapshot ---------------------

def _drive_snapshot(monkeypatch, tmp_path, resolved, endpoint=None):
    import huggingface_hub
    from huggingface_hub import file_download
    from api.routers.setup import download
    from services import segmented_download as sd

    rev = "a" * 40
    seen = {}

    class FakeApi:
        def __init__(self, endpoint=None, token=None):
            seen["api"] = token

        def repo_info(self, *a, **k):
            from types import SimpleNamespace as NS
            return NS(sha=rev, siblings=[NS(rfilename="config.yaml")])

    class Meta:
        location = None
        size = 3
        etag = '"' + "b" * 40 + '"'

    def fake_meta(url, token=None, **k):
        seen["meta"] = token
        return Meta()

    async def fake_seg(url, dest, token=None, **k):
        seen["seg"] = token
        with open(dest, "wb") as f:
            f.write(b"abc")

    monkeypatch.setattr("services.token_resolver.resolve", lambda: resolved)
    monkeypatch.setattr(huggingface_hub, "HfApi", FakeApi)
    monkeypatch.setattr(huggingface_hub.constants, "HF_HUB_CACHE", str(tmp_path))
    monkeypatch.setattr(file_download, "get_hf_file_metadata", fake_meta)
    monkeypatch.setattr(sd, "segmented_download", fake_seg)
    download._segmented_snapshot("org/repo", endpoint=endpoint, revision=rev)
    return seen


def test_segmented_snapshot_unwraps_record(monkeypatch, tmp_path):
    from services.token_resolver import ResolvedToken
    rec = ResolvedToken(token="hf_gatedsecret", source="app", username="tester")
    seen = _drive_snapshot(monkeypatch, tmp_path, rec)
    assert seen == {"api": "hf_gatedsecret", "meta": "hf_gatedsecret", "seg": "hf_gatedsecret"}


def test_segmented_snapshot_without_token(monkeypatch, tmp_path):
    seen = _drive_snapshot(monkeypatch, tmp_path, None)
    assert seen == {"api": None, "meta": None, "seg": None}


def test_auth_header_needs_string():
    from services.segmented_download import _auth_headers
    from services.token_resolver import ResolvedToken
    url = "https://huggingface.co/x"
    assert _auth_headers(url, "hf_x") == {"Authorization": "Bearer hf_x"}
    rec = ResolvedToken(token="hf_x", source="app", username="u")
    assert _auth_headers(url, rec) != {"Authorization": "Bearer hf_x"}
