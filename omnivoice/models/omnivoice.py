#!/usr/bin/env python3
# Copyright    2026  Xiaomi Corp.        (authors:  Han Zhu)
#
# See ../../LICENSE for clarification regarding multiple authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Core OmniVoice model implementation.

Defines the ``OmniVoice`` model class, generation config, and inference pipeline.
This is the main entry point for both inference and training:

- **Inference**: ``OmniVoice.from_pretrained()`` loads the model, then
  ``model.generate()`` supports voice cloning, voice design, and auto voice.
- **Training**: ``model.forward()`` computes the training loss; the model is
  built and used by ``omnivoice.training.builder`` and ``omnivoice.training.trainer``.

"""

import difflib
import logging
import math
import os
import re
import sys
from dataclasses import dataclass, fields
from functools import partial
from typing import Any, List, Optional, Union

import torch
import torch.nn as nn
import torch.nn.functional as F
import torchaudio
from torch.nn.attention.flex_attention import create_block_mask
from transformers import (
    AutoFeatureExtractor,
    AutoModel,
    AutoTokenizer,
    PretrainedConfig,
    PreTrainedModel,
)
from transformers.modeling_outputs import ModelOutput
from transformers.models.auto import CONFIG_MAPPING, AutoConfig

from omnivoice.utils.audio import (
    CLONE_REF_MAX_WINDOWS,
    CLONE_REF_NO_SPEECH_MARKER,
    CLONE_REF_TEXT_MAX_SECONDS,
    CLONE_REF_TOO_LONG_MARKER,
    CLONE_REF_WINDOW_SECONDS,
    clone_ref_transcript_too_long_message,
    cross_fade_chunks,
    fade_and_pad_audio,
    load_audio,
    remove_silence_safe,
    validate_clone_reference,
)
from omnivoice.utils.duration import RuleDurationEstimator
from omnivoice.utils.lang_map import LANG_IDS, LANG_NAMES
from omnivoice.utils.text import add_punctuation, chunk_text_punctuation
from omnivoice.utils.voice_design import (
    _INSTRUCT_ALL_VALID,
    _INSTRUCT_EN_TO_ZH,
    _INSTRUCT_MUTUALLY_EXCLUSIVE,
    _INSTRUCT_VALID_EN,
    _INSTRUCT_VALID_ZH,
    _INSTRUCT_ZH_TO_EN,
    _ZH_RE,
)

logger = logging.getLogger(__name__)

_AUDIO_TOKENIZER_FALLBACK_REPO = "eustlb/higgs-audio-v2-tokenizer"


class OmniVoiceModelAssetError(RuntimeError):
    """A fixed nested model repository failed while OmniVoice was loading."""

    def __init__(self, repository_id: str):
        super().__init__(f"Failed to load OmniVoice model asset: {repository_id}")
        self.repository_id = repository_id


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


_VOICE_CLONE_PROMPT_FORMAT_VERSION = 1


@dataclass
class VoiceClonePrompt:
    ref_audio_tokens: torch.Tensor  # (C, T)
    ref_text: str
    ref_rms: float

    def save(self, path: str) -> None:
        """Save this prompt to ``path`` for reuse in a later session.

        The file stores a plain dict with the audio tokens moved to CPU, so
        it can be loaded with ``torch.load(weights_only=True)`` (the default
        since torch 2.6) and is portable across devices.

        Args:
            path: Destination file path (e.g. ``"my_voice.pt"``).
        """
        torch.save(
            {
                "format_version": _VOICE_CLONE_PROMPT_FORMAT_VERSION,
                "ref_audio_tokens": self.ref_audio_tokens.detach().cpu(),
                "ref_text": self.ref_text,
                "ref_rms": float(self.ref_rms),
            },
            path,
        )

    @classmethod
    def load(cls, path: str, map_location: str = "cpu") -> "VoiceClonePrompt":
        """Load a prompt saved with :meth:`save`.

        The returned prompt can be passed directly to
        :meth:`OmniVoice.generate`; the audio tokens are moved to the model
        device automatically during generation, so no manual ``.to(device)``
        is needed.

        Args:
            path: File path previously written by :meth:`save`.
            map_location: Device to load the audio tokens onto.
        Returns:
            The restored :class:`VoiceClonePrompt`.
        """
        data = torch.load(path, map_location=map_location, weights_only=True)
        version = data.get("format_version")
        if version != _VOICE_CLONE_PROMPT_FORMAT_VERSION:
            raise ValueError(f"Unsupported VoiceClonePrompt format version: {version}")
        return cls(
            ref_audio_tokens=data["ref_audio_tokens"],
            ref_text=data["ref_text"],
            ref_rms=data["ref_rms"],
        )


@dataclass
class OmniVoiceGenerationConfig:
    num_step: int = 32
    guidance_scale: float = 2.0
    t_shift: float = 0.1
    layer_penalty_factor: float = 5.0
    position_temperature: float = 5.0
    class_temperature: float = 0.0
    denoise: bool = True
    preprocess_prompt: bool = True
    postprocess_output: bool = True
    audio_chunk_duration: float = 15.0
    audio_chunk_threshold: float = 30.0

    @classmethod
    def from_dict(cls, kwargs_dict):
        valid_keys = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in kwargs_dict.items() if k in valid_keys}
        return cls(**filtered)


@dataclass
class GenerationTask:
    batch_size: int
    texts: List[str]
    target_lens: List[int]
    langs: List[Optional[str]]
    instructs: List[Optional[str]]
    ref_texts: List[Optional[str]]
    ref_audio_tokens: List[Optional[torch.Tensor]]
    ref_rms: List[Optional[float]]
    speed: Optional[List[float]] = None

    def get_indices(self, config: OmniVoiceGenerationConfig, frame_rate: int):
        threshold = int(config.audio_chunk_threshold * frame_rate)
        short_idx = [i for i, l in enumerate(self.target_lens) if l <= threshold]
        long_idx = [i for i, l in enumerate(self.target_lens) if l > threshold]
        return short_idx, long_idx

    def slice_task(self, indices: List[int]):
        if not indices:
            return None
        return GenerationTask(
            batch_size=len(indices),
            texts=[self.texts[i] for i in indices],
            target_lens=[self.target_lens[i] for i in indices],
            langs=[self.langs[i] for i in indices],
            instructs=[self.instructs[i] for i in indices],
            ref_texts=[self.ref_texts[i] for i in indices],
            ref_audio_tokens=[self.ref_audio_tokens[i] for i in indices],
            ref_rms=[self.ref_rms[i] for i in indices],
            speed=[self.speed[i] for i in indices] if self.speed else None,
        )


@dataclass
class OmniVoiceModelOutput(ModelOutput):
    loss: Optional[torch.Tensor] = None
    logits: Optional[torch.Tensor] = None


# ---------------------------------------------------------------------------
# Config & Model
# ---------------------------------------------------------------------------


class OmniVoiceConfig(PretrainedConfig):
    model_type = "omnivoice"
    sub_configs = {"llm_config": AutoConfig}

    def __init__(
        self,
        audio_vocab_size: int = 1025,
        audio_mask_id: int = 1024,
        num_audio_codebook: int = 8,
        audio_codebook_weights: Optional[list[float]] = None,
        llm_config: Optional[Union[dict, PretrainedConfig]] = None,
        **kwargs,
    ):

        if isinstance(llm_config, dict):
            llm_config = CONFIG_MAPPING[llm_config["model_type"]](**llm_config)

        self.llm_config = llm_config

        super().__init__(**kwargs)
        self.audio_vocab_size = audio_vocab_size
        self.audio_mask_id = audio_mask_id
        self.num_audio_codebook = num_audio_codebook
        if audio_codebook_weights is None:
            audio_codebook_weights = [8, 8, 6, 6, 4, 4, 2, 2]
        self.audio_codebook_weights = audio_codebook_weights


def _audio_tokenizer_cls():
    """Resolve ``transformers.HiggsAudioV2TokenizerModel`` at the point of use.

    transformers exposes this class through its lazy module and gates it on the
    ``torchaudio`` backend, so the *attribute access* — not the `transformers`
    import — is what raises when torchaudio is missing, ABI-mismatched with
    torch, or installed without discoverable distribution metadata (Colab's
    system Python, an interrupted `uv pip install`). Resolving it at module
    scope made that a fatal import error for the WHOLE backend: `backend/main.py`
    imports the profiles router → `omnivoice` → this module, so one optional
    audio tokenizer took down TTS, dubbing, ASR and Settings alike, before
    FastAPI existed to classify it. The user saw only uvicorn's traceback and a
    "Backend did not become healthy within 5 minutes" timeout (#1229).

    Deferred here, the failure lands inside a request instead, where
    ``core.failure.classify()`` maps it to ``TRANSFORMERS_IMPORT`` and attaches
    a repair hint — and every feature that doesn't need this tokenizer keeps
    working.
    """
    try:
        from transformers import HiggsAudioV2TokenizerModel
    except Exception as e:
        raise ImportError(
            "Could not import module 'HiggsAudioV2TokenizerModel' — OmniVoice's "
            "audio tokenizer. transformers gates it on torchaudio, so this is "
            "almost always a torchaudio that is missing, broken, or mismatched "
            "with the installed torch/transformers. Reinstall them together "
            "(`uv pip install --reinstall torch torchaudio transformers`; add "
            "--system on Colab), then restart the backend. Underlying error: "
            f"{type(e).__name__}: {e}"
        ) from e
    return HiggsAudioV2TokenizerModel


def _resolve_snapshot_dir(checkpoint) -> str:
    """Local snapshot directory for ``checkpoint`` (a local dir or a HF repo id).

    Cache-first (#959): a COMPLETE local cache is resolved with
    ``snapshot_download(..., local_files_only=True)``, which never constructs
    an HTTP session — so no session-construction failure (e.g. httpx's
    ImportError under ``ALL_PROXY``/``HTTPS_PROXY=socks5://`` without socksio,
    a malformed proxy URL, a broken cert bundle) can break synthesis of an
    already-installed model. Only a cache miss / incomplete cache falls
    through to the original network ``snapshot_download``, whose errors
    (auth, connectivity, proxy) surface exactly as before.
    """
    if os.path.isdir(checkpoint):
        return checkpoint
    from huggingface_hub import snapshot_download

    try:
        return snapshot_download(checkpoint, local_files_only=True)
    except Exception:
        # Miss/incomplete (LocalEntryNotFoundError et al.) → network path.
        return snapshot_download(checkpoint)


_DEFAULT_ASR_MODEL = "openai/whisper-large-v3-turbo"


class OmniVoice(PreTrainedModel):
    _supports_flex_attn = True
    _supports_flash_attn_2 = True
    config_class = OmniVoiceConfig

    # ── Desktop-bundle safety overrides ─────────────────────────────────
    # transformers ≥4.52 calls `_can_set_experts_implementation()` and
    # `_can_set_attn_implementation()` during __init__, which open the
    # class's source file via `inspect.getsourcefile()`/`open(class_file)`.
    # In a Tauri desktop bundle the module __file__ path doesn't exist on
    # disk, causing FileNotFoundError.  OmniVoice doesn't use MoE experts,
    # so we can safely return False for experts.  For attention, return True
    # (we DO support flex_attn / flash_attn_2).
    @classmethod
    def _can_set_experts_implementation(cls) -> bool:
        return False

    @classmethod
    def _can_set_attn_implementation(cls) -> bool:
        return True

    def __init__(self, config: OmniVoiceConfig, llm: Optional[PreTrainedModel] = None):
        super().__init__(config)

        if llm is not None:
            # If an LLM instance is provided, use it directly
            # (skipping config-based init).
            self.llm = llm
        else:
            # Otherwise, initialize the LLM from the config.
            self.llm = AutoModel.from_config(self.config.llm_config)

        self.audio_embeddings = nn.Embedding(
            config.num_audio_codebook * config.audio_vocab_size,
            self.config.llm_config.hidden_size,
        )
        self.register_buffer(
            "codebook_layer_offsets",
            torch.arange(config.num_audio_codebook) * config.audio_vocab_size,
        )

        self.audio_heads = nn.Linear(
            self.config.llm_config.hidden_size,
            config.num_audio_codebook * config.audio_vocab_size,
            bias=False,
        )

        self.normalized_audio_codebook_weights = [
            w / sum(config.audio_codebook_weights)
            for w in config.audio_codebook_weights
        ]

        self.post_init()

        # Inference-only attributes (set by from_pretrained when not in train mode)
        self.text_tokenizer = None
        self.audio_tokenizer = None
        self.duration_estimator = None
        self.sampling_rate = None
        self._asr_pipe = None

    @classmethod
    def from_pretrained(cls, pretrained_model_name_or_path, *args, **kwargs):
        train_mode = kwargs.pop("train", False)
        load_asr = kwargs.pop("load_asr", False)
        asr_model_name = kwargs.pop("asr_model_name", _DEFAULT_ASR_MODEL)

        # Suppress noisy INFO logs from transformers/huggingface_hub during
        # loading. Scoped to those two logger trees — NOT logging.disable(),
        # which is process-global and only restored when this call returns: a
        # SIGTERM mid-load ran the entire app shutdown with INFO logging still
        # disabled, blacking out every "Shutting down"/"Shutdown: done." line
        # and making a clean quit look like a silent crash (#1174).
        _quiet_loggers = [
            logging.getLogger("transformers"),
            logging.getLogger("huggingface_hub"),
        ]
        _prev_levels = [(lg, lg.level) for lg in _quiet_loggers]
        for _lg in _quiet_loggers:
            _lg.setLevel(logging.WARNING)

        # Disable tqdm on non-TTY (e.g., Tauri backend) to prevent OSError on Windows
        _prev_tqdm = os.environ.get("TQDM_DISABLE")
        if sys.stdout is not None and not sys.stdout.isatty():
            os.environ["TQDM_DISABLE"] = "1"

        try:
            model = super().from_pretrained(
                pretrained_model_name_or_path, *args, **kwargs
            )

            if not train_mode:
                # Resolve local path for audio tokenizer subdirectory —
                # cache-first so a proxy-broken HTTP session can't fail an
                # installed model (#959; see _resolve_snapshot_dir).
                resolved_path = _resolve_snapshot_dir(pretrained_model_name_or_path)

                model.text_tokenizer = AutoTokenizer.from_pretrained(
                    pretrained_model_name_or_path
                )

                audio_tokenizer_path = os.path.join(resolved_path, "audio_tokenizer")

                if not os.path.isdir(audio_tokenizer_path):
                    # Fallback to the HuggingFace Hub path of transformers'
                    # HiggsAudioV2Tokenizer if the local subdirectory doesn't exist.
                    audio_tokenizer_path = _AUDIO_TOKENIZER_FALLBACK_REPO

                # higgs-audio-v2-tokenizer does not support MPS (output channels > 65536)
                tokenizer_device = (
                    "cpu" if str(model.device).startswith("mps") else model.device
                )
                try:
                    model.audio_tokenizer = _audio_tokenizer_cls().from_pretrained(
                        audio_tokenizer_path, device_map=tokenizer_device
                    )
                    model.feature_extractor = AutoFeatureExtractor.from_pretrained(
                        audio_tokenizer_path
                    )
                except Exception as exc:
                    if audio_tokenizer_path != _AUDIO_TOKENIZER_FALLBACK_REPO:
                        raise
                    raise OmniVoiceModelAssetError(
                        _AUDIO_TOKENIZER_FALLBACK_REPO
                    ) from exc

                model.sampling_rate = model.feature_extractor.sampling_rate

                model.duration_estimator = RuleDurationEstimator()

                if load_asr:
                    model.load_asr_model(model_name=asr_model_name)
        finally:
            for _lg, _lvl in _prev_levels:
                _lg.setLevel(_lvl)
            # Restore TQDM_DISABLE state
            if _prev_tqdm is None:
                os.environ.pop("TQDM_DISABLE", None)
            else:
                os.environ["TQDM_DISABLE"] = _prev_tqdm

        return model

    # -------------------------------------------------------------------
    # ASR support (optional, for auto-transcription)
    # -------------------------------------------------------------------

    def load_asr_model(self, model_name: str = _DEFAULT_ASR_MODEL):
        """Load a Whisper ASR model for reference audio transcription.

        Args:
            model_name: HuggingFace model name for the Whisper model.
        """
        from transformers import pipeline as hf_pipeline

        logger.info("Loading ASR model %s ...", model_name)
        asr_dtype = (
            torch.float16 if str(self.device).startswith("cuda") else torch.float32
        )
        self._asr_pipe = hf_pipeline(
            "automatic-speech-recognition",
            model=model_name,
            dtype=asr_dtype,
            device_map=self.device,
        )
        logger.info("ASR model loaded on %s.", self.device)

    def _load_cached_reference_asr(self):
        """Implicit cloning fallback may reuse local weights, never download them."""
        from huggingface_hub import snapshot_download
        from huggingface_hub.errors import LocalEntryNotFoundError

        try:
            snapshot = snapshot_download(_DEFAULT_ASR_MODEL, local_files_only=True)
        except LocalEntryNotFoundError as exc:
            raise ValueError(
                "Automatic reference transcription needs an installed speech-to-text "
                "model. Provide a matching reference transcript, or install and select "
                "a speech-to-text model in Model Catalogue, then try again."
            ) from exc
        # Pass a directory rather than the repo ID so transformers cannot make
        # metadata requests or download missing assets from a partial snapshot.
        self.load_asr_model(model_name=snapshot)

    @torch.inference_mode()
    def transcribe(
        self,
        audio: Union[str, tuple[torch.Tensor, int]],
    ) -> str:
        """Transcribe audio using the loaded Whisper ASR model.

        Args:
            audio: File path or (waveform, sample_rate) tuple.

        Returns:
            Transcribed text.
        """
        if self._asr_pipe is None:
            raise RuntimeError(
                "ASR model is not loaded. Call model.load_asr_model() first."
            )

        if isinstance(audio, str):
            return self._asr_pipe(audio)["text"].strip()
        else:
            waveform, sr = audio
            if waveform.dim() == 1:
                waveform = waveform.unsqueeze(0)
            if waveform.size(0) > 1:
                waveform = torch.mean(waveform, dim=0, keepdim=True)
            audio_input = {
                "array": waveform.squeeze(0).cpu().numpy(),
                "sampling_rate": sr,
            }
            return self._asr_pipe(audio_input)["text"].strip()

    def get_input_embeddings(self):
        return self.llm.get_input_embeddings()

    def set_input_embeddings(self, value):
        self.llm.set_input_embeddings(value)

    def _prepare_embed_inputs(
        self, input_ids: torch.Tensor, audio_mask: torch.Tensor
    ) -> torch.Tensor:
        """
        Prepares embeddings from input_ids of shape (batch_size, layers, seq_length).
        Embedding shape is (batch_size, seq_length, hidden_size).
        """
        text_embeds = self.get_input_embeddings()(input_ids[:, 0, :])

        # Apply shift to audio IDs based on codebook layer
        # audio_ids: [Batch, 8, Seq]
        # codebook_layer_offsets: [1, 8, 1]
        # Result: Layer 0 ID Layer 1 ID + Layer 2 ID + 2050...
        shifted_ids = (
            input_ids * audio_mask.unsqueeze(1)
        ) + self.codebook_layer_offsets.view(1, -1, 1)

        # input: [Batch, 8, Seq] -> output: [Batch, Seq, Hidden]
        audio_embeds = self.audio_embeddings(shifted_ids).sum(dim=1)

        return torch.where(audio_mask.unsqueeze(-1), audio_embeds, text_embeds)

    def forward(
        self,
        input_ids: torch.LongTensor,
        audio_mask: torch.Tensor,
        labels: Optional[torch.LongTensor] = None,
        attention_mask: Optional[torch.Tensor] = None,
        document_ids: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.LongTensor] = None,
    ):

        inputs_embeds = self._prepare_embed_inputs(input_ids, audio_mask)

        if attention_mask is None and document_ids is not None:
            attention_mask = create_block_mask(
                _get_packed_mask(
                    document_ids[0].to(inputs_embeds.device),
                ),
                B=None,
                H=None,
                Q_LEN=input_ids.size(-1),
                KV_LEN=input_ids.size(-1),
                _compile=True,
                device=inputs_embeds.device,
            )

        llm_outputs = self.llm(
            inputs_embeds=inputs_embeds,
            attention_mask=attention_mask,
            return_dict=True,
            position_ids=position_ids,
        )
        hidden_states = llm_outputs[0]

        loss = None

        # Shape: [B, S, C * Vocab]
        batch_size, seq_len, _ = hidden_states.shape
        logits_flat = self.audio_heads(hidden_states)
        # Shape: [B, S, C, Vocab] -> [B, C, S, Vocab]
        audio_logits = logits_flat.view(
            batch_size,
            seq_len,
            self.config.num_audio_codebook,
            self.config.audio_vocab_size,
        ).permute(0, 2, 1, 3)

        if labels is not None:

            # audio_logits.permute(0, 3, 1, 2):
            # [Batch, Layer, Seq, Vocab] -> [Batch, Vocab, Layer, Seq]
            # per_token_loss shape: [Batch, Layer, Seq]，ignore -100
            per_token_loss = torch.nn.functional.cross_entropy(
                audio_logits.permute(0, 3, 1, 2),
                labels,
                reduction="none",
                ignore_index=-100,
            )
            # valid_mask shape: [Batch, Layer, Seq]
            valid_mask = (labels != -100).float()

            # layer_means shape: [num_layers]
            layer_means = (per_token_loss * valid_mask).sum(
                dim=(0, 2)
            ) / valid_mask.sum(dim=(0, 2)).clamp(min=1.0)

            weights = torch.tensor(
                self.normalized_audio_codebook_weights, device=audio_logits.device
            )
            loss = (layer_means * weights).sum()

        return OmniVoiceModelOutput(
            loss=loss,
            logits=audio_logits,
        )

    def supported_language_ids(self) -> set[str]:
        """Return a list of supported language IDs."""
        return LANG_IDS

    def supported_language_names(self) -> set[str]:
        """Return a list of supported language names."""
        return LANG_NAMES

    # -------------------------------------------------------------------
    # Inference API
    # -------------------------------------------------------------------

    @torch.inference_mode()
    def generate(
        self,
        text: Union[str, list[str]],
        language: Union[str, list[str], None] = None,
        ref_text: Union[str, list[str], None] = None,
        ref_audio: Union[
            str,
            list[str],
            tuple[torch.Tensor, int],
            list[tuple[torch.Tensor, int]],
            None,
        ] = None,
        voice_clone_prompt: Union[
            VoiceClonePrompt, list[VoiceClonePrompt], None
        ] = None,
        instruct: Union[str, list[str], None] = None,
        duration: Union[float, list[Optional[float]], None] = None,
        speed: Union[float, list[Optional[float]], None] = None,
        generation_config: Optional[OmniVoiceGenerationConfig] = None,
        **kwargs,
    ) -> list[torch.Tensor]:
        """Generate speech audio given text in various modes.

        Supports three modes:

        1. **Voice clone** — clone the voice style from the reference audio.
            Should provide ``voice_clone_prompt`` (from
           :meth:`create_voice_clone_prompt`) or ``ref_text`` + ``ref_audio``.
        2. **Voice design** — provide ``instruct`` text describing
           the desired voice style; no reference audio needed.
        3. **Auto** — provide neither; the model picks a voice itself.

        Args:
            text: Target text (single string or list for batch).
            language: Language name (e.g. ``"English"``) or code
                (e.g. ``"en"``). ``None`` for language-agnostic mode.
                Performance is slightly better if you specify the language.
            ref_text: Optional reference text for voice cloning mode.
            ref_audio: Optional reference audio for voice cloning mode.
                Can be a file path or a (waveform, sample_rate) tuple.
            voice_clone_prompt: Reusable prompt from :meth:`create_voice_clone_prompt`.
                If provided, it overrides ``ref_text`` and ``ref_audio``.
            instruct: Style instruction for voice design mode.
            duration: Fixed output duration in seconds. If a single float,
                applies to all items; if a list, one value per item.
                ``None`` (default) lets the model estimate duration from text.
                Overrides ``speed`` when both are provided.
            speed: Speaking speed factor. ``> 1.0`` for faster, ``< 1.0`` for
                slower. If a list, one value per item. ``None`` (default) uses
                the model's default estimation.
            generation_config: Explicit config object. If provided, takes
                precedence over ``**kwargs``.
            **kwargs: Generation config or its fields:
                denoise: Whether to prepend the ``<|denoise|>`` token.
                num_step: Number of iterative decoding steps.
                guidance_scale: Classifier-free guidance scale.
                t_shift: Time-step shift (smaller → emphasise low-SNR).
                postprocess_output: Post-process output (remove silence, fade-in/out, pad edges).
                layer_penalty_factor: Penalty encouraging earlier codebook
                    layers to unmask first.
                position_temperature: Temperature for position selection.
                class_temperature: Temperature for token sampling (0 = greedy).
                audio_chunk_duration: If > 0, split long text into chunks of
                    this duration (seconds) and generate chunk by chunk.
                audio_chunk_threshold: Only apply chunking if estimated audio
                    duration exceeds this threshold (seconds).
        Returns:
            ``audios`` a list of 2-D ``torch.Tensor``, with the shape (1, T) and sampling rate
            consistent with the model's audio tokenizer (usually 24000 Hz).
        """

        if self.audio_tokenizer is None or self.text_tokenizer is None:
            raise RuntimeError(
                "Model is not loaded with audio/text tokenizers. Make sure you "
                "loaded the model with OmniVoice.from_pretrained()."
            )
        gen_config = (
            generation_config
            if generation_config is not None
            else OmniVoiceGenerationConfig.from_dict(kwargs)
        )

        self.eval()

        full_task = self._preprocess_all(
            text=text,
            language=language,
            ref_text=ref_text,
            ref_audio=ref_audio,
            voice_clone_prompt=voice_clone_prompt,
            instruct=instruct,
            preprocess_prompt=gen_config.preprocess_prompt,
            speed=speed,
            duration=duration,
        )

        short_idx, long_idx = full_task.get_indices(
            gen_config, self.audio_tokenizer.config.frame_rate
        )

        results = [None] * full_task.batch_size

        if short_idx:
            short_task = full_task.slice_task(short_idx)
            short_results = self._generate_iterative(short_task, gen_config)
            for idx, res in zip(short_idx, short_results):
                results[idx] = res

        if long_idx:
            long_task = full_task.slice_task(long_idx)
            long_results = self._generate_chunked(long_task, gen_config)
            for idx, res in zip(long_idx, long_results):
                results[idx] = res

        generated_audios = []
        for i in range(full_task.batch_size):
            assert results[i] is not None, f"Result {i} was not generated"
            generated_audios.append(
                self._decode_and_post_process(
                    results[i], full_task.ref_rms[i], gen_config  # type: ignore[arg-type]
                )
            )

        return generated_audios

    def create_voice_clone_prompt(
        self,
        ref_audio: Union[str, tuple[torch.Tensor, int]],
        ref_text: Optional[str] = None,
        preprocess_prompt: bool = True,
    ) -> VoiceClonePrompt:
        """Create a reusable voice clone prompt from reference audio.

        Args:
            ref_audio: File path (str) or ``(waveform, sample_rate)`` tuple.
                waveform should be a 1-D or 2-D torch.Tensor (channels x samples).
            ref_text: Transcript of the reference audio. If ``None``, use the
                loaded ASR pipeline or an installed Whisper snapshot without
                downloading. If neither is available, provide a transcript or
                explicitly load an ASR model with :meth:`load_asr_model`.
            preprocess_prompt: If ``True`` (default), apply silence removal and
                trimming to the reference audio, add punctuation in the end
                of reference text (if not already)

        Returns:
            A :class:`VoiceClonePrompt` that can be passed to :meth:`generate`.
        """
        if self.audio_tokenizer is None:
            raise RuntimeError(
                "Audio tokenizer is not loaded. Make sure you loaded the model "
                "with OmniVoice.from_pretrained()."
            )

        if isinstance(ref_audio, str):
            ref_wav = load_audio(ref_audio, self.sampling_rate)
        else:
            waveform, sr = ref_audio
            if waveform.dim() == 1:
                waveform = waveform.unsqueeze(0)
            if waveform.size(0) > 1:
                waveform = torch.mean(waveform, dim=0, keepdim=True)
            if sr != self.sampling_rate:
                waveform = torchaudio.functional.resample(
                    waveform, sr, self.sampling_rate
                )
            ref_wav = waveform

        ref_rms = torch.sqrt(torch.mean(torch.square(ref_wav))).item()
        # #1188: fail fast — and actionably — when the clip has no audio at
        # all (empty / digitally silent / NaN samples). Everything quieter
        # than the trim thresholds but real is recovered below, so this is
        # the only remaining hard failure for a reference clip.
        validate_clone_reference(ref_wav, ref_rms)
        input_gain = 1.0
        if 0 < ref_rms < 0.1:
            input_gain = 0.1 / ref_rms
            ref_wav = ref_wav * input_gain

        ref_duration = ref_wav.size(-1) / self.sampling_rate
        if ref_text is not None and ref_duration > CLONE_REF_TEXT_MAX_SECONDS:
            raise ValueError(clone_ref_transcript_too_long_message(ref_duration))
        if ref_text is None and ref_duration > CLONE_REF_WINDOW_SECONDS:
            # Transcript-free automatic selection examines every passage, but
            # caps the work at five bounded ASR calls. Longer references need
            # an explicit user-selected passage rather than a lossy sampling
            # policy that could silently miss speech between fixed windows.
            max_samples = int(CLONE_REF_WINDOW_SECONDS * self.sampling_rate)
            max_auto_samples = CLONE_REF_MAX_WINDOWS * max_samples
            if ref_wav.size(-1) > max_auto_samples:
                raise ValueError(
                    f"{CLONE_REF_TOO_LONG_MARKER} Reference audio is "
                    f"{ref_duration:.1f} seconds long; automatic transcript-free "
                    "selection supports at most 75 seconds. Trim the audio to a "
                    "3-10 second speech passage, or supply a matching transcript."
                )

            original_power = ref_wav.abs().amax(dim=0).square()
            activity_cap = max(float(original_power.mean()) * 100.0, 1e-10)

            def activity_score(audio):
                power = audio.abs().amax(dim=0).square().clamp_max(activity_cap)
                return float(power.double().sum())

            if self._asr_pipe is None:
                logger.info("Loading cached ASR for reference transcription ...")
                self._load_cached_reference_asr()
            candidates = list(ref_wav.split(max_samples, dim=-1))

            def speech_score(text):
                return len(re.sub(r"[^\w]+", "", text or "", flags=re.UNICODE))

            transcribed = [
                (self.transcribe((candidate, self.sampling_rate)), candidate)
                for candidate in candidates
            ]
            ref_text, ref_wav = max(
                transcribed,
                key=lambda item: (speech_score(item[0]), activity_score(item[1])),
            )
            if speech_score(ref_text) == 0:
                raise ValueError(
                    f"{CLONE_REF_NO_SPEECH_MARKER} Automatic speech detection "
                    "could not find spoken words in the reference. Trim it to a "
                    "clear 3-10 second speech passage, or supply a matching transcript."
                )

        if preprocess_prompt:
            # #1188: the fixed -50 dBFS silence threshold used to consume a
            # quiet-but-real recording wholesale and dead-end with
            # "Reference audio is empty after silence removal". The safe
            # variant retries with gentler thresholds and, as a last resort,
            # skips trimming — a quiet real clip must clone, not 400.
            ref_wav = remove_silence_safe(
                ref_wav,
                self.sampling_rate,
                mid_sil=200,
                lead_sil=100,
                trail_sil=200,
            )

        # Cropping and silence removal happen after the original validation.
        # Re-check the actual aligned passage that ASR/tokenization will see.
        prepared_rms = torch.sqrt(torch.mean(torch.square(ref_wav))).item()
        validate_clone_reference(ref_wav, prepared_rms)

        ref_duration = ref_wav.size(-1) / self.sampling_rate
        if ref_duration > 20.0:
            logger.warning(
                "Reference audio is %.1fs long (>20s). This may cause slower "
                "generation, higher memory usage, and degraded voice cloning "
                "quality. We recommend trimming it to 3-10s.",
                ref_duration,
            )

        # Auto-transcribe if ref_text not provided
        if ref_text is None:
            if self._asr_pipe is None:
                logger.info("Loading cached ASR for reference transcription ...")
                self._load_cached_reference_asr()
            ref_text = self.transcribe((ref_wav, self.sampling_rate))
            logger.debug("Auto-transcribed ref_text: %s", ref_text)

        chunk_size = self.audio_tokenizer.config.hop_length
        clip_size = int(ref_wav.size(-1) % chunk_size)
        ref_wav = ref_wav[:, :-clip_size] if clip_size > 0 else ref_wav
        aligned_rms = torch.sqrt(torch.mean(torch.square(ref_wav))).item()
        validate_clone_reference(ref_wav, aligned_rms)
        selected_ref_rms = aligned_rms / input_gain
        ref_audio_tokens = self.audio_tokenizer.encode(
            ref_wav.unsqueeze(0).to(self.audio_tokenizer.device),
        ).audio_codes.squeeze(
            0
        )  # (C, T)

        if preprocess_prompt:
            ref_text = add_punctuation(ref_text)

        return VoiceClonePrompt(
            ref_audio_tokens=ref_audio_tokens,
            ref_text=ref_text,
            ref_rms=selected_ref_rms,
        )

    def _decode_and_post_process(
        self,
        tokens: Union[torch.Tensor, List[torch.Tensor]],
        rms: Union[float, None],
        gen_config: OmniVoiceGenerationConfig,
    ) -> torch.Tensor:
        """
        Args:
            tokens: Audio tokens — either a single tensor of shape
                (num_codebooks, seq_len) or a list of chunk tensors.
            rms: RMS of the reference audio for volume adjustment.
            gen_config: Generation config for post-processing options.
        Returns:
            Decoded and post-processed audio tensor of shape (1, T).
        """
        tokenizer_device = self.audio_tokenizer.device
        if isinstance(tokens, list):
            chunk_audios = [
                self.audio_tokenizer.decode(t.to(tokenizer_device).unsqueeze(0))
                .audio_values[0]
                .cpu()
                for t in tokens
            ]
            audio_waveform = cross_fade_chunks(chunk_audios, self.sampling_rate)
        else:
            audio_waveform = (
                self.audio_tokenizer.decode(tokens.to(tokenizer_device).unsqueeze(0))
                .audio_values[0]
                .cpu()
            )

        return self._post_process_audio(
            audio_waveform,
            postprocess_output=gen_config.postprocess_output,
            ref_rms=rms,
        )

    def _post_process_audio(
        self,
        generated_audio: torch.Tensor,
        postprocess_output: bool,
        ref_rms: Union[float, None],
    ) -> torch.Tensor:
        """Optionally remove long silences, adjust volume, and add edge padding.

        Args:
            generated_audio: Audio tensor of shape (1, T).
            postprocess_output: If True, remove long silences and apply fade/pad.
            ref_rms: RMS of the reference audio for volume normalisation.
        Returns:
            Processed audio tensor of shape (1, T).
        """
        if postprocess_output:
            # #1188 (same class as the reference-clip bug): quiet generated
            # audio below the -50 dBFS silence threshold would be removed
            # wholesale here, producing an empty/near-empty WAV that fails
            # downstream decoding. The safe variant degrades to no trimming
            # instead of destroying the take.
            generated_audio = remove_silence_safe(
                generated_audio,
                self.sampling_rate,
                mid_sil=500,
                lead_sil=100,
                trail_sil=100,
            )

        if ref_rms is not None and ref_rms < 0.1:
            generated_audio = generated_audio * ref_rms / 0.1
        elif ref_rms is None:
            # No reference audio (voice design): peak-normalize to 0.5
            # to avoid clipping while keeping a comfortable volume level.
            peak = generated_audio.abs().max()
            if peak > 1e-6:
                generated_audio = generated_audio / peak * 0.5

        generated_audio = fade_and_pad_audio(
            generated_audio,
            sample_rate=self.sampling_rate,
        )
        return generated_audio

    def _generate_chunked(
        self, task: GenerationTask, gen_config: OmniVoiceGenerationConfig
    ) -> List[List[torch.Tensor]]:
        """Generate long audio by splitting text into chunks and batching.

        Each item in the returned list corresponds to one input and contains
        a list of audio token tensors — one per text chunk.

        Args:
            task: A :class:`GenerationTask` with one or more items whose
                estimated audio exceeds ``audio_chunk_threshold``.
            gen_config: Generation config (``audio_chunk_duration`` controls
                chunk size).
        Returns:
            Per-item list of chunk token-tensor lists.
        """
        # Chunk each item's text
        all_chunks = []
        for i in range(task.batch_size):
            avg_tokens_per_char = task.target_lens[i] / len(task.texts[i])
            text_chunk_len = int(
                gen_config.audio_chunk_duration
                * self.audio_tokenizer.config.frame_rate
                / avg_tokens_per_char
            )
            chunks = chunk_text_punctuation(
                text=task.texts[i],
                chunk_len=text_chunk_len,
                min_chunk_len=3,
            )
            logger.debug(f"Item {i} chunked into {len(chunks)} pieces: {chunks}")
            all_chunks.append(chunks)

        has_ref = [t is not None for t in task.ref_audio_tokens]
        assert all(has_ref) or not any(has_ref), (
            "Chunked inference requires all items to either have or not have "
            "ref_audio. Mixed ref/non-ref is not supported."
        )

        max_num_chunks = max(len(c) for c in all_chunks)

        # chunk_results[item_idx] = list of generated token tensors per chunk
        chunk_results = [[] for _ in range(task.batch_size)]

        def _run_batch(indices, texts, ref_audios, ref_texts):
            speed_list = task.speed
            target_lens = [
                self._estimate_target_tokens(
                    texts[j],
                    ref_texts[j],
                    ref_audios[j].size(-1) if ref_audios[j] is not None else None,
                    speed=speed_list[i] if speed_list else 1.0,
                )
                for j, i in enumerate(indices)
            ]
            sub_task = GenerationTask(
                batch_size=len(indices),
                texts=texts,
                target_lens=target_lens,
                langs=[task.langs[i] for i in indices],
                instructs=[task.instructs[i] for i in indices],
                ref_texts=ref_texts,
                ref_audio_tokens=ref_audios,
                ref_rms=[task.ref_rms[i] for i in indices],
                speed=[task.speed[i] for i in indices] if task.speed else None,
            )
            gen_tokens = self._generate_iterative(sub_task, gen_config)
            for j, idx in enumerate(indices):
                chunk_results[idx].append(gen_tokens[j])

        if all(has_ref):
            # All items have reference audio.
            # We still sequentially generate chunks within each item, but we
            # batch across items for the same chunk index. This allows to keep
            # the VRAM usage manageable while still benefiting from batching.
            for ci in range(max_num_chunks):
                indices = [i for i in range(task.batch_size) if ci < len(all_chunks[i])]
                if not indices:
                    continue
                _run_batch(
                    indices,
                    texts=[all_chunks[i][ci] for i in indices],
                    ref_audios=[task.ref_audio_tokens[i] for i in indices],
                    ref_texts=[task.ref_texts[i] for i in indices],
                )
        else:
            # No reference audio — generate chunk 0 for all items first,
            # then use chunk 0 output as reference for all subsequent chunks.
            indices_0 = [i for i in range(task.batch_size) if len(all_chunks[i]) > 0]
            _run_batch(
                indices_0,
                texts=[all_chunks[i][0] for i in indices_0],
                ref_audios=[None] * len(indices_0),
                ref_texts=[None] * len(indices_0),
            )
            first_chunk_map = {idx: chunk_results[idx][0] for idx in indices_0}

            # Batch all remaining chunks, using chunk 0 as fixed reference
            for ci in range(1, max_num_chunks):
                indices = [i for i in range(task.batch_size) if ci < len(all_chunks[i])]
                if not indices:
                    continue
                _run_batch(
                    indices,
                    texts=[all_chunks[i][ci] for i in indices],
                    ref_audios=[first_chunk_map[i] for i in indices],
                    ref_texts=[all_chunks[i][0] for i in indices],
                )

        return chunk_results

    def _preprocess_all(
        self,
        text: Union[str, list[str]],
        language: Union[str, list[str], None] = None,
        ref_text: Union[str, list[str], None] = None,
        ref_audio: Union[
            str,
            list[str],
            tuple[torch.Tensor, int],
            list[tuple[torch.Tensor, int]],
            None,
        ] = None,
        voice_clone_prompt: Union[
            VoiceClonePrompt, list[VoiceClonePrompt], None
        ] = None,
        instruct: Union[str, list[str], None] = None,
        preprocess_prompt: bool = True,
        speed: Union[float, list[Optional[float]], None] = None,
        duration: Union[float, list[Optional[float]], None] = None,
    ) -> GenerationTask:

        if isinstance(text, str):
            text_list = [text]
        else:
            assert isinstance(
                text, list
            ), "text should be a string or a list of strings"
            text_list = text
        batch_size = len(text_list)

        language_list = self._ensure_list(language, batch_size)
        language_list = [_resolve_language(lang) for lang in language_list]
        instruct_list = self._ensure_list(instruct, batch_size)
        for i, s in enumerate(instruct_list):
            if s is None:
                continue
            use_zh = bool(text_list[i] and _ZH_RE.search(text_list[i]))
            instruct_list[i] = _resolve_instruct(s, use_zh=use_zh)

        if voice_clone_prompt is not None and (
            ref_text is not None or ref_audio is not None
        ):
            logger.warning(
                "Both voice_clone_prompt and ref_text/ref_audio are provided. "
                "ref_text/ref_audio will be ignored."
            )
        if voice_clone_prompt is None and ref_audio is not None:
            # If voice_clone_prompt is not provided, create it from
            # ref_audio (ref_text will be auto-transcribed if not given).
            ref_text_list = self._ensure_list(ref_text, batch_size, auto_repeat=False)
            ref_audio_list = self._ensure_list(ref_audio, batch_size, auto_repeat=False)

            voice_clone_prompt = []
            for i in range(len(ref_text_list)):
                voice_clone_prompt.append(
                    self.create_voice_clone_prompt(
                        ref_audio=ref_audio_list[i],
                        ref_text=ref_text_list[i],
                        preprocess_prompt=preprocess_prompt,
                    )
                )

        voice_clone_prompt_list = self._ensure_list(voice_clone_prompt, batch_size)
        if voice_clone_prompt_list[0] is not None:
            ref_text_list = [vc.ref_text for vc in voice_clone_prompt_list]
            ref_audio_tokens_list = [
                vc.ref_audio_tokens for vc in voice_clone_prompt_list
            ]
            ref_rms_list = [vc.ref_rms for vc in voice_clone_prompt_list]
        else:
            ref_text_list = [None] * batch_size
            ref_audio_tokens_list = [None] * batch_size
            ref_rms_list = [None] * batch_size

        # Normalize speed/duration to per-item lists (may contain None).
        if speed is not None:
            if isinstance(speed, (int, float)):
                user_speed = [float(speed)] * batch_size
            else:
                user_speed = list(speed)
        else:
            user_speed = None

        if duration is not None:
            if isinstance(duration, (int, float)):
                durations = [float(duration)] * batch_size
            else:
                durations = list(duration)
        else:
            durations = None

        num_target_tokens_list = []
        for i in range(batch_size):
            # duration[i] overrides speed for estimation: use speed=1.0
            # to get the raw estimate, then override target_lens below.
            has_dur = durations is not None and durations[i] is not None
            item_speed = 1.0 if has_dur else (user_speed[i] if user_speed else 1.0)
            est = self._estimate_target_tokens(
                text_list[i],
                ref_text_list[i],
                ref_audio_tokens_list[i].size(-1)
                if ref_audio_tokens_list[i] is not None
                else None,
                speed=item_speed,
            )
            num_target_tokens_list.append(est)

        # Per-item duration overrides: set target_lens to exact frame count
        # and compute speed ratio so chunked generation scales proportionally.
        speed_list: Optional[List[float]] = None
        if durations is not None:
            frame_rate = self.audio_tokenizer.config.frame_rate
            speed_list = []
            for i in range(batch_size):
                if durations[i] is not None:
                    target_tokens = max(1, int(durations[i] * frame_rate))
                    est = num_target_tokens_list[i]
                    speed_list.append(est / target_tokens if target_tokens > 0 else 1.0)
                    num_target_tokens_list[i] = target_tokens
                else:
                    s = user_speed[i] if user_speed else None
                    speed_list.append(s if s is not None else 1.0)
        elif user_speed is not None:
            speed_list = [s if s is not None else 1.0 for s in user_speed]

        return GenerationTask(
            batch_size=batch_size,
            texts=text_list,
            target_lens=num_target_tokens_list,
            langs=language_list,
            instructs=instruct_list,
            ref_texts=ref_text_list,
            ref_audio_tokens=ref_audio_tokens_list,
            ref_rms=ref_rms_list,
            speed=speed_list,
        )

    def _estimate_target_tokens(self, text, ref_text, num_ref_audio_tokens, speed=1.0):
        """Estimate number of target audio tokens."""
        if num_ref_audio_tokens is None or ref_text is None or len(ref_text) == 0:
            # Fall back to a simple heuristic
            ref_text = "Nice to meet you."
            num_ref_audio_tokens = 25

        est = self.duration_estimator.estimate_duration(
            text, ref_text, num_ref_audio_tokens
        )
        if speed > 0 and speed != 1.0:
            est = est / speed
        return max(1, int(est))

    def _ensure_list(
        self, x: Union[Any, List[Any]], batch_size: int, auto_repeat: bool = True
    ) -> List[Any]:
        x_list = x if isinstance(x, list) else [x]
        if len(x_list) not in (
            1,
            batch_size,
        ):
            raise ValueError(
                f"should be either the number of the text or 1, but got {len(x_list)}"
            )
        if auto_repeat and len(x_list) == 1 and batch_size is not None:
            x_list = x_list * batch_size
        return x_list

    def _prepare_inference_inputs(
        self,
        text: str,
        num_target_tokens: int,
        ref_text: Optional[str] = None,
        ref_audio_tokens: Optional[torch.Tensor] = None,
        lang: Optional[str] = None,
        instruct: Optional[str] = None,
        denoise: bool = True,
    ):
        """Prepare input_ids and audio masks for inference.
        Args:
            text: Target text to generate.
            num_target_tokens: Number of audio tokens to generate.
            ref_text: Optional reference text for voice cloning.
            ref_audio_tokens: Optional reference audio tokens for voice cloning.
                with shape (C, T).
            lang: Optional language ID.
            instruct: Optional style instruction for voice design.
            denoise: Whether to include the <|denoise|> token.
        """

        # Build style tokens: <|denoise|> + <|lang_start|>...<|lang_end|>
        #                      + <|instruct_start|>...<|instruct_end|>
        style_text = ""
        if denoise and ref_audio_tokens is not None:
            style_text += "<|denoise|>"
        lang_str = lang if lang else "None"
        instruct_str = instruct if instruct else "None"
        style_text += f"<|lang_start|>{lang_str}<|lang_end|>"
        style_text += f"<|instruct_start|>{instruct_str}<|instruct_end|>"

        style_tokens = (
            self.text_tokenizer(style_text, return_tensors="pt")
            .input_ids.repeat(self.config.num_audio_codebook, 1)
            .unsqueeze(0)
        ).to(
            self.device
        )  # [1, C, N1]

        # Build text tokens
        full_text = _combine_text(ref_text=ref_text, text=text)
        wrapped_text = f"<|text_start|>{full_text}<|text_end|>"
        text_tokens = (
            _tokenize_with_nonverbal_tags(wrapped_text, self.text_tokenizer)
            .repeat(self.config.num_audio_codebook, 1)
            .unsqueeze(0)
        ).to(
            self.device
        )  # [1, C, N2]

        # Target: all MASK
        target_audio_tokens = torch.full(
            (1, self.config.num_audio_codebook, num_target_tokens),
            self.config.audio_mask_id,
            dtype=torch.long,
            device=self.device,
        )

        # Conditional input
        parts = [style_tokens, text_tokens]
        if ref_audio_tokens is not None:
            parts.append(ref_audio_tokens.unsqueeze(0).to(self.device))
        parts.append(target_audio_tokens)
        cond_input_ids = torch.cat(parts, dim=2)

        cond_total_length = cond_input_ids.shape[2]
        cond_audio_start_idx = cond_total_length - num_target_tokens
        if ref_audio_tokens is not None:
            cond_audio_start_idx -= ref_audio_tokens.size(-1)

        cond_audio_mask = torch.zeros(
            1, cond_total_length, dtype=torch.bool, device=self.device
        )
        cond_audio_mask[0, cond_audio_start_idx:] = True

        return {
            "input_ids": cond_input_ids,
            "audio_mask": cond_audio_mask,
        }

    def _generate_iterative(
        self, task: GenerationTask, gen_config: OmniVoiceGenerationConfig
    ) -> List[torch.Tensor]:
        """N-step iterative unmasked decoding.

        Args:
            task: A :class:`GenerationTask` containing batch texts, target
                lengths, languages, instructions, and optional reference data.
            gen_config: A :class:`OmniVoiceGenerationConfig` controlling
                decoding steps, guidance, temperatures, etc.
        Returns:
            List of generated audio token tensors of shape (C, T) (one per
            input text).
        """

        B = task.batch_size

        for i in range(B):
            logger.debug(
                "Item %d — text: %s | ref_text: %s | instruct: %s | lang: %s | target_tokens: %d",
                i,
                task.texts[i],
                task.ref_texts[i],
                task.instructs[i],
                task.langs[i],
                task.target_lens[i],
            )

        inputs_list = [
            self._prepare_inference_inputs(
                task.texts[i],
                task.target_lens[i],
                task.ref_texts[i],
                task.ref_audio_tokens[i],
                task.langs[i],
                task.instructs[i],
                gen_config.denoise,
            )
            for i in range(B)
        ]

        c_lens = [inp["input_ids"].size(2) for inp in inputs_list]
        max_c_len = max(c_lens)
        pad_id = self.config.audio_mask_id  # Or any other tokens

        batch_input_ids = torch.full(
            (2 * B, self.config.num_audio_codebook, max_c_len),
            pad_id,
            dtype=torch.long,
            device=self.device,
        )
        batch_audio_mask = torch.zeros(
            (2 * B, max_c_len), dtype=torch.bool, device=self.device
        )
        batch_attention_mask = torch.zeros(
            (2 * B, 1, max_c_len, max_c_len), dtype=torch.bool, device=self.device
        )

        for i, inp in enumerate(inputs_list):
            c_len, u_len = c_lens[i], task.target_lens[i]

            # Cond (0 ~ B-1)
            batch_input_ids[i, :, :c_len] = inp["input_ids"]
            batch_audio_mask[i, :c_len] = inp["audio_mask"]
            batch_attention_mask[i, :, :c_len, :c_len] = True

            # Uncond (B ~ 2B-1)
            batch_input_ids[B + i, :, :u_len] = inp["input_ids"][..., -u_len:]
            batch_audio_mask[B + i, :u_len] = inp["audio_mask"][..., -u_len:]
            batch_attention_mask[B + i, :, :u_len, :u_len] = True
            if max_c_len > u_len:
                pad_diag = torch.arange(u_len, max_c_len, device=self.device)
                batch_attention_mask[B + i, :, pad_diag, pad_diag] = True

        tokens = torch.full(
            (B, self.config.num_audio_codebook, max(task.target_lens)),
            self.config.audio_mask_id,
            dtype=torch.long,
            device=self.device,
        )

        timesteps = _get_time_steps(
            t_start=0.0,
            t_end=1.0,
            num_step=gen_config.num_step + 1,
            t_shift=gen_config.t_shift,
        ).tolist()
        schedules = []
        for t_len in task.target_lens:
            total_mask = t_len * self.config.num_audio_codebook
            rem = total_mask
            sched = []
            for step in range(gen_config.num_step):
                num = (
                    rem
                    if step == gen_config.num_step - 1
                    else min(
                        math.ceil(total_mask * (timesteps[step + 1] - timesteps[step])),
                        rem,
                    )
                )
                sched.append(int(num))
                rem -= int(num)
            schedules.append(sched)

        layer_ids = torch.arange(
            self.config.num_audio_codebook, device=self.device
        ).view(1, -1, 1)

        for step in range(gen_config.num_step):
            batch_logits = self(
                input_ids=batch_input_ids,
                audio_mask=batch_audio_mask,
                attention_mask=batch_attention_mask,
            ).logits.to(torch.float32)

            for i in range(B):
                k = schedules[i][step]
                if k <= 0:
                    continue

                c_len, t_len = c_lens[i], task.target_lens[i]

                # Extract real target Logits
                # [1, C, T, V]
                c_logits = batch_logits[i : i + 1, :, c_len - t_len : c_len, :]
                u_logits = batch_logits[B + i : B + i + 1, :, :t_len, :]

                pred_tokens, scores = self._predict_tokens_with_scoring(
                    c_logits, u_logits, gen_config
                )

                scores = scores - (layer_ids * gen_config.layer_penalty_factor)

                if gen_config.position_temperature > 0.0:
                    scores = _gumbel_sample(scores, gen_config.position_temperature)

                sample_tokens = tokens[i : i + 1, :, :t_len]
                scores.masked_fill_(
                    sample_tokens != self.config.audio_mask_id, -float("inf")
                )

                _, topk_idx = torch.topk(scores.flatten(), k)
                flat_tokens = sample_tokens.flatten()
                flat_tokens[topk_idx] = pred_tokens.flatten()[topk_idx]
                sample_tokens.copy_(flat_tokens.view_as(sample_tokens))

                # Update individual slices into batched structure
                tokens[i : i + 1, :, :t_len] = sample_tokens
                batch_input_ids[i : i + 1, :, c_len - t_len : c_len] = sample_tokens
                batch_input_ids[B + i : B + i + 1, :, :t_len] = sample_tokens

        return [tokens[i, :, : task.target_lens[i]] for i in range(B)]

    def _predict_tokens_with_scoring(self, c_logits, u_logits, gen_config):
        if gen_config.guidance_scale != 0:
            c_log_probs = F.log_softmax(c_logits, dim=-1)
            u_log_probs = F.log_softmax(u_logits, dim=-1)
            log_probs = torch.log_softmax(
                c_log_probs + gen_config.guidance_scale * (c_log_probs - u_log_probs),
                dim=-1,
            )
        else:
            log_probs = F.log_softmax(c_logits, dim=-1)

        log_probs[..., self.config.audio_mask_id] = -float("inf")

        if gen_config.class_temperature > 0.0:
            filtered_probs = _filter_top_k(log_probs, ratio=0.1)
            pred_tokens = _gumbel_sample(
                filtered_probs, gen_config.class_temperature
            ).argmax(dim=-1)
        else:
            pred_tokens = log_probs.argmax(dim=-1)

        confidence_scores = log_probs.max(dim=-1)[0]

        return pred_tokens, confidence_scores


# ---------------------------------------------------------------------------
# Standalone helpers
# ---------------------------------------------------------------------------


def _get_packed_mask(document_ids):
    return partial(_mask_mod_packed, document_ids)


def _mask_mod_packed(document_ids, b, h, q_idx, kv_idx):
    # 1. Sequence Packing Logic: Tokens must belong to the same document.
    # Note: The doc_id for padding tokens is -1, which will automatically not match
    # (if handled correctly) or be ignored.
    same_doc = document_ids[q_idx] == document_ids[kv_idx]
    return same_doc


def _resolve_language(language: Optional[str]) -> Union[str, None]:
    from omnivoice.utils.lang_map import LANG_IDS, LANG_NAME_TO_ID

    if language is None or language.lower() == "none":
        return None
    if language in LANG_IDS:
        return language
    key = language.lower()
    if key in LANG_NAME_TO_ID:
        return LANG_NAME_TO_ID[key]
    logger.warning(
        f"Language '{language}' is not recognized. "
        f"Please use a valid language ID (e.g., 'en', 'zh', 'ja', 'de') "
        f"or a full language name (e.g., 'English', 'Chinese', 'Japanese'). "
        f"See supported_language_ids() or supported_language_names() for details. "
        f"Falling back to None (language-agnostic mode)."
    )
    return None


def _resolve_instruct(
    instruct: Optional[str], use_zh: bool = False
) -> Union[str, None]:
    """Validate and normalise a voice-design instruct string.

    Supported instruct items (case-insensitive for English):

    English (comma + space separated):
        gender: male, female
        age: child, teenager, young adult, middle-aged, elderly
        pitch: very low pitch, low pitch, moderate pitch,
               high pitch, very high pitch
        style: whisper
        accent: american accent, british accent, australian accent, ...

    Chinese (full-width comma separated):
        gender: 男, 女
        age: 儿童, 少年, 青年, 中年, 老年
        pitch: 极低音调, 低音调, 中音调, 高音调, 极高音调
        style: 耳语
        dialect: 河南话, 陕西话, 四川话, 贵州话, 云南话,
                 桂林话, 济南话, 石家庄话, 甘肃话, 宁夏话,
                 青岛话, 东北话

    Minor issues (auto-fixed):
      - Wrong separator (half-width comma in Chinese instruct or
        full-width comma in English instruct)
      - Leading / trailing commas

    Major issues (raise ``ValueError``):
      - Unsupported or misspelled instruct items
      - Suggestions are offered for close matches

    Args:
        instruct: Raw instruct string, or ``None``.
        use_zh: If True, normalise all items to Chinese (used when the
            synthesis text contains Chinese and no accent is specified).

    Returns:
        Normalised instruct string, or ``None``.

    Raises:
        ValueError: if any instruct item is unsupported or misspelled.
    """
    if instruct is None:
        return None

    instruct_str = instruct.strip()
    if not instruct_str:
        return None

    # Split on both half-width and full-width commas
    raw_items = re.split(r"\s*[,，]\s*", instruct_str)
    # Tolerate the "[object Object]" sentinel a buggy frontend build could have
    # persisted into voice_profiles.instruct (#550 et al): drop it instead of
    # 400-ing the whole generation. The 0006 migration heals stored values; this
    # guards any that slip through (e.g. generation_history rows).
    raw_items = [x for x in raw_items if x and x.strip().lower() != "[object object]"]

    # Validate each item
    unknown = []
    normalised = []
    for raw in raw_items:
        n = raw.strip().lower()
        if n in _INSTRUCT_ALL_VALID:
            normalised.append(n)
        else:
            sug = difflib.get_close_matches(n, _INSTRUCT_ALL_VALID, n=1, cutoff=0.6)
            unknown.append((raw, n, sug[0] if sug else None))

    if unknown:
        lines = []
        for raw, n, sug in unknown:
            if sug:
                lines.append(f"  '{raw}' -> '{n}' (unsupported; did you mean '{sug}'?)")
            else:
                lines.append(f"  '{raw}' -> '{n}' (unsupported)")
        err = (
            f"Unsupported instruct items found in {instruct_str}:\n"
            + "\n".join(lines)
            + "\n\nValid English items: "
            + ", ".join(sorted(_INSTRUCT_VALID_EN))
            + "\nValid Chinese items: "
            + "，".join(sorted(_INSTRUCT_VALID_ZH))
            + "\n\nTip: Use only English or only Chinese instructs. "
            "English instructs should use comma + space (e.g. "
            "'male, indian accent'),\nChinese instructs should use full-width "
            "comma (e.g. '男，河南话')."
        )
        raise ValueError(err)

    # --- Language consistency: dialect forces Chinese, accent forces English ---
    has_dialect = any(n.endswith("话") for n in normalised)
    has_accent = any(" accent" in n for n in normalised)

    if has_dialect and has_accent:
        raise ValueError(
            "Cannot mix Chinese dialect and English accent in a single instruct. "
            "Dialects are for Chinese speech, accents for English speech."
        )

    if has_dialect:
        use_zh = True
    elif has_accent:
        use_zh = False

    # --- Unify to single language ---
    if use_zh:
        normalised = [_INSTRUCT_EN_TO_ZH.get(n, n) for n in normalised]
    else:
        normalised = [_INSTRUCT_ZH_TO_EN.get(n, n) for n in normalised]

    # --- Category conflict check ---
    conflicts = []
    for cat in _INSTRUCT_MUTUALLY_EXCLUSIVE:
        hits = [n for n in normalised if n in cat]
        if len(hits) > 1:
            conflicts.append(hits)
    if conflicts:
        parts = []
        for group in conflicts:
            parts.append(" vs ".join(f"'{x}'" for x in group))
        raise ValueError(
            "Conflicting instruct items within the same category: "
            + "; ".join(parts)
            + ". Each category (gender, age, pitch, style, accent, dialect) "
            "allows at most one item."
        )

    # Determine separator based on language
    has_zh = any(any("\u4e00" <= c <= "\u9fff" for c in n) for n in normalised)
    separator = "，" if has_zh else ", "

    return separator.join(normalised)


def _filter_top_k(logits: torch.Tensor, ratio: float = 0.1) -> torch.Tensor:
    k = math.ceil(ratio * logits.shape[-1])
    val, ind = logits.topk(k, dim=-1)
    probs = torch.full_like(logits, float("-inf"))
    probs.scatter_(-1, ind, val)
    return probs


def _gumbel_sample(logits: torch.Tensor, temperature: float) -> torch.Tensor:
    scaled_logits = logits / temperature
    u = torch.rand_like(scaled_logits)
    gumbel_noise = -torch.log(-torch.log(u + 1e-10) + 1e-10)
    return scaled_logits + gumbel_noise


def _get_time_steps(
    t_start: float = 0.0,
    t_end: float = 1.0,
    num_step: int = 10,
    t_shift: float = 1.0,
    device: torch.device = torch.device("cpu"),
) -> torch.Tensor:
    timesteps = torch.linspace(t_start, t_end, num_step + 1).to(device)
    timesteps = t_shift * timesteps / (1 + (t_shift - 1) * timesteps)
    return timesteps


_NONVERBAL_PATTERN = re.compile(
    r"\[(laughter|sigh|confirmation-en|question-en|question-ah|question-oh|"
    r"question-ei|question-yi|surprise-ah|surprise-oh|surprise-wa|"
    r"surprise-yo|dissatisfaction-hnn)\]"
)


def _tokenize_with_nonverbal_tags(text: str, tokenizer) -> torch.Tensor:
    """Tokenize text containing non-verbal tags, handling each tag independently.

    Non-verbal tags are tokenized standalone to guarantee consistent token
    IDs regardless of surrounding language context (Chinese, English, etc.).

    Args:
        text: Full text string potentially containing non-verbal tags.
        tokenizer: HuggingFace text tokenizer instance.
    Returns:
        Token IDs tensor of shape (1, seq_len).
    """
    parts = []
    last_end = 0
    for m in _NONVERBAL_PATTERN.finditer(text):
        if m.start() > last_end:
            segment = text[last_end : m.start()]
            ids = tokenizer(segment, add_special_tokens=False).input_ids
            if ids:
                parts.append(ids)
        tag_ids = tokenizer(m.group(), add_special_tokens=False).input_ids
        if tag_ids:
            parts.append(tag_ids)
        last_end = m.end()
    if last_end < len(text):
        segment = text[last_end:]
        ids = tokenizer(segment, add_special_tokens=False).input_ids
        if ids:
            parts.append(ids)

    if not parts:
        result = tokenizer(text, return_tensors="pt").input_ids
    else:
        combined = []
        for p in parts:
            combined.extend(p)
        result = torch.tensor([combined], dtype=torch.long)
    return result


def _combine_text(text, ref_text: Optional[str] = None) -> str:

    # combine with reference text if not None
    if ref_text:
        full_text = ref_text.strip() + " " + text.strip()
    else:
        full_text = text.strip()

    # filter out newline / carriage-return characters
    full_text = re.sub(r"[\r\n]+", "", full_text)

    # collapse consecutive spaces / tabs into a single space
    full_text = re.sub(r"[ \t]+", " ", full_text)

    # remove spaces around chinese characters
    chinese_range = r"[\u4e00-\u9fff]"
    pattern = rf"(?<={chinese_range})\s+|\s+(?={chinese_range})"
    full_text = re.sub(pattern, "", full_text)

    return full_text


# ---------------------------------------------------------------------------
# Register with HuggingFace Auto classes
# ---------------------------------------------------------------------------

AutoConfig.register("omnivoice", OmniVoiceConfig)
AutoModel.register(OmniVoiceConfig, OmniVoice)
