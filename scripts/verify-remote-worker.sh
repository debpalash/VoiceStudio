#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# verify-remote-worker.sh — Non-destructive remote-worker acceptance
#
# Usage:
#   scripts/verify-remote-worker.sh --worker-id ID --ssh-target USER@HOST
#
# Environment equivalents: WORKER_ID, WORKER_SSH_TARGET, VOICESTUDIO_API,
# WORKER_CONTROL_PORT. The active worker may be discovered when there is
# exactly one connected worker; SSH_TARGET may also come from WORKER_SSH_TARGET.
# ──────────────────────────────────────────────────────────────────────
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

API="${VOICESTUDIO_API:-http://127.0.0.1:8000}"
CONTROL_PORT="${WORKER_CONTROL_PORT:-7443}"
WORKER_ID="${WORKER_ID:-}"
SSH_TARGET="${WORKER_SSH_TARGET:-}"
SSH_IDENTITY="${WORKER_SSH_IDENTITY:-}"
WORKER_START_COMMAND="${WORKER_START_COMMAND:-OMNIVOICE_WORKER_MODE=1 uv run uvicorn backend.main:app --host 127.0.0.1 --port 3900}"
SSH_OPTS=(-n -o BatchMode=yes -o ConnectTimeout=10)
if [ -x .venv/bin/python ]; then
    PYTEST=(.venv/bin/python -m pytest)
else
    PYTEST=(uv run pytest)
fi

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
FAILURES=0
MANUALS=0

pass() { echo -e "  ${GREEN}✓ PASS${NC} $1"; }
fail() { echo -e "  ${RED}✗ FAIL${NC} $1"; FAILURES=$((FAILURES + 1)); }
manual() { echo -e "  ${YELLOW}⚠ MANUAL${NC} $1"; MANUALS=$((MANUALS + 1)); }
info() { echo -e "  ${CYAN}→${NC} $1"; }
header() { echo -e "\n${BOLD}$1${NC}"; }

usage() {
    echo "Usage: $0 [--worker-id ID] [--ssh-target USER@HOST] [--ssh-identity PATH] [--api URL] [--control-port PORT] [--worker-start-command CMD]"
    echo "Environment: WORKER_ID, WORKER_SSH_TARGET, WORKER_SSH_IDENTITY, WORKER_START_COMMAND, VOICESTUDIO_API, WORKER_CONTROL_PORT"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --worker-id) WORKER_ID="${2:-}"; shift 2 ;;
        --ssh-target) SSH_TARGET="${2:-}"; shift 2 ;;
        --ssh-identity) SSH_IDENTITY="${2:-}"; shift 2 ;;
        --api) API="${2:-}"; shift 2 ;;
        --control-port) CONTROL_PORT="${2:-}"; shift 2 ;;
        --worker-start-command) WORKER_START_COMMAND="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

TMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

json_value() {
    local expression="$1"
    python3 -c "import json,sys; d=json.load(sys.stdin); v=$expression; print('' if v is None else v)"
}

run_contract() {
    local label="$1"; shift
    if "$@" >"$TMP_DIR/contract.log" 2>&1; then
        pass "$label"
        return 0
    fi
    fail "$label"
    sed 's/^/      /' "$TMP_DIR/contract.log" | tail -20
    return 1
}

header "VoiceStudio — Remote Worker Acceptance"
echo "   API: $API | control port: $CONTROL_PORT | $(date)"

# Preconditions are intentionally fatal: continuing would produce misleading
# phase results. Never detect the remote worker with `pgrep -f`: the SSH shell
# contains the searched command and makes that test pass by matching itself.
header "Preconditions"

if ! command -v lsof >/dev/null 2>&1; then
    fail "lsof is required to prove listener ownership"
elif ! lsof -nP -iTCP:"$CONTROL_PORT" -sTCP:LISTEN >"$TMP_DIR/listeners" 2>&1; then
    fail "no control-plane listener on TCP $CONTROL_PORT"
else
    LISTENER_PIDS="$(awk 'NR > 1 {print $2}' "$TMP_DIR/listeners" | sort -u)"
    LISTENER_COUNT="$(printf '%s\n' "$LISTENER_PIDS" | sed '/^$/d' | wc -l | tr -d ' ')"
    if [ "$LISTENER_COUNT" = 1 ]; then
        pass "exactly one listener PID on TCP $CONTROL_PORT ($LISTENER_PIDS)"
    else
        fail "expected exactly one listener PID on TCP $CONTROL_PORT; found $LISTENER_COUNT"
        sed 's/^/      /' "$TMP_DIR/listeners"
    fi
fi

if ! curl -fsS "$API/workers" >"$TMP_DIR/workers.json" 2>"$TMP_DIR/curl.err"; then
    fail "control-plane HTTP API is unreachable at $API"
else
    if [ -z "$WORKER_ID" ]; then
        WORKER_ID="$(python3 - "$TMP_DIR/workers.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
rows = [w for w in d.get("workers", []) if w.get("connected")]
print(rows[0].get("id", "") if len(rows) == 1 else "")
PY
)"
    fi
    if [ -z "$WORKER_ID" ]; then
        fail "pass --worker-id (automatic discovery requires exactly one connected worker)"
    elif python3 - "$TMP_DIR/workers.json" "$WORKER_ID" <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); wid = sys.argv[2]
w = next((x for x in d.get("workers", []) if x.get("id") == wid), None)
assert w, "worker is not enrolled"
assert w.get("connected"), "worker is not connected"
assert w.get("status") == "ready", f'worker status is {w.get("status")!r}, not ready'
assert w.get("approved") or w.get("consent_granted"), "worker lacks consent"
assert w.get("enabled", True), "worker is disabled"
PY
    then
        pass "worker $WORKER_ID is connected, ready, approved, and enabled"
    else
        fail "worker $WORKER_ID is not ready"
    fi
fi

if [ -z "$SSH_TARGET" ]; then
    fail "--ssh-target is required for remote OS/GPU preflight"
else
    # -n plus stdin/stdout/stderr redirection prevents a backgrounded remote
    # child from retaining the SSH channel. The dotted path pattern cannot
    # match the SSH shell's own command line.
    if [ -n "$SSH_IDENTITY" ]; then
        SSH_OPTS+=(-i "$SSH_IDENTITY")
    fi
    if ssh "${SSH_OPTS[@]}" "$SSH_TARGET" \
        'uname -srm; nvidia-smi --query-gpu=name --format=csv,noheader; pgrep -af "[.]venv/bin/python"' \
        </dev/null >"$TMP_DIR/remote-preflight" 2>&1; then
        if grep -q '^Linux ' "$TMP_DIR/remote-preflight" && grep -Eiq 'NVIDIA|RTX|Tesla|A[0-9]+|L[0-9]+' "$TMP_DIR/remote-preflight"; then
            pass "worker reports Linux and an NVIDIA GPU via SSH"
            sed 's/^/      /' "$TMP_DIR/remote-preflight" | head -3
        else
            fail "worker did not report both Linux and an NVIDIA GPU"
            sed 's/^/      /' "$TMP_DIR/remote-preflight"
        fi
    else
        fail "SSH preflight failed for $SSH_TARGET"
        sed 's/^/      /' "$TMP_DIR/remote-preflight"
    fi
fi

printf -v SSH_TARGET_QUOTED '%q' "$SSH_TARGET"
printf -v WORKER_START_QUOTED '%q' "$WORKER_START_COMMAND"

if [ "$FAILURES" -gt 0 ]; then
    echo -e "\n${RED}Preconditions failed; phase checks were not run.${NC}"
    exit 1
fi

WORKER_LABEL="$(python3 - "$TMP_DIR/workers.json" "$WORKER_ID" <<'PY'
import json, sys
w = next(x for x in json.load(open(sys.argv[1]))["workers"] if x["id"] == sys.argv[2])
print(w.get("name") or w.get("label") or w["id"])
PY
)"

header "Phase 4 — Dispatch preflight"
read -r ABSENT_ENGINE ABSENT_REPO <<EOF
$(python3 - "$TMP_DIR/workers.json" "$WORKER_ID" <<'PY'
import json, sys
w = next(x for x in json.load(open(sys.argv[1]))["workers"] if x["id"] == sys.argv[2])
for c in w.get("capabilities", []):
    repos = c.get("repo_ids") or []
    if c.get("downloaded") is False and repos:
        print(c.get("engine", ""), repos[0]); break
PY
)
EOF
if [ -z "${ABSENT_ENGINE:-}" ] || [ -z "${ABSENT_REPO:-}" ]; then
    # The management snapshot intentionally omits capability details. MLX is
    # physically unsupported on a Linux/CUDA worker, so this catalog engine is
    # genuinely absent without inspecting or modifying its cache.
    ABSENT_ENGINE="mlx-audio"
    ABSENT_REPO="mlx-community/Kokoro-82M-bf16"
    info "using the bundled Apple-Silicon-only engine as the absent CUDA model"
fi
if [ -n "${ABSENT_ENGINE:-}" ] && [ -n "${ABSENT_REPO:-}" ]; then
    HTTP_CODE="$(curl -sS -o "$TMP_DIR/generate.json" -w '%{http_code}' \
        -F 'text=Remote worker acceptance probe.' -F 'language=en' -F "engine=$ABSENT_ENGINE" \
        "$API/generate" 2>"$TMP_DIR/generate.err" || true)"
    if [ "$HTTP_CODE" = 409 ] && python3 - "$TMP_DIR/generate.json" "$ABSENT_REPO" "$WORKER_LABEL" <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); detail = d.get("detail", {})
assert detail.get("error") == "model_not_downloaded"
assert sys.argv[2] in detail.get("repo_ids", [])
assert sys.argv[3] in (detail.get("target_label", "") + detail.get("message", ""))
PY
    then
        pass "absent $ABSENT_REPO returned typed 409 naming $WORKER_LABEL before dispatch"
    else
        fail "absent $ABSENT_REPO did not return the expected typed 409 (HTTP $HTTP_CODE)"
        sed 's/^/      /' "$TMP_DIR/generate.json"
    fi
fi
run_contract "inconclusive model probe fails open" \
    "${PYTEST[@]}" -q tests/test_gpu_gateway.py::test_missing_download_fact_fails_open

header "Phase 5 — Target-aware models and progress"
run_contract "progress rows are keyed independently by (target, repo_id)" \
    bun run --cwd electron test -- src/shared/test/modelStoreInstallError.test.jsx
manual "Model-list hardware check: on a macOS control plane select $WORKER_LABEL, open Settings → Models, search for 'mlx-community/' (expect zero rows), then clear the search and confirm CUDA-appropriate models are present. The management API deliberately does not expose the worker's full capability list."
manual "Concurrent-download hardware check (downloads only; never delete caches): choose a repo reported downloaded=false on BOTH targets, open Settings → Models, start it on Local and $WORKER_LABEL together, and confirm two rows remain visible with targets Local and $WORKER_LABEL. Watch: curl -N '$API/setup/download-stream'."

header "Phase 6 — Offline gallery"
run_contract "offline present-model preview stays local and gallery failure is silent" \
    "${PYTEST[@]}" -q tests/test_gallery_previews.py::test_offline_gallery_miss_still_renders_locally tests/test_gallery_previews.py::test_offline_fails_silently
run_contract "tampered gallery manifests are rejected" \
    "${PYTEST[@]}" -q 'tests/test_gallery_previews.py::test_verify_rejects_tampering'
manual "True-airplane-mode UI check: disconnect the control plane from all networks, preview an installed archetype, and confirm audio renders locally; open Gallery and confirm there is no toast or blocked control. Reconnect afterward. Do not change or remove any model cache."

header "Phase 7 — Dubbing placement"
if curl -fsS "$API/workers/target?op=dub" >"$TMP_DIR/dub-target.json" && \
   python3 - "$TMP_DIR/dub-target.json" <<'PY'
import json, sys
# Dubbing dispatches the coarse `dub_segments` worker op (dub_generate.py), so
# a remote answer is CORRECT once that port lands. What must never happen is
# the picker contradicting itself: claiming remote placement for an operation
# the control plane does not list as remotely producible.
d=json.load(open(sys.argv[1])); a=d.get("active", {})
ops=set(d.get("remote_operations") or [])
if a.get("remote"):
    assert "dub" in ops, ("picker claims remote for an op with no producer", d)
    assert a.get("label") != "Local", d
else:
    assert a.get("label") == "Local", d
PY
then
    pass "dubbing placement agrees with the advertised remote_operations"
else
    fail "dubbing picker API contradicts its own remote_operations list"
fi
run_contract "dubbing and dictation picker presentation stays local" \
    bun run --cwd electron test -- src/shared/components/GpuTarget.test.jsx

header "Phase 8 — Audiobook fallback and dictation"
run_contract "remote audiobook chapter and local fallback contracts" \
    "${PYTEST[@]}" -q tests/test_audiobook_remote.py tests/test_gpu_gateway.py::test_notice_names_the_machine_that_was_skipped
if curl -fsS "$API/workers/target?op=dictation" >"$TMP_DIR/dictation-target.json" && \
   python3 - "$TMP_DIR/dictation-target.json" <<'PY'
import json, sys
d=json.load(open(sys.argv[1])); a=d.get("active", {})
assert not a.get("remote") and a.get("label") == "Local", d
PY
then
    pass "dictation resolves to Local"
else
    fail "dictation did not resolve to Local"
fi
manual "Worker-loss audiobook check: start a multi-chapter audiobook with $WORKER_LABEL selected; after one chapter completes, stop ONLY the worker with: ssh -n $SSH_TARGET_QUOTED 'pkill -TERM -x python3.11 || pkill -TERM -x python3' </dev/null >/tmp/voicestudio-worker-stop.log 2>&1. Confirm remaining chapters render locally and exactly ONE combined fallback notice appears. Restart with: ssh -n $SSH_TARGET_QUOTED \"nohup sh -c $WORKER_START_QUOTED </dev/null >/tmp/voicestudio-worker-start.log 2>&1 &\" </dev/null >/tmp/voicestudio-worker-ssh.log 2>&1. Then verify Dictation still transcribes locally. This intentionally disruptive step is never run automatically."

header "Result"
if [ "$FAILURES" -gt 0 ]; then
    echo -e "${RED}$FAILURES failure(s), $MANUALS manual step(s) outstanding.${NC}"
    exit 1
fi
echo -e "${GREEN}No automated failures.${NC} ${YELLOW}$MANUALS manual step(s) remain unverified and were not reported as PASS.${NC}"
