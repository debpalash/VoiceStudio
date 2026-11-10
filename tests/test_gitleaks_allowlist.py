"""The secret-scan allowlist must remain exact and value-scoped."""
import hashlib
from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_EXACT_REGEXES = {
    "^528e871c2a26c4f0f7773b9754e2e1acae20899d$",
    "^hf_abcdefghijklmnopqrstuvwxyz01234567890abcd$",
    "^hf_abcdefghijklmnopqrstuvwxyz0123456789ABCDEF$",
    "^hf_QWERTYUIOPasdfghjklZXCVBNM0123456789xyzAB$",
    "^max_length=400$",
    "^Ed25519PrivateKey$",
    r"^omnivoice\.dubSplit\.v1$",
}


def test_gitleaks_allowlist_contains_only_reviewed_exact_values():
    config = tomllib.loads((ROOT / ".gitleaks.toml").read_text(encoding="utf-8"))
    allowlist = config["allowlist"]

    assert set(allowlist) == {"description", "regexes"}
    assert set(allowlist["regexes"]) == EXPECTED_EXACT_REGEXES
    assert all(
        regex.startswith("^") and regex.endswith("$")
        for regex in allowlist["regexes"]
    )
    assert "rules" not in config


def test_dub_storage_key_allowlist_does_not_hide_similar_credentials():
    config = tomllib.loads((ROOT / ".gitleaks.toml").read_text(encoding="utf-8"))
    regexes = config["allowlist"]["regexes"]
    assert any(re.fullmatch(regex, "omnivoice.dubSplit.v1") for regex in regexes)
    for value in (
        "omnivoiceXdubSplitXv1",
        "omnivoice.dubSplit.v1-secret",
        "secret-omnivoice.dubSplit.v1",
    ):
        assert not any(re.fullmatch(regex, value) for regex in regexes)


# SHA-256 of each retired PostHog project token that history still holds (the
# value itself may only appear in the two canonical analytics files).
_RETIRED_TOKEN_SHA256 = {"cf19c9566b83f7375f591f5fbf776b9c344025805d12c16bd21333f70a708f72"}
_FINGERPRINT = re.compile(r"^([0-9a-f]{40}):([^:]+):([a-z0-9-]+):([0-9]+)$")


def _ignored_fingerprints():
    text = (ROOT / ".gitleaksignore").read_text(encoding="utf-8")
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]


# Reviewed false positives that are not the retired PostHog token. Each is one finding in
# one commit; adding to this table is the review step for a new non-PostHog ignore entry.
_REVIEWED_NON_SECRETS = {
    "f6d634e19902c0fd0e64947e635359d5bc1f85de:electron/tests/fake-health-backend.test.mjs:generic-api-key:59":
        "RFC 6455 sample Sec-WebSocket-Key in a test",
    "97ee0f37079c06a8e2e80eb3625bc60885bc8a27:docs/mobile.md:curl-auth-header:114":
        "curl docs use the literal YOUR_TOKEN placeholder",
    "4dab817e78d54b055967860581687eeb5bb77472:mobile/android/core/network/src/main/kotlin/"
    "com/voicestudio/mobile/network/ContentReports.kt:generic-api-key:38":
        "Crockford base32 alphabet used to build the report idempotency key",
}


def test_gitleaks_ignores_only_commit_pinned_posthog_findings():
    """An ignore entry names one finding in one commit, never a path or a rule."""
    import subprocess

    entries = _ignored_fingerprints()
    assert entries
    shallow = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout.strip() == "true"
    for entry in entries:
        match = _FINGERPRINT.fullmatch(entry)
        assert match, f"not a commit-pinned fingerprint: {entry}"
        commit, path, _rule, line = match.groups()
        if entry in _REVIEWED_NON_SECRETS:
            continue
        if shallow:
            continue
        shown = subprocess.run(
            ["git", "show", f"{commit}:{path}"], cwd=ROOT, capture_output=True, text=True,
        )
        assert shown.returncode == 0, f"cannot read the pinned commit and path: {entry}"
        source_line = shown.stdout.splitlines()[int(line) - 1]
        # Only the retired public PostHog project token may be skipped.
        token = re.search(r"phc_[A-Za-z0-9]{20,}", source_line)
        assert token, entry
        assert hashlib.sha256(token.group(0).encode()).hexdigest() in _RETIRED_TOKEN_SHA256, entry


def test_every_reviewed_non_secret_is_still_ignored():
    """A table row with no ignore entry is stale and would hide a removal."""
    assert set(_REVIEWED_NON_SECRETS) <= set(_ignored_fingerprints())
