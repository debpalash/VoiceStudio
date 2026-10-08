"""The autouse shutdown-state reset must actually reset, and fail loudly.

``conftest._clean_model_manager_shutdown_state`` is the fix for #1269: a test
that runs the app lifespan leaves ``model_manager._shutting_down`` set and the
GPU pool torn down, and the next test then finds every ``run_in_executor``
raising "cannot schedule new futures after shutdown" — swallowed by the preload
path as a benign shutdown, so the symptom is a load that silently never starts.

A fixture with nothing asserting it is a fixture nobody notices breaking. The
two pairs below are deliberately order-dependent (pytest runs tests in
definition order within a file): the first test of each pair dirties exactly the
state the lifespan dirties, the second asserts it arrived clean. Delete the
fixture and the second test fails; that is the fail-before/pass-after this file
exists to provide.

The second pair covers the stale-alias half of the fixture. Test modules bind
``import services.model_manager as mm`` at COLLECTION time, and ``tests/backend/
**`` purges ``services.*`` from ``sys.modules`` after every test it owns — so in
a combined ``pytest tests/ backend/tests/`` run the alias and the live module
are two different objects, and a fixture that cleans only ``sys.modules`` leaves
the alias dirty. That is not hypothetical: it is how ``test_next_test_starts_
clean`` below failed in combined runs while passing alone. The pair fabricates
the duplicate module explicitly so the condition reproduces in isolation.
"""

import importlib
import os
import sys

import services.model_manager as mm

# The fixture lives in the repository-root conftest.py so tests/ shares it.
_CONFTEST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "conftest.py"
)


def test_dirty_the_shutdown_state():
    """Stand in for any lifespan-running test: leave the module globals in the
    exact state graceful shutdown leaves them."""
    mm.begin_shutdown()
    mm._reset_gpu_pool()
    assert mm.is_shutting_down()


def test_next_test_starts_clean():
    """Runs immediately after the test above and must not inherit its state."""
    assert not mm.is_shutting_down(), (
        "the shutdown flag leaked from the previous test — the autouse fixture "
        "in the root conftest.py is not resetting it, and every executor "
        "submit in this test will now raise 'cannot schedule new futures after "
        "shutdown' and be misread as a benign cancellation (#1269)"
    )


# Bound by the test below to a SECOND copy of services.model_manager — the
# stand-in for the alias a sibling suite's sys.modules purge strands. Module
# scope on purpose: the fixture discovers cleanup targets by scanning this
# module's globals, exactly as it discovers a real `import ... as mm` alias.
stale_mm = None


def test_dirty_a_stale_module_alias():
    """Leave a *duplicate* model_manager module in the post-shutdown state."""
    global stale_mm
    live = sys.modules.pop("services.model_manager")
    try:
        stale_mm = importlib.import_module("services.model_manager")
    finally:
        sys.modules["services.model_manager"] = live
        # Re-point the PACKAGE attribute too. `import services.model_manager as
        # mod` reads it (not sys.modules) for the `as` binding, so leaving it on
        # the duplicate would hand the fixture the very module this test just
        # stranded — the guard would pass for the wrong reason.
        sys.modules["services"].model_manager = live
    assert stale_mm is not live, "expected a genuinely distinct module object"
    assert importlib.import_module("services.model_manager") is live

    stale_mm.begin_shutdown()
    stale_mm._reset_gpu_pool()
    assert stale_mm.is_shutting_down()


def test_stale_module_alias_starts_clean():
    """Runs immediately after the test above. The fixture has to reach the
    alias, not just ``sys.modules["services.model_manager"]``."""
    assert stale_mm is not None, "the previous test did not run — check ordering"
    assert not stale_mm.is_shutting_down(), (
        "the shutdown flag leaked on a stale duplicate of services.model_manager "
        "— the autouse fixture in the root conftest.py is only cleaning the "
        "module in sys.modules, so in a combined `pytest tests/ backend/tests/` "
        "run every backend/tests module that holds an `import services."
        "model_manager as mm` alias starts dirty (#1269)"
    )


def test_reset_failures_are_not_swallowed():
    """A reset wrapped in ``except: pass`` hands the next test stale state while
    reporting success — the fixture would look like it worked and #1269 would
    come back with the evidence removed. Mechanical, so it stays true."""
    with open(_CONFTEST, encoding="utf-8") as fh:
        src = fh.read()
    marker = "def _clean_model_manager_shutdown_state("
    assert marker in src, f"fixture renamed or removed from {_CONFTEST}"
    body = src.split(marker, 1)[1].split("\n@", 1)[0]
    # Comments and the docstring explain *why* there is no try/except; only the
    # code is evidence of whether there is one.
    code = "\n".join(
        line for line in body.split('"""')[-1].splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "except" not in code, (
        "the shutdown-state reset catches exceptions; a failed reset must fail "
        "the test that caused it, not leak into the next one:\n" + code
    )
