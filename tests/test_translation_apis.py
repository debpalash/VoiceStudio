import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest


@pytest.mark.parametrize("provider,key,target,expected_host", [
    ("deepl", "fixture:fx", "zh-CN", "api-free.deepl.com"),
    ("deepl", "fixture-paid", "de", "api.deepl.com"),
    ("microsoft", "fixture", "zh-CN", "api.cognitive.microsofttranslator.com"),
    ("google-cloud", "fixture", "es", "translation.googleapis.com"),
])
def test_paid_http_contract(monkeypatch, provider, key, target, expected_host):
    from services.translation_apis import Translator
    for name in ["DEEPL_API_KEY", "DEEPL_BASE_URL", "MICROSOFT_API_KEY", "MICROSOFT_BASE_URL", "GOOGLE_TRANSLATE_API_KEY"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MICROSOFT_REGION", "eastus")
    seen = []
    def handler(request):
        seen.append(request)
        if provider == "deepl": body = {"translations": [{"text": "translated"}]}
        elif provider == "microsoft": body = [{"translations": [{"text": "translated"}]}]
        else: body = {"data": {"translations": [{"translatedText": "translated &amp; decoded"}]}}
        return httpx.Response(200, json=body)
    client_type = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: client_type(transport=httpx.MockTransport(handler), **kw))
    assert Translator(provider, "auto", target, api_key=key).translate("hello").startswith("translated")
    request = seen[0]
    assert request.url.host == expected_host
    assert key not in str(request.url)
    assert request.extensions["timeout"]["connect"] == 5
    body = json.loads(request.content)
    if provider == "deepl":
        assert request.headers["Authorization"] == "DeepL-Auth-Key " + key
        assert "source_lang" not in body
        if target == "zh-CN": assert body["target_lang"] == "ZH-HANS"
    elif provider == "microsoft":
        assert request.headers["Ocp-Apim-Subscription-Region"] == "eastus"
        assert request.url.params["to"] == "zh-Hans"
        assert "from" not in request.url.params
    else:
        assert request.headers["X-Goog-Api-Key"] == key
        assert body["format"] == "text"


def test_amazon_uses_sdk_and_closes_client(monkeypatch):
    from services.translation_apis import Translator
    import boto3
    calls = []; closed = []
    client = SimpleNamespace(translate_text=lambda **kw: calls.append(kw) or {"TranslatedText": "hola"}, close=lambda: closed.append(True))
    monkeypatch.setattr(boto3.session, "Session", lambda: SimpleNamespace(client=lambda *args, **kw: client))
    assert Translator("amazon", "auto", "zh-CN").translate("hello") == "hola"
    assert calls == [{"Text": "hello", "SourceLanguageCode": "auto", "TargetLanguageCode": "zh"}]
    assert closed


def test_translation_key_save_is_encrypted_and_legacy_pref_removed(monkeypatch):
    from api.routers import system
    from services import settings_store
    saved = []; removed = []
    monkeypatch.setattr(settings_store, "set_secret", lambda *args: saved.append(args))
    monkeypatch.setattr(system, "prefs_delete", lambda key: removed.append(key))
    monkeypatch.setattr(system, "prefs_set", lambda *args: pytest.fail("Secret written to plaintext prefs"))
    monkeypatch.setenv("GOOGLE_TRANSLATE_API_KEY", "")
    monkeypatch.delenv("GOOGLE_TRANSLATE_API_KEY", raising=False)
    asyncio.run(system.set_env_var({"key": "GOOGLE_TRANSLATE_API_KEY", "value": "fixture-secret"}))
    assert saved == [("translation_env.GOOGLE_TRANSLATE_API_KEY", "fixture-secret")]
    assert removed == ["env.GOOGLE_TRANSLATE_API_KEY"]


def test_restore_migrates_legacy_keys_and_preserves_external_env(monkeypatch):
    import os
    from core import prefs
    from services import settings_store
    stored = {}; removed = []
    monkeypatch.setattr(settings_store, "get_secret", lambda key: stored.get(key))
    monkeypatch.setattr(settings_store, "set_secret", lambda key, value: stored.__setitem__(key, value))
    monkeypatch.setattr(prefs, "delete", lambda key: removed.append(key))
    monkeypatch.setenv("DEEPL_API_KEY", "external")
    monkeypatch.setenv("MICROSOFT_API_KEY", "")
    monkeypatch.delenv("MICROSOFT_API_KEY", raising=False)
    prefs.restore_env({"env.DEEPL_API_KEY": "legacy", "env.MICROSOFT_API_KEY": "microsoft"})
    assert stored["translation_env.DEEPL_API_KEY"] == "legacy"
    assert os.environ["DEEPL_API_KEY"] == "external"
    assert os.environ["MICROSOFT_API_KEY"] == "microsoft"
    assert "env.DEEPL_API_KEY" in removed


def test_amazon_readiness_allows_default_credential_chain(monkeypatch):
    from services.translation_engines import _configured
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    assert _configured({"id": "amazon"}) == (True, None)


@pytest.mark.parametrize("region,credentials,expected", [
    (None, True, "region"), ("us-east-1", False, "credentials"),
])
def test_amazon_explicit_preflight_rejects_incomplete_setup(monkeypatch, region, credentials, expected):
    import boto3
    from services.translation_apis import validate_amazon_configuration
    frozen = SimpleNamespace(access_key="fixture", secret_key="fixture")
    credential = SimpleNamespace(get_frozen_credentials=lambda: frozen) if credentials else None
    session = SimpleNamespace(region_name=region, get_credentials=lambda: credential)
    monkeypatch.setattr(boto3.session, "Session", lambda **kw: session)
    with pytest.raises(ValueError, match=expected): validate_amazon_configuration()


def test_amazon_selection_validates_before_saving(monkeypatch):
    from api.routers import engines
    from services import translation_apis, translation_engines
    monkeypatch.setattr(translation_engines, "is_installed", lambda _: True)
    monkeypatch.setattr(translation_engines, "is_ready", lambda _: True)
    monkeypatch.setattr(engines.prefs, "set_", lambda *a: pytest.fail("Must not persist an invalid selection"))
    monkeypatch.setattr(translation_apis, "validate_amazon_configuration", lambda: (_ for _ in ()).throw(translation_apis.AmazonConfigurationError("AWS credentials missing")))
    with pytest.raises(Exception) as exc: engines.select_translation_engine(engines.TranslationSelection(engine_id="amazon"))
    assert exc.value.status_code == 409


@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
def test_amazon_preflight_sanitizes_sdk_errors(monkeypatch, error_type):
    import boto3
    from services.translation_apis import AmazonConfigurationError, validate_amazon_configuration

    def broken_session(**kwargs):
        raise error_type("private profile path or credential data")

    monkeypatch.setattr(boto3.session, "Session", broken_session)
    with pytest.raises(AmazonConfigurationError) as error:
        validate_amazon_configuration()
    assert error.value.public_message == "AWS credentials could not be resolved. Check your AWS profile and sign-in."
    assert "private" not in str(error.value)


@pytest.mark.parametrize("failure", ["get", "set", "delete"])
def test_secret_migration_failure_preserves_other_settings(monkeypatch, failure):
    import os
    from core import prefs
    from services import settings_store

    removed = []
    def get_secret(key):
        if key.endswith("DEEPL_API_KEY") and failure == "get":
            raise OSError("store unavailable")
        return None
    def set_secret(key, value):
        if key.endswith("DEEPL_API_KEY") and failure == "set":
            raise OSError("disk full")
    def delete(key):
        if key == "env.DEEPL_API_KEY" and failure == "delete":
            raise OSError("disk full")
        removed.append(key)
    monkeypatch.setattr(settings_store, "get_secret", get_secret)
    monkeypatch.setattr(settings_store, "set_secret", set_secret)
    monkeypatch.setattr(prefs, "delete", delete)
    for key in ("DEEPL_API_KEY", "MICROSOFT_API_KEY", "BACKEND_PORT"):
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)
    prefs.restore_env({"env.DEEPL_API_KEY": "legacy", "env.MICROSOFT_API_KEY": "other", "env.BACKEND_PORT": "3999"})
    assert os.environ["BACKEND_PORT"] == "3999"
    assert os.environ["DEEPL_API_KEY"] == "legacy"
    assert os.environ["MICROSOFT_API_KEY"] == "other"
    assert "env.DEEPL_API_KEY" not in removed
    assert "env.MICROSOFT_API_KEY" in removed


@pytest.mark.parametrize("provider", ["deepl", "microsoft"])
@pytest.mark.parametrize("base", ["http://example.com", "//example.com", "https://"])
def test_paid_provider_rejects_insecure_url_before_request(monkeypatch, provider, base):
    from services.translation_apis import Translator
    monkeypatch.setenv(provider.upper() + "_BASE_URL", base)
    monkeypatch.setattr(httpx, "Client", lambda **kw: pytest.fail("Must reject before constructing HTTP client"))
    with pytest.raises(ValueError, match="HTTPS"):
        Translator(provider, "auto", "en", api_key="fixture").translate("hello")


@pytest.mark.parametrize("provider,status,body,expected", [
    ("deepl", 403, {"message": "Authorization failed. Please supply a valid auth_key parameter."},
     ["DeepL refused the translation (HTTP 403)", "Authorization failed", "Settings"]),
    ("microsoft", 401, {"error": {"code": 401000, "message": "The request is not authorized because credentials are missing or invalid."}},
     ["Microsoft Translator refused the translation (HTTP 401)", "credentials are missing or invalid", "Settings"]),
    ("google-cloud", 400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key."}},
     ["Google Cloud Translation refused the translation (HTTP 400)", "API key not valid"]),
    ("deepl", 456, {"message": "Quota exceeded"}, ["HTTP 456", "Quota exceeded", "quota is used up"]),
    ("microsoft", 500, None, ["HTTP 500", "server problem"]),
    ("google-cloud", 403, "Authentication rejected by upstream", ["HTTP 403", "Authentication rejected by upstream"]),
])
def test_paid_provider_rejection_explains_itself_without_the_key(monkeypatch, provider, status, body, expected):
    """#4357: raise_for_status() dropped the provider's error body, so a wrong or
    expired key showed only a bare "403 Forbidden for url ..."."""
    from services.translation_apis import TranslationProviderError, Translator
    for name in ["DEEPL_API_KEY", "DEEPL_BASE_URL", "MICROSOFT_API_KEY", "MICROSOFT_BASE_URL", "GOOGLE_TRANSLATE_API_KEY"]:
        monkeypatch.delenv(name, raising=False)
    key = "-".join(["fixture", "secret", "value", "1234"])  # built at runtime: not a real key
    echoed = dict(body) if isinstance(body, dict) else body
    if isinstance(echoed, dict) and "message" in echoed:
        echoed["message"] += f" (got {key})"

    def handler(request):
        if echoed is None:
            return httpx.Response(status, text="")
        if isinstance(echoed, str):
            return httpx.Response(status, json=echoed)
        return httpx.Response(status, json=echoed)

    client_type = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: client_type(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(TranslationProviderError) as caught:
        Translator(provider, "auto", "de", api_key=key).translate("hello")
    message = str(caught.value)
    for part in expected:
        assert part in message
    assert key not in message
    assert caught.value.status == status
