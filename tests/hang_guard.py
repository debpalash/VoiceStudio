"""The shared ceiling for awaits that only guard against a hang.

A hang guard says "this must finish", not "this must finish fast". The worker
control plane's admission, commit, staging and fetch paths are durability
barriers (real fsyncs of files and their directories) and hops through the
default thread pool, so a one-second guard measured the disk and the machine,
not the product. With other suites writing to the same disk, two fsyncs past
0.5 s each timed an upload commit out and
`test_concurrent_uploads_cannot_share_one_attempt_partial` flaked in full
local runs. A correct run still returns in milliseconds; only a real hang
pays this ceiling.

A blocking fake's watchdog (the ``release.wait(N)`` that frees a barrier the
test forgot) must outlast the hang guard. If it did not, "the event loop
stalled on this barrier" would become a stall that ends after N seconds, and a
long hang guard would accept it. With the watchdog longer, a stalled loop
still fails the hang guard.

Use a short literal only where the timeout firing is the outcome under test
(``pytest.raises(asyncio.TimeoutError)``, an ``except TimeoutError`` the test
expects to take, ``assert not event.wait(...)``).
``tests/test_worker_hang_guards.py`` enforces both constants in the worker
suites.
"""

HANG_GUARD_S = 15.0
BARRIER_WATCHDOG_S = 2 * HANG_GUARD_S
