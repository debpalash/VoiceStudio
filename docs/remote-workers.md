# Remote GPU workers

Run VoiceStudio on this machine, but hand individual jobs to GPUs on your other
machines. Results come back here.

This is **opt-in and off by default**. Until you turn it on and approve a
worker, nothing leaves your computer, no port is opened, and the app behaves
exactly as it did before.

Worker management is an admin surface. In Docker/server mode, viewing status
works during bare bootstrap, but joining, enabling, approving, issuing keys,
disconnecting, or removing machines remotely requires `OMNIVOICE_API_KEY`.
The share PIN and trusted-network exemptions authorize playback, not worker
administration.

> **Not the same as [Remote backend](remote-gpu.md).** That points this app at
> a backend running somewhere else, so the whole app — your projects, your
> voices, your history — lives on that machine. This keeps everything here and
> only sends out individual tasks. Both still work; pick whichever matches what
> you want.

---

## What you need

* VoiceStudio builds with a compatible worker protocol on both machines. The
  durable-enrollment v2 boundary requires updating both sides; the app refuses
  an unsafe pairing with an update instruction before any task runs.
* The worker machine must be able to **reach** this one over the network. Same
  LAN is enough at home; across networks, a VPN such as
  [Tailscale](https://tailscale.com/) is the reliable answer. The worker dials
  out to the control plane, so the *worker* never needs a public address or a
  forwarded port — but this machine does need to be reachable.
* The engine you want to use must be installed on the worker. A worker reports
  what it actually has, and the scheduler only sends it work it can run.

## Setting it up

**1. On this machine (the one you work on):**

Settings → System → Remote workers → turn on **Use remote workers**.

The panel shows the address workers should connect to, and a **Generate token**
button.

For a Docker Compose Studio, start it with the host address workers can reach;
Compose publishes the TLS worker port (`7443`) separately from the loopback-only
web UI:

```bash
OMNIVOICE_WORKER_ENDPOINT_HOST=192.168.1.20 \
OMNIVOICE_WORKER_PUBLISH_HOST=0.0.0.0 docker compose \
  -f deploy/docker-compose.yml --profile gpu up -d
```

Use the host's LAN or private-overlay address, not the container's bridge IP.
The worker port is published on loopback by default; setting
`OMNIVOICE_WORKER_PUBLISH_HOST=0.0.0.0` is the explicit opt-in that makes it
reachable from the LAN. Keep the default when a host-side tunnel or proxy
provides reachability. Until remote workers are enabled in VoiceStudio, the
container has no process listening on the published control-plane port.

**2. Generate a join code.**

The panel shows it as text **and as a QR code**, with a countdown. Copy it, or
scan the QR with your phone if the worker machine is across the room. It is
shown once, works once, and expires after 15 minutes — only its hash is stored
here, so it cannot be shown again. If you lose it, generate another.

**3. On the worker machine:**

Settings → System → Remote workers → **Lend this machine's GPU** → paste the
join code → **Join**. Nothing has to be restarted, and no environment variables
are involved.

The worker generates its own key pair on first run, presents the code once to
enroll, and proves possession of that key on every later connection. The code
is spent at that point and never used again. The control plane's address comes
with it and is remembered, so the machine reconnects on its own after a
restart; the same panel's switch stops and resumes that without asking for
another code.

Headless machines still take the environment route:

```bash
OMNIVOICE_WORKER_TOKEN='ovw_…' OMNIVOICE_WORKER_MODE=1 \
  uv run uvicorn backend.main:app --host 127.0.0.1 --port 3900
```

Run that command from the repository root. Uvicorn hosts the application
lifespan that owns the worker agent; binding it to loopback means no Studio UI
is exposed, and no browser interaction is required.

For a worker-only NVIDIA Docker container, use the included Compose profile:

```bash
OMNIVOICE_WORKER_TOKEN='ovw_…' docker compose \
  -f deploy/docker-compose.yml --profile worker-gpu up -d
```

Use `worker-rocm` instead for AMD GPUs. Neither profile publishes an HTTP
port. The control-plane address inside the join code must be reachable from
the container, so use its LAN or private-overlay address rather than
`127.0.0.1`. Worker identity, pinned certificate, and endpoint persist in the
profile's data volume. After the first successful enrollment, restarts ignore
that same now-spent environment token and reconnect by proving possession of
the identity key. Replacing it with a fresh join code can move a non-revoked
worker to another control plane. A revoked identity remains revoked; start
with a fresh worker data volume to generate a new identity.

The container reports healthy only after the control plane accepts its initial
registration. A missing, malformed, expired, or rejected join code leaves the
worker service running for diagnosis but unhealthy; inspect its logs, correct
the token, and recreate the container.

`OMNIVOICE_WORKER_MODE` wins over the in-app switch when it is set, so a
deployment that pins worker mode cannot be turned off from the UI — the panel
says so instead of showing a switch that springs back.

**4. Approve the worker.**

It appears in the list on this machine. Approving it is what allows your audio,
reference voices, and text to be sent there — consent is recorded per worker,
because agreeing to use your own desktop is not agreeing to use whatever gets
added later.

## Sharing one GPU machine with other people

The setup above has the GPU machine dial this app. That is the default and the
right choice for a machine only you use — but it connects to exactly one app.
Pointing it somewhere else means editing its settings and restarting, which
disconnects whoever had it.

If more than one person needs the same GPU box, turn it around: let the box
**accept connections** instead.

**On the GPU machine:** Settings → System → Remote workers → **Accept
connections**. It listens on `127.0.0.1:7444` to begin with, which only that
machine can reach — set **Reachable from** to your network address to let other
machines in.

Then **Add a person** for each panel that should have access. You get a
connection string:

```
ovnode://ovnode_xxxxxxxx@192.168.0.110:7444?fingerprint=<64-hex-digits>
```

Copy it once — it is not shown again. Give a separate one to each person.

**On each person's machine:** Settings → System → Remote workers → **Connect to
a GPU machine**, paste the string. That is the whole flow: no shell access to
the GPU box, no restart, and everyone stays connected at the same time. If two
people send work at once, the second job waits for a free slot rather than
failing.

**Removing someone** revokes only their connection string. Everyone else keeps
working, which is why each person gets their own.

**Who is using it** is on the GPU machine, under Accept connections: every
panel currently attached, where it connected from, how many jobs it has run,
and a **Disconnect** button.

**Disconnect and Remove do different things.** Disconnect ends the session now
and keeps that person out for a minute — use it to get someone off the card
immediately. Their app reconnects by itself after that, because their
connection string is still valid. To stop someone for good, remove their
connection string instead.

> **Keep the connection string private.** It contains the API key and the GPU
> machine's certificate fingerprint. VoiceStudio checks that fingerprint before
> sending credentials, audio, or jobs; a mismatch fails closed. Every inbound
> connection uses TLS with no plaintext fallback. The design is recorded in
> [the decision record](adr/inbound-node-mode.md).

## What you can change

| Control | What it does |
|---|---|
| Enable / disable | Stop sending new work without removing the worker |
| Preferred | Prefer this worker when several can run a task |
| Resume | Clear a paused worker after you've fixed it |
| Remove | Revoke its key — it cannot reconnect without a new token |

That is the whole surface, deliberately. **Preferred** pins new work to that
worker; if it is asleep, VoiceStudio names that worker instead of silently
sending the job elsewhere. There are no routing weights or per-model
concurrency settings: concurrency is measured from free VRAM at runtime because
a configured value silently corrupts output on compiled models and crashes
small cards.

## What runs remotely

**Speech synthesis, audiobook chapters, and dub segment synthesis.** Audiobooks
are dispatched one chapter at a time. A dub sends all fresh segments as one
coarse task and receives their WAVs in one result bundle; fitting, assembly and
RVC still run on this machine. If a remote multi-unit render fails, its local
fallback is reported once. ASR, diarization and translation also remain local. Dictation always
runs here, deliberately and permanently, because there latency *is* the
feature. The remaining operations are being ported one at a time.

### Voice identity parity

For TTS, the worker receives the complete local rendering contract: the voice
profile's reference audio and transcript, its pinned seed, model quality
controls, text chunking/crossfade settings, and output effect preset. The
worker runs the same native or generic rendering pipeline as local
`/generate`; selecting a gallery voice therefore does not turn it into a new
random voice merely because it was rendered on another GPU.

The picker knows this. It resolves against the surface you are on, so a chosen
worker reads **Local** on a tab whose work has no remote path yet and names the
reason, instead of showing a green dot next to a GPU that receives nothing. The
same choice is in the status bar at the bottom of the window — the **Compute**
control, which also carries the master switch and can mint a join code without
opening Settings. It appears only once you have opted in or enrolled a machine.
The Dictation surface states that it always uses this machine without showing
the generic "not ported yet" notice.

For protocol development, a task can also be placed by hand with
`POST /workers/tasks` — a **development-only** endpoint. It is admin-gated,
sits behind the same opt-in as everything else here, takes a mandatory
deadline, submits one task and waits for it. On desktop that means loopback;
in server mode a remote caller needs `OMNIVOICE_API_KEY`. It is not a stable
API and goes away once generation routes itself.

## How work is placed

A task goes to a worker that is connected, approved, enabled, has the engine,
has a free slot, and is not paused. An explicitly preferred worker is a hard
choice. Without one, VoiceStudio chooses the least-busy eligible worker and
breaks ties in favour of a worker that already has the model loaded — a warm
model is seconds away where a cold one can be minutes.
Model identities are stable scheduling keys; the worker reports a separate
human-readable model name, so label changes do not split capacity or history.

If every capable worker is busy, the task waits. If **no** worker can run it at
all, it fails immediately and says so, rather than waiting for something that
will never happen.

## When things go wrong

**A worker disconnects mid-task.** Nothing is failed straight away. It has a
grace window to come back, and if it returns carrying a finished result, that
result is used — the task is never run twice just because a network blip
happened. Only when the window expires is the task retried elsewhere.

Each attempt retains the deadline budget granted at dispatch, including after a
worker disconnect or control-plane restart; changed worker availability cannot
shorten an in-flight attempt’s execution allowance.

**A worker fails repeatedly.** After three consecutive failures that are
actually its fault, it is paused for a minute, then automatically given one
task to prove itself. Repeated trips back off further, up to thirty minutes.
Being busy, being asked for an engine it doesn't have, or losing its network
connection are *not* counted against it.

Long-running work sends explicit keepalive frames. They let a slow render live
past the two-minute progress lease, but cannot extend it beyond the current
phase budget when the worker is genuinely stuck.

The row tells you what happened in words — "Paused after 3 failures … retrying
in 45s" — and **Resume** clears it immediately when you've fixed the machine.

**You quit the app mid-task.** Remote work keeps running on the worker. On next
launch VoiceStudio recovers those tasks and reconciles with each worker about
what is genuinely still in flight.

**Version or feature mismatch.** Release numbers alone do not prove that a
worker understands every additive command. Registration negotiates an explicit
protocol range and declares named features for task inputs, progress leases,
remote model downloads, and the voice-identity render pipeline. Durable
enrollment changed the handshake from protocol v1 to v2, so that boundary is
intentionally incompatible in either direction. A worker outside the supported
protocol range, or one missing a required feature, is refused with
`UPGRADE_REQUIRED` and an update instruction before any task runs. It can never
silently render without reference audio, substitute a different voice, or leave
a download stuck at 0%.

Every remote failure includes a concrete next step. Capacity, missing models,
expired leases or sessions, authentication, rejected inputs, and result upload
failures are shown as named errors with advice to retry, reconnect, install the
model, free resources, or re-enroll as appropriate; they do not reach the UI
with a blank hint.

## Security

The guarantees below describe the **default** setup, where the GPU machine
dials this app. "Accept connections" mode trades several of them away
deliberately — see the warning in
[Sharing one GPU machine](#sharing-one-gpu-machine-with-other-people) and
[the decision record](adr/inbound-node-mode.md). In that mode there is no
encryption and no server verification; the connection string is the whole of
admission, and it is only as private as the network it crosses. Everything else
below still holds: identity is still a key the GPU machine never sends,
revoking still survives a restart, and engines are still named from a fixed
registry.

* **All traffic is TLS.** There is no way to disable verification.
* This machine generates its own certificate. The enrollment token carries that
  certificate's fingerprint, and the worker pins it — so a machine on the same
  café Wi-Fi cannot impersonate your control plane.
* **A worker's identity is a key it generates and never sends.** The worker ID
  is a display name, not a credential; knowing it gets an attacker nothing.
* **Removing a worker revokes its key**, and that survives restarting the app.
* Idle worker sessions use TLS keepalives, so NAT mappings stay open without
  the control plane mistaking its own keepalive interval for abusive traffic.
* Tasks name engines from a fixed registry, never file paths — a path here
  would be remote code execution on every worker.

**What a worker can see:** to synthesise your text it has to receive that text,
and to clone a voice it has to receive the reference audio. There is no way
around that. Only add machines you control, which is why approval is per
worker and never implicit.

## Turning it off

Settings → System → Remote workers → toggle off, or the **Compute** control in
the status bar at the bottom of the window. The listening socket closes and the
background loops stop. Your enrolled workers and their settings are kept, so
turning it back on does not mean setting everything up again.

On a machine that is lending its GPU, the switch in **Lend this machine's GPU**
stops it taking work. The enrollment survives, so turning it back on needs no
new code.

## Environment variables

| Variable | Purpose |
|---|---|
| `OMNIVOICE_REMOTE_WORKERS` | `1`/`0` — enable without the UI (headless, Docker) |
| `OMNIVOICE_WORKER_PORT` | Control-plane port (default `7443`) |
| `OMNIVOICE_WORKER_ENDPOINT_HOST` | Override the address shown to workers |
| `OMNIVOICE_WORKER_PUBLISH_HOST` | Compose-only host address for publishing the control-plane port (default `127.0.0.1`; set `0.0.0.0` to opt into LAN reachability) |
| `OMNIVOICE_INBOUND_NODE` | `1`/`0` — accept connections from other panels |
| `OMNIVOICE_INBOUND_BIND` | Address to accept them on (default `127.0.0.1`) |
| `OMNIVOICE_INBOUND_PORT` | Port to accept them on (default `7444`) |
| `OMNIVOICE_ENGINE_IDLE_UNLOAD_SECONDS` | How long a model may sit unused before its VRAM is handed back (default `600`, minimum `5`) |
| `OMNIVOICE_IDLE_SWEEP_SECONDS` | How often that check runs (default `60`, minimum `1`) |
| `OMNIVOICE_WORKER_MODE` | `1` on the worker machine — overrides the in-app switch |
| `OMNIVOICE_WORKER_TOKEN` | Join code, consumed on first successful enrollment; a persisted container value is ignored on later restarts |
| `OMNIVOICE_WORKER_ENDPOINT` | Control plane to dial when no code is being redeemed; normally remembered from the code |

`OMNIVOICE_ENGINE_IDLE_UNLOAD_SECONDS` and `OMNIVOICE_IDLE_SWEEP_SECONDS` exist
so the ten-minute unload can be watched in a minute while testing — set them
together, since shortening only the threshold still means waiting a full sweep
interval to see it fire. Values that are unparseable or below the floor are
ignored with a warning rather than honoured: a zero threshold would unload a
model the instant it went idle and reload it for the next request.

### Two idle timers, not one

A worker node runs the full app, so two independent reapers can release the
same model and they are configured separately:

| Timer | Default | Set with |
|---|---|---|
| Engine registry — drops the cached engine instance and, for VoiceStudio, the shared model with it | 600 s | `OMNIVOICE_ENGINE_IDLE_UNLOAD_SECONDS` |
| In-process model reaper — the backstop, also releases the dictation ASR, the watermark models, the word-alignment models and the speaker-diarization pipeline | 900 s | `OMNIVOICE_IDLE_TIMEOUT` (or Settings) |

In practice the first one gets there first and the second finds nothing to do.
Shortening only `OMNIVOICE_ENGINE_IDLE_UNLOAD_SECONDS` is the right move when
testing; the backstop is not worth touching.

Only one VoiceStudio instance can accept remote workers on a given port. If
another instance already owns the configured port, the app continues running
with remote workers unavailable and shows the conflict in Settings. Close the
other instance, or give this one a different `OMNIVOICE_WORKER_PORT` and
restart it.

State lives under your data directory in `workers/`: the certificate and key,
the worker's own key, and received artifacts.

## Contributor acceptance check

After changing remote-worker routing or transport, run the non-destructive
hardware acceptance script from the repository root:

```bash
scripts/verify-remote-worker.sh \
  --worker-id '<worker-id>' \
  --ssh-target '<user@worker-host>'
```

`WORKER_ID`, `WORKER_SSH_TARGET`, `WORKER_START_COMMAND`, `VOICESTUDIO_API`,
and `WORKER_CONTROL_PORT` are equivalent environment variables. Pass
`--worker-start-command` (or its environment equivalent) when the worker does
not use the headless command documented above; it is printed only in the manual
worker-loss procedure. The worker id is optional only when exactly one worker
is connected. The script requires an SSH target so it can verify the worker's
OS and NVIDIA GPU before accepting any result.

The check never deletes model caches or user data. It selects an engine the
worker itself reports as absent for the missing-model check. Operations that
would disrupt the machine or network, including airplane mode, simultaneous
downloads, and stopping a worker during an audiobook, are printed as exact
`MANUAL` steps and are never reported as passed automatically. A failed
precondition or automated check exits non-zero.

Remote compute targets show available CPU/GPU usage and free VRAM. Unavailable
metrics are omitted; a transient sampling failure retains the last successful
reading. Telemetry runs off the control loop with at most one probe per worker
client, retained across reconnects. Read-only probes never block task draining or
shutdown; a stuck driver probe cannot accumulate more threads.
