"""Registration signs a challenge the control plane issued, once.

The worker used to pick its own challenge, so a recorded Register verified for
as long as the key was enrolled. Now WorkerService.IssueChallenge (outbound)
or the Attach metadata (inbound) supplies a single-use, expiring value, and a
peer that cannot do this is refused by name instead of failing obscurely.
"""
from __future__ import annotations

import sqlite3

import grpc
import pytest
import pytest_asyncio

from worker import identity
from worker.identity import ChallengeBook, WorkerKeypair
from worker.pool import WorkerPool
from worker.protocol.gen import worker_v1_pb2 as pb
from worker.scheduler import Scheduler
from worker.transport import codec
from worker.transport.client import TerminalRegistrationError, WorkerClient, WorkerConfig
from worker.transport.server import (
    PROTOCOL_VERSION,
    REQUIRED_FEATURES,
    WorkerServicer,
)


@pytest.fixture
def db(tmp_path, monkeypatch):
    from worker import registry as reg

    db_globals = reg.db_conn.__wrapped__.__globals__
    path = str(tmp_path / "userdata.db")
    with sqlite3.connect(path) as conn:
        conn.executescript(db_globals["_BASE_SCHEMA"])
    monkeypatch.setitem(db_globals, "DB_PATH", path)
    return path


class _Context:
    def peer(self) -> str:
        return "ipv4:127.0.0.1:5555"

    def invocation_metadata(self):
        return ()


def _request(keypair, *, challenge, worker_id="", epoch=0, token="", features=None):
    nonce = identity.new_challenge()
    return pb.RegisterRequest(
        features=sorted(REQUIRED_FEATURES if features is None else features),
        envelope=pb.Envelope(sequence=epoch),
        protocol_version_min=PROTOCOL_VERSION,
        protocol_version_max=PROTOCOL_VERSION,
        enrollment_token=token,
        worker_id=worker_id,
        public_key=keypair.public_bytes(),
        challenge=challenge,
        challenge_signature=keypair.sign(
            identity.challenge_message(
                challenge=challenge, worker_id=worker_id, session_epoch=epoch, nonce=nonce
            )
        ),
        nonce=nonce,
        key_id=keypair.key_id,
        host=codec.host_to_pb({"hostname": "gpu2", "os": "linux", "arch": "x86_64"}),
        max_concurrent_tasks=1,
    )


@pytest_asyncio.fixture
async def enrolled(tmp_path, db):
    """A servicer and a worker that has enrolled once."""
    from worker import registry

    pool = WorkerPool()
    servicer = WorkerServicer(
        Scheduler(pool, persist=False), pool, artifact_dir=str(tmp_path / "artifacts")
    )
    keypair = WorkerKeypair.generate()
    token = registry.create_enrollment(endpoint="localhost:1", cert_fingerprint="fp")
    issued = await servicer.IssueChallenge(pb.ChallengeRequest(), _Context())
    response = await servicer.Register(
        _request(keypair, challenge=issued.challenge, token=token.encode()), _Context()
    )
    assert not response.error.code, response.error.message
    return servicer, keypair, response.worker_id


async def _issued(servicer) -> bytes:
    return (await servicer.IssueChallenge(pb.ChallengeRequest(), _Context())).challenge


@pytest.mark.asyncio
async def test_a_recorded_register_cannot_be_replayed(enrolled):
    servicer, keypair, worker_id = enrolled
    frame = _request(keypair, challenge=await _issued(servicer), worker_id=worker_id)

    first = await servicer.Register(frame, _Context())
    replayed = await servicer.Register(frame, _Context())

    assert not first.error.code, first.error.message
    assert replayed.error.code == "CHALLENGE_EXPIRED"
    assert not replayed.session_token


@pytest.mark.asyncio
async def test_a_self_chosen_challenge_proves_nothing(enrolled):
    servicer, keypair, worker_id = enrolled
    frame = _request(keypair, challenge=identity.new_challenge(), worker_id=worker_id)

    response = await servicer.Register(frame, _Context())

    assert response.error.code == "CHALLENGE_EXPIRED"
    assert not response.session_token


@pytest.mark.asyncio
async def test_a_worker_without_server_challenges_is_told_to_update(enrolled):
    servicer, keypair, worker_id = enrolled
    features = set(REQUIRED_FEATURES) - {identity.SERVER_CHALLENGE_FEATURE}
    frame = _request(
        keypair, challenge=identity.new_challenge(), worker_id=worker_id, features=features
    )

    response = await servicer.Register(frame, _Context())

    assert response.error.code == "UPGRADE_REQUIRED"
    assert identity.SERVER_CHALLENGE_FEATURE in response.error.message
    assert "Update" in response.error.message


def test_a_challenge_is_single_use_and_expires():
    book = ChallengeBook(ttl_seconds=60)
    spent = book.issue(now=1000.0)
    late = book.issue(now=1000.0)

    assert book.consume(spent, now=1001.0)
    assert not book.consume(spent, now=1001.0)
    assert not book.consume(late, now=1060.0)
    assert not book.consume(b"", now=1000.0)


def test_unanswered_challenges_cannot_grow_without_bound():
    book = ChallengeBook(ttl_seconds=60, limit=3)
    oldest = book.issue(now=0.0)
    for _ in range(3):
        newest = book.issue(now=1.0)

    assert not book.consume(oldest, now=2.0)
    assert book.consume(newest, now=2.0)


# ── The worker side ────────────────────────────────────────────────────────


def _client():
    return WorkerClient(
        WorkerConfig(
            endpoint="unused", cert_fingerprint="", certificate_pem=b"",
            keypair=WorkerKeypair.generate(), worker_id="w1",
        ),
        execute=lambda _assignment: None,
        capability_probe=lambda: [],
    )


class _Stub:
    def __init__(self, issue):
        self._issue = issue
        self.sent = None

    async def IssueChallenge(self, request):
        return self._issue()

    async def Register(self, request):
        self.sent = request
        return pb.RegisterResponse()


class _Unimplemented(grpc.aio.AioRpcError):
    def __init__(self):
        super().__init__(
            grpc.StatusCode.UNIMPLEMENTED, grpc.aio.Metadata(), grpc.aio.Metadata()
        )


@pytest.mark.asyncio
async def test_the_worker_signs_the_challenge_the_server_issued():
    client = _client()
    issued = identity.new_challenge()
    stub = _Stub(lambda: pb.ChallengeResponse(challenge=issued))

    await client._register(stub)

    sent = stub.sent
    assert sent.challenge == issued
    assert identity.SERVER_CHALLENGE_FEATURE in sent.features
    assert identity.verify_signature(
        sent.public_key,
        identity.challenge_message(
            challenge=issued, worker_id="w1", session_epoch=sent.envelope.sequence,
            nonce=sent.nonce,
        ),
        sent.challenge_signature,
    )


@pytest.mark.asyncio
async def test_the_worker_still_reaches_an_older_control_plane():
    def unimplemented():
        raise _Unimplemented()

    stub = _Stub(unimplemented)

    await _client()._register(stub)

    assert len(stub.sent.challenge) == 32


@pytest.mark.asyncio
async def test_an_expired_challenge_is_retried_not_treated_as_a_verdict():
    refusal = pb.RegisterResponse(error=pb.Error(code="CHALLENGE_EXPIRED", message="x"))

    with pytest.raises(RuntimeError) as raised:
        await _client().accept_registration(refusal)

    assert not isinstance(raised.value, TerminalRegistrationError)
