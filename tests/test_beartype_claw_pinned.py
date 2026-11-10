"""beartype's import-hook state must survive ``patch.dict("sys.modules")`` blocks.

Without it pinned at session start, importing ``pocket_tts`` inside such a block
left beartype's global import hook installed but its modules gone, so every later
new import on that worker failed with a circular ``claw_state`` ImportError.
"""
import sys
from unittest.mock import patch

import pytest


def test_claw_state_is_loaded_before_any_snapshot():
    pytest.importorskip("beartype")
    assert "claw_state" in vars(sys.modules["beartype.claw._clawstate"])


def test_claw_state_survives_a_sys_modules_restore():
    pytest.importorskip("beartype")
    with patch.dict(sys.modules):
        import beartype.claw._clawstate  # noqa: F401
    assert "claw_state" in vars(sys.modules["beartype.claw._clawstate"])
