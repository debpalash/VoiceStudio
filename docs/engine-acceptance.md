# Adding a TTS or ASR engine

VoiceStudio ships a lot of engines. That breadth is only an asset while every one
of them still works on every platform — otherwise it is a pile of support
queues, and the project's actual promise (*a first run that works*) is what pays
for it.

So new engines are **hired for a job**, not added to a list.

## The job map

Every engine in the tree owns at least one job. A job has exactly one holder.

| Job | Held by |
| --- | --- |
| Best zero-shot clone quality | `omnivoice` |
| Widest language coverage | `omnivoice` |
| Crash isolation for the default model | `omnivoice-subprocess` |
| Fastest CPU render / lowest latency | *open — see #1306* |
| Best Chinese/Japanese expressiveness | `cosyvoice`, `indextts2` |
| CPU-realtime English, tiny footprint | `kittentts`, `supertonic3` |
| Bilingual reference-free voice design and direction | `audiocpp` |
| Best transcription accuracy | `whisperx`, `faster-whisper` |
| Fastest Apple-Silicon transcription | `parakeet-mlx`, `mlx-whisper` |
| Crash isolation for transcription | `faster-whisper-isolated` |

A proposal must either **take a job from its current holder** (with numbers) or
**claim a job nothing covers**. "It benchmarks well" is not a job.

## The bar

A new engine is accepted when all of these hold. Miss one and the answer is no —
which is a property of the bar, not a judgement of the contributor.

1. **A job, named.** Which row above it takes or adds, and why the incumbent
   does not cover it. Latency, language, hardware envelope or quality tier —
   something a user would choose it *for*.
2. **Documented rights for each component.** Record the code, model weights,
   tokenizer and other required assets separately, including their licence,
   source/revision, commercial permission and required credits. Unknown or
   conflicting terms do not count as commercial approval. A restricted engine
   may be accepted for the free app only with explicit owner approval and a
   first-use acceptance step that discloses its terms. Pro must reject
   non-commercial or uncleared assets on the backend unless the necessary
   rights are obtained. The application licence does not grant model or output
   rights. Existing integrations also need this audit; the controls remain
   tracked in #2587. The previously approved `audiocpp` / Breeze-TTS-2 exception
   remains restricted and stewarded by `@debpalash`.
3. **Every platform, or explicitly opt-in.** macOS (Apple Silicon and Intel),
   Windows, Linux. A CPU path is required — an engine that only runs on one
   accelerator is fine, but it must degrade rather than break, and a
   platform-only engine goes behind an opt-in (see CLAUDE.md's parity rule).
4. **Fits the existing adapter.** `TTSBackend` / `SubprocessBackend` with no
   changes to core pipelines. A dependency profile that conflicts with ours
   means a sidecar (`SubprocessBackend`), which is a solved shape — see
   `docs/engines/omnivoice-subprocess.md`.
5. **A CI smoke test in the same PR.** It does not need a GPU: stub the sidecar,
   assert the adapter contract. An integration with no test is an integration
   nobody will notice breaking.
6. **A named steward.** The proposer commits to being the point of contact for
   that engine's issues for 12 months. No steward, no merge — this is the
   difference between breadth and debt.
7. **Demand evidence.** A real request, a real workflow, a real user. Ideally
   someone already working around its absence.

## Deprecation

Breadth is only worth carrying while it is alive. An engine is archived when,
for two consecutive releases, it has **no steward** and **no passing smoke
test**. Archiving is not a judgement either — it is how the remaining engines
stay trustworthy.

## If it doesn't clear the bar

The adapter interface is public. An engine can live out-of-tree, be installed
alongside, and be selected by id — you do not need our merge to use your engine,
and we would rather link a good external engine than carry a half-maintained
internal one.
