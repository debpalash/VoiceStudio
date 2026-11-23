"""Paid translation APIs with explicit credentials and bounded requests."""
import html
import os
from urllib.parse import urlsplit

PAID_PROVIDERS = {"deepl", "microsoft", "google-cloud", "amazon"}
SECRET_ENV_KEYS = {"DEEPL_API_KEY", "MICROSOFT_API_KEY", "GOOGLE_TRANSLATE_API_KEY", "TRANSLATE_API_KEY"}


class AmazonConfigurationError(ValueError):
    """Only fixed, user-safe setup messages cross the API boundary."""

    def __init__(self, public_message):
        super().__init__(public_message)
        self.public_message = public_message


def validate_amazon_configuration():
    """Explicit selection/job preflight; catalogue reads never resolve AWS identity."""
    import boto3
    try:
        session = boto3.session.Session(
            region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"))
        if not session.region_name:
            raise AmazonConfigurationError("Configure an AWS region before using Amazon Translate.")
        credentials = session.get_credentials()
        frozen = credentials.get_frozen_credentials() if credentials else None
        if not frozen or not frozen.access_key or not frozen.secret_key:
            raise AmazonConfigurationError("Configure AWS credentials before using Amazon Translate.")
    except AmazonConfigurationError:
        raise
    except Exception:
        raise AmazonConfigurationError("AWS credentials could not be resolved. Check your AWS profile and sign-in.") from None
    return session


_PROVIDER_NAMES = {"deepl": "DeepL", "microsoft": "Microsoft Translator", "google-cloud": "Google Cloud Translation"}
_DETAIL_LIMIT = 300


class TranslationProviderError(RuntimeError):
    """A paid provider refused the request; the message is safe to show."""

    def __init__(self, provider, status, detail=""):
        name = _PROVIDER_NAMES.get(provider, provider)
        if status in (401, 403):
            hint = f"Check the {name} key in Settings \u2192 Translation: it must be a valid key for this provider."
        elif status == 456:
            hint = f"The {name} character quota is used up; wait for it to reset or raise the plan limit."
        elif status == 429:
            hint = f"{name} is rate-limiting requests; try again in a moment."
        elif status >= 500:
            hint = f"{name} had a server problem; try again later."
        else:
            hint = ""
        parts = [f"{name} refused the translation (HTTP {status})."]
        if detail:
            parts.append(f"It said: {detail}")
        if hint:
            parts.append(hint)
        super().__init__(" ".join(parts))
        self.provider, self.status, self.detail = provider, status, detail


def _provider_detail(response, secret=""):
    """The provider's own explanation from an error body, short and key-free.

    DeepL answers ``{"message": ...}``; Microsoft and Google nest it as
    ``{"error": {"message": ...}}``. Anything else falls back to the text."""
    try:
        body = response.json()
    except Exception:
        body = None
    text = body if isinstance(body, str) else ""
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            text = error.get("message") or ""
        elif isinstance(error, str):
            text = error
        text = text or body.get("message") or body.get("detail") or ""
    if not isinstance(text, str) or not text:
        try:
            text = response.text if body is None else ""
        except Exception:
            text = ""
    text = " ".join(str(text).split())
    if secret and len(secret) >= 4:
        text = text.replace(secret, "[redacted]")
    return text[:_DETAIL_LIMIT]


class Translator:
    def __init__(self, provider, source, target, api_key=""):
        self.provider, self.source, self.target = provider, source, target
        self.api_key = api_key

    def translate(self, text):
        import httpx
        source, target = self.source, self.target
        provider = self.provider
        if provider == "amazon":
            import boto3
            from botocore.config import Config
            aliases = {"zh-CN": "zh", "zh-TW": "zh-TW"}
            client = boto3.session.Session().client("translate", region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"),
                config=Config(connect_timeout=5, read_timeout=30, retries={"total_max_attempts": 1}))
            try:
                return client.translate_text(Text=text, SourceLanguageCode=aliases.get(source, source),
                    TargetLanguageCode=aliases.get(target, target))["TranslatedText"]
            finally:
                client.close()
        headers = {}
        params = {}
        if provider == "deepl":
            key = os.environ.get("DEEPL_API_KEY") or self.api_key
            base = os.environ.get("DEEPL_BASE_URL") or ("https://api-free.deepl.com/v2" if key.endswith(":fx") else "https://api.deepl.com/v2")
            url = base.rstrip("/") + "/translate"
            headers["Authorization"] = "DeepL-Auth-Key " + key
            target = {"zh-CN": "ZH-HANS", "zh-TW": "ZH-HANT"}.get(target, target.upper())
            payload = {"text": [text], "target_lang": target}
            if source != "auto": payload["source_lang"] = source.split("-")[0].upper()
        elif provider == "microsoft":
            key = os.environ.get("MICROSOFT_API_KEY") or self.api_key
            url = (os.environ.get("MICROSOFT_BASE_URL") or "https://api.cognitive.microsofttranslator.com").rstrip("/") + "/translate"
            headers["Ocp-Apim-Subscription-Key"] = key
            if os.environ.get("MICROSOFT_REGION"):
                headers["Ocp-Apim-Subscription-Region"] = os.environ["MICROSOFT_REGION"]
            aliases = {"zh-CN": "zh-Hans", "zh-TW": "zh-Hant"}
            params = {"api-version": "3.0", "to": aliases.get(target, target)}
            if source != "auto": params["from"] = aliases.get(source, source)
            payload = [{"Text": text}]
        elif provider == "google-cloud":
            key = os.environ.get("GOOGLE_TRANSLATE_API_KEY") or self.api_key
            url = "https://translation.googleapis.com/language/translate/v2"
            headers["X-Goog-Api-Key"] = key
            payload = {"q": text, "target": target, "format": "text"}
            if source != "auto": payload["source"] = source
        else:
            raise ValueError("Unknown translation API")
        if not key:
            raise ValueError("Translation API key is missing")
        endpoint = urlsplit(url)
        if endpoint.scheme != "https" or not endpoint.hostname:
            raise ValueError("Translation API endpoints must use HTTPS with a valid host")
        # No redirects: provider keys must remain at the configured endpoint.
        with httpx.Client(timeout=httpx.Timeout(30, connect=5), follow_redirects=False) as client:
            response = client.post(url, headers=headers, params=params, json=payload)
            if not response.is_success:
                # raise_for_status() discards the body, which is the only place
                # the provider says why (wrong-provider key, expired, quota).
                raise TranslationProviderError(provider, response.status_code, _provider_detail(response, key))
            body = response.json()
        if provider == "deepl": return body["translations"][0]["text"]
        if provider == "microsoft": return body[0]["translations"][0]["text"]
        return html.unescape(body["data"]["translations"][0]["translatedText"])
