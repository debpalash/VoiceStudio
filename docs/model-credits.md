# Model credits in About

The About section in Settings → System preflight displays model and conversion authors, source links, and upstream
terms in the maintained Electron desktop and web UI. Credits remain visible before
the backend answers or a model is installed. Surrounding labels are translated in
all 21 loaded locales; mandatory legal notices retain their original English text.
The shared locale files mirror those labels for parity.

This is a visible-credit inventory, not a clearance registry. Model-card labels
are evidence of an upstream statement, not proof that all components, training
data, voices, distribution conditions, or commercial uses have been cleared.
VoiceStudio's application licence does not replace model or component terms.
The displayed list is intentionally partial; bundled licence texts, notices,
acceptance requirements, and dependency audits still need separate review.

## Sources checked on 2026-10-03

| Credit | Primary evidence | Attribution scope |
| --- | --- | --- |
| Higgs Audio 2 / Boson AI / Meta Llama 3 | [OmniVoice tokenizer licence at `c5fdb5c`](https://huggingface.co/k2-fsa/OmniVoice/blob/c5fdb5ccb189668d56333f77ba2629f4cd7535f4/audio_tokenizer/LICENSE), clause 1.b.i | Display the exact quoted “Built with…” notice, including the upstream singular “All Right Reserved”, and link Boson and Meta Llama 3 terms. This UI change does not fulfil every redistribution requirement in clauses 1.b.i–iii. |
| Parakeet TDT 0.6B v2 / NVIDIA | [Card at `ae9ad07`](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2/blob/ae9ad07059c7c739ffaf932226a8fe64ae2620b0/README.md) | The governing-terms section names CC BY 4.0; credit NVIDIA and link the model and licence. |
| Parakeet TDT 0.6B v3 / NVIDIA | [Card at `541d1f9`](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3/blob/541d1f99c6b0c3cd0b11a95167540bb8edefd82b/README.md) | The governing-terms section names CC BY 4.0. |
| Parakeet MLX conversion / mlx-community | [Card at `ed2b7e8`](https://huggingface.co/mlx-community/parakeet-tdt-0.6b-v3/blob/ed2b7e8c15f9aaa0b5772e2efb986255eaef7e15/README.md) | Credit the named conversion separately from NVIDIA's model. |
| Parakeet sherpa-onnx INT8 conversions / csukuangfj | [v2 card at `1ab9323`](https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8/blob/1ab9323565ddb038682214b292f588070a538ce2/README.md), [v3 repository](https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8) | The v2 card contains only a CC BY 4.0 metadata label. The v3 raw card returned 404. Both links identify conversion sources; neither establishes conversion-specific clearance. |
| WeSpeaker VoxCeleb ResNet34-LM / WeSpeaker / pyannote | [Wrapper card at `837717d`](https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM/blob/837717ddb9ff5507820346191109dc79c958d614/README.md) | The card identifies the WeSpeaker wrapper and connects the pretrained model's CC BY 4.0 terms to VoxCeleb. Credit both projects and link the source and terms. |
| Pocket TTS / Kyutai | [Official code repository](https://github.com/kyutai-labs/pocket-tts), [weight repository](https://huggingface.co/kyutai/pocket-tts) | The HF API reports CC BY 4.0, but the raw weight card returned 401. About credits Kyutai and links the weight repository without presenting that label as verified terms. The code repository's MIT licence does not resolve the weight or voice terms. No gated access was bypassed. |
| OmniVoice / Han Zhu and coauthors | [Card at `c5fdb5c`](https://huggingface.co/k2-fsa/OmniVoice/blob/c5fdb5ccb189668d56333f77ba2629f4cd7535f4/README.md) | Credit the authors named by the citation. The card distinguishes Apache-2.0 code from CC-BY-NC weights without specifying a CC version; retain that distinction and link its licence section. |
| OmniVoice-GGUF / Serveurperso | [Card at `0170941`](https://huggingface.co/Serveurperso/OmniVoice-GGUF/blob/017094167b5c9ed565a5076ac9b3b93c5ecf5c73/README.md) | Credit the GGUF conversion and upstream OmniVoice; its metadata names CC BY-NC 4.0. The card's description of the codec as Apache does not supersede the tokenizer's actual Boson licence. |
| NLLB-200 distilled 600M / Meta AI | [Card at `f8d333a`](https://huggingface.co/facebook/nllb-200-distilled-600M/blob/f8d333a098d19b4fd9a8b18f94170487ad3f821d/README.md) | Credit Meta AI and link the CC BY-NC 4.0 terms named by the card. |
| Llama-OuteTTS-1.0-1B MLX 4-bit / OuteAI / mlx-community / Meta | [Conversion card at `3ac2cff`](https://huggingface.co/mlx-community/Llama-OuteTTS-1.0-1B-4bit/blob/3ac2cff406f7de16a3216c60d0108571a916acc0/README.md), [original model](https://huggingface.co/OuteAI/Llama-OuteTTS-1.0-1B), [Llama 3.2 terms](https://huggingface.co/meta-llama/Llama-3.2-1B/blob/main/LICENSE.txt) | The original card separates Llama 3.2 components from OuteAI's CC BY-NC-SA 4.0 additions. Credit the MLX conversion, retain the required “Built with Llama” wording, and link both sources of terms. |

The static presentation lives in
`electron/src/renderer/src/features/settings/model-credits.tsx`, mounted by
`DiagnosticsSettings`. It makes no model downloads, remote licence probes,
entitlement changes, or licence acceptance decisions. Update the source evidence
and tests when adding or changing an attribution.
