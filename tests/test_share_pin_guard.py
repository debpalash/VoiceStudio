# tests/test_share_pin_guard.py
"""Wrong guesses of the Network sharing PIN are limited (VC-CVD-01892-V)."""
from fastapi.testclient import TestClient

from services import network_share as ns
from services import pin_guard as pg


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _guard():
    clock = _Clock()
    return pg.PinGuard(clock=clock), clock


def test_a_client_is_locked_out_after_five_wrong_pins_and_the_lock_doubles():
    guard, clock = _guard()
    for _ in range(pg.CLIENT_FREE_FAILURES - 1):
        guard.failed("10.0.0.5", "123456")
    assert guard.retry_after("10.0.0.5", "123456") == 0
    guard.failed("10.0.0.5", "123456")
    assert guard.retry_after("10.0.0.5", "123456") == pg.CLIENT_LOCK_S
    assert guard.retry_after("10.0.0.6", "123456") == 0  # another client is not locked
    clock.now += pg.CLIENT_LOCK_S
    assert guard.retry_after("10.0.0.5", "123456") == 0
    for _ in range(pg.CLIENT_FREE_FAILURES):
        guard.failed("10.0.0.5", "123456")
    assert guard.retry_after("10.0.0.5", "123456") == 2 * pg.CLIENT_LOCK_S


def test_the_lock_never_exceeds_an_hour():
    guard, clock = _guard()
    for _ in range(20):
        for _ in range(pg.CLIENT_FREE_FAILURES):
            guard.failed("10.0.0.5", "123456")
        wait = guard.retry_after("10.0.0.5", "123456")
        assert wait <= pg.CLIENT_LOCK_MAX_S
        clock.now += wait
    for _ in range(pg.CLIENT_FREE_FAILURES):
        guard.failed("10.0.0.5", "123456")
    assert guard.retry_after("10.0.0.5", "123456") == pg.CLIENT_LOCK_MAX_S


def test_failures_older_than_the_window_are_forgotten():
    guard, clock = _guard()
    for _ in range(pg.CLIENT_FREE_FAILURES - 1):
        guard.failed("10.0.0.5", "123456")
    clock.now += pg.WINDOW_S + 1
    guard.failed("10.0.0.5", "123456")
    assert guard.retry_after("10.0.0.5", "123456") == 0


def test_guesses_spread_over_many_addresses_lock_out_everyone_who_has_not_entered_the_pin():
    guard, _ = _guard()
    guard.passed("10.0.0.2", "123456")
    for i in range(pg.GLOBAL_FAILURES):
        guard.failed(f"10.0.1.{i}", "123456")
    assert guard.retry_after("10.0.9.9", "123456") == pg.GLOBAL_LOCK_S
    assert guard.retry_after("10.0.0.2", "123456") == 0  # already in keeps working


def test_ipv6_clients_are_counted_by_their_64():
    guard, _ = _guard()
    for i in range(pg.CLIENT_FREE_FAILURES):
        guard.failed(f"2001:db8:1:2::{i + 1}", "123456")
    assert guard.retry_after("2001:db8:1:2::ffff", "123456") > 0
    assert guard.retry_after("2001:db8:1:3::1", "123456") == 0


def test_a_new_pin_starts_again():
    guard, _ = _guard()
    for _ in range(pg.CLIENT_FREE_FAILURES):
        guard.failed("10.0.0.5", "123456")
    assert guard.retry_after("10.0.0.5", "654321") == 0


def _app_with_pin(pin):
    from main import app

    app.state.network_share = ns.ShareState(enabled=True, share_port=3901, pin=pin, lan_addresses=["10.0.0.9"])
    return app


def teardown_function():
    from main import app

    app.state.network_share = ns.ShareState()


def test_the_share_refuses_a_locked_out_client_even_with_the_right_pin():
    c = TestClient(_app_with_pin("654321"), client=("10.0.0.5", 1))
    for guess in range(pg.CLIENT_FREE_FAILURES):
        assert c.get("/api/voices", headers={"X-OmniVoice-Pin": f"00000{guess}"}).status_code == 401
    locked = c.get("/api/voices", headers={"X-OmniVoice-Pin": "654321"})
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0
    assert "654321" not in locked.text
    other = TestClient(_app_with_pin("654321"), client=("10.0.0.6", 1))
    assert other.get("/api/voices", headers={"X-OmniVoice-Pin": "654321"}).status_code != 401


def test_requests_without_a_pin_are_not_counted_as_guesses():
    c = TestClient(_app_with_pin("654321"), client=("10.0.0.5", 1))
    for _ in range(pg.CLIENT_FREE_FAILURES * 3):
        assert c.get("/api/voices").status_code == 401
    assert c.get("/api/voices", headers={"X-OmniVoice-Pin": "654321"}).status_code not in (401, 429)


def test_loopback_is_never_counted_or_locked():
    c = TestClient(_app_with_pin("654321"), client=("127.0.0.1", 1))
    for _ in range(pg.CLIENT_FREE_FAILURES * 2):
        c.get("/system/info", headers={"X-OmniVoice-Pin": "000000"})
    assert c.get("/system/info").status_code == 200
