"""The gallery submodule pin may only move forward along the gallery's main.

#3447 committed a stale submodule and reverted the pin from the #3438 bump to
the seed commit; these build a tiny gallery history and replay that move.
"""
import importlib.util
import subprocess
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "gallery_pin", Path(__file__).resolve().parents[1] / ".github" / "scripts" / "gallery_pin.py"
)
gallery_pin = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gallery_pin)


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _repo(tmp_path):
    repo = tmp_path / "gallery"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    shas = []
    for n in range(3):
        (repo / "manifest.json").write_text(str(n))
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", f"c{n}")
        shas.append(_git(repo, "rev-parse", "HEAD"))
    _git(repo, "checkout", "-q", "-b", "side", shas[1])
    (repo / "x").write_text("x")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "side")
    side = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    return repo, shas, side


def test_forward_move_on_main_passes(tmp_path):
    repo, (seed, mid, tip), _ = _repo(tmp_path)
    assert gallery_pin.check(seed, tip, str(repo), "main") is None
    assert gallery_pin.check(tip, tip, str(repo), "main") is None


def test_stale_pointer_moving_backwards_is_refused(tmp_path):
    repo, (seed, mid, tip), _ = _repo(tmp_path)
    problem = gallery_pin.check(tip, seed, str(repo), "main")
    assert problem and "backwards" in problem


def test_unmerged_branch_commit_is_refused(tmp_path):
    repo, (seed, mid, tip), side = _repo(tmp_path)
    assert "not on the gallery's main" in gallery_pin.check(seed, side, str(repo), "main")


def test_unknown_commit_is_refused(tmp_path):
    repo, (seed, mid, tip), _ = _repo(tmp_path)
    assert "not a commit" in gallery_pin.check(seed, "f" * 40, str(repo), "main")
