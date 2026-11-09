"""Sync API client: request shapes, headers and error mapping (sync-api.md)."""
import asyncio
import json

import httpx
import pytest

from services.cloud_sync.client import CloudOff, SyncApiError, SyncClient

BASE = "https://appapi.voicestudio.sh/v1/sync"


def make(handler, token="tok", device="Dv7kQ2mZx9LpR4sT"):
    seen = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = SyncClient(BASE, "Or5tG7hJ9kL1zX3c", token=lambda: token, device_id=device,
                        transport=httpx.MockTransport(wrapped))
    return client, seen


def run(coro):
    return asyncio.run(coro)


def item(**over):
    base = {"collection": "settings", "item_id": "appearance", "revision": 57, "deleted": False,
            "schema_version": 1, "content": {"values": {"theme": "dark"}}, "blobs": []}
    return {**base, **over}


def test_put_sends_if_match_idempotency_device_and_bearer():
    client, seen = make(lambda r: httpx.Response(200, json=item()))
    result = run(client.put_item("settings", "appearance", 56, 1, {"values": {"theme": "dark"}}, "k" * 32))
    request = seen[0]
    assert request.method == "PUT"
    assert str(request.url) == f"{BASE}/organizations/Or5tG7hJ9kL1zX3c/items/settings/appearance"
    assert request.headers["authorization"] == "Bearer tok"
    assert request.headers["x-sync-device"] == "Dv7kQ2mZx9LpR4sT"
    assert request.headers["idempotency-key"] == "k" * 32
    assert request.headers["if-match"] == '"56"'
    assert json.loads(request.content) == {"schema_version": 1, "content": {"values": {"theme": "dark"}}, "blobs": []}
    assert result["revision"] == 57


def test_device_registration_sends_no_device_header():
    client, seen = make(lambda r: httpx.Response(201, json={"device": {"id": "Dv1"}}), device=None)
    device = run(client.register_device("Studio PC", "linux", "oss", "0.6.0", "k" * 32, os_version="Linux 6.12"))
    assert device == {"id": "Dv1"}
    assert "x-sync-device" not in seen[0].headers
    assert json.loads(seen[0].content) == {
        "name": "Studio PC", "platform": "linux", "app": "oss", "app_version": "0.6.0", "os_version": "Linux 6.12",
    }


def test_changes_query():
    client, seen = make(lambda r: httpx.Response(200, json={"changes": [], "cursor": 9, "has_more": False, "head": 9}))
    run(client.changes(since=4, collections=["settings", "favorites"]))
    assert seen[0].url.params["since"] == "4"
    assert seen[0].url.params["collections"] == "settings,favorites"
    assert seen[0].url.params["limit"] == "500"


def test_errors_map_to_code_details_and_retry_after():
    body = {"error": {"code": "revision_conflict", "message": "x", "request_id": "r1", "details": {"current": item(revision=58)}}}
    client, _ = make(lambda r: httpx.Response(409, json=body))
    with pytest.raises(SyncApiError) as caught:
        run(client.delete_item("settings", "appearance", 56, "k" * 32))
    assert (caught.value.status, caught.value.code) == (409, "revision_conflict")
    assert caught.value.details["current"]["revision"] == 58
    assert caught.value.request_id == "r1"

    client, _ = make(lambda r: httpx.Response(429, headers={"Retry-After": "7"},
                                              json={"error": {"code": "rate_limited", "details": {}}}))
    with pytest.raises(SyncApiError) as caught:
        run(client.usage())
    assert caught.value.retry_after == 7.0


def test_a_non_json_error_still_maps():
    client, _ = make(lambda r: httpx.Response(502, text="bad gateway"))
    with pytest.raises(SyncApiError) as caught:
        run(client.collections())
    assert (caught.value.status, caught.value.code) == (502, "http_502")


def test_no_token_means_no_request():
    client, seen = make(lambda r: httpx.Response(200, json={}), token=None)
    with pytest.raises(CloudOff):
        run(client.usage())
    assert seen == []


def test_a_rejected_token_is_replaced_once_then_the_call_retried():
    answers = [httpx.Response(401, json={"error": {"code": "unauthenticated"}}), httpx.Response(200, json={"used_bytes": 1})]
    asked = []

    def token(fresh=False):
        asked.append(fresh)
        return "fresh" if fresh else "stale"

    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        return answers.pop(0)

    client = SyncClient(BASE, "Or1", token=token, device_id="Dv1", transport=httpx.MockTransport(handler))
    assert run(client.usage()) == {"used_bytes": 1}
    assert asked == [False, True]
    assert seen == ["Bearer stale", "Bearer fresh"]


@pytest.mark.parametrize(
    "base,org",
    [
        ("http://evil.example/v1/sync", "Or1"),
        ("file:///etc/passwd", "Or1"),
        ("https://appapi.voicestudio.sh/v1/sync", "../admin"),
        ("https://appapi.voicestudio.sh/v1/sync", "Or1/x"),
    ],
)
def test_only_https_or_loopback_endpoints_and_base62_organizations(base, org):
    with pytest.raises(ValueError):
        SyncClient(base, org, token=lambda: "t")


def test_local_development_endpoint_is_allowed():
    client = SyncClient("http://127.0.0.1:8090/v1/sync", "Or5tG7hJ9kL1zX3c", token=lambda: "t")
    assert client.base == "http://127.0.0.1:8090/v1/sync/organizations/Or5tG7hJ9kL1zX3c"


@pytest.mark.parametrize("base", [
    "https://appapi.voicestudio.sh/v1/sync",
    "https://dev-appapi.voicestudio.sh/v1/sync",
    "http://127.0.0.1:8090/v1/sync",
])
def test_the_known_sync_endpoints_are_accepted(base):
    assert SyncClient(base, "Or1", token=lambda: "t").base.startswith(base + "/organizations/")


def test_any_other_https_host_is_refused():
    with pytest.raises(ValueError):
        SyncClient("https://attacker.example/v1/sync", "Or1", token=lambda: "t")
