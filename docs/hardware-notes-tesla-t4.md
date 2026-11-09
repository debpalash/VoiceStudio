# Running the default engine on a Tesla T4

Notes from a real run of the default `omnivoice` TTS engine on a 16 GB NVIDIA Tesla T4 (Turing, sm_75).

## Test setup

| Item | Value |
| --- | --- |
| Driver | 550.163.01 (CUDA 12.8) |
| PyTorch | 2.8.0+cu128 |
| transformers | 5.3.0 |
| Python | 3.11, managed by uv |
| Engine | `OMNIVOICE_TTS_BACKEND=omnivoice` |

## The first request can time out

On a fresh install the first `POST /v1/audio/speech` may fail with an error saying the job "exceeded 300s and was abandoned". The GPU is not the problem: it sat at about 2 GB and 0% utilisation the whole time.

Cause: the first generate call downloads the roughly 2.3 GB `k2-fsa/OmniVoice` checkpoint, and that download counts against the generate time budget (`OMNIVOICE_GENERATE_TIMEOUT_S`, default 300 s). Once the weights are cached, the same request finishes in about 1 s.

Two ways around it:

- Fetch the model first. `POST /models/install` with a JSON body containing `repo_id` (required, and it must be a model the app knows about; see `InstallModelRequest` in `backend/api/schemas.py`). Watch progress on the `/setup/download-stream` SSE feed.
- Raise the budget under Settings -> Performance & Device. If the environment variable is set, it wins over the setting.

## Request parameters

`/v1/audio/speech` has no `num_step` or `guidance_scale` fields. If you send them they are dropped without an error (the pydantic model ignores extra fields). The multipart `POST /generate` endpoint accepts both.

The app's Voice and Design pages default `num_step` to 32, the model's documented quality preset (see [generation-parameters.md](generation-parameters.md)). When a take is measured to be slow, the app offers a lower value such as 16; it never lowers it on its own (see [audio-quality.md](audio-quality.md)). A multipart `POST /generate` that omits `num_step` uses the steps of the Speed / quality preset chosen under Settings -> Performance & Device (Fast 8, Balanced 16, Quality 32, Max 64; OmniVoice engines only), or 16 when no preset is chosen or the engine has none.

## Speed checklist

- **dtype:** fixed at float16 for this engine, and there is no environment override. That suits Turing, which has no bf16 tensor cores.
- **Attention:** falls back to `sdpa` because `flash_attn` is not installed.
- **int8:** this engine has no int8 path.
- **CUDA Graphs:** not used on pre-Ampere GPUs since #2135. The app picks the `torch.compile` mode per GPU and uses the non-cudagraph `default` mode below sm_80. Set `OMNIVOICE_FORCE_CUDAGRAPH=1` to bring back the old behaviour, e.g. for benchmarking.
- **torch.compile:** still attempted in `default` mode. Turn it off in Settings -> Performance, or set `TORCH_COMPILE_DISABLE=1`.

## Memory

Peak use was 2487 MiB as reported by `nvidia-smi`, or 2.050 GB by `torch.cuda.max_memory_allocated()`. That is well inside the 4 GB minimum tier.
