"""Shared cleanup for tests/backend — undo sys.modules surgery.

Several files in this tree (test_perf_settings.py, test_engine_spawn_token.py,
api/test_engines_route_shape.py, services/test_token_resolver.py, …) purge
``core`` / ``api`` / ``services`` from ``sys.modules`` and re-import them under
a per-test, monkeypatched ``OMNIVOICE_DATA_DIR``. monkeypatch restores the ENV
at teardown, but the re-imported modules stay cached — bound to the now-dead
tmp_path (``core.config`` freezes DB_PATH/VOICES_DIR at import time). Any
later test that lazily resolves those modules (e.g. a route handler doing
``from services import x`` at request time) then reads/writes a data dir that
no other part of that test uses: in combined ``pytest tests/ backend/tests/``
runs this broke backend/tests' personas import (voice file written into the
poisoned VOICES_DIR) and audiobook resume (job seeded in one DB, endpoint
reading another). CI's isolated invocations never see it; local combined runs
do.

The autouse teardown below restores the module objects that existed before
each test here (see ``tests/backend_module_state.py``). Purging alone left
fresh twins behind: test modules collected earlier kept the originals, so a
later test could patch ``core.db`` while ``services.dub_pipeline`` still wrote
through the original module (#2585 CI). Modules first imported under a test's
temporary environment are dropped, so the next consumer re-imports them
against the RESTORED env.
"""
import pytest

from backend_module_state import restore, snapshot


@pytest.fixture(autouse=True)
def _repurge_backend_modules_after_module_surgery():
    before = snapshot()
    yield
    restore(before)
