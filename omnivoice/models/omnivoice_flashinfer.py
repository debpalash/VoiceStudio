# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION.  All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""FlashInfer-accelerated iterative decoding for OmniVoice.

Approach (mirrors CosyVoice/runtime/triton_trtllm/token2wav_dit_flashinfer.py):

- Sequence packing: the baseline pads the uncond (CFG) sequence to the cond
  length and runs batch=2 with a (2,1,S,S) bool mask. Here cond+uncond are
  packed into ONE row of length c_len+u_len with per-document positions and
  flashinfer ragged attention (qo_indptr = document boundaries) — no pad
  compute, no S^2 mask materialization.
- Attention: registered as a custom HF attention implementation
  ("omnivoice_fi") via AttentionInterface; reads the wrapper planned
  once per generation from a module-level context. HF mask construction is
  bypassed by passing attention_mask={"full_attention": None}.
- KV cache: disabled (llm.config.use_cache=False). Iterative bidirectional
  decoding recomputes the full sequence every step, so the DynamicCache the
  baseline builds each forward is pure overhead.
- Optional CUDA graphs: one graph per packed shape; all 32 denoising steps
  replay the same graph (input_ids/audio_mask/position_ids are copied into
  static buffers). Each shape owns a private flashinfer wrapper, since a
  plan bakes its launch metadata into the captured graph.

Usage:
    from omnivoice_flashinfer import apply_flashinfer
    apply_flashinfer(model, enable_cuda_graph=True)

Ported from upstream k2-fsa/OmniVoice master with one behavioural change:
the unmasking schedule uses ``num_step + 1`` timesteps to match this repo's
``_generate_iterative``. VoiceStudio enables it via ``OMNIVOICE_FLASHINFER``
(see services/model_manager.py); ``flashinfer`` is an optional dependency and
this module must only be imported after that opt-in.
"""

import math
import time
from types import MethodType
from typing import List

import flashinfer
import torch
import torch.nn.functional as F
from transformers.modeling_utils import AttentionInterface

from omnivoice.models.omnivoice import (
    GenerationTask,
    OmniVoiceGenerationConfig,
    _get_time_steps,
    _gumbel_sample,
)

_WORKSPACE_SIZE = 128 * 1024 * 1024
# Context read by the registered attention function. "wrapper" must be planned
# for the current packed layout before any llm forward.
_CTX = {"wrapper": None}


def _flashinfer_attention(
    module, query, key, value, attention_mask, scaling=None, dropout=0.0, **kwargs
):
    """query (1, Hq, S, D), key/value (1, Hkv, S, D) — packed documents."""
    _b, hq, s, d = query.shape
    hkv = key.shape[1]
    q = query.transpose(1, 2).reshape(s, hq, d)
    k = key.transpose(1, 2).reshape(s, hkv, d)
    v = value.transpose(1, 2).reshape(s, hkv, d)
    out = _CTX["wrapper"].run(q, k, v)  # (S, Hq, D)
    return out.view(1, s, hq, d), None


AttentionInterface.register("omnivoice_fi", _flashinfer_attention)


def _fi_rmsnorm_forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
    """Single-kernel replacement for Qwen3RMSNorm.forward (a 7-kernel
    fp32-upcast chain in eager mode). flashinfer.norm.rmsnorm computes in
    fp32 internally and matches to fp16 rounding."""
    shape = hidden_states.shape
    out = flashinfer.norm.rmsnorm(
        hidden_states.reshape(-1, shape[-1]).contiguous(),
        self.weight,
        eps=self.variance_epsilon,
    )
    return out.view(shape)


def _patch_rmsnorm(llm):
    from transformers.models.qwen3.modeling_qwen3 import Qwen3RMSNorm

    n = 0
    for module in llm.modules():
        if isinstance(module, Qwen3RMSNorm):
            module.forward = MethodType(_fi_rmsnorm_forward, module)
            n += 1
    return n


def _fi_attention_module_forward(
    self,
    hidden_states,
    position_embeddings=None,
    attention_mask=None,
    past_key_values=None,
    **kwargs,
):
    """NHD-layout replacement for Qwen3Attention.forward (packed batch=1).

    The stock forward works in (B, H, S, D): the rotate-half RoPE costs a cat
    plus four elementwise passes, and handing (B,H,S,D) to the ragged wrapper
    costs three transpose copies. Keeping everything in (S, H, D) removes all
    of that; RoPE is one fused in-place kernel driven by packed position ids
    (read from _CTX, set per generation / baked per graph)."""
    s = hidden_states.shape[1]
    x = hidden_states[0]  # (S, hidden)
    if getattr(self, "_fi_w_qkv", None) is not None:
        qkv = F.linear(x, self._fi_w_qkv)
        q, k, v = qkv.split(self._fi_qkv_split, dim=-1)
        # split views are strided; reshape materializes contiguous copies
        # (q/k would be copied inside the fused rmsnorm anyway)
        q = self.q_norm(q.reshape(s, -1, self.head_dim))
        k = self.k_norm(k.reshape(s, -1, self.head_dim))
        v = v.reshape(s, -1, self.head_dim)
    else:
        q = self.q_norm(self.q_proj(x).view(s, -1, self.head_dim))
        k = self.k_norm(self.k_proj(x).view(s, -1, self.head_dim))
        v = self.v_proj(x).view(s, -1, self.head_dim)
    flashinfer.rope.apply_rope_pos_ids_inplace(
        q, k, _CTX["pos_ids"], rope_theta=self._fi_rope_theta, interleave=False
    )
    slots = _CTX.get("doc_slots")
    if slots is not None:
        # bucketed-graph mode: a flashinfer plan bakes document boundaries
        # into the graph, so attention runs per fixed-length document slot as
        # SDPA with an O(slot) key-padding mask whose contents are rewritten
        # per generation. (A dense (S,S) block-diag mask scales quadratically
        # and the enable_gqa+mask combo drops SDPA to the math backend, so
        # k/v are pre-expanded to full heads instead.)
        ng = self.num_key_value_groups
        k = k.repeat_interleave(ng, dim=1)  # (S, Hq, D)
        v = v.repeat_interleave(ng, dim=1)
        out = torch.empty_like(q)
        for start, slot_len, m in slots:
            od = F.scaled_dot_product_attention(
                q[start : start + slot_len].transpose(0, 1).unsqueeze(0),
                k[start : start + slot_len].transpose(0, 1).unsqueeze(0),
                v[start : start + slot_len].transpose(0, 1).unsqueeze(0),
                attn_mask=m,
            )
            out[start : start + slot_len] = od.squeeze(0).transpose(0, 1)
    else:
        out = _CTX["wrapper"].run(q, k, v)  # (S, Hq, D)
    return self.o_proj(out.reshape(s, -1)).unsqueeze(0), None


def _patch_attention_forward(llm, fuse_qkv=True):
    theta = llm.config.rope_parameters["rope_theta"]
    for layer in llm.layers:
        attn = layer.self_attn
        attn._fi_rope_theta = theta
        if fuse_qkv:
            attn._fi_w_qkv = torch.cat(
                [attn.q_proj.weight, attn.k_proj.weight, attn.v_proj.weight], dim=0
            )
            attn._fi_qkv_split = [
                attn.q_proj.weight.shape[0],
                attn.k_proj.weight.shape[0],
                attn.v_proj.weight.shape[0],
            ]
        attn.forward = MethodType(_fi_attention_module_forward, attn)


def _fi_mlp_forward(self, x):
    """Qwen3MLP with fused gate+up GEMM and flashinfer silu_and_mul
    (2 GEMMs + silu + mul -> 1 GEMM + 1 fused kernel)."""
    y = F.linear(x[0], self._fi_w_gate_up)  # (S, 2*inter)
    y = flashinfer.activation.silu_and_mul(y)
    return self.down_proj(y).unsqueeze(0)


def _patch_mlp(llm):
    for layer in llm.layers:
        mlp = layer.mlp
        mlp._fi_w_gate_up = torch.cat([mlp.gate_proj.weight, mlp.up_proj.weight], dim=0)
        mlp.forward = MethodType(_fi_mlp_forward, mlp)


class PackedAttnRunner:
    def __init__(
        self,
        num_qo_heads,
        num_kv_heads,
        head_dim,
        device,
        workspace_size=_WORKSPACE_SIZE,
    ):
        self.num_qo_heads = num_qo_heads
        self.num_kv_heads = num_kv_heads
        self.head_dim = head_dim
        self.device = device
        self._workspace = torch.zeros(workspace_size, dtype=torch.uint8, device=device)
        self.wrapper = flashinfer.BatchPrefillWithRaggedKVCacheWrapper(
            self._workspace, "NHD"
        )
        self._planned_key = None

    def plan(self, doc_lens: List[int], dtype: torch.dtype):
        key = (tuple(doc_lens), dtype)
        if key == self._planned_key:
            return
        indptr = torch.zeros(len(doc_lens) + 1, dtype=torch.int32, device=self.device)
        indptr[1:] = torch.cumsum(
            torch.tensor(doc_lens, dtype=torch.int32, device=self.device), dim=0
        )
        self.wrapper.plan(
            indptr,
            indptr,
            self.num_qo_heads,
            self.num_kv_heads,
            self.head_dim,
            causal=False,
            sm_scale=self.head_dim**-0.5,
            q_data_type=dtype,
            kv_data_type=dtype,
        )
        self._planned_key = key


def _generate_iterative_packed(
    self, task: GenerationTask, gen_config: OmniVoiceGenerationConfig
) -> List[torch.Tensor]:
    """Packed-sequence rewrite of OmniVoice._generate_iterative.

    Documents are packed as [cond_0, uncond_0, cond_1, uncond_1, ...] into a
    single batch row; the scoring/unmasking math is identical to the original.
    """
    B = task.batch_size
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
    u_lens = list(task.target_lens)
    doc_lens = []
    for c, u in zip(c_lens, u_lens):
        doc_lens.extend([c, u])

    use_graph = getattr(self, "_fi_enable_cuda_graph", False)
    buckets = getattr(self, "_fi_graph_buckets", None)  # durations in seconds

    # Choose the packed layout. Bucketed-graph mode places each item in fixed
    # slots [C_budget | U_budget] so one graph per (batch, duration bucket)
    # serves any sample that fits; otherwise pack tightly.
    bucket_U = None
    if use_graph and buckets is not None:
        frame_rate = self.audio_tokenizer.config.frame_rate
        t_max = max(u_lens)
        overhead_max = max(c - u for c, u in zip(c_lens, u_lens))
        bucket_U = next(
            (int(d * frame_rate) for d in sorted(buckets) if d * frame_rate >= t_max),
            None,
        )
        if bucket_U is None or overhead_max > self._fi_overhead_budget:
            bucket_U = None
            use_graph = False  # too long for the buckets: eager fallback

    if bucket_U is not None:
        U_b = bucket_U
        C_b = U_b + self._fi_overhead_budget
        offsets = []
        for i in range(B):
            offsets.extend([i * (C_b + U_b), i * (C_b + U_b) + C_b])
        total_len = B * (C_b + U_b)
    else:
        offsets = [0]
        for l in doc_lens[:-1]:
            offsets.append(offsets[-1] + l)
        total_len = sum(doc_lens)

    C = self.config.num_audio_codebook
    packed_ids = torch.full(
        (1, C, total_len),
        self.config.audio_mask_id,
        dtype=torch.long,
        device=self.device,
    )
    packed_audio_mask = torch.zeros(
        (1, total_len), dtype=torch.bool, device=self.device
    )
    position_ids = torch.zeros((1, total_len), dtype=torch.long, device=self.device)

    for i, inp in enumerate(inputs_list):
        c_off, u_off = offsets[2 * i], offsets[2 * i + 1]
        c_len, u_len = c_lens[i], u_lens[i]
        packed_ids[0, :, c_off : c_off + c_len] = inp["input_ids"][0]
        packed_audio_mask[0, c_off : c_off + c_len] = inp["audio_mask"][0]
        position_ids[0, c_off : c_off + c_len] = torch.arange(c_len, device=self.device)
        # uncond doc = target region only
        packed_ids[0, :, u_off : u_off + u_len] = inp["input_ids"][0, :, -u_len:]
        packed_audio_mask[0, u_off : u_off + u_len] = inp["audio_mask"][0, -u_len:]
        position_ids[0, u_off : u_off + u_len] = torch.arange(u_len, device=self.device)

    # num_step + 1 mirrors our _generate_iterative's schedule (a local
    # divergence from upstream k2-fsa): packed decoding must unmask on exactly
    # the same schedule as the eager path or outputs differ between the two.
    timesteps = _get_time_steps(
        t_start=0.0,
        t_end=1.0,
        num_step=gen_config.num_step + 1,
        t_shift=gen_config.t_shift,
    ).tolist()
    schedules = []
    for t_len in task.target_lens:
        total_mask = t_len * C
        rem = total_mask
        sched = []
        for step in range(gen_config.num_step):
            num = (
                rem
                if step == gen_config.num_step - 1
                else min(
                    math.ceil(total_mask * (timesteps[step + 1] - timesteps[step])), rem
                )
            )
            sched.append(int(num))
            rem -= int(num)
        schedules.append(sched)

    layer_ids = torch.arange(C, device=self.device).view(1, -1, 1)

    # gather indices of the logits-consuming positions, laid out as
    # [all cond-target blocks | all uncond blocks] so the guidance/scoring
    # math can run over every item in one batched pass. flat_spans[i] gives
    # the item's (start, len) within each half; in bucket mode items sit at a
    # fixed stride U_b with junk rows (pointing at position 0) in between.
    cond_ranges, uncond_ranges = [], []
    flat_spans = []
    for i in range(B):
        c_off, u_off = offsets[2 * i], offsets[2 * i + 1]
        c_len, t_len = c_lens[i], task.target_lens[i]
        if bucket_U is not None:
            flat_spans.append((U_b * i, t_len))
            cond_rows = torch.zeros(U_b, dtype=torch.long, device=self.device)
            cond_rows[:t_len] = torch.arange(
                c_off + c_len - t_len, c_off + c_len, device=self.device
            )
            uncond_rows = torch.zeros(U_b, dtype=torch.long, device=self.device)
            uncond_rows[:t_len] = torch.arange(u_off, u_off + t_len, device=self.device)
            cond_ranges.append(cond_rows)
            uncond_ranges.append(uncond_rows)
        else:
            prev = 0 if i == 0 else flat_spans[-1][0] + flat_spans[-1][1]
            flat_spans.append((prev, t_len))
            cond_ranges.append(
                torch.arange(c_off + c_len - t_len, c_off + c_len, device=self.device)
            )
            uncond_ranges.append(torch.arange(u_off, u_off + t_len, device=self.device))
    T_flat = (U_b * B) if bucket_U is not None else sum(task.target_lens)
    tgt_index = torch.cat(cond_ranges + uncond_ranges)

    # flat per-position token state aligned with the cond half of the gathered
    # layout. Junk positions (bucket-mode slot padding) are initialized to -1
    # so the global "already unmasked" fill gives them -inf scores and topk
    # never selects them.
    tokens_flat = torch.full((C, T_flat), -1, dtype=torch.long, device=self.device)
    for st, t_len in flat_spans:
        tokens_flat[:, st : st + t_len] = self.config.audio_mask_id

    if use_graph and bucket_U is not None:
        graph_entry = _get_or_capture_bucket_graph(self, B, U_b, C_b)
        # refresh the per-generation static contents (shape-invariant, data-variant)
        graph_entry["audio_mask"].copy_(packed_audio_mask)
        graph_entry["position_ids"].copy_(position_ids)
        graph_entry["pos_ids_i32"].copy_(position_ids[0].to(torch.int32))
        graph_entry["tgt_index"].copy_(tgt_index)
        for d_idx, m in enumerate(graph_entry["doc_masks"]):
            length = c_lens[d_idx // 2] if d_idx % 2 == 0 else u_lens[d_idx // 2]
            m[..., :length] = True
            m[..., length:] = False
    elif use_graph:
        graph_entry = _get_or_capture_graph(self, tuple(doc_lens), tgt_index)
        graph_entry["audio_mask"].copy_(packed_audio_mask)
        graph_entry["position_ids"].copy_(position_ids)
    else:
        self._fi_runner.plan(doc_lens, torch.float16)
        _CTX["wrapper"] = self._fi_runner.wrapper
        _CTX["pos_ids"] = position_ids[0].to(torch.int32)
        _CTX["doc_slots"] = None

    # optional llm timing hook (set by the benchmark; graph replays bypass
    # model.forward, so wrapping forward would miss them)
    stats = getattr(self, "_fi_llm_stats", None)

    for step in range(gen_config.num_step):
        if stats is not None:
            torch.cuda.synchronize()
            t0 = time.perf_counter()
        if use_graph:
            graph_entry["input_ids"].copy_(packed_ids)
            graph_entry["graph"].replay()
            batch_logits = graph_entry["logits"].to(torch.float32)
        else:
            batch_logits = _forward_logits(
                self, packed_ids, packed_audio_mask, position_ids, tgt_index
            ).to(torch.float32)
        if stats is not None:
            torch.cuda.synchronize()
            stats["seconds"] += time.perf_counter() - t0
            stats["calls"] += 1

        # batched scoring over every item at once: the guidance/log_softmax/
        # argmax/gumbel chain (the GPU-heavy part) runs on the whole
        # [cond | uncond] halves; only topk + scatter stay per item.
        c_logits_all = batch_logits[:, :, :T_flat, :]
        u_logits_all = batch_logits[:, :, T_flat:, :]
        pred_all, scores_all = self._predict_tokens_with_scoring(
            c_logits_all, u_logits_all, gen_config
        )
        scores_all = scores_all - (layer_ids * gen_config.layer_penalty_factor)
        if gen_config.position_temperature > 0.0:
            scores_all = _gumbel_sample(scores_all, gen_config.position_temperature, gen_config._generator)
        # -inf for already-unmasked positions AND bucket-slot junk (-1)
        scores_all.masked_fill_(
            (tokens_flat != self.config.audio_mask_id).unsqueeze(0), -float("inf")
        )
        pred_all, scores_all = pred_all[0], scores_all[0]  # (C, T_flat)

        for i in range(B):
            k = schedules[i][step]
            if k <= 0:
                continue
            c_off, u_off = offsets[2 * i], offsets[2 * i + 1]
            c_len, t_len = c_lens[i], task.target_lens[i]
            st, _ = flat_spans[i]

            _, topk_idx = torch.topk(scores_all[:, st : st + t_len].reshape(-1), k)
            flat_tokens = tokens_flat[:, st : st + t_len].reshape(-1)
            flat_tokens[topk_idx] = pred_all[:, st : st + t_len].reshape(-1)[topk_idx]
            new_tokens = flat_tokens.view(C, t_len)
            tokens_flat[:, st : st + t_len] = new_tokens

            packed_ids[0, :, c_off + c_len - t_len : c_off + c_len] = new_tokens
            packed_ids[0, :, u_off : u_off + t_len] = new_tokens

    return [tokens_flat[:, st : st + t_len] for (st, t_len) in flat_spans]


def _forward_logits(model, input_ids, audio_mask, position_ids, tgt_index):
    """LLM forward + audio head over target positions only.

    The scoring step consumes logits at the cond-target and uncond ranges
    (2*sum(t_len) of the packed positions); running the 1024->8200 audio_heads
    GEMM and the fp32 upcast on the full packed length is wasted work.
    Returns logits of shape (1, C, 2*sum(t_len), V) laid out as
    [all cond-target blocks | all uncond blocks] — matching tgt_index
    (torch.cat(cond_ranges + uncond_ranges)) and the caller's split at T_flat.
    """
    inputs_embeds = model._prepare_embed_inputs(input_ids, audio_mask)
    hidden = model.llm(
        inputs_embeds=inputs_embeds,
        attention_mask={"full_attention": None},
        return_dict=True,
        position_ids=position_ids,
    )[0]
    tgt_hidden = hidden[0, tgt_index]  # (2T, hidden)
    logits_flat = model.audio_heads(tgt_hidden)
    n = tgt_hidden.shape[0]
    return logits_flat.view(
        1, n, model.config.num_audio_codebook, model.config.audio_vocab_size
    ).permute(0, 2, 1, 3)


def _get_or_capture_graph(model, doc_lens_key, tgt_index):
    cache = model._fi_graph_cache
    entry = cache.get(doc_lens_key)
    if entry is not None:
        return entry

    device = model.device
    total_len = sum(doc_lens_key)
    C = model.config.num_audio_codebook
    llm_cfg = model.config.llm_config
    runner = PackedAttnRunner(
        llm_cfg.num_attention_heads,
        llm_cfg.num_key_value_heads,
        llm_cfg.head_dim,
        device,
        workspace_size=64 * 1024 * 1024,
    )
    runner.plan(list(doc_lens_key), torch.float16)
    _CTX["wrapper"] = runner.wrapper

    # positions are fully determined by doc_lens (the cache key), so both the
    # long buffer (model-level rotary) and the int32 copy (fused rope) can be
    # baked with their final values
    positions = torch.cat([torch.arange(l, device=device) for l in doc_lens_key])
    static = {
        "input_ids": torch.full(
            (1, C, total_len),
            model.config.audio_mask_id,
            dtype=torch.long,
            device=device,
        ),
        "audio_mask": torch.zeros((1, total_len), dtype=torch.bool, device=device),
        "position_ids": positions.unsqueeze(0).contiguous(),
    }
    pos_ids_i32 = positions.to(torch.int32)
    _CTX["pos_ids"] = pos_ids_i32
    _CTX["doc_slots"] = None

    side_stream = torch.cuda.Stream()
    side_stream.wait_stream(torch.cuda.current_stream())
    with torch.cuda.stream(side_stream):
        for _ in range(2):
            _forward_logits(
                model,
                static["input_ids"],
                static["audio_mask"],
                static["position_ids"],
                tgt_index,
            )
    torch.cuda.current_stream().wait_stream(side_stream)
    torch.cuda.synchronize()

    graph = torch.cuda.CUDAGraph()
    with torch.cuda.graph(graph):
        # tgt_index depends only on doc_lens (the cache key), so it is safe
        # to bake into the graph
        logits = _forward_logits(
            model,
            static["input_ids"],
            static["audio_mask"],
            static["position_ids"],
            tgt_index,
        )

    # tgt_index is baked into the captured gather by pointer — the entry must
    # keep it alive or the allocator will reuse its memory for later samples
    entry = {
        "graph": graph,
        "logits": logits,
        "runner": runner,
        "tgt_index": tgt_index,
        "pos_ids_i32": pos_ids_i32,
        **static,
    }
    cache[doc_lens_key] = entry
    return entry


def _get_or_capture_bucket_graph(model, B, U_b, C_b):
    """One graph per (batch, duration-bucket): items sit in fixed
    [C_budget | U_budget] slots; attention runs as SDPA over a runtime-updated
    block-diagonal mask, so any sample that fits the slots replays exactly."""
    key = ("bucket", B, U_b)
    cache = model._fi_graph_cache
    entry = cache.get(key)
    if entry is not None:
        return entry

    device = model.device
    total_len = B * (C_b + U_b)
    C = model.config.num_audio_codebook

    static = {
        "input_ids": torch.full(
            (1, C, total_len),
            model.config.audio_mask_id,
            dtype=torch.long,
            device=device,
        ),
        "audio_mask": torch.zeros((1, total_len), dtype=torch.bool, device=device),
        "position_ids": torch.zeros((1, total_len), dtype=torch.long, device=device),
        "pos_ids_i32": torch.zeros(total_len, dtype=torch.int32, device=device),
        "tgt_index": torch.zeros(2 * B * U_b, dtype=torch.long, device=device),
    }
    # per-document key-padding masks (contents updated per generation);
    # init all-True so warmup/capture has no fully-masked softmax rows
    doc_masks, doc_slots = [], []
    for i in range(B):
        for slot_start, slot_len in (
            (i * (C_b + U_b), C_b),
            (i * (C_b + U_b) + C_b, U_b),
        ):
            m = torch.ones(1, 1, 1, slot_len, dtype=torch.bool, device=device)
            doc_masks.append(m)
            doc_slots.append((slot_start, slot_len, m))

    _CTX["wrapper"] = None
    _CTX["pos_ids"] = static["pos_ids_i32"]
    _CTX["doc_slots"] = doc_slots

    side_stream = torch.cuda.Stream()
    side_stream.wait_stream(torch.cuda.current_stream())
    with torch.cuda.stream(side_stream):
        for _ in range(2):
            _forward_logits(
                model,
                static["input_ids"],
                static["audio_mask"],
                static["position_ids"],
                static["tgt_index"],
            )
    torch.cuda.current_stream().wait_stream(side_stream)
    torch.cuda.synchronize()

    graph = torch.cuda.CUDAGraph()
    with torch.cuda.graph(graph):
        logits = _forward_logits(
            model,
            static["input_ids"],
            static["audio_mask"],
            static["position_ids"],
            static["tgt_index"],
        )

    entry = {
        "graph": graph,
        "logits": logits,
        "doc_masks": doc_masks,
        "doc_slots": doc_slots,
        **static,
    }
    cache[key] = entry
    return entry


def apply_flashinfer(
    model,
    enable_cuda_graph: bool = False,
    fuse_rmsnorm: bool = True,
    fuse_attention: bool = True,
    cuda_graph_buckets=None,
    overhead_budget: int = 512,
):
    """Patch an OmniVoice instance to use flashinfer packed attention."""
    model.llm.set_attn_implementation("omnivoice_fi")
    if fuse_rmsnorm:
        _patch_rmsnorm(model.llm)
    if fuse_attention:
        _patch_attention_forward(model.llm)
        _patch_mlp(model.llm)
    # Bidirectional iterative decoding recomputes everything each step; the
    # DynamicCache the baseline allocates+fills per forward is pure overhead.
    model.llm.config.use_cache = False

    llm_cfg = model.config.llm_config
    model._fi_runner = PackedAttnRunner(
        llm_cfg.num_attention_heads,
        llm_cfg.num_key_value_heads,
        llm_cfg.head_dim,
        model.device,
    )
    model._fi_graph_cache = {}
    model._fi_enable_cuda_graph = enable_cuda_graph or cuda_graph_buckets is not None
    model._fi_graph_buckets = cuda_graph_buckets
    model._fi_overhead_budget = overhead_budget
    model._generate_iterative = MethodType(_generate_iterative_packed, model)
    return model
