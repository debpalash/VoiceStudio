"""Runs assigned tasks on a worker, using the engines already installed there.

A worker is the ordinary backend in worker mode, not a separate slim agent.
That is deliberate: engines, their per-engine sidecar venvs, model downloading,
VRAM budgeting, and the deliberately serial GPU lane all already live in
``services/``. A second implementation would fork every one of them and drift.

So this module is a translator, not an engine. It takes a wire assignment,
calls the same code path a local generation would, and reports progress in the
terms the protocol expects.

The serial GPU gate is honoured rather than bypassed: work runs through the
same ``gpu_queue`` that protects local jobs, so a machine serving both a remote
task and its own user cannot double-book its GPU.
"""
from __future__ import annotations

import asyncio
import base64
import errno
import hashlib
import io
import json
import logging
import os
import threading
import time
import uuid
import zipfile
from typing import Any, Awaitable, Callable, Optional

from worker.async_utils import drain_task
from worker.errors import ErrorClass, WorkerError

logger = logging.getLogger("omnivoice.worker")

# Results at or below this ride the control stream inline; anything larger is
# uploaded separately so it cannot head-of-line block heartbeats.
INLINE_LIMIT_BYTES = 256 * 1024

# Where the control plane reports that it could not stage an input. Mirrors
# ``codec._INPUT_ERRORS_KEY``; the two are pinned together by a test rather
# than by an import, because this module must not depend on the transport.
INPUT_ERRORS_PARAM = "input_errors"

# Fetched inputs are cached by content hash, so the second clone of a voice
# transfers nothing. Bounded, because a cache with no ceiling is the same disk
# leak on the worker that unpurged artifacts were on the control plane.
INPUT_CACHE_LIMIT_BYTES = 2 * 1024 * 1024 * 1024
_FALLBACK_INPUT_FETCH_SECONDS = 600.0
_STALE_INPUT_PARTIAL_SECONDS = 60 * 60.0

# Pruning runs in worker threads and every executor instance shares the same
# on-disk cache, so active-path leases are process-wide and thread-safe.
_INPUT_CACHE_LEASE_LOCK = threading.Lock()
_INPUT_CACHE_LEASES: dict[str, int] = {}
_INPUT_CACHE_FETCH_LEASES: dict[str, int] = {}
_INPUT_CACHE_MUTATIONS: set[str] = set()
# Concurrent fetches keep distinct partial files but serialize the instant a
# verified generation is published at its content address.
_INPUT_CACHE_FETCH_LOCKS: dict[str, asyncio.Lock] = {}
_INPUT_CACHE_FETCH_USERS: dict[str, int] = {}

#   on_progress(fraction: float, stage: str)
#   on_model_loading(fraction: float, detail: str)
#
# Passed per call by the transport, which binds them to the assignment's ref —
# one executor serves every slot, so a reporter installed on the instance could
# not say which task a fraction belongs to. The constructor keywords remain for
# a caller that drives the executor directly.
ProgressReporter = Callable[[float, str], Awaitable[None]]
LoadReporter = Callable[[float, str], Awaitable[None]]

#   fetch_input(ref: ArtifactRef, destination: str) -> Awaitable[Any]
#
# Supplied by the transport, which owns the ``DownloadArtifact`` stream and the
# session credentials it needs. The executor decides *what* to fetch and where
# it lands; it does not know there is a network.
InputFetcher = Callable[[Any, str], Awaitable[Any]]

# Used when an assignment carries no deadlines (the HTTP mirror, and tests).
# Generous on purpose: the server lease is the real bound, and a worker-side
# timeout that fires first turns a slow-but-healthy job into a hard failure.
_FALLBACK_MODEL_LOAD_SECONDS = 1_200.0
_FALLBACK_EXECUTION_SECONDS = 1_800.0


class UnsupportedOperation(Exception):
    """The worker was handed an operation it does not implement."""


class TaskExecutor:
    """Executes protocol assignments against the local engine stack."""

    def __init__(
        self,
        *,
        on_progress: Optional[ProgressReporter] = None,
        on_model_loading: Optional[LoadReporter] = None,
        fetch_input: Optional[InputFetcher] = None,
        input_dir: Optional[str] = None,
    ) -> None:
        self._on_progress = on_progress
        self._on_model_loading = on_model_loading
        self._fetch_input = fetch_input
        self._input_dir = input_dir
        self._blocking_tasks: set[asyncio.Task] = set()

    async def execute(
        self,
        assignment,
        *,
        on_progress: Optional[ProgressReporter] = None,
        on_model_loading: Optional[LoadReporter] = None,
        fetch_input: Optional[InputFetcher] = None,
    ) -> dict:
        """Run one assignment and return ``{"meta": {...}, "payload": bytes}``.

        Raises a ``WorkerError``-carrying exception on failure so the client
        reports a classified error rather than a bare string — the difference
        between "retry elsewhere" and "stop, this input is bad".

        The reporters arrive per call, already bound to this assignment's ref
        by the transport; they are what renews the server's progress lease, so
        an executor that ignored them would die of apparent silence on any task
        longer than the lease — starting with the cold model load.
        """
        operation = (assignment.operation or "tts").lower()
        params = _parse_params(assignment.params_json)
        params, leased_inputs = await self._materialize_inputs(
            assignment, params, fetch_input or self._fetch_input
        )
        try:
            handler = {
                "tts": self._run_tts,
                "clone": self._run_tts,
                "audiobook": self._run_audiobook,
                "batch_segments": self._run_dub_segments,
                "dub_segments": self._run_dub_segments,
            }.get(operation)
            if handler is None:
                raise TaskFailure(
                    WorkerError(
                        error_class=ErrorClass.CAPABILITY,
                        code="OPERATION_UNSUPPORTED",
                        message=f"This worker cannot run '{operation}' tasks.",
                        hint="Run this task locally, or use a worker that supports it.",
                    )
                )
            return await handler(
                assignment,
                params,
                _Reporters(
                    on_progress or self._on_progress,
                    on_model_loading or self._on_model_loading,
                ),
            )
        finally:
            self._release_inputs_after_active_work(leased_inputs)

    async def _run_dub_segments(self, assignment, params: dict, report: "_Reporters") -> dict:
        """Render every requested dub line under one lease and return one bundle."""
        rows = params.get("segments") or []
        refs = params.get("ref_audio") or []
        if not rows:
            raise TaskFailure(WorkerError(
                error_class=ErrorClass.TERMINAL, code="INVALID_TASK_PARAMS",
                message="The dubbing task carried no segments.",
                hint="Re-open the dub and try again.",
            ))
        load_budget, run_budget = _budgets(assignment)
        await report.loading(0.0, f"preparing {assignment.engine}")
        backend = await self._bounded_thread(
            self._load_backend,
            assignment.engine,
            timeout=load_budget, code="MODEL_LOAD_TIMEOUT", what=f"Loading '{assignment.engine}'",
        )
        await report.loading(1.0, "model ready")
        rendered: list[tuple[int, bytes]] = []
        prepared_rows = []
        for index, source in enumerate(rows):
            row = dict(source)
            row["ref_audio"] = refs[index] if index < len(refs) else None
            prepared_rows.append(row)

        # Remote dubbing used to hold one coarse GPU lease but still call the
        # engine once per line. That bypassed the same native variable-length
        # batch path used by local dubbing, leaving large GPUs mostly idle.
        # Group only rows whose scalar generation contract is compatible;
        # language, reference, duration, speed and instruct remain per-row.
        try:
            from services.dub_batching import batch_timeout_s, native_batch_width
            from services.tts_backend import TTSBackend

            has_native_batch = (
                getattr(type(backend), "generate_batch", TTSBackend.generate_batch)
                is not TTSBackend.generate_batch
            )
            batch_width = native_batch_width(backend) if has_native_batch else 1
        except Exception:  # noqa: BLE001 - capability probing takes the safe path
            batch_timeout_s = None
            batch_width = 1

        index = 0
        while index < len(prepared_rows):
            row = prepared_rows[index]
            batch = [row]
            if batch_width > 1 and row.get("seed") is None:
                compatibility = self._dub_batch_compatibility(row)
                for candidate in prepared_rows[index + 1 : index + batch_width]:
                    if (
                        candidate.get("seed") is not None
                        or self._dub_batch_compatibility(candidate) != compatibility
                    ):
                        break
                    batch.append(candidate)

            audios = None
            if len(batch) > 1:
                try:
                    timeout = (
                        batch_timeout_s([str(item.get("text") or "") for item in batch], backend)
                        if batch_timeout_s is not None
                        else run_budget
                    )
                    audios = await self._bounded_thread(
                        self._synthesize_dub_batch,
                        backend,
                        batch,
                        timeout=min(run_budget, timeout),
                        code="EXECUTION_TIMEOUT",
                        what=f"Dubbing segments {index + 1}-{index + len(batch)}",
                    )
                except TaskFailure:
                    raise
                except Exception as exc:  # native batching is an optimization
                    logger.warning(
                        "Native remote dub batch failed for segments %s-%s; falling back: %s",
                        index + 1,
                        index + len(batch),
                        exc,
                    )

            if audios is None:
                batch = [row]
                audios = [
                    await self._bounded_thread(
                        self._synthesize_dub_segment,
                        backend,
                        row,
                        timeout=run_budget,
                        code="EXECUTION_TIMEOUT",
                        what=f"Dubbing segment {index + 1}",
                    )
                ]

            encoded = await asyncio.gather(
                *(self._thread_call(self._encode, audio, item, backend)
                  for audio, item in zip(audios, batch))
            )
            for offset, (item, (payload, _meta)) in enumerate(zip(batch, encoded), 1):
                rendered.append((int(item.get("index", index + offset - 1)), payload))
                completed = index + offset
                await report.progress(
                    completed / len(prepared_rows),
                    f"segment {completed} of {len(prepared_rows)}",
                )
            index += len(batch)

        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_STORED) as archive:
            for index, payload in rendered:
                archive.writestr(f"segments/{index}.wav", payload)
        data = bundle.getvalue()
        return {"payload": data, "meta": {
            "filename": "dub-segments.zip", "content_type": "application/zip",
            "segments": len(rendered), "bytes": len(data),
        }}

    @staticmethod
    def _synthesize_dub_segment(backend, row: dict):
        """Worker-side equivalent of dubbing's text-to-engine chokepoint."""
        from services.audio_dsp import apply_effects_chain, apply_mastering, get_effect_chain, normalize_audio
        from services.text_normalization import normalize_for_tts

        text = normalize_for_tts(row.get("text") or "", row.get("language"))
        seed = None
        if row.get("seed") is not None:
            import torch
            seed = int(row["seed"])
            torch.manual_seed(seed)
        kwargs = {
            "language": row.get("language") if row.get("language") != "Auto" else None,
            "ref_audio": row.get("ref_audio"), "ref_text": row.get("ref_text"),
            "cache_ref": not bool(row.get("ref_single_use")),
            "instruct": row.get("instruct") or None, "duration": row.get("duration"),
            "num_step": int(row.get("num_step") or 16),
            "guidance_scale": float(row.get("guidance_scale") or 2.0),
            "speed": float(row.get("speed") or 1.0), "denoise": True,
            "postprocess_output": bool(row.get("postprocess_output", True)),
        }
        if (
            getattr(backend, "supports_native_omnivoice_controls", False)
            and seed is not None
        ):
            kwargs["seed"] = seed
        audio = backend.generate(text=text, **kwargs)
        preset = row.get("effect_preset") or "broadcast"
        if preset != "raw":
            if not getattr(backend, "applies_own_mastering", False):
                audio = apply_mastering(audio, sample_rate=backend.sample_rate)
            chain = get_effect_chain(preset)
            if chain:
                audio = apply_effects_chain(audio, sample_rate=backend.sample_rate, chain=chain)
            audio = normalize_audio(audio, target_dBFS=-2.0)
        return audio

    @staticmethod
    def _dub_batch_compatibility(row: dict) -> tuple:
        """Scalar options that a native backend requires to match in a batch."""
        return (
            not bool(row.get("ref_single_use")),
            bool(row.get("ref_audio")),
            int(row.get("num_step") or 16),
            float(row.get("guidance_scale") or 2.0),
            bool(row.get("postprocess_output", True)),
        )

    @staticmethod
    def _synthesize_dub_batch(backend, rows: list[dict]):
        """Worker-side equivalent of local dubbing's native batch path."""
        from services.audio_dsp import (
            apply_effects_chain,
            apply_mastering,
            get_effect_chain,
            normalize_audio,
        )
        from services.text_normalization import normalize_for_tts

        texts = [normalize_for_tts(row.get("text") or "", row.get("language")) for row in rows]
        outputs = backend.generate_batch(
            texts,
            language=[
                row.get("language") if row.get("language") != "Auto" else None for row in rows
            ],
            ref_audio=[row.get("ref_audio") for row in rows],
            ref_text=[row.get("ref_text") for row in rows],
            cache_ref=not bool(rows[0].get("ref_single_use")),
            instruct=[row.get("instruct") or None for row in rows],
            duration=[row.get("duration") for row in rows],
            num_step=int(rows[0].get("num_step") or 16),
            guidance_scale=float(rows[0].get("guidance_scale") or 2.0),
            speed=[float(row.get("speed") or 1.0) for row in rows],
            denoise=True,
            postprocess_output=bool(rows[0].get("postprocess_output", True)),
        )
        if len(outputs) != len(rows):
            raise RuntimeError(
                f"native batch returned {len(outputs)} outputs for {len(rows)} segments"
            )
        rendered = []
        for output, row in zip(outputs, rows):
            preset = row.get("effect_preset") or "broadcast"
            if preset != "raw":
                if not getattr(backend, "applies_own_mastering", False):
                    output = apply_mastering(output, sample_rate=backend.sample_rate)
                chain = get_effect_chain(preset)
                if chain:
                    output = apply_effects_chain(
                        output, sample_rate=backend.sample_rate, chain=chain
                    )
                output = normalize_audio(output, target_dBFS=-2.0)
            rendered.append(output)
        return rendered

    # ── Operations ────────────────────────────────────────────────────────

    async def _run_tts(self, assignment, params: dict, report: "_Reporters") -> dict:
        text = (params.get("text") or "").strip()
        if not text:
            raise TaskFailure(
                WorkerError(
                    error_class=ErrorClass.TERMINAL,
                    code="INVALID_TASK_PARAMS",
                    message="The task carried no text to synthesise.",
                    hint="This will fail on any worker — check the request.",
                )
            )

        load_budget, run_budget = _budgets(assignment)

        await report.loading(0.0, f"preparing {assignment.engine}")
        backend = await self._bounded_thread(
            self._load_backend,
            assignment.engine,
            timeout=load_budget,
            code="MODEL_LOAD_TIMEOUT",
            what=f"Loading '{assignment.engine}'",
        )
        await report.loading(1.0, "model ready")

        await report.progress(0.05, "synthesising")
        audio = await self._bounded_thread(
            self._synthesize,
            backend,
            text,
            params,
            timeout=run_budget,
            code="EXECUTION_TIMEOUT",
            what="Synthesis",
        )
        await report.progress(0.9, "encoding")

        payload, meta = await self._bounded_thread(
            self._encode,
            audio,
            params,
            backend,
            timeout=run_budget,
            code="EXECUTION_TIMEOUT",
            what="Encoding",
        )
        await report.progress(1.0, "done")
        return {"meta": meta, "payload": payload}

    async def _run_audiobook(self, assignment, params: dict, report: "_Reporters") -> dict:
        """Render one chapter as one leased unit, matching local longform assembly."""
        spans = params.get("spans") or []
        voices = params.get("voices") or []
        if not spans or len(voices) != len(spans):
            raise TaskFailure(WorkerError(
                error_class=ErrorClass.TERMINAL,
                code="INVALID_TASK_PARAMS",
                message="The audiobook task carried an invalid chapter.",
                hint="Re-plan the audiobook and try again.",
            ))
        load_budget, run_budget = _budgets(assignment)
        await report.loading(0.0, f"preparing {assignment.engine}")
        backend = await self._bounded_thread(
            self._load_backend,
            assignment.engine,
            timeout=load_budget, code="MODEL_LOAD_TIMEOUT",
            what=f"Loading '{assignment.engine}'",
        )
        await report.loading(1.0, "model ready")
        await report.progress(0.05, "synthesising chapter")
        audio = await self._bounded_thread(
            self._synthesize_audiobook,
            backend,
            spans,
            voices,
            params,
            timeout=run_budget, code="EXECUTION_TIMEOUT", what="Audiobook chapter",
        )
        await report.progress(0.9, "encoding")
        payload, meta = await self._thread_call(
            self._encode, audio, params, backend
        )
        await report.progress(1.0, "done")
        return {"meta": meta, "payload": payload}

    @staticmethod
    def _synthesize_audiobook(backend, rows: list[dict], voices: list[dict], params: dict):
        from services.audiobook import ExpressiveOptions, Span, segment_seed, synthesize_chapter
        from services.tts_backend import OmniVoiceBackend

        refs = params.get("ref_audio") or []
        voices = [dict(voice, ref_audio=refs[i] if i < len(refs) else None)
                  for i, voice in enumerate(voices)]
        opts = ExpressiveOptions.from_manifest(params.get("expressive"))
        language = params.get("language")
        extra = {
            key: value for key, value in opts.to_manifest().items()
            if value is not None
            and key not in ("seed", "vary_repeats", *ExpressiveOptions.JOIN_KEYS)
        }
        native_proxy = bool(
            getattr(backend, "supports_native_omnivoice_controls", False)
        )
        if isinstance(backend, OmniVoiceBackend) or native_proxy:
            extra.setdefault("num_step", 32)
            extra.setdefault("guidance_scale", 2.0)
            for key in ("emo_vector", "emo_text", "emo_alpha"):
                extra.pop(key, None)
        occurrence = {"value": 0}
        def synth(text, index, speed=None):
            voice = voices[int(index)]
            base_seed = opts.seed if opts.seed is not None else voice.get("seed")
            seed = None
            if base_seed is not None:
                import torch
                nonce = occurrence["value"] if opts.vary_repeats else 0
                occurrence["value"] += 1
                seed = segment_seed(base_seed, text, nonce)
                torch.manual_seed(seed)
            kwargs = {
                "language": language,
                "ref_audio": voice.get("ref_audio"),
                "ref_text": voice.get("ref_text"),
                "instruct": voice.get("instruct"),
                "speed": float(speed) if speed else 1.0,
                **extra,
            }
            if native_proxy and seed is not None:
                kwargs["seed"] = seed
            return backend.generate(text, **kwargs)

        spans = [Span(voice_id=str(i), text=row.get("text", ""),
                      pause_ms_after=int(row.get("pause_ms_after") or 0),
                      speed=row.get("speed"),
                      join=row.get("join") if row.get("join") in ("continue", "paragraph") else None)
                 for i, row in enumerate(rows)]
        sample_rate = int(getattr(backend, "sample_rate", 0) or 24_000)
        audio, _duration = synthesize_chapter(
            spans, synth, sample_rate, lexicon=params.get("lexicon"),
            **opts.join_kwargs(),
        )
        return _mark(audio, sample_rate, params)

    # ── Inputs ────────────────────────────────────────────────────────────

    async def _materialize_inputs(
        self, assignment, params: dict, fetch
    ) -> tuple[dict, list[str]]:
        """Turn declared inputs into local files, then point the params at them.

        The control plane sends artifact ids, never paths — its own paths mean
        nothing here. So a clone arrives with ``ref_audio`` set to an id, and
        the audio itself only exists once this has fetched it. Getting that
        wrong does not fail loudly: the engine renders in the default voice and
        the user gets audio that is simply not their clone.
        """
        errors = params.get(INPUT_ERRORS_PARAM)
        if errors:
            detail = "; ".join(str(e) for e in errors) if isinstance(errors, list) else str(errors)
            raise TaskFailure(
                WorkerError(
                    error_class=ErrorClass.TERMINAL,
                    code="INPUT_UNAVAILABLE",
                    message=f"The task's input files could not be prepared: {detail}",
                    hint="Check that the reference audio still exists, then try again.",
                )
            )

        refs = [ref for ref in (getattr(assignment, "inputs", None) or []) if ref.artifact_id]
        if not refs:
            return params, []
        if fetch is None:
            raise TaskFailure(
                WorkerError(
                    error_class=ErrorClass.CAPABILITY,
                    code="INPUT_TRANSFER_UNSUPPORTED",
                    message="This worker cannot fetch task inputs.",
                    hint="Update the worker, or run this task on a worker that can.",
                )
            )

        _, run_budget = _budgets(assignment)
        local: dict[str, str] = {}
        leased: list[str] = []
        try:
            for ref in refs:
                path = await self._bounded(
                    self._fetch_one(ref, fetch, retain=True),
                    timeout=min(run_budget, _FALLBACK_INPUT_FETCH_SECONDS),
                    code="INPUT_FETCH_TIMEOUT",
                    what=f"Fetching '{ref.filename or ref.artifact_id}'",
                )
                local[ref.artifact_id] = path
                leased.append(path)
            return _rewrite_params(params, local), leased
        except BaseException:
            for path in leased:
                _release_input_cache_path(path)
            raise

    async def _fetch_one(self, ref, fetch, *, retain: bool = False) -> str:
        """The local copy of one input, downloaded only if we lack it.

        Content-addressed: the name is the hash the control plane computed, so
        a second clone of the same voice — or a retry of this very task on this
        worker — costs no transfer at all.
        """
        directory = self._input_dir or default_input_dir()
        await self._thread_call(_durable_makedirs, directory)
        destination = os.path.join(directory, _cache_name(ref))
        return await self._fetch_one_owned(
            ref,
            fetch,
            directory=directory,
            destination=destination,
            retain=retain,
        )

    async def _fetch_one_owned(
        self,
        ref,
        fetch,
        *,
        directory: str,
        destination: str,
        retain: bool,
    ) -> str:
        """Validate, fetch, and safely publish one content address."""
        await _acquire_input_cache_path(destination, fetching=True)
        leased_result = destination
        succeeded = False
        try:
            # Cache hits still hash the advertised content identity. Filename
            # plus size is not proof after disk corruption or external edits.
            if await self._thread_call(_already_held, destination, ref):
                await self._thread_call(_touch, destination)
                succeeded = True
                return destination

            partial = f"{destination}.{uuid.uuid4().hex}.part"
            _lease_input_cache_path(partial)
            finalized = False
            try:
                try:
                    await fetch(ref, partial)
                except TaskFailure:
                    raise
                except Exception as exc:
                    raise _input_fetch_failure(ref, exc) from exc

                # Hashing and durability barriers can both block on a large
                # source or slow disk. Keep them off the loop and drain before
                # cleanup so Windows never unlinks a file still in use.
                await self._thread_call(_verify, partial, ref)
                gate_key = _cache_path_key(destination)
                gate = _INPUT_CACHE_FETCH_LOCKS.setdefault(
                    gate_key, asyncio.Lock()
                )
                _INPUT_CACHE_FETCH_USERS[gate_key] = (
                    _INPUT_CACHE_FETCH_USERS.get(gate_key, 0) + 1
                )
                try:
                    async with gate:
                        # A concurrent fetch may have published these exact
                        # bytes while this one was downloading its own partial.
                        if await self._thread_call(
                            _already_held, destination, ref
                        ):
                            await self._thread_call(_discard, partial)
                            finalized = True
                        elif _claim_input_cache_mutation(destination):
                            try:
                                await self._thread_call(
                                    _durable_replace, partial, destination
                                )
                            finally:
                                _finish_input_cache_mutation(destination)
                            finalized = True
                        else:
                            # Another execution is actively reading the
                            # canonical generation. Never unlink or replace
                            # bytes underneath it; publish this verified fetch
                            # under a leased sibling path and let a later
                            # unshared fetch repair canonical.
                            stem, suffix = os.path.splitext(destination)
                            alternate = (
                                f"{stem}.{uuid.uuid4().hex}.generation{suffix}"
                            )
                            await _acquire_input_cache_path(
                                alternate, fetching=True
                            )
                            try:
                                await self._thread_call(
                                    _durable_replace, partial, alternate
                                )
                            except BaseException:
                                _release_input_cache_path(
                                    alternate, fetching=True
                                )
                                await self._thread_call(_discard, alternate)
                                raise
                            finalized = True
                            _release_input_cache_path(
                                destination, fetching=True
                            )
                            leased_result = alternate
                except OSError as exc:
                    raise _input_fetch_failure(ref, exc) from exc
                finally:
                    remaining = _INPUT_CACHE_FETCH_USERS[gate_key] - 1
                    if remaining:
                        _INPUT_CACHE_FETCH_USERS[gate_key] = remaining
                    else:
                        _INPUT_CACHE_FETCH_USERS.pop(gate_key, None)
                        if _INPUT_CACHE_FETCH_LOCKS.get(gate_key) is gate:
                            _INPUT_CACHE_FETCH_LOCKS.pop(gate_key, None)
            finally:
                if not finalized:
                    await self._thread_call(_discard, partial)
                _release_input_cache_path(partial)

            await self._thread_call(_prune_input_cache, directory)
            succeeded = True
            return leased_result
        finally:
            if retain and succeeded:
                _promote_input_cache_lease(leased_result)
            else:
                _release_input_cache_path(leased_result, fetching=True)

    # ── Engine plumbing ───────────────────────────────────────────────────

    @staticmethod
    def _load_backend(engine_id: str):
        """Resolve the requested engine and make sure its weights are resident.

        ``engine_id`` is a registry NAME, never a path — the protocol forbids
        paths precisely because model loading is pickle-backed here, and a path
        would be remote code execution on every worker in the fleet.

        The instance comes from the process-wide cache, so the second task on
        an engine costs nothing: instantiating per task made every remote job
        pay a cold load, which is most of what the model-load budget and the
        progress lease were being blown on.

        ``ensure_ready()`` is what actually spends that budget. Every adapter
        loads lazily inside ``generate()``; without this the load phase is
        instantaneous, the cold load happens under the execution budget, and
        the two-phase split the protocol mirrors (#1033/#1037) is decorative.
        """
        from services import tts_backend  # noqa: PLC0415

        try:
            backend = tts_backend.get_engine_instance_for(engine_id)
        except Exception as exc:
            raise TaskFailure(
                WorkerError(
                    error_class=ErrorClass.CAPABILITY,
                    code="MODEL_NOT_INSTALLED",
                    message=f"Engine '{engine_id}' is not available on this worker.",
                    hint="Install it on the worker machine, or route this task elsewhere.",
                )
            ) from exc
        try:
            # Loading can mean a multi-GB download; the sweep must not decide
            # halfway through that nobody wants this engine.
            with tts_backend.engine_in_use(backend):
                backend.ensure_ready()
        except Exception as exc:
            from worker import errors as worker_errors  # noqa: PLC0415

            raise TaskFailure(worker_errors.from_exception(exc)) from exc
        return backend

    @staticmethod
    def _synthesize(backend, text: str, params: dict):
        """Render through the same seeded pipeline as local ``/generate``.

        Held against the idle sweep for the duration: a long generation touches
        the instance cache once, at the start, so on elapsed time alone it is
        indistinguishable from a model nobody wants any more.

        Do not reduce this to ``backend.generate()``.  The control plane sends
        a complete render contract (pinned gallery seed, synthetic reference,
        quality controls, chunking, effects); calling the adapter directly
        silently turns a selected gallery voice into a fresh random take.
        """
        from services import tts_backend  # noqa: PLC0415
        from api.routers.generation import _run_backend_inference, _run_inference  # noqa: PLC0415

        language = params.get("language")
        ref_audio = params.get("ref_audio")
        ref_text = params.get("ref_text")
        instruct = params.get("instruct")
        duration = params.get("duration")
        num_step = params.get("num_step", 16)
        guidance_scale = params.get("guidance_scale", 2.0)
        speed = params.get("speed", 1.0)
        denoise = params.get("denoise", True)
        postprocess_output = params.get("postprocess_output", True)
        used_seed = params.get("seed")
        effect_preset = params.get("effect_preset", "broadcast")
        max_chunk_chars = params.get("max_chunk_chars")
        crossfade_ms = params.get("crossfade_ms")
        try:
            with tts_backend.engine_in_use(backend):
                if isinstance(backend, tts_backend.OmniVoiceBackend):
                    # The OSS default engine has an extended native surface;
                    # preserving it is required for a gallery preview and a
                    # GPU-worker take to share the same voice identity.
                    return _run_inference(
                        backend._model, text, language, ref_audio, ref_text,
                        instruct, duration, num_step, guidance_scale, speed,
                        params.get("t_shift"), denoise, postprocess_output,
                        params.get("layer_penalty_factor"),
                        params.get("position_temperature"),
                        params.get("class_temperature"), used_seed,
                        effect_preset, max_chunk_chars, crossfade_ms,
                    )
                return _run_backend_inference(
                    backend, text, language, ref_audio, ref_text, instruct,
                    duration, num_step, guidance_scale, speed, denoise,
                    postprocess_output, used_seed, effect_preset,
                    max_chunk_chars, crossfade_ms,
                    t_shift=params.get("t_shift"),
                    layer_penalty_factor=params.get("layer_penalty_factor"),
                    position_temperature=params.get("position_temperature"),
                    class_temperature=params.get("class_temperature"),
                )
        except Exception as exc:
            from worker import errors as worker_errors  # noqa: PLC0415

            raise TaskFailure(worker_errors.from_exception(exc)) from exc

    @staticmethod
    def _encode(audio, params: dict, backend=None) -> tuple[bytes, dict]:
        """Mark the waveform, then turn it into wav bytes plus metadata.

        The engine's own rate is the fallback, not a flat 24 kHz: VoxCPM2
        renders at 48 kHz, and encoding its output as 24 kHz plays it back at
        half speed.
        """
        import io  # noqa: PLC0415

        import soundfile as sf  # noqa: PLC0415

        sample_rate = int(
            params.get("sample_rate") or getattr(backend, "sample_rate", 0) or 24_000
        )
        audio = _mark(audio, sample_rate, params)
        array = audio
        try:
            array = audio.detach().cpu().numpy()
        except AttributeError:
            pass
        if getattr(array, "ndim", 1) > 1:
            array = array.squeeze()

        buffer = io.BytesIO()
        sf.write(buffer, array, sample_rate, format="WAV")
        payload = buffer.getvalue()
        duration = float(len(array)) / sample_rate if sample_rate else 0.0
        return payload, {
            "sample_rate": sample_rate,
            "duration_seconds": round(duration, 3),
            "bytes": len(payload),
            "inline": len(payload) <= INLINE_LIMIT_BYTES,
        }

    # ── Bounding ──────────────────────────────────────────────────────────

    def _release_inputs_after_active_work(self, paths: list[str]) -> None:
        """Keep files leased while a timed-out engine thread still owns them."""
        pending = [task for task in self._blocking_tasks if not task.done()]
        if not pending:
            for path in paths:
                _release_input_cache_path(path)
            return
        remaining = {"count": len(pending)}

        def finished(_task: asyncio.Task) -> None:
            remaining["count"] -= 1
            if remaining["count"] == 0:
                for path in paths:
                    _release_input_cache_path(path)

        for task in pending:
            task.add_done_callback(finished)

    def _start_thread(self, function, /, *args) -> asyncio.Task:
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        self._blocking_tasks.add(task)

        def finished(completed: asyncio.Task) -> None:
            self._blocking_tasks.discard(completed)
            if not completed.cancelled():
                # Timed-out calls intentionally finish in the background. Read
                # their exception so asyncio never reports an unowned task.
                completed.exception()

        task.add_done_callback(finished)
        return task

    async def _thread_call(self, function, /, *args):
        task = self._start_thread(function, *args)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await drain_task(task)
            raise

    async def drain_active_work(self) -> None:
        """Wait until every blocking engine call has relinquished the process."""
        cancelled = bool(
            (current := asyncio.current_task()) is not None and current.cancelling()
        )
        while self._blocking_tasks:
            for task in list(self._blocking_tasks):
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    # Cancellation cannot make a Python GPU thread stop. Hold
                    # authority until it really exits, then propagate the
                    # cancellation so callers never publish a false free slot.
                    cancelled = True
                    await drain_task(task)
                except BaseException:
                    # The owner reports/classifies the engine exception. This
                    # barrier only establishes that the thread has finished.
                    pass
        if cancelled:
            raise asyncio.CancelledError

    async def _bounded_thread(
        self, function, /, *args, timeout: float, code: str, what: str
    ):
        """Bound a blocking call without losing ownership of its live thread."""
        task = self._start_thread(function, *args)
        try:
            done, _pending = await asyncio.wait({task}, timeout=timeout)
        except asyncio.CancelledError:
            await drain_task(task)
            raise
        if done:
            return task.result()
        # A GPU call cannot be killed. Return the timeout so the scheduler can
        # park its slot, but retain the task above so terminal authority loss
        # can drain it before claiming this worker has stopped.
        raise TaskFailure(
            WorkerError(
                error_class=ErrorClass.TIMEOUT,
                code=code,
                message=f"{what} exceeded the {timeout:g}s budget for this task.",
                hint="Try a shorter input, or a worker with more headroom.",
            )
        )

    @staticmethod
    async def _bounded(coro, *, timeout: float, code: str, what: str):
        """Run ``coro`` under the server's budget for this phase.

        The worker's own bound, not a replacement for the server lease: the
        lease can only notice that frames stopped arriving, while this ends the
        wait on a thread that is never coming back. Both exist because either
        alone leaves a hole — a wedged GPU thread keeps the keepalive timer
        ticking, and a dead connection stops the lease from being renewed.
        """
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise TaskFailure(
                WorkerError(
                    error_class=ErrorClass.TIMEOUT,
                    code=code,
                    message=f"{what} exceeded the {timeout:g}s budget for this task.",
                    hint="Try a shorter input, or a worker with more headroom.",
                )
            ) from exc


class _Reporters:
    """The two optional callbacks, so every call site can report unconditionally."""

    __slots__ = ("_progress", "_loading")

    def __init__(
        self,
        progress: Optional[ProgressReporter],
        loading: Optional[LoadReporter],
    ) -> None:
        self._progress = progress
        self._loading = loading

    async def progress(self, fraction: float, stage: str) -> None:
        if self._progress is not None:
            await self._progress(fraction, stage)

    async def loading(self, fraction: float, detail: str) -> None:
        if self._loading is not None:
            await self._loading(fraction, detail)


class TaskFailure(Exception):
    """Carries a classified ``WorkerError`` across the execution boundary."""

    def __init__(self, error: WorkerError) -> None:
        super().__init__(error.message)
        self.error = error


def _budgets(assignment) -> tuple[float, float]:
    """(model-load, execution) seconds for this assignment.

    Server-computed and relative — worker wall clocks are untrusted. A zero or
    missing field means "the server did not state one", never "no time".
    """
    deadlines = getattr(assignment, "deadlines", None)
    load = float(getattr(deadlines, "model_load_seconds", 0) or 0)
    run = float(getattr(deadlines, "execution_seconds", 0) or 0)
    return (
        load or _FALLBACK_MODEL_LOAD_SECONDS,
        run or _FALLBACK_EXECUTION_SECONDS,
    )


def default_input_dir() -> str:
    """Where fetched inputs are cached on this worker.

    Under the app's own data directory when there is one — a worker is the
    ordinary backend in worker mode — and the system temp dir otherwise, so a
    stripped-down install still runs instead of failing on a missing path.
    """
    try:
        from core.config import DATA_DIR  # noqa: PLC0415

        return os.path.join(str(DATA_DIR), "workers", "inputs")
    except Exception:  # pragma: no cover — no app data dir on this host
        import tempfile  # noqa: PLC0415

        return os.path.join(tempfile.gettempdir(), "omnivoice-worker-inputs")


def _cache_path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _lease_input_cache_path(path: str, *, fetching: bool = False) -> bool:
    key = _cache_path_key(path)
    with _INPUT_CACHE_LEASE_LOCK:
        if key in _INPUT_CACHE_MUTATIONS:
            return False
        _INPUT_CACHE_LEASES[key] = _INPUT_CACHE_LEASES.get(key, 0) + 1
        if fetching:
            _INPUT_CACHE_FETCH_LEASES[key] = (
                _INPUT_CACHE_FETCH_LEASES.get(key, 0) + 1
            )
        return True


async def _acquire_input_cache_path(
    path: str, *, fetching: bool = False
) -> None:
    while not _lease_input_cache_path(path, fetching=fetching):
        await asyncio.sleep(0.01)


def _release_input_cache_path(path: str, *, fetching: bool = False) -> None:
    key = _cache_path_key(path)
    with _INPUT_CACHE_LEASE_LOCK:
        if fetching:
            fetch_remaining = _INPUT_CACHE_FETCH_LEASES.get(key, 0) - 1
            if fetch_remaining > 0:
                _INPUT_CACHE_FETCH_LEASES[key] = fetch_remaining
            else:
                _INPUT_CACHE_FETCH_LEASES.pop(key, None)
        remaining = _INPUT_CACHE_LEASES.get(key, 0) - 1
        if remaining > 0:
            _INPUT_CACHE_LEASES[key] = remaining
        else:
            _INPUT_CACHE_LEASES.pop(key, None)


def _promote_input_cache_lease(path: str) -> None:
    """Turn a fetcher's lease into the active execution lease it returns."""
    key = _cache_path_key(path)
    with _INPUT_CACHE_LEASE_LOCK:
        remaining = _INPUT_CACHE_FETCH_LEASES.get(key, 0) - 1
        if remaining > 0:
            _INPUT_CACHE_FETCH_LEASES[key] = remaining
        else:
            _INPUT_CACHE_FETCH_LEASES.pop(key, None)


def _leased_input_cache_paths() -> set[str]:
    with _INPUT_CACHE_LEASE_LOCK:
        return set(_INPUT_CACHE_LEASES)


def _claim_input_cache_mutation(path: str) -> bool:
    key = _cache_path_key(path)
    with _INPUT_CACHE_LEASE_LOCK:
        if key in _INPUT_CACHE_MUTATIONS:
            return False
        active_leases = _INPUT_CACHE_LEASES.get(
            key, 0
        ) - _INPUT_CACHE_FETCH_LEASES.get(key, 0)
        # Fetchers can safely converge under the publication gate. A lease
        # already promoted to an execution may have this exact pathname open.
        if active_leases > 0:
            return False
        _INPUT_CACHE_MUTATIONS.add(key)
        return True


def _finish_input_cache_mutation(path: str) -> None:
    key = _cache_path_key(path)
    with _INPUT_CACHE_LEASE_LOCK:
        _INPUT_CACHE_MUTATIONS.discard(key)


def _fsync_file(path: str) -> None:
    with open(path, "r+b") as handle:
        os.fsync(handle.fileno())


def _fsync_parent_directory(directory: str) -> None:
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if directory_flag is None:
        return
    unsupported = {
        errno.EINVAL,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }
    try:
        descriptor = os.open(directory, os.O_RDONLY | directory_flag)
    except OSError as exc:
        if exc.errno in unsupported:
            return
        raise
    try:
        os.fsync(descriptor)
    except OSError as exc:
        if exc.errno not in unsupported:
            raise
    finally:
        os.close(descriptor)


def _durable_makedirs(directory: str) -> None:
    target = os.path.abspath(directory)
    missing: list[str] = []
    current = target
    while not os.path.isdir(current):
        if os.path.exists(current):
            if os.path.isdir(current):
                break
            raise NotADirectoryError(current)
        missing.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    for path in reversed(missing):
        try:
            os.mkdir(path)
        except FileExistsError:
            if not os.path.isdir(path):
                raise
        _fsync_parent_directory(os.path.dirname(path) or ".")
    if not missing:
        _fsync_parent_directory(os.path.dirname(target) or ".")


def _durable_replace(source: str, destination: str) -> None:
    _fsync_file(source)
    os.replace(source, destination)
    _fsync_parent_directory(os.path.dirname(destination) or ".")


def _input_fetch_failure(ref, error: BaseException) -> TaskFailure:
    return TaskFailure(
        WorkerError(
            # Transient on purpose: an id we cannot resolve now is far more
            # often a dropped stream/disk barrier than a permanently missing
            # file, and one wasted retry beats failing real work.
            error_class=ErrorClass.TRANSIENT,
            code="INPUT_FETCH_FAILED",
            message=f"Could not fetch '{ref.filename or ref.artifact_id}': {error}",
            hint="The control plane may have restarted; the task will be retried.",
        )
    )


def _cache_name(ref) -> str:
    """A safe, content-addressed local name for one input.

    Never the wire filename: that is remote input, and joining it onto a
    directory is how a peer writes outside it. The hash the control plane sent
    is the identity; the extension is kept only when it is a plain one,
    because an engine that shells out to ffmpeg reads the suffix.
    """
    digest = "".join(c for c in (getattr(ref, "sha256", "") or "") if c in "0123456789abcdef")
    if len(digest) != 64:
        digest = hashlib.sha256((ref.artifact_id or "").encode("utf-8")).hexdigest()
    suffix = os.path.splitext(os.path.basename(str(getattr(ref, "filename", "") or "")))[1].lower()
    if not (1 < len(suffix) <= 9 and suffix[1:].isalnum()):
        suffix = ""
    return f"{digest}{suffix}"


def _already_held(path: str, ref) -> bool:
    """Do we already have this exact input?

    The filename is content-addressed, but disks and external edits can still
    change bytes at that name. Re-hash the advertised identity before reuse.
    """
    try:
        expected = int(getattr(ref, "size_bytes", 0) or 0)
        if not os.path.isfile(path):
            return False
        if expected and os.path.getsize(path) != expected:
            return False
        expected_hash = (getattr(ref, "sha256", "") or "").strip().lower()
        if expected_hash:
            digest = hashlib.sha256()
            with open(path, "rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != expected_hash:
                return False
        return True
    except OSError:
        return False


def _touch(path: str) -> None:
    try:
        os.utime(path, None)
    except OSError:  # pragma: no cover
        pass


def _discard(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _verify(path: str, ref) -> None:
    """Refuse a transfer that does not match what was announced.

    A truncated reference clip does not fail — it clones three seconds of
    silence — so the check has to happen before the file is committed.
    """
    expected_size = int(getattr(ref, "size_bytes", 0) or 0)
    expected_hash = (getattr(ref, "sha256", "") or "").lower()
    try:
        actual_size = os.path.getsize(path)
    except OSError as exc:
        _discard(path)
        raise TaskFailure(
            WorkerError(
                error_class=ErrorClass.TRANSIENT,
                code="INPUT_FETCH_FAILED",
                message=f"The input '{ref.filename or ref.artifact_id}' did not arrive.",
                hint="The task will be retried.",
            )
        ) from exc

    actual_hash = ""
    if expected_hash:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        actual_hash = digest.hexdigest()

    if (expected_size and actual_size != expected_size) or (
        expected_hash and actual_hash != expected_hash
    ):
        _discard(path)
        raise TaskFailure(
            WorkerError(
                error_class=ErrorClass.TRANSIENT,
                code="INPUT_CORRUPT",
                message=f"The input '{ref.filename or ref.artifact_id}' arrived damaged.",
                hint="The transfer will be retried.",
            )
        )


def _prune_input_cache(
    directory: str,
    limit_bytes: int = INPUT_CACHE_LIMIT_BYTES,
    now: Optional[float] = None,
) -> None:
    """Keep the cache bounded without deleting inputs a task is still using."""
    try:
        entries = []
        total = 0
        stamp = time.time() if now is None else now
        leased = _leased_input_cache_paths()
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            if not os.path.isfile(path):
                continue
            stat = os.stat(path)
            key = _cache_path_key(path)
            is_partial = name.endswith(".part")
            if (
                is_partial
                and key not in leased
                and stamp - stat.st_mtime >= _STALE_INPUT_PARTIAL_SECONDS
            ):
                os.remove(path)
                continue
            total += stat.st_size
            # Active finals and partial transfers count toward the ceiling but
            # cannot be evicted. Young unleased .part files may belong to a
            # process that has not yet rebuilt its in-memory lease after fork;
            # the age sweep will remove them if they are crash leftovers.
            if key not in leased and not is_partial:
                entries.append((stat.st_mtime, stat.st_size, path))
        for _mtime, size, path in sorted(entries):
            if total <= limit_bytes:
                break
            try:
                os.remove(path)
            except FileNotFoundError:
                continue
            total -= size
    except OSError:  # pragma: no cover — a full cache is not a failed task
        logger.debug("Could not prune the worker input cache", exc_info=True)


def _rewrite_params(params: dict, local: dict[str, str]):
    """Replace every artifact id in the params with its local path."""
    if isinstance(params, dict):
        return {key: _rewrite_params(value, local) for key, value in params.items()}
    if isinstance(params, list):
        return [_rewrite_params(item, local) for item in params]
    if isinstance(params, str):
        return local.get(params, params)
    return params


def _mark(audio, sample_rate: int, params: dict):
    """Provenance-mark synthetic audio before it is encoded (EU AI Act 50(2)).

    The decision is the CONTROL PLANE user's, carried on the assignment: the
    watermark pref belongs to whoever asked for the audio, not to whoever owns
    the GPU that rendered it. So ``force=True`` — a worker machine with the
    pref switched off must not strip the mark off someone else's output.

    Absent field means mark. A worker running an older control plane's
    assignment has no way to learn the user's answer, and the failure that
    matters here is shipping unmarked synthetic speech.
    """
    if params.get("watermark") is False:
        return audio
    try:
        from services.watermark import mark_synthetic  # noqa: PLC0415

        return mark_synthetic(
            audio, sample_rate, context="worker.executor.tts", force=True
        )
    except Exception:
        # mark_synthetic never raises by contract; an import failure on a
        # stripped-down worker install still must not lose the audio.
        logger.warning("Provenance marking unavailable on this worker", exc_info=True)
        return audio


def _parse_params(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def encode_inline(payload: bytes) -> str:
    """Base64 for transports that cannot carry raw bytes (the HTTP mirror)."""
    return base64.b64encode(payload).decode("ascii")


__all__ = [
    "INLINE_LIMIT_BYTES",
    "INPUT_CACHE_LIMIT_BYTES",
    "INPUT_ERRORS_PARAM",
    "InputFetcher",
    "TaskExecutor",
    "TaskFailure",
    "UnsupportedOperation",
    "default_input_dir",
]
