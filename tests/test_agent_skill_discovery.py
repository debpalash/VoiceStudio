from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_installs_skills_from_the_canonical_repository() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "npx skills add debpalash/VoiceStudio" in readme
    assert "npx skills add debpalash/omnivoice-studio" not in readme


def test_public_skill_surfaces_use_current_identity_and_license() -> None:
    canonical = (ROOT / "skills/voicestudio/SKILL.md").read_text(encoding="utf-8")
    claude = (ROOT / ".claude/skills/omnivoice/LEGACY.md").read_text(encoding="utf-8")

    for skill in (canonical, claude):
        assert "github.com/debpalash/VoiceStudio" in skill
        assert "FSL-1.1" not in skill

    # The archived entry keeps only its pointer; the old helper scripts are gone.
    assert not (ROOT / ".claude/skills/omnivoice/scripts").exists()


def test_published_skills_have_unique_branded_names() -> None:
    import yaml

    paths = list((ROOT / "skills").glob("*/SKILL.md"))
    names = []
    for path in paths:
        metadata = yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1])
        assert metadata["name"] == path.parent.name
        assert metadata["description"]
        names.append(metadata["name"])
    assert set(names) == {"voicestudio", "voicestudio-maintainer"}
    assert len(names) == len(set(names))
    assert not (ROOT / ".claude/skills/omnivoice/SKILL.md").exists()
