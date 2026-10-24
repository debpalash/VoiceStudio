"""Runtime API-base injection helpers (Docker / reverse-proxy deployments).

A prebuilt image can't take a build-time VITE_* override, so the backend
injects OMNIVOICE_PUBLIC_API_BASE into index.html as a window global. These
test the pure helpers without booting the app.
"""
from __future__ import annotations

import base64
import hashlib

import pytest

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


def test_inject_api_base_allows_remote_http_and_websocket_connections():
    doc = '<meta http-equiv="Content-Security-Policy" content="connect-src \'self\' blob:">'
    out = inject_api_base(doc, "https://api.example.com/v1")
    assert "connect-src 'self' https://api.example.com wss://api.example.com blob:" in out


@pytest.mark.parametrize("url", [
    "https://[", "https://[::1", "https://?api", "https://#api",
    "https://example.com:bad", "https://example.com:65536",
    "https://user:password@example.com/api",
])
def test_public_api_base_rejects_unusable_authorities(url):
    assert not is_valid_public_api_base(url)


@pytest.mark.parametrize("url", [
    "http://[::1]:3900/api", "https://example.com:443/api",
])
def test_public_api_base_accepts_valid_ipv6_and_ports(url):
    assert is_valid_public_api_base(url)
    assert "window.__OMNIVOICE_API_BASE__" in inject_api_base("<head></head>", url)


@pytest.mark.parametrize("url", [
    "https://[::1]oops/api", "https://exa%zz.com/api",
    "https://example.com%23evil.com/api", "https://example%2f.com/api",
    "https://[v1.host]/api", "https://[fe80::1%25en0]/api", "https://exa\x7fmple.com/api",
])
def test_public_api_base_rejects_browser_invalid_hosts(url):
    from core.spa_inject import is_valid_public_api_base
    assert not is_valid_public_api_base(url)


@pytest.mark.parametrize("url", ["https://%65xample.com/api", "https://例え.テスト/api"])
def test_public_api_base_preserves_browser_valid_hosts(url):
    from core.spa_inject import is_valid_public_api_base
    assert is_valid_public_api_base(url)


@pytest.mark.parametrize(("base", "authority"), [
    ("https://例え.テスト:3900/api", "xn--r8jz45g.xn--zckzah:3900"),
    ("https://faß.test:3900/api", "xn--fa-hia.test:3900"),
    ("https://%65xample.test:3900/api", "example.test:3900"),
])
def test_csp_api_origins_match_browser_dns_hosts(base, authority):
    from core.spa_inject import inject_api_base, is_valid_public_api_base

    assert is_valid_public_api_base(base)
    doc = '<head><meta content="connect-src \'self\'"></head>'
    out = inject_api_base(doc, base)
    assert f"connect-src 'self' https://{authority} wss://{authority}" in out


def test_idna_unrepresentable_host_is_rejected_before_injection():
    from core.spa_inject import is_valid_public_api_base

    assert not is_valid_public_api_base("https://\u200d.test/api")


def test_rejected_runtime_api_url_warning_does_not_disclose_credentials(caplog):
    import ast
    import logging
    from pathlib import Path

    source = Path(__file__).parents[1] / "backend" / "main.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    finalize = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_phase_a_finalize")
    guards = [node for node in ast.walk(finalize) if isinstance(node, ast.If)
              and any(isinstance(part, ast.Name) and part.id == "is_valid_public_api_base"
                      for part in ast.walk(node.test))]
    assert len(guards) == 1
    guard = guards[0]
    code = compile(ast.Module(body=[guard], type_ignores=[]), str(source), "exec")
    scope = {
        "logging": logging,
        "is_valid_public_api_base": is_valid_public_api_base,
        "_public_api_base": "https://fixture-user:synthetic-fixture-password@api.example.com",
    }
    with caplog.at_level(logging.WARNING, logger="omnivoice.api"):
        exec(code, scope)
    assert scope["_public_api_base"] == ""
    assert "OMNIVOICE_PUBLIC_API_BASE" in caplog.text
    assert "fixture-user" not in caplog.text
    assert "synthetic-fixture-password" not in caplog.text
    assert "api.example.com" not in caplog.text
