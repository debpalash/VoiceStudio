"""Test isolation shared by every suite (``tests/`` and ``backend/tests/``).

Only fixtures that both suites need live here; each suite's own conftest.py
still owns its import path and hermetic data dir.
"""
import sys
import types

import pytest


@pytest.fixture(autouse=True)
def _clean_model_manager_shutdown_state(request):
    """Start every test with the model manager NOT in shutdown mode (#1269).

    ``model_manager._shutting_down`` is a module-global Event and the GPU pool is
    a module-global executor. Any test that runs the app lifespan flips both on
    the way out — ``begin_shutdown()`` plus ``_reset_gpu_pool()`` — and nothing
    puts them back, because in production that state is correct: the process is
    ending.

    Across a test session it is not correct, and it is not a cosmetic leak. A
    test that arrives with the flag set finds a shut-down executor, so its very
    first ``run_in_executor`` raises "cannot schedule new futures after
    shutdown" — which the preload path classifies as a benign shutdown and
    swallows. The symptom is a load that silently never starts:
    ``test_lifespan_shutdown_mid_load_is_clean_and_clears_sentinel`` failed on
    ``assert started.is_set()`` for exactly this reason, while passing alone.
    It is also why the post-generation offload tests failed after the dub tests
    (which exit the app lifespan) in ``tests/``: the offload stands down while
    the process is shutting down.

    This used to live in ``backend/tests/conftest.py`` only, so ``tests/`` —
    which CI runs in one process — never got it. It is here so every suite does.

    Reset before AND after: before so an inherited flag cannot decide this test,
    after so a test that legitimately shuts down does not hand the state on.

    Cleans the module in ``sys.modules``, the ``services`` package attribute,
    AND any module-typed alias the test module holds (``import
    services.model_manager as mm`` at module scope). Test modules bind that
    alias at COLLECTION time; ``tests/backend/**`` purges ``services.*`` from
    ``sys.modules`` after every test it owns, so in a combined run the alias and
    the live module can be different objects. Cleaning only one of them means a
    test dirties the alias and the next test reads it still dirty
    (``backend/tests/test_shutdown_state_isolation.py``). A module nobody has
    imported holds no state, so nothing is imported just to reset it.
    """

    def _targets():
        package = sys.modules.get("services")
        found = {
            id(m): m
            for m in (
                sys.modules.get("services.model_manager"),
                getattr(package, "model_manager", None),
            )
            if isinstance(m, types.ModuleType)
        }
        test_module = getattr(request, "module", None)
        if test_module is not None:
            for val in vars(test_module).values():
                if (isinstance(val, types.ModuleType)
                        and getattr(val, "__name__", "") == "services.model_manager"):
                    found[id(val)] = val
        return found.values()

    def _clean():
        # Deliberately NOT wrapped in try/except. A reset that fails silently
        # leaves the next test with stale shutdown or executor state, which is
        # precisely the order-dependent failure this fixture exists to remove —
        # swallowing the error would defeat the fixture while looking like it
        # worked (CodeRabbit). If either of these can raise, that is a real
        # problem in model_manager and it should be loud.
        for mod in _targets():
            mod.reset_shutdown_flag()
            mod._reset_gpu_pool()

    _clean()
    yield
    _clean()
