"""Curated Hugging Face inputs are immutable and repair preserves installs."""
from pathlib import Path

import pytest
import yaml

from services import hf_revisions


def test_catalog_repos_and_dependencies_all_have_full_sha_pins():
    cfg = Path(__file__).resolve().parents[1] / "backend" / "config" / "models.yaml"
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    entries = data["models"] if isinstance(data, dict) else data
    repos = set()
    for entry in entries:
        if entry.get("repo_id"):
            repos.add(entry["repo_id"])
        for dep in entry.get("dependencies") or []:
            repos.add(dep["repo_id"])
    unpinned = sorted(repos - set(hf_revisions.CURATED_REVISIONS))
    assert not unpinned, f"catalogue repos without a pinned revision: {unpinned}"
    for repo, rev in hf_revisions.CURATED_REVISIONS.items():
        assert len(rev) == 40, repo
        int(rev, 16)  # raises ValueError if not hex


def test_nllb_components_use_the_reviewed_revision():
    from api.routers import dub_translate

    calls = []

    class FakeFactory:
        @classmethod
        def from_pretrained(cls, repo_id, **kwargs):
            calls.append((repo_id, kwargs))
            return object()

    dub_translate._load_nllb_component(FakeFactory)

    repo_id = "facebook/nllb-200-distilled-600M"
    assert calls == [
        (
            repo_id,
            {
                "revision": hf_revisions.revision_for(repo_id),
                "local_files_only": True,
            },
        )
    ]


def test_installed_revision_round_trips_for_repair(tmp_path):
    repo_id = "k2-fsa/OmniVoice"
    installed = "f" * 40
    hf_revisions.remember_revision(repo_id, installed, str(tmp_path))
    assert hf_revisions.installed_revision(repo_id, str(tmp_path)) == installed


def test_missing_or_invalid_marker_falls_back_to_reviewed_pin(tmp_path):
    repo_id = "k2-fsa/OmniVoice"
    assert hf_revisions.installed_revision(repo_id, str(tmp_path)) == hf_revisions.revision_for(repo_id)
    marker = tmp_path / "models--k2-fsa--OmniVoice" / "voicestudio-revision"
    marker.parent.mkdir(parents=True)
    marker.write_text("main\n", encoding="ascii")
    assert hf_revisions.installed_revision(repo_id, str(tmp_path)) == hf_revisions.revision_for(repo_id)


def test_existing_hub_main_ref_is_preserved_for_upgrade_repair(tmp_path):
    repo_id = "k2-fsa/OmniVoice"
    existing = "e" * 40
    ref = tmp_path / "models--k2-fsa--OmniVoice" / "refs" / "main"
    ref.parent.mkdir(parents=True)
    ref.write_text(existing + "\n", encoding="ascii")
    assert hf_revisions.installed_revision(repo_id, str(tmp_path)) == existing


def test_unknown_repo_cannot_start_a_network_repair(tmp_path):
    repo_dir = tmp_path / "models--attacker--unreviewed"
    for marker in (repo_dir / "voicestudio-revision", repo_dir / "refs" / "main"):
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("a" * 40 + "\n", encoding="ascii")
    try:
        hf_revisions.installed_revision("attacker/unreviewed", str(tmp_path))
    except ValueError as exc:
        assert "No reviewed revision" in str(exc)
    else:  # pragma: no cover - assertion message is clearer than pytest.raises here
        raise AssertionError("unreviewed repository unexpectedly received a revision")


def test_non_ascii_marker_falls_back_instead_of_raising(tmp_path):
    """#2634: an undecodable marker must not block the next marker or the pin."""
    repo_id = "k2-fsa/OmniVoice"
    repo_dir = tmp_path / "models--k2-fsa--OmniVoice"
    (repo_dir / "refs").mkdir(parents=True)
    (repo_dir / "voicestudio-revision").write_bytes(b"\xff\xfe" + b"a" * 40)
    existing = "e" * 40
    (repo_dir / "refs" / "main").write_text(existing + "\n", encoding="ascii")
    assert hf_revisions.installed_revision(repo_id, str(tmp_path)) == existing
    (repo_dir / "refs" / "main").write_bytes(b"\x80\x81")
    assert hf_revisions.installed_revision(repo_id, str(tmp_path)) == hf_revisions.revision_for(repo_id)


def test_curated_revisions_survives_unreadable_registry(tmp_path):
    """#2882: a corrupt or truncated curated-registry file must not crash
    the import that populates :data:`CURATED_REVISIONS`. The same fallback
    policy that 1f28c05 applied to per-marker reads (treat as absent, log,
    let the next layer fail with a precise diagnostic) covers the
    registry read too.

    Three failure modes are covered: a non-UTF-8 byte sequence (raises
    ``UnicodeDecodeError``), a truncated JSON file (raises
    ``json.JSONDecodeError``), and a missing file (raises ``OSError``).
    Each must return an empty mapping without raising.
    """

    # Non-UTF-8 bytes: a stray BOM or binary fragment left by an interrupted
    # write or a write through some text/binary mode mix-up.
    bad_bytes = tmp_path / "model_licenses.json"
    bad_bytes.write_bytes(b"\xff\xfe" + b"{\"models\": []}")
    assert hf_revisions._curated_revisions(bad_bytes) == {}

    # Truncated JSON: the file looks like text but stops mid-record.
    truncated = tmp_path / "model_licenses.json"
    truncated.write_text('{"models": [', encoding="utf-8")
    assert hf_revisions._curated_revisions(truncated) == {}

    # Missing file: the registry is not present at all (e.g. a stripped
    # install or a path the user moved).
    missing = tmp_path / "missing_model_licenses.json"
    assert hf_revisions._curated_revisions(missing) == {}


@pytest.mark.parametrize("body", ["null", "[]", '"x"', '{"models": null}', '{"models": {}}', "{}"])
def test_curated_revisions_survives_unexpected_registry_shape(tmp_path, body):
    path = tmp_path / "shape_model_licenses.json"
    path.write_text(body, encoding="utf-8")
    assert hf_revisions._curated_revisions(path) == {}


def test_curated_revisions_warning_omits_absolute_path(tmp_path, caplog):
    path = tmp_path / "secret_dir" / "model_licenses.json"
    with caplog.at_level("WARNING", logger="omnivoice.hf_revisions"):
        assert hf_revisions._curated_revisions(path) == {}
    assert str(tmp_path) not in caplog.text
    assert "model_licenses.json" in caplog.text
