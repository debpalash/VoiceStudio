"""core.scrub — privacy scrubber for diagnostic/bug-report text.

The scrubber is the last gate before text can reach a prefilled GitHub
Issues URL, so these tests pin the exact redaction behavior per platform
path style and per credential shape.
"""
import os

import pytest

import core.scrub as scrub_module

from core.scrub import REDACTED, scrub_provider_error, scrub_text


# ── Home directory redaction ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("/Users/alice/Library/Logs/app.log", "~/Library/Logs/app.log"),
        ("/home/bob/.omnivoice/omnivoice.log", "~/.omnivoice/omnivoice.log"),
        (r"C:\Users\carol\AppData\Roaming\OmniVoice", r"~\AppData\Roaming\OmniVoice"),
        (r"D:\Users\dave\models", r"~\models"),
        # Windows paths normalized to forward slashes (file URLs, traces)
        ("C:/Users/erin/AppData/Local/OmniVoice/app.log", "~/AppData/Local/OmniVoice/app.log"),
        ("file:///D:/Users/frank/voice.wav", "file:///~/voice.wav"),
        # repr()/JSON escaping doubles the separators, which is how an OSError
        # message and a JSON log line render a Windows path.
        (
            r"[Errno 13] Permission denied: 'C:\\Users\\grace\\models\\weights.bin'",
            r"[Errno 13] Permission denied: '~\\models\\weights.bin'",
        ),
        # A Windows account name can contain a space.
        (r"C:\Users\Mary Jane\Documents\out.wav", r"~\Documents\out.wav"),
        ("/Users/Mary Jane/Library/Logs/app.log", "~/Library/Logs/app.log"),
        # ...or several, and an escaped OSError path can end at the home itself.
        (r"C:\\Users\\Mary Jane Smith\\models", r"~\\models"),
        (r"[Errno 2] No such file: 'C:\\Users\\Mary Jane'", r"[Errno 2] No such file: '~'"),
        ('"/home/Mary Jane Smith" is read-only', '"~" is read-only'),
        ("/Users/Mary\u00a0Jane/file", "~/file"),
        (r"C:\\Users\\O'Brien\\x", r"~\\x"),
        ("'/home/o'brien'", "'~'"),
        # A line break ends the name, so the next log line keeps its start.
        ("/home/bob\nRetry/path", "~\nRetry/path"),
        # The words after a short path stay: only the path is redacted.
        ("/home/bob: Permission denied", "~: Permission denied"),
        (r"C:\Users\Mary was not found", r"~ was not found"),
    ],
)
def test_home_paths_redacted(raw, expected):
    assert scrub_text(raw) == expected


def test_actual_process_home_redacted():
    home = os.path.expanduser("~")
    assert home not in scrub_text(f"failed to open {home}/some/file.wav")


def test_home_redaction_inside_traceback():
    tb = (
        'Traceback (most recent call last):\n'
        '  File "/home/eve/OmniVoice/backend/main.py", line 42, in synth\n'
        "FileNotFoundError: /Users/eve/voice.wav not found"
    )
    out = scrub_text(tb)
    assert "/home/eve" not in out
    assert "/Users/eve" not in out
    assert 'File "~/OmniVoice/backend/main.py"' in out


# ── Credential-shaped substrings ──────────────────────────────────────────


@pytest.mark.parametrize(
    "secret",
    [
        "hf_" + "A" * 34,                  # HuggingFace token
        "ghp_" + "B" * 36,                 # GitHub classic PAT
        "github_pat_" + "C" * 22,          # GitHub fine-grained PAT
        "sk-" + "d" * 40,                  # OpenAI-style key
    ],
)
def test_tokens_redacted(secret):
    out = scrub_text(f"auth failed with token={secret} (401)")
    assert secret not in out
    assert REDACTED in out


@pytest.mark.parametrize(
    "benign",
    ["hf_hub", "hf_pipeline_load", "sk-learn", "ghp_x"],
)
def test_short_identifiers_survive(benign):
    # Identifiers shorter than real-token length must NOT be clobbered —
    # they're exactly what makes a stack trace debuggable.
    assert benign in scrub_text(f"import error in {benign} module")


# ── Env-var secret values ─────────────────────────────────────────────────


def test_env_secret_value_redacted(monkeypatch):
    monkeypatch.setenv("TRANSLATE_API_KEY", "super-secret-value-123")
    out = scrub_text("request failed: api_key=super-secret-value-123 rejected")
    assert "super-secret-value-123" not in out
    assert REDACTED in out


def test_env_secret_short_value_not_swept(monkeypatch):
    # A short value would shred unrelated text (every "yes" in the report).
    monkeypatch.setenv("SOME_PASSWORD", "yes")
    assert scrub_text("yes, the export worked") == "yes, the export worked"


def test_env_non_secret_name_untouched(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_MODEL", "k2-fsa/OmniVoice")
    assert "k2-fsa/OmniVoice" in scrub_text("loading k2-fsa/OmniVoice")


# ── Robustness ────────────────────────────────────────────────────────────


def test_none_and_empty():
    assert scrub_text(None) == ""
    assert scrub_text("") == ""


def test_non_string_coerced():
    assert scrub_text(42) == "42"


# ── Hardening regressions (diagnostics audit) ─────────────────────────────

@pytest.mark.parametrize("raw", [
    r"c:\users\john\AppData\log.txt",   # lowercase drive + Users
    r"C:\Users\john\AppData\log.txt",   # canonical
    "C:/USERS/john/app/log.txt",        # upper, forward slashes
])
def test_windows_home_case_insensitive(raw):
    # The username must never survive, regardless of Users/users casing.
    assert "john" not in scrub_text(raw)


# Built from low-entropy parts (not real-secret literals) so they match the
# scrubber's shape without tripping GitHub push-protection secret scanning.
@pytest.mark.parametrize("secret", [
    "eyJ" + "a" * 20 + "." + "b" * 20 + "." + "c" * 20,  # JWT
    "AIza" + "B" * 35,                                   # Google API key
    "xox" + "b-" + "C" * 20,                             # Slack
    "AKIA" + "D" * 16,                                   # AWS access key id
])
def test_broadened_token_shapes_redacted(secret):
    assert secret not in scrub_text(f"request failed: {secret} (401)")


def test_url_query_secret_value_redacted_name_kept():
    out = scrub_text("open https://host/api?token=supersecretvalue12345&x=1")
    assert "supersecretvalue12345" not in out
    assert "token=" in out          # param name preserved for legibility
    assert "x=1" in out             # non-secret params untouched


def test_share_pin_in_a_request_url_is_redacted():
    # The desktop share link is http://<lan-host>:<port>/?pin=<digits>, the
    # backend accepts that parameter as a consumption credential, and crash
    # requests are written to the crash log verbatim — so the PIN must not
    # survive into a diagnostic bundle a user pastes into a public report.
    out = scrub_text("Request: http://192.168.1.20:3901/?pin=481516")
    assert "481516" not in out
    assert "pin=" in out            # param name preserved, like token=


@pytest.mark.parametrize('name,decoded',[
    ('%70in','pin'),('p%69%6e','pin'),('api%5Fkey','api_key'),
    ('%74oken','token'),('pass%77ord','password'),('auth%2Dtoken','auth-token'),
    ('%61%63%63%65%73%73%5F%74%6F%6B%65%6E','access_token'),
])
@pytest.mark.parametrize('secret',['481516','short'])
def test_encoded_credential_query_names_match_http_parsing(name,decoded,secret):
    from starlette.requests import Request
    request=Request({'type':'http','scheme':'http','server':('192.0.2.1',3901),
                     'path':'/','headers':[],
                     'query_string':f'view=models&{name}={secret}&page=2'.encode()})
    assert request.query_params[decoded]==secret
    out=scrub_text(f'Request: {request.url}')
    assert secret not in out
    assert f'{name}={REDACTED}' in out
    assert 'view=models' in out and 'page=2' in out


def test_query_scrubbing_preserves_unrelated_names_and_one_decode_semantics():
    raw='http://192.0.2.1/?spin=481516&pin_code=481516&key=short&%2570in=481516&x=%GG'
    assert scrub_text(raw)==raw


def test_duplicate_literal_and_encoded_pin_values_are_all_redacted():
    out=scrub_text('http://192.0.2.1/?pin=481516&%70in=151623&api_key=short&view=models')
    assert all(secret not in out for secret in ('481516','151623','short'))
    assert 'view=models' in out


@pytest.mark.parametrize('query',[
    "pass%77ord='orchid-river-72'", 'pass%77ord="orchid-river-72"',
    "password='orchid-river-72'", "pass%77ord='orchid-river-72",
    "pass%77ord='prefix\\'orchid-river-72'",
])
def test_quoted_credential_query_values_are_fully_redacted(query):
    from starlette.requests import Request
    request=Request({'type':'http','scheme':'http','server':('192.0.2.1',3901),
                     'path':'/','headers':[],
                     'query_string':f'{query}&view=models'.encode()})
    assert 'orchid-river-72' in request.query_params['password']
    out=scrub_text(f'Request: {request.url}')
    assert 'orchid-river-72' not in out
    assert REDACTED in out


def test_unquoted_credential_keeps_the_surrounding_log_quote():
    assert scrub_text("Request: 'http://h/?pin=1234' next") == f"Request: 'http://h/?pin={REDACTED}' next"


@pytest.mark.parametrize('name,secret',[('password','PRIVATE_FIXTURE'),('%70in','482916')])
@pytest.mark.parametrize('quote',['"',"'"])
@pytest.mark.parametrize('escape_depth',[1,3,7])
def test_log_escaped_quoted_credential_is_removed_in_full(name,secret,quote,escape_depth):
    marker='\\'*escape_depth+quote
    raw=f'https://h/?{name}={marker}{secret}{marker}&x=1'
    out=scrub_text(raw)
    assert secret not in out
    assert f'{name}={REDACTED}&x=1' in out


@pytest.mark.parametrize('name',['api_key','api%5Fkey'])
@pytest.mark.parametrize('quote',["'",'"'])
@pytest.mark.parametrize('leading_quoted',[False,True])
def test_internal_quote_in_parsed_http_credential_does_not_leak(name,quote,leading_quoted):
    import httpx
    from starlette.requests import Request
    from core.auth import _credential_candidate
    prefix=f'{quote}prefix{quote}' if leading_quoted else 'prefix'
    secret=f'{prefix}{quote}PRIVATE_FIXTURE{quote}'
    url=httpx.URL(f'https://h/?{name}={secret}&x=1')
    request=Request({'type':'http','scheme':'https','server':('h',443),
                     'path':'/','headers':[],'query_string':url.query})
    assert _credential_candidate(request).value==secret
    out=scrub_text(str(request.url))
    assert 'PRIVATE_FIXTURE' not in out
    assert '&x=1' in out


@pytest.mark.parametrize('raw',[
    "Request: 'http://h/?pin=1234', status=500\nTraceback: decoder failed",
    '{"request":"http://h/?pin=1234"}\nTraceback: decoder failed',
    '{"request":"http://h/?pin=1234","status":500}\nTraceback: decoder failed',
])
def test_outer_url_quote_keeps_comma_and_brace_diagnostic_tail(raw):
    assert scrub_text(raw)==raw.replace('1234',REDACTED)


def test_outer_log_quote_does_not_expose_an_opaque_internal_credential_quote():
    import httpx
    from starlette.requests import Request
    from core.auth import _credential_candidate
    secret="prefix',PRIVATE_FIXTURE'"
    url=httpx.URL(f'https://h/?api_key={secret}&x=1')
    request=Request({'type':'http','scheme':'https','server':('h',443),
                     'path':'/','headers':[],'query_string':url.query})
    assert _credential_candidate(request).value==secret
    raw=f"Request: '{request.url}', status=500\nTraceback: decoder failed"
    out=scrub_text(raw)
    assert 'PRIVATE_FIXTURE' not in out
    assert '&x=1' in out and 'status=500' in out and 'Traceback: decoder failed' in out


@pytest.mark.parametrize('secret,tail',[
    ("prefix'],PRIVATE_FIXTURE'",'&x=1'),
    ("prefix']]],PRIVATE_FIXTURE'",'&x=1'),
    ("prefix']},PRIVATE_FIXTURE'",'&x=1'),
    ('prefix\'],"status":PRIVATE_FIXTURE\'','&x=1'),
    ("prefix'],PRIVATE_FIXTURE'?continue=1",''),
    ("prefix'],PRIVATE_FIXTURE'",''),
])
def test_opaque_http_credential_brackets_cannot_close_the_log_url(secret,tail):
    import httpx
    from starlette.requests import Request
    from core.auth import _credential_candidate
    url=httpx.URL(f'https://h/?api_key={secret}{tail}')
    request=Request({'type':'http','scheme':'https','server':('h',443),
                     'path':'/','headers':[],'query_string':url.query})
    assert _credential_candidate(request).value==secret
    raw=f"Request: '{request.url}', status=500\nTraceback: decoder failed"
    out=scrub_text(raw)
    assert 'PRIVATE_FIXTURE' not in out
    assert 'status=500' in out and 'Traceback: decoder failed' in out
    if tail:assert '&x=1' in out


def test_json_url_field_keeps_other_string_fields_and_urls():
    import json
    raw=json.dumps({'request':'https://h/?api_key=PRIVATE_FIXTURE',
                    'status':'decoder failed','other':'https://h/?view=models'})
    assert scrub_text(raw)==raw.replace('PRIVATE_FIXTURE',REDACTED)


def test_malformed_query_stress_finishes_within_a_process_bound():
    import subprocess
    import sys
    from pathlib import Path
    # A stalled diagnostic scrub must not stall the test process. The old
    # overlapping suffix regex exceeds this generous bound on either input.
    script = """
import json
from core.scrub import scrub_text
cases = (
    "?" * 100000,
    "&" + "?!" * 50000,
    '\"https://h/?view=models' * 5000,
    json.dumps({str(i): "https://h/?view=models" for i in range(5000)}),
)
assert all(scrub_text(value) == value for value in cases)
quote = chr(39)
value = "Request: " + quote + "https://h/?api_key=prefix" + quote + "]" * 100000 + ",PRIVATE_FIXTURE" + quote + "&x=1" + quote + ", status=500"
out = scrub_text(value)
assert "PRIVATE_FIXTURE" not in out and "status=500" in out
"""
    subprocess.run([sys.executable,'-c',script],check=True,timeout=5,
                   cwd=Path(scrub_module.__file__).resolve().parents[1],capture_output=True)


def test_home_superstring_not_corrupted(monkeypatch):
    # A home of /Users/john must not rewrite /Users/johnny to '~ny'.
    monkeypatch.setenv("HOME", "/Users/john")
    out = scrub_text("/Users/johnny/secret.wav")
    assert "~ny" not in out
    assert "johnny" not in out       # still redacted by the generic macOS shape
    assert out == "~/secret.wav"


_NONSTANDARD_HOME = "/mnt/md0/homes/alice"


@pytest.fixture
def nonstandard_home(monkeypatch):
    # Patch the lookup itself so the case runs identically on Windows, where
    # expanduser reads USERPROFILE rather than HOME.
    monkeypatch.setattr(scrub_module.os.path, "expanduser", lambda _p: _NONSTANDARD_HOME)


@pytest.mark.parametrize(
    "raw, expected",
    [
        (f"Cannot use models dir {_NONSTANDARD_HOME}.", "Cannot use models dir ~."),
        (f"in {_NONSTANDARD_HOME}, retrying", "in ~, retrying"),
        (f"(cwd {_NONSTANDARD_HOME})", "(cwd ~)"),
        (f"home={_NONSTANDARD_HOME}; next", "home=~; next"),
        (f"at {_NONSTANDARD_HOME}: denied", "at ~: denied"),
        (f"{_NONSTANDARD_HOME}/voice.wav", "~/voice.wav"),
        (_NONSTANDARD_HOME, "~"),
    ],
)
def test_nonstandard_home_before_punctuation_redacted(nonstandard_home, raw, expected):
    assert scrub_text(raw) == expected


@pytest.mark.parametrize("sibling", ["alicex", "alice-old", "alice_2", "alice.bak"])
def test_nonstandard_home_sibling_not_mangled(nonstandard_home, sibling):
    path = f"/mnt/md0/homes/{sibling}/file"
    assert scrub_text(path) == path


class _BrokenPattern:
    def sub(self, *_args, **_kwargs):
        raise RuntimeError("redaction engine failed")


def test_env_secret_collection_failure_is_fail_closed(monkeypatch):
    secret = "private-provider-key-value"
    monkeypatch.setattr(
        scrub_module,
        "_env_secret_values",
        lambda: (_ for _ in ()).throw(RuntimeError("environment unavailable")),
    )
    assert scrub_text(f"provider rejected {secret}") == REDACTED


@pytest.mark.parametrize("target", ["_TOKEN_PATTERNS", "_URL_SECRET_RE", "_HOME_PATTERNS"])
def test_redaction_pattern_failure_is_fail_closed(monkeypatch, target):
    value = (_BrokenPattern(),) if target.endswith("PATTERNS") else _BrokenPattern()
    monkeypatch.setattr(scrub_module, target, value)
    assert scrub_text("token=private-provider-key-value /home/alice/file") == REDACTED


def test_actual_home_redaction_failure_is_fail_closed(monkeypatch):
    monkeypatch.setattr(scrub_module.os.path, "expanduser", lambda _path: (_ for _ in ()).throw(RuntimeError()))
    assert scrub_text("token=private-provider-key-value /private/alice/file") == REDACTED


def test_provider_exact_key_redaction_failure_is_fail_closed():
    class _BrokenKey:
        def __bool__(self):
            return True

        def __eq__(self, _other):
            return False

        def __len__(self):
            raise RuntimeError("key inspection failed")

    assert scrub_provider_error("provider echoed a credential", _BrokenKey()) == REDACTED

# ── Inline URL credentials (`https://user:token@host`) ──────────────────────
# An index, mirror or proxy URL can carry its credential in the userinfo, which
# is a sanctioned configuration for restricted networks and was not covered by
# the query-parameter pass. uv/pip/git echo those URLs verbatim into logs.

def _scrub(text):
    # Resolved per call so a test that swaps sys.modules can't leave a stale binding.
    import importlib
    return importlib.import_module("core.scrub").scrub_text(text)


def test_inline_url_userinfo_is_redacted():
    scrubbed = _scrub("Collecting from https://user:s3cr3t@mirror.example/simple/ ...")
    assert "s3cr3t" not in scrubbed
    assert "mirror.example/simple/" in scrubbed


def test_inline_url_userinfo_is_redacted_inside_an_env_assignment():
    scrubbed = _scrub("HTTP_PROXY=https://ops:hunter2@proxy.internal:8080")
    assert "hunter2" not in scrubbed
    assert "proxy.internal:8080" in scrubbed


def test_token_as_username_is_redacted_too():
    # GitHub's documented form puts the token in the username, with no colon.
    scrubbed = _scrub("remote: https://ghp_abcdefghijklmnopqrstuvwxyz012345@github.com/x/y.git")
    assert "ghp_abcdefghijklmnopqrstuvwxyz012345" not in scrubbed


def test_an_ssh_identity_is_not_userinfo_and_stays_readable():
    # No secret, and mangling it would make an error message useless.
    assert _scrub("fatal: could not read from ssh://git@github.com/o/r.git") == (
        "fatal: could not read from ssh://git@github.com/o/r.git"
    )
    assert _scrub("git@github.com:owner/repo.git") == "git@github.com:owner/repo.git"


def test_a_path_handle_is_not_userinfo():
    assert _scrub("https://example.com/@alice") == "https://example.com/@alice"


def test_userinfo_with_an_apostrophe_is_redacted():
    scrubbed = _scrub("https://user:orchid'river@proxy.internal:8080/simple/")
    assert "orchid" not in scrubbed and "river" not in scrubbed
    assert "proxy.internal:8080/simple/" in scrubbed


def test_userinfo_containing_a_query_style_name_is_redacted_whole():
    scrubbed = _scrub("https://user:orchid&token=river@mirror.example/simple/")
    assert "orchid" not in scrubbed and "river" not in scrubbed
    assert "mirror.example/simple/" in scrubbed


def test_userinfo_with_an_unencoded_at_sign_is_redacted_whole():
    scrubbed = _scrub("https://user:pa@ss@mirror.example/simple/")
    assert "pa@ss" not in scrubbed and "ss@" not in scrubbed
    assert "mirror.example/simple/" in scrubbed


def test_a_query_credential_before_an_at_sign_is_not_taken_as_userinfo():
    # A pathless URL whose query value holds an `@` must still lose the whole value.
    scrubbed = _scrub("https://mirror.example?password=orchid@river")
    assert "orchid" not in scrubbed and "river" not in scrubbed
