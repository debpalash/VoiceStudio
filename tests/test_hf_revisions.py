"""Curated Hugging Face inputs are immutable and repair preserves installs."""
from pathlib import Path

import yaml

from services import hf_revisions


def test_every_catalog_repo_has_an_immutable_revision():
    catalog = yaml.safe_load(Path("backend/config/models.yaml").read_text(encoding="utf-8"))
    # Dependency repos are downloaded by the installer exactly like top-level
    # ones (setup/download.py resolves `revision_for(dependency["repo_id"])`),
    # and `revision_for` raises on an unpinned repo — so an unpinned dependency
    # ships an install that always fails. Pin them under the same rule (#2163).
    curated = []
    for model in catalog["models"]:
        curated.append(model["repo_id"])
        for dependency in model.get("dependencies") or ():
            curated.append(dependency["repo_id"])
    missing = {
        repo_id for repo_id in curated if repo_id not in hf_revisions.CURATED_REVISIONS
    }
    assert missing == set()
    assert all(len(revision) == 40 for revision in hf_revisions.CURATED_REVISIONS.values())
    assert all(int(revision, 16) >= 0 for revision in hf_revisions.CURATED_REVISIONS.values())


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
