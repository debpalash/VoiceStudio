"""Runtime API-base injection helpers (Docker / reverse-proxy deployments).

A prebuilt image can't take a build-time VITE_* override, so the backend
injects OMNIVOICE_PUBLIC_API_BASE into index.html as a window global. These
test the pure helpers without booting the app.
"""
from __future__ import annotations

import base64
import hashlib

from core.spa_inject import inject_api_base, is_valid_public_api_base


def test_valid_public_api_base_accepts_http_urls():
    assert is_valid_public_api_base("https://api.example.com")
    assert is_valid_public_api_base("http://10.0.0.5:3900")
    assert is_valid_public_api_base("https://voice.example.com/api")


def test_valid_public_api_base_rejects_unsafe_or_empty():
    assert not is_valid_public_api_base("")
    assert not is_valid_public_api_base("not a url")
    assert not is_valid_public_api_base("javascript:alert(1)")
    # No script breakout possible — angle brackets / quotes are rejected.
    assert not is_valid_public_api_base('https://x"</script><script>evil()')
    assert not is_valid_public_api_base("https://x</script>")


def test_inject_api_base_into_head():
    doc = "<html><head><title>x</title></head><body></body></html>"
    out = inject_api_base(doc, "https://api.example.com")
    assert '<head><script>window.__OMNIVOICE_API_BASE__="https://api.example.com";</script>' in out
    assert out.count("<head>") == 1  # injected once, original head preserved


def test_inject_api_base_prepends_when_no_head():
    out = inject_api_base("<body>x</body>", "http://10.0.0.5:3900")
    assert out.startswith('<script>window.__OMNIVOICE_API_BASE__="http://10.0.0.5:3900";</script>')


def test_inject_api_base_json_encodes_value():
    # json.dumps wraps in double quotes; combined with is_valid_public_api_base
    # the value can't contain a quote, so the snippet is always well-formed.
    out = inject_api_base("<head></head>", "https://a/b")
    assert '="https://a/b";' in out


def test_inject_api_base_authorizes_exact_script_under_renderer_csp():
    script = 'window.__OMNIVOICE_API_BASE__="https://api.example.com";'
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    doc = '<meta http-equiv="Content-Security-Policy" content="script-src \'self\';">'
    out = inject_api_base(doc, "https://api.example.com")
    assert f"script-src 'self' 'sha256-{digest}'" in out
