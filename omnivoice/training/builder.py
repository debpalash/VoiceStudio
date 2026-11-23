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

"""Builders for constructing training components.

Provides factory functions to assemble the model, tokenizer, and data loaders
from a ``TrainingConfig``. Called by ``omnivoice.cli.train`` to set up training.

Key functions:
- ``build_model_and_tokenizer()``: Loads the model and text tokenizer.
- ``build_dataloaders()``: Builds packed train/eval data loaders
  from a data config JSON.
"""

import logging
from functools import partial
from typing import Tuple

import torch
from torch.utils.data import DataLoader
from transformers import AutoConfig, AutoModel, AutoTokenizer
from transformers import logging as hf_logging
from transformers.trainer_utils import seed_worker

from omnivoice.data.batching import PackingIterableDataset
from omnivoice.data.collator import PackingDataCollator
from omnivoice.data.dataset import WebDatasetReader, prepare_data_manifests_from_json
from omnivoice.data.processor import OmniVoiceSampleProcessor
from omnivoice.models.omnivoice import OmniVoice, OmniVoiceConfig
from omnivoice.training.config import TrainingConfig

logger = logging.getLogger(__name__)


def build_model_and_tokenizer(
    config: TrainingConfig,
) -> Tuple[OmniVoice, AutoTokenizer]:
    """Load Tokenizer and Model, handle resizing and special tokens."""
    logger.info("Initializing Model & Tokenizer...")

    # 1. Tokenizer
    tokenizer_path = (
        config.init_from_checkpoint
        if config.init_from_checkpoint
        else config.llm_name_or_path
    )
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_path)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    new_tokens = [
        "<|denoise|>",
        "<|lang_start|>",
        "<|lang_end|>",
        "<|instruct_start|>",
        "<|instruct_end|>",
        "<|text_start|>",
        "<|text_end|>",
    ]

    tokens_to_add = [t for t in new_tokens if t not in tokenizer.get_vocab()]
    if tokens_to_add:
        tokenizer.add_special_tokens({"additional_special_tokens": tokens_to_add})

    if config.init_from_checkpoint:
        logger.info(f"Loading weights from {config.init_from_checkpoint}")
        model = OmniVoice.from_pretrained(
            config.init_from_checkpoint,
            attn_implementation="flex_attention",
            dtype=torch.float32,
            train=True,
        )
    else:
        llm_config = AutoConfig.from_pretrained(config.llm_name_or_path)

        ov_config = OmniVoiceConfig(
            audio_vocab_size=config.audio_vocab_size,
            audio_mask_id=config.audio_mask_id,
            num_audio_codebook=config.num_audio_codebook,
            audio_codebook_weights=config.audio_codebook_weights,
            llm_config=llm_config,
        )

        original_level = hf_logging.get_verbosity()
        hf_logging.set_verbosity_error()  # suppress expected lm_head.weight warnings

        # Windows: read the base LLM's weights with pread too (#4142).
        from omnivoice.utils.safetensors_pread import install_pread_backend

        install_pread_backend()
        llm = AutoModel.from_pretrained(
            config.llm_name_or_path,
            attn_implementation="flex_attention",
            dtype=torch.float32,
        )

        hf_logging.set_verbosity(original_level)
        model = OmniVoice(config=ov_config, llm=llm)

    # 3. Resize Embeddings
    if len(tokenizer) != model.config.llm_config.vocab_size:
        model.llm.resize_token_embeddings(len(tokenizer))
        model.config.llm_config.vocab_size = len(tokenizer)

    # 4. Config IDs
    model.config.pad_token_id = tokenizer.pad_token_id
    model.config.bos_token_id = tokenizer.bos_token_id
    model.config.eos_token_id = tokenizer.eos_token_id

    return model, tokenizer


def build_dataloaders(
    config: TrainingConfig, tokenizer: AutoTokenizer
) -> Tuple[DataLoader, DataLoader]:
    """Setup Data Pipeline: Manifests -> WDS -> Packing -> Loaders."""
    logger.info("Initializing Data Readers...")

    processor = OmniVoiceSampleProcessor(
        text_tokenizer=tokenizer,
        num_channels=config.num_audio_codebook,
        audio_mask_id=config.audio_mask_id,
        prompt_ratio_range=config.prompt_ratio_range,
        mask_ratio_range=config.mask_ratio_range,
        drop_cond_ratio=config.drop_cond_ratio,
        language_ratio=config.language_ratio,
        use_pinyin_ratio=config.use_pinyin_ratio,
        instruct_ratio=config.instruct_ratio,
        only_instruct_ratio=config.only_instruct_ratio,
    )

    train_manifests, dev_manifests = prepare_data_manifests_from_json(
        config.data_config
    )
    raw_train_ds = WebDatasetReader(manifests=train_manifests, evaluation=False)

    train_dataset = PackingIterableDataset(raw_train_ds, processor, config.batch_tokens)

    collate_fn = PackingDataCollator(processor, config.batch_tokens)

    init_fn = partial(
        seed_worker,
        num_workers=config.num_workers,
        rank=torch.distributed.get_rank() if torch.distributed.is_initialized() else 0,
    )

    train_loader = DataLoader(
        train_dataset,
        batch_size=None,  # Each item is a batch packed to the target batch_tokens
        num_workers=config.num_workers,
        collate_fn=collate_fn,
        worker_init_fn=init_fn,
        pin_memory=True,
        prefetch_factor=4,
    )

    eval_loader = None
    if dev_manifests:
        raw_dev_ds = WebDatasetReader(manifests=dev_manifests, evaluation=True)
        dev_dataset = PackingIterableDataset(raw_dev_ds, processor, config.batch_tokens)
        eval_loader = DataLoader(
            dev_dataset,
            batch_size=None,  # Each item is a batch packed to the target batch_tokens
            num_workers=1,
            collate_fn=collate_fn,
            pin_memory=True,
            prefetch_factor=2,
        )

    return train_loader, eval_loader
