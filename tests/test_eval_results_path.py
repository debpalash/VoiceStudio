"""Every eval harness must accept a bare ``--decode-path`` filename.

``os.makedirs(os.path.dirname("results.txt"))`` calls ``makedirs("")`` and raises
``FileNotFoundError``, so the documented results-file mode failed with a traceback
unless the path had a directory component. Seven harnesses carried the same copy
of it; ``omnivoice/eval`` is in ``norecursedirs`` (pyproject.toml), so nothing
could fail.

The scripts are run by path (``python3 omnivoice/eval/wer/fleurs.py``), which puts
only their own directory on ``sys.path``, so the guard is inlined instead of
imported from the package.
"""

import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
EVAL_SCRIPTS = [
    REPO / "omnivoice/eval/mos/utmos.py",
    REPO / "omnivoice/eval/speaker_similarity/sim.py",
    REPO / "omnivoice/eval/wer/fleurs.py",
    REPO / "omnivoice/eval/wer/hubert.py",
    REPO / "omnivoice/eval/wer/minimax.py",
    REPO / "omnivoice/eval/wer/seedtts.py",
    REPO / "omnivoice/eval/wer/sensevoice.py",
]


def test_the_unguarded_shape_cannot_open_a_bare_filename(tmp_path, monkeypatch):
    """The failure the guard removes, kept as the reason it exists."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError):
        os.makedirs(os.path.dirname("results.txt"), exist_ok=True)


@pytest.mark.parametrize("script", EVAL_SCRIPTS, ids=lambda p: p.name)
def test_eval_harness_creates_the_results_directory_only_when_there_is_one(script):
    assert script.is_file(), f"{script} moved; update this list"
    source = script.read_text(encoding="utf-8")
    assert "os.makedirs(os.path.dirname(" not in source, (
        f"{script.name} still calls makedirs(dirname(...)), which raises for a bare filename"
    )
    assert "parent = os.path.dirname(args.decode_path)" in source
    assert "if parent:" in source