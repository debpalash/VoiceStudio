# Voice fine-tuning in VoiceStudio — findings and design

Status: proposal (branch `feat/voice-finetune-spec`). Companion scripts in
`tools/finetune/` are the exact pipeline used for the experiments below.

## 1. What we tested (Sep 2026)

Goal: train a personal narration voice from ~2 h of one speaker's audiobook
reading, then run it locally in VoiceStudio.

| step | what | outcome |
| --- | --- | --- |
| Data | 2.27 h raw → 524 segments (5.6–20 s, mean 15.6 s, 32 kHz mono) + Whisper transcripts; 493 kept after a WER audit (dropped 4 with WER > 25 %) | 487/497 segments under 10 % WER: transcript quality was not the bottleneck |
| GPT-SoVITS v2ProPlus, run 1 | SoVITS 12 ep, GPT 15 ep, defaults, RTX 4090 (Vast.ai) | Trained in ~8 min. GPT brittle: with a reference clip whose transcript ended in a comma it emitted EOS after ~12 tokens ≈ 0.7 s of silence, stochastically |
| GPT-SoVITS v2ProPlus, run 2 | SoVITS 24 ep, GPT 20 ep + DPO, warmup 2000→300 steps (first run never reached peak LR: 930 total steps) | Fixed the dropped-clause behaviour (verbatim ASR on test passages); similarity 0.85 → 0.89 |
| Reference-clip study | same weights, six clips | Reference clip capped timbre: 0.85 vs 0.916 similarity for the best clip. Clips must end right after speech and the transcript must be verbatim, or GPT stops early |
| Inference knobs | speed 0.9, temperature, multi-ref (`inp_refs`) | speed 0.9 brought 212 → 188 wpm (natural 177) and doubled pitch range; multi-ref *lowered* similarity; temperature no effect on range. User preferred speed 1.0 by ear |
| F5-TTS v1 fine-tune | 3900 updates, lr 1e-5, batch 6400 frames, 4090 | Similarity 0.863 (zero-shot) → 0.884; perfect WER; but prosody flattened (44 → 30 Hz pitch range) |
| Zero-shot bake-off | VoxCPM2, Qwen3-TTS 1.7B Base (MLX), F5-TTS, all with the best clip | VoxCPM2 0.867 (flat, RTF 1.4); Qwen3-TTS 0.80/0.75 (worst, 240 wpm, flattest); F5 0.878 |
| ElevenLabs PVC (same speaker) | measured, not trained | 0.91 similarity, 179 wpm, ~50 Hz range, 0.56 s pauses after sentences |

Metric harness (`tools/finetune/metrics.py`): speaker similarity (ERes2NetV2
embedding cosine vs the speaker's raw clips; the speaker's own clips agree
at 0.93), words per minute, pitch range (p10–p90 of F0), Whisper WER.

### Conclusions

- **GPT-SoVITS v2ProPlus is the right first engine**: best similarity (0.89),
  the only one keeping the speaker's pitch range (66 Hz vs raw 63), 8-minute
  training, CPU inference at RTF 0.3 on an M4 Pro. F5-TTS is the credible
  alternative for stability (never drops words) at the cost of flatter
  delivery; Qwen3-TTS is not a candidate for this use.
- The remaining gap to ElevenLabs (0.91 vs 0.89, pauses) is prosody, not
  timbre. It will not close with more epochs; it needs a different model
  class or a post-processing pass (sentence pauses are mostly missing).
- Two things dominate quality after training and must be productised, not
  left to the user: the **reference clip** (auto-select by similarity, cut
  at speech end, verbatim transcript) and **DPO + short warmup** for the GPT
  stage.
- Cost: ~$0.40–0.60 of GPU time per GPT-SoVITS run on a rented 4090; setup
  and data upload dominate wall-clock (25 min of a 45 min session). Upload
  bandwidth from the user's machine is the real constraint (500 MB of WAV;
  FLAC halves it).

## 2. Product shape

**Settings → Voice training** (new section) and a **"Train from my recordings"**
entry in Clone.

### Data collection ("2 hours" made concrete)

- Show a progress ring: *Recorded 0:00 of 2:00 (minimum 30 min)*. Under
  30 min we do not offer training; between 30 min and 2 h we train but
  label the result "preview quality"; the ElevenLabs PVC guidance is the
  same 30 min–3 h.
- Three input routes: **record in-app** (we provide reading material),
  **import audiobook files** (what we did; the EPUB→script path already
  exists), **import existing voice profiles' clips**.
- Reading material: ship 2–3 h of public-domain prose in the user's
  language, split into 20–40 s passages shown one at a time with a big
  record button; each take is stored with its known text, so no ASR pass
  is needed for those. Imported audio goes through the existing ASR engine
  and the WER audit (drop > 25 %).
- Preparation is ours: silence trim, 5–20 s segmentation at sentence
  boundaries, loudness normalisation (−23 LUFS), 32 kHz mono, transcript
  cleaning, FLAC packaging. All local, before anything leaves the machine.

### Where to train

Detected at open, chosen per run:

| target | detection | notes |
| --- | --- | --- |
| Local GPU | existing `device_caps` (CUDA ≥ 8 GB VRAM; MPS possible but ~5× slower and lower quality upstream) | free; runs in a sidecar venv like other engines |
| Vast.ai | API key in Settings → Credentials (same pattern as HF token) | ~$0.50/run; we create/destroy the instance, user sees cost estimate and a hard cap; our scripts in `tools/finetune/` are the job |
| Other providers | later; RunPod/Lambda have similar CLIs | keep the job definition provider-agnostic: a tarball + a shell entrypoint |

Local-first rule: remote training is opt-in per run with an explicit
"this uploads N MB of your recordings to a rented machine" confirmation;
default is local when a capable GPU exists, otherwise the button says
"Train on a rented GPU (~$0.50)".

### Job model (backend)

`POST /voices/train` → job id; `GET /voices/train/{id}` → stage, progress,
log tail, cost so far; `DELETE` cancels (and destroys the instance).
Stages: `preparing` (local) → `uploading` → `setting_up` → `training`
(SoVITS then GPT, epoch progress) → `evaluating` (metrics harness on held-out
segments, renders a fixed passage) → `downloading` → `ready`. Artefacts land
in `DATA_DIR/finetunes/<voice>/` as a **trained voice profile**: weights,
chosen reference clip + transcript, the evaluation renders and scores.
The job runner is a subprocess (crash isolation, cancellable) driving the
same `tools/finetune/*.sh` locally or over SSH for Vast.

### Multiple outputs per run

One run can emit several profiles cheaply (the box is already up):
GPT-SoVITS (default, ~8 min) and F5-TTS (~40 min, +$0.30). The UI lists
each with its scores on the same passage and a play button; the user picks
which to keep. Local runs default to GPT-SoVITS only; add F5 as a checkbox.

### Using the result

A trained profile is a normal voice profile with an attached engine
binding, so Clone, Stories and Dubbing use it like any clip-based profile.
The GPT-SoVITS engine gets an in-app server lifecycle (start `api_v2.py`
with the profile's weights) instead of the current "run it yourself"; the
adapter work in #2200 is the prerequisite.

### Settings surface

- Voice training section: default target, Vast.ai key + spend cap, keep
  intermediate checkpoints, minimum-duration override for experiments.
- Credentials: Vast.ai API key (encrypted like the HF token).

## 3. Delivery plan (separate PRs)

1. `tools/finetune/` job scripts + metrics harness (this branch).
2. Backend job runner (local target only) + trained-profile storage.
3. Recording/import wizard with reading material and the audit.
4. Vast.ai target (create/upload/run/download/destroy with cost cap).
5. Multi-output (F5) + comparison view.
6. GPT-SoVITS managed server lifecycle (depends on #2200).

## 4. Open questions

- Reading-material licensing and languages beyond English.
- Where 500 MB–2 GB of user audio lives (DATA_DIR quota, cleanup).
- Whether to offer a "prosody pass" (sentence-pause insertion) since that
  is the measured gap to ElevenLabs.
