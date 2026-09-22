"""Persisted settings must not leak from one test into the next.

The hermetic data dir is shared by the whole session, so before the
`_isolate_persisted_settings` guard in conftest.py any pref a test persisted —
directly or via the app lifespan's startup profile reconciliation — steered
every later test. That is how a developer with a sherpa dictation model in
their HF cache saw `test_versioned_stream_has_session_envelope` routed to the
sherpa handler in full runs while CI stayed green.

The tests run in file order: the first writes, the second must not see it.
"""
from __future__ import annotations

import importlib
import os


_LEAK_KEY = "dictation.model_id"
_LEAK_ENV = "OMNIVOICE_TEST_SETTINGS_LEAK"


def _prefs():
    # Resolve at call time: other suites purge and re-import core.* modules,
    # and the guard protects whichever module is live in sys.modules.
    return importlib.import_module("core.prefs")


def _user_env():
    return importlib.import_module("core.user_env")


def test_a_test_persists_a_pref_and_a_user_env_var():
    assert _prefs()._PREFS_PATH == os.path.join(
        os.environ["OMNIVOICE_DATA_DIR"], "prefs.json"
    )
    _prefs().set_(_LEAK_KEY, "sherpa-leaked-by-an-earlier-test")
    _user_env().set_user_env(_LEAK_ENV, "leaked")
    assert _prefs().get(_LEAK_KEY) == "sherpa-leaked-by-an-earlier-test"
    assert _user_env().get_user_env(_LEAK_ENV) == "leaked"


def test_the_next_test_starts_from_the_same_persisted_settings():
    assert _prefs().get(_LEAK_KEY) is None
    assert _user_env().get_user_env(_LEAK_ENV) is None


def test_a_test_leaves_prefs_bound_to_its_own_tmp_dir(tmp_path):
    # The shape of a first `core.prefs` import inside a test that pointed
    # OMNIVOICE_DATA_DIR at tmp_path: the module-level path is frozen there
    # and nothing undoes it.
    _prefs()._PREFS_PATH = str(tmp_path / "prefs.json")
    _prefs().set_(_LEAK_KEY, "sherpa-in-a-dead-tmp-dir")


def test_the_next_test_uses_the_session_prefs_store_again():
    assert _prefs()._PREFS_PATH == os.path.join(
        os.environ["OMNIVOICE_DATA_DIR"], "prefs.json"
    )
    assert _prefs().get(_LEAK_KEY) is None
