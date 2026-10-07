"""Remote worker management API.

Deliberately small. The council's warning about the original design was that
seven strategies times three execution modes times priorities times weights
times per-model concurrency is a configuration surface nobody can test and
every knob is a compatibility promise forever. So this exposes what a user
actually needs to run their other GPU: see workers, add one, name it, prefer
one, pause one, remove one.

Two things here are not conveniences and must not be softened:

  * **Consent is explicit and per worker.** Audio, reference voices, and text
    leave the machine for a worker, so each one is approved individually. There
    is no global "trust all workers".
  * **A token is shown exactly once.** Only its hash is stored, so it cannot be
    re-displayed — which is the point.

One endpoint here is not part of that surface: `POST /workers/tasks` submits a
single task and waits for it, and exists only because the scheduler otherwise
has no caller at all outside the tests. It is marked dev-only everywhere it
appears and is replaced by the GPU gateway.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.dependencies import require_admin
from core.poll_guard import PollGuard
from worker import registry, routing, service
from worker.async_utils import drain_task, to_thread_and_defer_cancellation

logger = logging.getLogger("omnivoice.worker")

# How often an awaiting request checks whether its caller is still there.
# Starlette does not cancel a handler when the client hangs up, so polling is
# the only way the "cancel what nobody is waiting for" rule can fire before
# the task's own deadline does.
_DISCONNECT_POLL_SECONDS = 1.0

# Management is admin-gated: these endpoints mint join tokens and revoke
# machines, so Docker writes require the API key while desktop stays loopback.
router = APIRouter(prefix="/workers", tags=["workers"], dependencies=[Depends(require_admin)])


class EnableRequest(BaseModel):
    enabled: bool


class EnrollRequest(BaseModel):
    label: str = Field("", max_length=120)
    endpoint: str = Field("", max_length=256)
    ttl_seconds: int = Field(900, ge=60, le=24 * 3600)


class JoinRequest(BaseModel):
    """A join code, as pasted (or scanned) from the control plane."""

    token: str = Field(..., max_length=4096)


class TargetRequest(BaseModel):
    """`local`, or the id of an enrolled worker."""

    target: str = Field(..., max_length=64)


class WorkerUpdate(BaseModel):
    name: str | None = Field(None, max_length=120)
    enabled: bool | None = None
    priority: int | None = Field(None, ge=0, le=100)


class SubmitTaskRequest(BaseModel):
    """One unit of work for a remote worker. **Dev only** — see `submit_task`."""

    engine: str = Field(..., max_length=64)
    operation: str = Field("tts", max_length=32)
    model_id: str = Field("", max_length=128)
    params: dict = Field(default_factory=dict)
    # Mandatory, and deliberately without a default: the sweeper fails a task
    # on its deadline only while it is QUEUED, so one submitted without a
    # deadline while no worker is online waits forever with nothing left in
    # the system that would ever time it out.
    deadline_seconds: float = Field(..., gt=0, le=6 * 3600)
    idempotency_key: str | None = Field(None, max_length=128)


class _ClientGone(Exception):
    """The caller hung up while its task was still running."""


class _WaitExpired(Exception):
    """The task did not reach a terminal state inside its deadline."""


@router.get("")
def list_workers() -> dict:
    """Everything the workers panel renders, in one call."""
    return service.control_plane.snapshot()


# Polled every second while work runs: it reads SQLite, so it must not be able
# to occupy the shared worker pool. ONE target-wide snapshot serves every `op`
# (routing.status_for_operation), so the guard has a single in-flight key that
# no caller-chosen value can multiply or exhaust.
_target_poll = PollGuard("workers-target", lambda: routing.status())


@router.get("/target")
async def get_target(op: str = "") -> dict:
    """What the GPU picker shows: the choice, the resolved answer, the options.

    `active` is the same answer the generation path uses, so the badge cannot
    claim work goes somewhere the router will not send it. Pass `op` for the
    surface being rendered — omitting it answers for the target as a whole,
    which is what the picker's own menu asks.
    """
    chosen = op.strip()
    # Surface names are open-ended, so validate their shape
    # (routing.OPERATION_NAME) rather than enumerate them. The name only labels
    # the answer; it never selects or multiplies the work done.
    if chosen and not routing.valid_operation_name(chosen):
        raise HTTPException(status_code=422, detail="invalid operation name")
    return routing.status_for_operation(await _target_poll.get(), chosen)


@router.get("/runtime")
async def get_runtime(engine: str = "", op: str = "tts") -> dict:
    """Runtime/model facts for the machine that will execute this operation."""
    from services import gpu_gateway  # noqa: PLC0415

    return await gpu_gateway.status(
        engine=engine.strip() or None,
        op=op.strip() or "tts",
        control_plane=service.control_plane,
    )


@router.post("/target")
def set_target(request: TargetRequest) -> dict:
    """Choose where work runs. Exactly one target is active at a time."""
    chosen = request.target.strip() or routing.LOCAL
    if chosen != routing.LOCAL:
        worker = registry.get(chosen)
        if worker is None or worker.revoked:
            raise HTTPException(status_code=404, detail="No such worker.")
    routing.set_target_id(chosen)
    return routing.status()


@router.post("/enabled")
async def set_enabled(request: EnableRequest) -> dict:
    """Turn the feature on or off.

    Off means off: the control plane stops, the listening socket closes, and
    the app is exactly what it was before the toggle existed.
    """
    service.set_remote_workers_enabled(request.enabled)
    if request.enabled:
        try:
            await service.control_plane.start()
        except Exception as exc:
            service.control_plane.startup_error = str(exc)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    else:
        await service.control_plane.stop()
    return service.control_plane.snapshot()


@router.get("/agent")
def agent_status() -> dict:
    """The other side of the same feature: is THIS machine lending its GPU?

    Separate from `GET /workers`, which answers for the control plane. A
    machine can legitimately be both — a desktop that borrows a laptop's GPU
    and lends its own to a colleague — so neither status can stand in for the
    other.
    """
    from worker import agent as worker_agent  # noqa: PLC0415

    return worker_agent.agent.status()


@router.get("/agent/readiness", include_in_schema=False, response_model=None)
def agent_readiness() -> JSONResponse:
    """Container readiness: 200 only after this process registered as a worker."""
    from worker import agent as worker_agent  # noqa: PLC0415

    readiness = worker_agent.agent.readiness()
    return JSONResponse(
        status_code=200 if readiness["ready"] else 503,
        content=readiness,
        headers={} if readiness["ready"] else {"Retry-After": "2"},
    )


def _refuse_when_env_pinned(worker_agent) -> None:
    """OMNIVOICE_WORKER_MODE wins over the setting everywhere else.

    `worker_mode_enabled()` reads the variable first and `status()` reports the
    machine as env-pinned, so a route that changed worker mode anyway would
    contradict both: it writes a setting nothing consults, and the next restart
    undoes whatever the user just saw happen.
    """
    if worker_agent.agent.status()["env_pinned"]:
        raise HTTPException(
            status_code=409,
            detail=(
                "OMNIVOICE_WORKER_MODE controls this machine's worker mode. Unset it "
                "and restart VoiceStudio to manage it from here."
            ),
        )


async def _finish_cleanup(awaitable):
    """Run rollback to completion even if its HTTP task was cancelled."""
    task = asyncio.create_task(awaitable)
    await drain_task(task)
    return task.result()


async def _set_worker_mode(worker_agent, enabled: bool) -> None:
    _result, cancelled = await to_thread_and_defer_cancellation(
        worker_agent.set_worker_mode_enabled, enabled
    )
    if cancelled:
        raise asyncio.CancelledError


async def _restore_agent_transaction(
    worker_agent, previous: dict, *, was_running: bool
) -> None:
    """Restore durable enrollment/settings and the exact prior live state."""
    try:
        await _finish_cleanup(worker_agent.agent.stop())
        await _finish_cleanup(worker_agent.restore_enrollment(previous))
        if was_running and not worker_agent.agent.running:
            await _finish_cleanup(worker_agent.agent.start())
        elif not was_running and worker_agent.agent.running:
            await _finish_cleanup(worker_agent.agent.stop())
    except worker_agent.EnrollmentRollbackError:
        raise
    except BaseException as exc:
        message = (
            "The previous worker state could not be restored safely. "
            "Worker mode remains stopped; fix its enrollment/settings storage, then retry."
        )
        with contextlib.suppress(BaseException):
            await _finish_cleanup(worker_agent.agent.stop())
        worker_agent.agent.last_error = message
        raise worker_agent.EnrollmentRollbackError(message) from exc


def _raise_agent_transaction_failure(
    worker_agent, operation: BaseException, rollback: BaseException | None
) -> None:
    if isinstance(operation, asyncio.CancelledError):
        if rollback is not None:
            logger.error(
                "Worker rollback failed during request cancellation",
                exc_info=(type(rollback), rollback, rollback.__traceback__),
            )
        raise operation
    if rollback is not None:
        raise HTTPException(status_code=409, detail=str(rollback)) from rollback
    if isinstance(operation, Exception):
        worker_agent.agent.last_error = str(operation)
        raise HTTPException(status_code=409, detail=str(operation)) from operation
    raise operation


@router.post("/agent/join")
async def join_control_plane(request: JoinRequest) -> dict:
    """Redeem a join code and start working for that control plane.

    This is the endpoint that makes the feature reachable. Joining used to mean
    setting OMNIVOICE_WORKER_MODE and OMNIVOICE_WORKER_TOKEN in the environment
    and relaunching the app — a step most users will never take, on the machine
    that is usually the least convenient to configure by hand.

    The code is single-use and short-lived, so a failure here is nearly always
    "expired" or "wrong address"; it is returned verbatim rather than as a bare
    409, because the user's next action depends on which one it was.
    """
    from worker import agent as worker_agent  # noqa: PLC0415

    token = request.token.strip()
    if not token:
        raise HTTPException(status_code=422, detail="Paste the join code first.")
    # Same rule as the toggle below: joining ENABLES worker mode, so under
    # OMNIVOICE_WORKER_MODE it would write a setting the rest of the app
    # ignores — and with the variable set to 0, hand the user a machine that
    # says it joined and never lends anything (CodeRabbit).
    _refuse_when_env_pinned(worker_agent)
    async with worker_agent.agent.lifecycle:
        try:
            previous, cancelled = await to_thread_and_defer_cancellation(
                worker_agent.snapshot_enrollment
            )
        except worker_agent.EnrollmentStateError as exc:
            worker_agent.agent.last_error = str(exc)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if cancelled:
            raise asyncio.CancelledError
        was_running = worker_agent.agent.running

        # A rejoin stops a working agent before the replacement is accepted.
        # Stop, acceptance and the durable setting are one transaction: every
        # failure, including cancellation, restores both trust and live state.
        try:
            await worker_agent.agent.stop()
            await worker_agent.agent.start(token_text=token)
            # Success is the control plane ACCEPTING this worker, not the
            # connection being scheduled — see wait_until_registered.
            await worker_agent.agent.wait_until_registered()
            await _set_worker_mode(worker_agent, True)
        except BaseException as exc:
            rollback_exc = None
            try:
                await _restore_agent_transaction(
                    worker_agent, previous, was_running=was_running
                )
            except BaseException as rollback_error:
                rollback_exc = rollback_error
            _raise_agent_transaction_failure(worker_agent, exc, rollback_exc)
        worker_agent.agent.last_error = ""
        return worker_agent.agent.status()


@router.post("/agent/enabled")
async def set_agent_enabled(request: EnableRequest) -> dict:
    """Start or stop lending this machine, without forgetting the enrollment.

    Off stops the agent and clears the setting, so nothing dials out; the
    pinned certificate stays, which is what lets "on" resume without asking for
    another code.
    """
    from worker import agent as worker_agent  # noqa: PLC0415

    _refuse_when_env_pinned(worker_agent)
    async with worker_agent.agent.lifecycle:
        try:
            previous, cancelled = await to_thread_and_defer_cancellation(
                worker_agent.snapshot_enrollment
            )
        except worker_agent.EnrollmentStateError as exc:
            worker_agent.agent.last_error = str(exc)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if cancelled:
            raise asyncio.CancelledError
        was_running = worker_agent.agent.running

        try:
            if request.enabled:
                await worker_agent.agent.start()
                await worker_agent.agent.wait_until_registered()
                await _set_worker_mode(worker_agent, True)
            else:
                await worker_agent.agent.stop()
                await _set_worker_mode(worker_agent, False)
        except BaseException as exc:
            rollback_exc = None
            try:
                await _restore_agent_transaction(
                    worker_agent, previous, was_running=was_running
                )
            except BaseException as rollback_error:
                rollback_exc = rollback_error
            _raise_agent_transaction_failure(worker_agent, exc, rollback_exc)
        worker_agent.agent.last_error = ""
        return worker_agent.agent.status()


@router.post("/enrollments")
def create_enrollment(request: EnrollRequest) -> dict:
    """Mint a single-use join token.

    The plaintext is returned once and never stored — the response is the only
    time it exists outside the worker that redeems it.
    """
    if not service.control_plane.running:
        raise HTTPException(
            status_code=409,
            detail="Remote workers are turned off. Enable them in Settings → System → Remote workers first.",
        )
    try:
        token = service.control_plane.create_enrollment(
            endpoint=request.endpoint,
            label=request.label,
            ttl_seconds=request.ttl_seconds,
        )
    except service.EndpointCertificateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "token": token.encode(),
        "endpoint": token.endpoint,
        "fingerprint": token.cert_fingerprint,
        "expires_at": token.expires_at,
        "shown_once": True,
    }


def _persist_worker_update(
    worker_id: str, request: WorkerUpdate
):
    """Write policy on a worker thread; live publication stays loop-owned."""
    return registry.update_policy(
        worker_id,
        name=request.name,
        enabled=request.enabled,
        priority=request.priority,
    )


@router.patch("/{worker_id}")
async def update_worker(worker_id: str, request: WorkerUpdate) -> dict:
    pool = service.control_plane.pool if service.control_plane.running else None
    live = None
    was_pending = False
    if pool is not None:
        # Quiesce dispatch before releasing authority for the SQLite write.
        # The publication after the await restores the exact prior state, so a
        # concurrent registration handoff remains quiesced for its own reason.
        with registry.authority_guard():
            live = pool.get(worker_id)
            if live is not None:
                was_pending = live.registration_pending
                live.registration_pending = True
    updated = None
    cancelled = False
    try:
        updated, cancelled = await to_thread_and_defer_cancellation(
            _persist_worker_update, worker_id, request
        )
    finally:
        if pool is not None:
            with registry.authority_guard():
                if updated is not None:
                    # Pool state, including the cached record the scheduler
                    # reads, belongs to the app's event loop.
                    pool.refresh_record(updated)
                current = pool.get(worker_id)
                if current is live:
                    current.registration_pending = was_pending
    if updated is None:
        if cancelled:
            raise asyncio.CancelledError
        raise HTTPException(status_code=404, detail="No such worker.")
    if cancelled:
        raise asyncio.CancelledError
    return updated.to_dict()


@router.post("/{worker_id}/consent")
def grant_consent(worker_id: str) -> dict:
    """Record the user's explicit yes to sending their audio to this machine."""
    if registry.get(worker_id) is None:
        raise HTTPException(status_code=404, detail="No such worker.")
    registry.grant_consent(worker_id)
    worker = registry.get(worker_id)
    return worker.to_dict() if worker else {}


@router.post("/{worker_id}/resume")
async def clear_breaker(worker_id: str) -> dict:
    """Clear a paused worker's circuit breakers.

    The user fixed the machine and knows it — a breaker with no manual clear is
    the quarantine trap the reputation system had.
    """
    if not service.control_plane.running:
        raise HTTPException(status_code=409, detail="Remote workers are turned off.")
    breakers = service.control_plane.pool.breakers
    for breaker in breakers.open_breakers(worker_id):
        breaker.force_close()
    return {"ok": True}


@router.delete("/{worker_id}")
async def revoke_worker(worker_id: str) -> dict:
    """Remove a worker — which means revoke its key, not hide the row.

    Its in-flight work is released so it can be retried elsewhere rather than
    waiting out a lease on a machine that will never answer again.
    """
    pool = service.control_plane.pool if service.control_plane.running else None
    live = None
    was_pending = False
    if pool is not None:
        with registry.authority_guard():
            live = pool.get(worker_id)
            if live is not None:
                was_pending = live.registration_pending
                live.registration_pending = True
    try:
        revoked, cancelled = await to_thread_and_defer_cancellation(
            registry.revoke, worker_id
        )
    except BaseException:
        if pool is not None:
            with registry.authority_guard():
                current = pool.get(worker_id)
                if current is live:
                    current.registration_pending = was_pending
        raise
    if not revoked:
        if pool is not None:
            with registry.authority_guard():
                current = pool.get(worker_id)
                if current is live:
                    current.registration_pending = was_pending
        if cancelled:
            raise asyncio.CancelledError
        raise HTTPException(status_code=404, detail="No such worker.")

    # The tombstone committed before any egress/session mutation. Everything
    # below is loop-owned and published under the same scheduler authority read
    # used by next_assignment(), so no task can bind in the handoff window.
    with registry.authority_guard():
        if service.control_plane.running:
            if service.control_plane.servicer is not None:
                service.control_plane.servicer.revoke_worker_sessions(worker_id)
            service.control_plane.scheduler.on_disconnected(worker_id)
            service.control_plane.pool.breakers.forget_worker(worker_id)
    if cancelled:
        raise asyncio.CancelledError
    return {"ok": True, "revoked": worker_id}


@router.get("/tasks")
def list_tasks(limit: int = 50) -> dict:
    """Recent remote tasks, for the queue view."""
    if not service.control_plane.running:
        return {"tasks": [], "queue_depth": 0}
    from worker import task_store  # noqa: PLC0415

    return {
        "queue_depth": service.control_plane.scheduler.queue_depth,
        "tasks": [t.to_dict() for t in task_store.list_tasks(limit=min(200, max(1, limit)))],
    }


@router.post("/tasks")
async def submit_task(request: Request, body: SubmitTaskRequest) -> dict:
    """Run one task on a remote worker and wait for it. **DEV ONLY.**

    This is the producer the remote pipeline never had: until it existed the
    scheduler had no caller outside the test suite, so picking a remote GPU
    changed the badge and nothing else — every job still ran locally. It is
    the smallest thing that makes remote execution observable end to end, not
    the shipping surface: the GPU gateway takes over routing real generation
    and this endpoint goes with it.

    Loopback-only and behind the same opt-in as the rest of the feature, so a
    user who never enabled remote workers cannot reach it at all.
    """
    from worker.lifecycle import TaskState  # noqa: PLC0415
    from worker.scheduler import QueueFull, SchedulerStopped  # noqa: PLC0415

    if not service.remote_workers_enabled() or not service.control_plane.running:
        raise HTTPException(status_code=409, detail="Remote workers are turned off.")
    if not routing.supports_operation(body.operation):
        raise HTTPException(
            status_code=400,
            detail=f"'{body.operation}' does not run on a remote worker yet.",
        )

    scheduler = service.control_plane.scheduler
    try:
        submit = getattr(scheduler, "submit_async", None)
        submit = submit if callable(submit) else scheduler.submit
        submitted = submit(
            operation=body.operation,
            engine=body.engine,
            model_id=body.model_id,
            params=body.params,
            idempotency_key=body.idempotency_key or None,
            deadline_seconds=body.deadline_seconds,
            pinned_worker_id=routing.decide().worker_id or None,
        )
        task = await submitted if asyncio.iscoroutine(submitted) else submitted
    except QueueFull as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    settled = None
    reason = "the request was interrupted"
    try:
        settled = await _await_terminal(
            request, scheduler, task.task_id, timeout=body.deadline_seconds
        )
    except _ClientGone:
        reason = "the client disconnected"
        raise HTTPException(status_code=499, detail="The client stopped waiting.") from None
    except _WaitExpired:
        reason = "the task passed its deadline"
        raise HTTPException(
            status_code=504,
            detail=f"The task did not finish within {body.deadline_seconds:g}s.",
        ) from None
    except SchedulerStopped as exc:
        # Deliberately no cancel: the worker was never told to stop and may
        # still be rendering, so claiming the task is cancelled would be a
        # statement about someone else's GPU that we cannot make.
        reason = None
        raise HTTPException(status_code=503, detail=str(exc)) from None
    finally:
        # Nothing else will stop it: a worker holds its slot — often its only
        # one — until the control plane says otherwise, and the sweeper only
        # enforces deadlines on tasks that are still queued. Swallowed because
        # a failure here would replace the caller's real error with a 500.
        if settled is None and reason is not None:
            try:
                await service.control_plane.cancel(task.task_id, reason=reason)
            except Exception:
                logger.exception("Could not cancel abandoned remote task %s", task.task_id)

    payload = settled.to_dict()
    if settled.state is TaskState.COMPLETED:
        return payload
    # A failure that answered 200 would be indistinguishable from success to
    # anything that does not read `state` — which is the whole point of this
    # endpoint existing before the gateway does.
    raise HTTPException(
        status_code=409 if settled.state is TaskState.CANCELLED else 502, detail=payload
    )


async def _await_terminal(request: Request, scheduler, task_id: str, *, timeout: float):
    """Wait for a terminal task, giving up if the caller does first."""
    waiter = asyncio.ensure_future(scheduler.wait(task_id, timeout=timeout))
    while True:
        done, _pending = await asyncio.wait({waiter}, timeout=_DISCONNECT_POLL_SECONDS)
        if done:
            try:
                settled = waiter.result()
            except (asyncio.TimeoutError, TimeoutError) as exc:
                raise _WaitExpired() from exc
            if settled is None or not settled.state.terminal:
                raise _WaitExpired()
            return settled
        if await request.is_disconnected():
            waiter.cancel()
            raise _ClientGone()


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str) -> dict:
    if not service.control_plane.running:
        raise HTTPException(status_code=409, detail="Remote workers are turned off.")
    cancelled = await service.control_plane.cancel(task_id, reason="cancelled by user")
    if not cancelled:
        raise HTTPException(status_code=404, detail="No such active task.")
    return {"ok": True}


# ── Inbound mode ───────────────────────────────────────────────────────────
#
# The other direction: this machine accepts connections from panels, or dials
# out to nodes that do. Outbound enrollment above is unchanged and remains the
# default — see docs/adr/inbound-node-mode.md for why this exists alongside it
# rather than replacing it.


class InboundEnableRequest(BaseModel):
    enabled: bool
    # Widening the bind is a separate decision from turning the feature on,
    # so it is a separate field with a safe default rather than a flag that
    # rides along with `enabled`.
    bind: str = ""
    port: int = 0


class IssueKeyRequest(BaseModel):
    label: str = Field(default="", max_length=64)


class ConnectRequest(BaseModel):
    connection_string: str = Field(min_length=1, max_length=512)


@router.get("/inbound")
def inbound_status() -> dict:
    from worker.inbound import service as inbound_service  # noqa: PLC0415

    return {
        **inbound_service.node.snapshot(),
        "connections": inbound_service.outbound.snapshot(),
    }


@router.post("/inbound/enabled")
async def set_inbound_enabled(request: InboundEnableRequest) -> dict:
    from worker.inbound import service as inbound_service  # noqa: PLC0415

    if inbound_service.enabled_override() is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Accept connections is controlled by OMNIVOICE_INBOUND_NODE on this "
                "machine. Change that environment setting and restart VoiceStudio."
            ),
        )

    requested_bind = (
        inbound_service.normalise_bind_host(request.bind)
        if request.bind
        else inbound_service.bind_host()
    )
    requested_port = request.port or inbound_service.bind_port()
    if (
        request.enabled
        and inbound_service.node.running
        and (
            requested_bind != inbound_service.bind_host()
            or requested_port != inbound_service.node.port
        )
    ):
        # start() is intentionally idempotent while a listener owns its
        # socket. Persisting a new endpoint here would make the UI report a
        # narrower/different bind while the original socket stayed live.
        raise HTTPException(
            status_code=409,
            detail=(
                "Turn off Accept connections before changing its bind address "
                "or port."
            ),
        )
    if request.bind:
        inbound_service.set_bind_host(requested_bind)
    if request.port:
        inbound_service.set_bind_port(request.port)
    inbound_service.set_enabled(request.enabled)

    if inbound_service.enabled():
        await inbound_service.node.start()
        if inbound_service.node.startup_error:
            logger.error("Inbound worker listener failed to start; details withheld.")
            raise HTTPException(
                status_code=409,
                detail=(
                    "The inbound worker listener could not start; "
                    "check the backend log for details."
                ),
            )
    else:
        await inbound_service.node.stop()
    return inbound_service.node.snapshot()


@router.post("/inbound/keys")
def issue_inbound_key(request: IssueKeyRequest) -> dict:
    """Mint one panel's key and return the string it pastes.

    The secret is in this response and nowhere else afterwards — only its hash
    is stored, so it cannot be shown again, only replaced.
    """
    from worker.inbound import service as inbound_service  # noqa: PLC0415
    from worker.inbound.keys import KeyLimitExceeded  # noqa: PLC0415

    if not inbound_service.node.running:
        raise HTTPException(
            status_code=409,
            detail=(
                "This machine is not accepting connections yet. Turn on "
                "Settings → System → Remote workers → Accept connections first."
            ),
        )
    try:
        issued = inbound_service.node.keys.issue(request.label)
    except KeyLimitExceeded as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "key_id": issued.key.key_id,
        "label": issued.key.label,
        "connection_string": inbound_service.node.connection_string(issued.secret),
        "exposed": inbound_service.is_exposed(),
        "shown_once": True,
    }


@router.delete("/inbound/keys/{key_id}")
async def revoke_inbound_key(key_id: str) -> dict:
    """Revoke one panel. Everyone else stays connected — the whole reason keys
    are per panel rather than one shared node key."""
    from worker.inbound import service as inbound_service  # noqa: PLC0415

    if not await inbound_service.node.revoke_key(key_id):
        raise HTTPException(status_code=404, detail="No such key.")
    return inbound_service.node.snapshot()


@router.post("/inbound/sessions/{session_id}/disconnect")
def disconnect_inbound_session(session_id: str) -> dict:
    from worker.inbound import service as inbound_service  # noqa: PLC0415

    if not inbound_service.node.log.kick(session_id):
        raise HTTPException(status_code=404, detail="That connection has already ended.")
    return inbound_service.node.snapshot()


@router.post("/inbound/connections")
async def add_inbound_connection(request: ConnectRequest) -> dict:
    """Paste a connection string from a GPU machine and dial it."""
    from worker.inbound import service as inbound_service  # noqa: PLC0415
    from worker.inbound.connection_string import InvalidConnectionString  # noqa: PLC0415
    from worker.inbound.connector import InboundConnectionError  # noqa: PLC0415

    if not service.control_plane.running:
        raise HTTPException(
            status_code=409,
            detail=(
                "Remote workers are turned off. Enable them in "
                "Settings → System → Remote workers first."
            ),
        )
    try:
        connection = await inbound_service.outbound.add(
            request.connection_string, service.control_plane.servicer
        )
    except InvalidConnectionString as exc:
        # 400 with the parser's own words: every one of these otherwise
        # surfaces as "cannot connect", which is what a firewall, a wrong port
        # and a dead node all say too.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InboundConnectionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"endpoint": connection.endpoint, "connections": inbound_service.outbound.snapshot()}


@router.delete("/inbound/connections/{endpoint}")
async def remove_inbound_connection(endpoint: str) -> dict:
    from worker.inbound import service as inbound_service  # noqa: PLC0415
    from worker.inbound.connector import InboundConnectionError  # noqa: PLC0415

    try:
        await inbound_service.outbound.remove(endpoint)
    except InboundConnectionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"connections": inbound_service.outbound.snapshot()}
