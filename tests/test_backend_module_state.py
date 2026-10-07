"""Suites that re-import backend modules must hand back the originals (#2585 CI).

``tests/backend`` and ``tests/smoke`` run before these files and re-import
``core`` / ``services`` under temporary data dirs. When they only purged on
teardown, modules collected earlier kept the originals while in-test imports
resolved fresh twins, so a patched ``core.db.DB_PATH`` never reached
``services.dub_pipeline.db_conn``.
"""
import importlib
import sys

import backend_module_state
import core.db as db_at_collection
from services import dub_pipeline as dub_pipeline_at_collection


def test_earlier_suites_leave_collection_time_modules_in_place():
    # Fails in a full `pytest tests/` run when an earlier suite leaves twins.
    assert sys.modules["core.db"] is db_at_collection
    assert importlib.import_module("core").db is db_at_collection
    assert importlib.import_module("services").dub_pipeline is dub_pipeline_at_collection
    assert dub_pipeline_at_collection.db_conn is db_at_collection.db_conn


def test_restore_reinstates_modules_and_package_bindings():
    import core

    before = backend_module_state.snapshot()
    try:
        backend_module_state.purge()
        fresh = importlib.import_module("core.db")
        assert fresh is not db_at_collection
        # A module first imported after the snapshot through an ORIGINAL package.
        sys.modules["core"] = core
        sys.modules["core._module_state_probe"] = probe = type(sys)("core._module_state_probe")
        core._module_state_probe = probe
    finally:
        backend_module_state.restore(before)

    assert sys.modules["core.db"] is db_at_collection
    from core import db
    assert db is db_at_collection
    assert "core._module_state_probe" not in sys.modules
    assert not hasattr(core, "_module_state_probe")
