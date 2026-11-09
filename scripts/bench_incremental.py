"""Phase 4.1 benchmark — is "Regen N changed" actually ≤5s on a real clip?

Drives the full pipeline via HTTP: ingest URL → prep → transcribe → full
generate → edit one segment → regen-only → time it. Prints both wall-clocks.

Success criterion: incremental regen ≤ 5.0s (roadmap Phase 4.1 exit target).

Usage:
    uv run python scripts/bench_incremental.py [URL]

Default URL is the Fireship clip the user picked. The server must be live on
the configured backend port with the models loaded (first cold run can take minutes).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError

_APP_CONFIG = json.loads(
    (Path(__file__).resolve().parents[1] / "config" / "app.json").read_text(
        encoding="utf-8"
    )
)
API = f"http://127.0.0.1:{os.environ.get('OMNIVOICE_PORT', _APP_CONFIG['ports']['backend'])}"
DEFAULT_URL = "https://www.youtube.com/watch?v=ZzI9JE0i6Lc"
TARGET_S = 5.0
# Cap baseline segments to keep full-dub wall-clock tractable. The incremental
# target is single-segment regen speed; 30 segs is plenty to populate caches
# for that measurement. Override via env.
MAX_SEGS = int(os.environ.get("BENCH_MAX_SEGS", "30"))


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode())


def stream_sse(url: str, on_event):
    """Parse SSE frames. Handles both shapes the backend emits:

      1. `data: {"type": "X", ...}` (prep pipeline, dub_generate)
      2. `event: X\\ndata: {...}`  (dub_transcribe — named events)

    Normalises both into {"type": <name>, ...payload}. `on_event` returning
    truthy stops the loop.
    """
    req = urllib.request.Request(f"{API}{url}")
    pending_event = None
    last = None
    # 1 hour read window — full-dub on a 489-seg clip can run long on Apple Silicon.
    with urllib.request.urlopen(req, timeout=3600) as r:
        for raw in r:
            line = raw.decode().rstrip("\r\n")
            if line.startswith("event:"):
                pending_event = line[6:].strip()
                continue
            if not line.startswith("data:"):
                # Blank line between frames resets named-event state.
                if not line:
                    pending_event = None
                continue
            body = line[5:].strip()
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                continue
            if pending_event and isinstance(payload, dict) and "type" not in payload:
                payload = {"type": pending_event, **payload}
            last = payload
            if on_event(payload):
                return payload
    return last


def wait_prep(task_id: str) -> dict:
    print(f"  · streaming /tasks/stream/{task_id} …")

    def cb(ev):
        t = ev.get("type") or ev.get("stage")
        if t in ("ready", "done"):
            return True
        if t == "error":
            raise RuntimeError(f"prep error: {ev}")
        if ev.get("type") == "progress":
            s = ev.get("stage") or ""
            p = ev.get("pct")
            if p is not None:
                print(f"    prep {s} {int(p)}%")
            else:
                print(f"    prep {s}")
        return False

    return stream_sse(f"/tasks/stream/{task_id}", cb)


def transcribe(job_id: str) -> list[dict]:
    print(f"  · streaming /dub/transcribe-stream/{job_id} …")
    final: list[dict] = []

    def cb(ev):
        t = ev.get("type")
        if t == "final":
            # The diarised post-pass replaces per-chunk segs; this is what we want.
            final.extend(ev.get("segments", []))
        if t == "done":
            return True
        if t == "error":
            raise RuntimeError(f"transcribe error: {ev}")
        return False

    stream_sse(f"/dub/transcribe-stream/{job_id}", cb)
    return final


DUB_SEG_FIELDS = {"start", "end", "text", "instruct", "profile_id", "speed", "gain", "target_lang"}


def to_dub_segment(s: dict) -> dict:
    """Project a raw transcribe-output segment onto DubSegment's schema."""
    out = {k: v for k, v in s.items() if k in DUB_SEG_FIELDS}
    out.setdefault("text", s.get("text", ""))
    out.setdefault("start", float(s.get("start", 0.0)))
    out.setdefault("end", float(s.get("end", 0.0)))
    return out


def generate(job_id: str, segments: list[dict], *, regen_only: list[str] | None = None) -> dict:
    """POST → {task_id}, then stream /tasks/stream/{task_id} for SSE progress."""
    seg_ids = [str(s.get("id") or f"seg_{i}") for i, s in enumerate(segments)]
    body = {
        "segments": [to_dub_segment(s) for s in segments],
        "segment_ids": seg_ids,
        "language": "English",
        "language_code": "en",
    }
    if regen_only is not None:
        body["regen_only"] = regen_only

    r = post(f"/dub/generate/{job_id}", body)
    task_id = r["task_id"]

    done_ev = None

    def cb(ev):
        nonlocal done_ev
        t = ev.get("type")
        if t == "progress":
            print(f"    gen {ev.get('current', 0)}/{ev.get('total', '?')}",
                  end="\r", flush=True)
        elif t == "done":
            done_ev = ev
            return True
        elif t == "error":
            raise RuntimeError(f"generate error: {ev}")
        return False

    stream_sse(f"/tasks/stream/{task_id}", cb)
    print()
    if not done_ev:
        raise RuntimeError("generate task ended without 'done' event")
    return done_ev


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    print(f"Benchmark target: {TARGET_S}s incremental regen")
    print(f"Fixture URL:      {url}")

    try:
        urllib.request.urlopen(f"{API}/system/info", timeout=3)
    except (HTTPError, URLError) as e:
        sys.exit(f"Server not reachable at {API}: {e}")

    # 1. Ingest URL
    print("\n[1/4] Ingest URL …")
    r = post("/dub/ingest-url", {"url": url})
    job_id, task_id = r["job_id"], r["task_id"]
    print(f"  job_id={job_id} task_id={task_id}")

    # 2. Wait for prep
    print("\n[2/4] Prep (download / extract / demucs / scene) …")
    t0 = time.perf_counter()
    wait_prep(task_id)
    print(f"  prep: {time.perf_counter() - t0:.1f}s")

    # 3. Transcribe
    print("\n[3/4] Transcribe …")
    t0 = time.perf_counter()
    segments = transcribe(job_id)
    print(f"  transcribe: {time.perf_counter() - t0:.1f}s · {len(segments)} segments")
    if not segments:
        sys.exit("No segments returned — can't benchmark.")
    if len(segments) > MAX_SEGS:
        print(f"  · capping to first {MAX_SEGS} segs for baseline (BENCH_MAX_SEGS to change)")
        segments = segments[:MAX_SEGS]

    # 4a. Full generate (baseline — produces on-disk seg_N.wav)
    print("\n[4a/4] FULL generate (baseline) …")
    t0 = time.perf_counter()
    done = generate(job_id, segments)
    t_full = time.perf_counter() - t0
    print(f"  full generate: {t_full:.2f}s · {done.get('segments_processed', '?')} segs")

    # 4b. Edit ONE segment, regen only it
    edited = list(segments)
    edit_id = str(edited[0].get("id") or "seg_0")
    edited[0] = {**edited[0], "text": edited[0].get("text", "") + " (edited)"}

    print(f"\n[4b/4] INCREMENTAL regen (seg id={edit_id}) …")
    t0 = time.perf_counter()
    done_inc = generate(job_id, edited, regen_only=[edit_id])
    t_inc = time.perf_counter() - t0
    print(f"  incremental regen: {t_inc:.2f}s · {done_inc.get('segments_processed', '?')} segs")

    # 5. Verdict
    print("\n── verdict ──")
    print(f"  full dub:     {t_full:>7.2f}s")
    print(f"  incremental:  {t_inc:>7.2f}s   target ≤ {TARGET_S:.1f}s")
    passed = t_inc <= TARGET_S
    print(f"  exit 4.1:     {'✅ PASS' if passed else '❌ MISS'}")
    print(f"  speedup vs full: {t_full / t_inc:.1f}×")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
