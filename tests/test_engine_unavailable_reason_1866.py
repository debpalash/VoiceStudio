"""#1866 — an unavailable engine must say what KIND of problem it has.

Model Catalogue rendered "Engine unavailable. Check installation and
configuration." plus "Last error: A previous engine check failed." for engines
the user had simply never installed. Neither names a missing package, a missing
step, or a next action, and the second reads like a crash or a poisoned cache
rather than "you have not installed this yet".

The probe's own sentence still cannot cross the boundary — it carries exception
text, local paths and sometimes credentials. What changed is that the private
diagnostic is now CLASSIFIED into a VoiceStudio-owned category, the same shape
`_public_routing_reason` already uses for routing.
"""
import pytest

from api.public_engine_metadata import public_backends

_PRIVATE_PATH = "/Users/alice/Library/Caches/secret-token-abc123"


def _reason(diagnostic):
    return public_backends([{"id": "e", "reason": diagnostic}])[0]["reason"]


@pytest.mark.parametrize(
    "diagnostic",
    [
        "voxcpm package not installed.",
        "transformers not installed",
        "funasr not installed. Install with: uv pip install funasr",
        "kittentts not installed: No module named 'kittentts'",
        "omnivoice package missing: cannot import name",
    ],
)
def test_a_missing_package_says_so(diagnostic):
    assert "isn't installed yet" in _reason(diagnostic)


@pytest.mark.parametrize(
    "diagnostic",
    [
        "Set ELEVENLABS_API_KEY environment variable.",
        "Configure a server endpoint in Model Catalogue",
        "unconfigured",
    ],
)
def test_a_configuration_gap_says_so(diagnostic):
    assert "needs to be configured" in _reason(diagnostic)


@pytest.mark.parametrize(
    "diagnostic",
    [
        "file is empty (0 bytes) — a placeholder, not a real binary",
        "file is missing",
        "ASR sidecar script missing at /opt/thing/run.py",
    ],
)
def test_a_missing_file_says_so(diagnostic):
    assert "missing or unreadable" in _reason(diagnostic)


def test_an_unrecognised_probe_falls_back_to_the_generic_line():
    # The categories must not guess. Anything unclassified keeps the old text
    # rather than asserting a cause the probe never gave.
    assert _reason("something entirely unexpected") == (
        "Engine unavailable. Check installation and configuration."
    )


@pytest.mark.parametrize(
    "diagnostic",
    [
        f"kittentts not installed: No module named 'kittentts' at {_PRIVATE_PATH}",
        f"file is unreadable ({_PRIVATE_PATH})",
        f"Set ELEVENLABS_API_KEY; current value read from {_PRIVATE_PATH}",
    ],
)
def test_no_private_text_crosses_the_boundary(diagnostic):
    # The whole reason the reason was replaced in the first place.
    out = _reason(diagnostic)
    assert _PRIVATE_PATH not in out
    assert "alice" not in out
    assert "secret-token-abc123" not in out


def test_registry_authored_fields_still_pass_through():
    row = public_backends(
        [
            {
                "id": "e",
                "reason": "voxcpm package not installed.",
                "install_hint": "uv pip install voxcpm",
                "docs_url": "https://example.invalid/docs",
                "setup_snippet": "export FOO=1",
            }
        ]
    )[0]
    assert row["install_hint"] == "uv pip install voxcpm"
    assert row["docs_url"] == "https://example.invalid/docs"
    assert row["setup_snippet"] == "export FOO=1"


def test_the_input_row_is_not_mutated():
    original = {"id": "e", "reason": "voxcpm package not installed."}
    public_backends([original])
    assert original["reason"] == "voxcpm package not installed."


@pytest.mark.parametrize(
    "diagnostic",
    [
        # The engines' own wording (Supertonic3Backend / PocketTTSBackend).
        "Supertonic-3 license not accepted. Open Model Catalogue → "
        "Supertonic-3 and click Accept to enable. (MIT code license + OpenRAIL-M "
        "model license.)",
        "PocketTTS license not accepted. Open Model Catalogue → "
        "PocketTTS and review the MIT code license, CC-BY-4.0 model license, "
        "and gated-access conditions before enabling it.",
    ],
)
@pytest.mark.parametrize("one_click", [None, True, False])
def test_a_license_gate_keeps_the_words_the_accept_button_needs(diagnostic, one_click):
    """The Accept button renders only when the reason matches the matrix's
    /license not accepted/i. Collapsing the reason into the generic line left
    Supertonic-3 and PocketTTS with no way to be enabled."""
    import re
    from pathlib import Path

    row = {"id": "e", "reason": diagnostic}
    if one_click is not None:
        row["one_click_install"] = one_click
    reason = public_backends([row])[0]["reason"]

    # The engine list's display helpers own the matcher since the list +
    # detail split (engines/engineDisplay.js, used by the row and the panel).
    source = (
        Path(__file__).resolve().parents[1]
        / "electron/src/shared/components/engines/engineDisplay.js"
    ).read_text(encoding="utf-8")
    m = re.search(r"function reasonMentionsLicense\(reason\)[^}]*?return /([^/]+)/(\w*)\.test", source, re.S)
    assert m, "engineDisplay.reasonMentionsLicense changed shape"
    flags = re.I if "i" in m.group(2) else 0
    assert re.search(m.group(1), reason, flags), reason


@pytest.mark.parametrize(
    "diagnostic",
    [
        "MLX requires Apple Silicon; this host is win32/AMD64",
        "MLX requires Apple Silicon; this Mac is Intel",
        "mlx-whisper unavailable: not supported on this platform",
        "PocketTTS is unavailable on Intel Macs because its required PyTorch "
        "version has no macOS x86_64 wheel.",
        "dots.tts is not supported on Windows — upstream targets Linux and macOS.",
    ],
)
@pytest.mark.parametrize("one_click", [None, False])
def test_a_platform_gap_is_not_reported_as_an_install_gap(diagnostic, one_click):
    """No install can fix these, so neither "isn't installed yet" nor "check
    installation" is true."""
    row = {"id": "e", "reason": diagnostic}
    if one_click is not None:
        row["one_click_install"] = one_click
    reason = public_backends([row])[0]["reason"]
    assert "platform" in reason
    assert "install" not in reason.lower()


def test_the_real_mlx_gate_is_classified_as_a_platform_gap():
    from core import device_caps

    ok, why = device_caps.mlx_supported()
    # Only the non-Apple branch is a platform gap. Apple Silicon without MPS,
    # or without torch, is not; the literal cases below cover those anywhere.
    if ok or not why.startswith("MLX requires Apple Silicon"):
        pytest.skip("this host is Apple Silicon")
    assert "platform" in _reason(why)


def test_apple_silicon_without_mps_is_told_what_is_missing():
    """The platform is right here; the installation's PyTorch is not. Neither
    the platform sentence nor the generic line says that."""
    reason = _reason(
        "Apple Silicon detected but torch MPS unavailable; "
        "reinstall torch with MPS support"
    )
    assert "MPS" in reason
    assert "platform" not in reason
    assert "Check installation and configuration" not in reason


def test_a_missing_mlx_package_on_apple_silicon_is_still_an_install_gap():
    # The same engine on a Mac it does support: there, installing does help.
    diagnostic = (
        "mlx-audio unavailable: No module named 'mlx_audio'. This backend is "
        "Apple Silicon only — available on mac-ARM dev installs; not shipped on "
        "Linux/Windows/mac-Intel."
    )
    assert "isn't installed yet" in _reason(diagnostic)


def _reason_for(diagnostic, **row):
    return public_backends([{"id": "e", "reason": diagnostic, **row}])[0]["reason"]


@pytest.mark.parametrize(
    ("diagnostic", "one_click", "points_at"),
    [
        ("voxcpm package not installed.", True, "Model Catalogue"),
        ("voxcpm package not installed.", False, "guide"),
        ("file is missing", True, "Model Catalogue"),
        ("file is missing", False, "guide"),
    ],
)
def test_the_next_step_matches_whether_the_app_can_install_it(diagnostic, one_click, points_at):
    """Pointing at Model Catalogue for an engine with no Install button sent
    people to a page that could not help them."""
    reason = _reason_for(diagnostic, one_click_install=one_click)
    assert points_at in reason
    if not one_click:
        assert "Model Catalogue" not in reason


def test_rows_without_the_install_field_keep_the_catalogue_wording():
    # ASR / LLM / translation rows carry no one_click_install field.
    assert "Model Catalogue" in _reason_for("transformers not installed")
