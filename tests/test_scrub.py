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
