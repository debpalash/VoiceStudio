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

"""Denoise audio with Sidon and pack results into WebDataset shards.

Supports two input modes:

1. WebDataset manifest (data.lst):
    python denoise_audio.py \
        --input_manifest data.lst \
        --tar_output_pattern output/audios/shard-%06d.tar \
        --jsonl_output_pattern output/txts/shard-%06d.jsonl \
        --feature_extractor_path sidon-v0.1/feature_extractor_cuda.pt \
        --decoder_path sidon-v0.1/decoder_cuda.pt

2. Raw JSONL (each line: {"id": "...", "audio_path": "...", ...}):
    python denoise_audio.py \
        --input_jsonl data.jsonl \
        --tar_output_pattern output/audios/shard-%06d.tar \
        --jsonl_output_pattern output/txts/shard-%06d.jsonl \
        --feature_extractor_path sidon-v0.1/feature_extractor_cuda.pt \
        --decoder_path sidon-v0.1/decoder_cuda.pt

Output structure:
    output_dir/
    ├── audios/           # WebDataset tar shards (.flac audio + .json metadata)
    │   ├── shard_000000.tar
    │   └── ...
    ├── txts/             # Per-shard JSONL metadata
    │   ├── shard_000000.jsonl
    │   └── ...
    ├── data.lst          # Manifest: <tar_path> <jsonl_path> <sample_count> <total_duration>
    └── errors.jsonl      # Failed samples with error details
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import pickle
import struct
import subprocess
import sys
import threading
from concurrent.futures import FIRST_COMPLETED, Future, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import numpy as np
import torch
import torchaudio
from omnivoice.utils.audio import save_audio
import webdataset as wds
from torch.utils.data import DataLoader
from tqdm.auto import tqdm

from omnivoice.data.batching import StreamLengthGroupDataset
from omnivoice.data.dataset import JsonlDatasetReader, WebDatasetReader
from omnivoice.utils.common import str2bool

SIDON_INPUT_SAMPLE_RATE = 16_000
SIDON_OUTPUT_SAMPLE_RATE = 48_000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)

    # ── Input (mutually exclusive) ──
    parser.add_argument(
        "--input_manifest",
        default=None,
        help="WebDataset manifest (data.lst). Each line: "
        "<tar_path> <jsonl_path> <num_items> <duration>",
    )
    parser.add_argument(
        "--input_jsonl",
        default=None,
        help="Raw JSONL file. Each line: " '{"id": "...", "audio_path": "...", ...}',
    )

    # ── Output ──
    parser.add_argument(
        "--tar_output_pattern",
        default=None,
        help="Tar shard pattern, e.g. output/audios/shard_%%06d.tar",
    )
    parser.add_argument(
        "--jsonl_output_pattern",
        default=None,
        help="JSONL shard pattern, e.g. output/txts/shard_%%06d.jsonl",
    )
    parser.add_argument(
        "--samples_per_shard",
        type=int,
        default=1_000,
        help="Maximum records per output shard",
    )

    # ── Model ──
    parser.add_argument(
        "--feature_extractor_path",
        default=None,
        help="Path to feature_extractor_cuda.pt",
    )
    parser.add_argument(
        "--decoder_path",
        default=None,
        help="Path to decoder_cuda.pt",
    )
    parser.add_argument(
        "--target_sample_rate",
        type=int,
        default=24_000,
        help="Sample rate of the denoised output audio",
    )

    # ── Filtering ──
    parser.add_argument(
        "--min_length",
        type=float,
        default=0.0,
        help="Minimum audio duration in seconds",
    )
    parser.add_argument(
        "--max_length",
        type=float,
        default=80.0,
        help="Maximum audio duration in seconds",
    )

    # ── Batching ──
    parser.add_argument(
        "--batch_duration",
        type=float,
        default=200.0,
        help="Target batch duration in seconds for dynamic batching",
    )
    parser.add_argument(
        "--max_sample",
        type=int,
        default=32,
        help="Maximum samples per batch for dynamic batching",
    )

    # ── Distributed ──
    parser.add_argument(
        "--num_machines",
        type=int,
        default=1,
        help="Total number of machines for distributed runs",
    )
    parser.add_argument(
        "--machine_index",
        type=int,
        default=0,
        help="Zero-based machine index when distributing across multiple "
        "machines (e.g. 0, 1, ... num_machines-1)",
    )

    # ── Parallelism ──
    parser.add_argument(
        "--nj_per_gpu",
        type=int,
        default=1,
        help="Worker processes per GPU (default 1)",
    )
    parser.add_argument(
        "--loader_workers",
        type=int,
        default=16,
        help="PyTorch DataLoader worker threads",
    )

    # ── Data order (JSONL mode) ──
    parser.add_argument(
        "--shuffle",
        type=str2bool,
        default=True,
        help="Shuffle JSONL entries",
    )
    parser.add_argument(
        "--shuffle_seed",
        type=int,
        default=42,
        help="Seed for JSONL shuffle",
    )

    # ── Error handling ──
    parser.add_argument(
        "--skip_errors",
        action="store_true",
        help="Skip items that fail to denoise instead of aborting",
    )
    parser.add_argument(
        "--_subprocess_worker",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def count_lines(path: str) -> int:
    """Count newlines efficiently by reading binary chunks."""
    count = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            count += chunk.count(b"\n")
    return count


PaddingStrategy = Union[bool, str]
ReturnType = Union[torch.Tensor, np.ndarray]


def extract_seamless_m4t_features(
    raw_speech: Union[torch.Tensor, List[float], List[torch.Tensor], List[List[float]]],
    sampling_rate: int = 16000,
    num_mel_bins: int = 80,
    frame_length: int = 25,
    frame_shift: int = 10,
    preemphasis_coefficient: float = 0.97,
    dither: float = 0.0,
    window_type: str = "povey",
    do_normalize_per_mel_bins: bool = True,
    stride: int = 2,
    padding: PaddingStrategy = "longest",
    max_length: Optional[int] = None,
    pad_to_multiple_of: Optional[int] = 2,
    return_tensors: Optional[str] = "pt",
    return_attention_mask: bool = True,
    padding_value: float = 0.0,
    device: torch.device = torch.device("cpu"),
) -> Dict[str, ReturnType]:
    """Extract SeamlessM4T features using Torch-only operators."""
    if not isinstance(raw_speech, list):
        raw_speech = [raw_speech]

    processed_speech = [
        torch.as_tensor(sample, dtype=torch.float32, device=device)
        for sample in raw_speech
    ]

    features: List[torch.Tensor] = []
    for waveform in processed_speech:
        if waveform.ndim > 1:
            waveform = waveform[0]
        waveform_tensor = waveform.unsqueeze(0)
        feature = torchaudio.compliance.kaldi.fbank(
            waveform=waveform_tensor,
            sample_frequency=sampling_rate,
            num_mel_bins=num_mel_bins,
            frame_length=frame_length,
            frame_shift=frame_shift,
            dither=dither,
            preemphasis_coefficient=preemphasis_coefficient,
            remove_dc_offset=True,
            window_type=window_type,
            use_energy=False,
            energy_floor=1.192092955078125e-07,
        )
        features.append(feature.squeeze(0))

    if do_normalize_per_mel_bins:
        normalised: List[torch.Tensor] = []
        for feature in features:
            mean = feature.mean(0, keepdim=True)
            var = feature.var(0, keepdim=True)
            normalised.append((feature - mean) / torch.sqrt(var + 1e-5))
        features = normalised

    def _pad_batch(
        features: List[torch.Tensor],
        padding_strategy: PaddingStrategy = "longest",
        max_length: Optional[int] = None,
        pad_to_multiple_of: Optional[int] = None,
        padding_value: float = 0.0,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        if padding_strategy == "longest":
            target_length = max(f.shape[0] for f in features)
        elif max_length is not None:
            target_length = max_length
        else:
            raise ValueError(
                "max_length must be provided when padding_strategy is not 'longest'"
            )

        if pad_to_multiple_of is not None:
            target_length = (
                (target_length + pad_to_multiple_of - 1)
                // pad_to_multiple_of
                * pad_to_multiple_of
            )

        batch_size = len(features)
        feature_dim = features[0].shape[1]
        device = features[0].device

        padded_features = torch.full(
            (batch_size, target_length, feature_dim),
            padding_value,
            dtype=torch.float32,
            device=device,
        )
        attention_mask = torch.zeros(
            (batch_size, target_length),
            dtype=torch.int64,
            device=device,
        )

        for index, feature_tensor in enumerate(features):
            seq_len = feature_tensor.shape[0]
            padded_features[index, :seq_len] = feature_tensor
            attention_mask[index, :seq_len] = 1

        return padded_features, attention_mask

    input_features, attention_mask = _pad_batch(
        features,
        padding_strategy=padding,
        max_length=max_length,
        pad_to_multiple_of=pad_to_multiple_of,
        padding_value=padding_value,
    )

    batch_size, num_frames, num_channels = input_features.shape
    new_num_frames = (num_frames // stride) * stride
    input_features = input_features[:, :new_num_frames, :]
    if return_attention_mask:
        attention_mask = attention_mask[:, :new_num_frames]

    input_features = input_features.reshape(
        batch_size, new_num_frames // stride, num_channels * stride
    )

    output: Dict[str, ReturnType] = {"input_features": input_features}
    if return_attention_mask:
        output["attention_mask"] = attention_mask[:, 1::stride]

    if return_tensors == "np":
        for key, value in output.items():
            output[key] = value.cpu().numpy()  # type: ignore[assignment]

    return output


def serialise_flac(key: str, waveform: torch.Tensor, sample_rate: int) -> dict:
    buffer = io.BytesIO()
    audio = waveform.to(dtype=torch.float32).cpu()
    if audio.ndim == 1:
        audio = audio.unsqueeze(0)
    save_audio(buffer, audio, sample_rate, format="flac", bits_per_sample=16)
    return {"__key__": key, "flac": buffer.getvalue()}


def _normalise_value(value: Any) -> Any:
    """Convert tensors and NumPy scalars to serialisable Python objects."""
    if isinstance(value, torch.Tensor):
        if value.ndim == 0:
            return value.item()
        return value.cpu().tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    return value


def _encode_metadata(metadata: dict[str, Any]) -> bytes:
    cleaned: dict[str, Any] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        cleaned[key] = _normalise_value(value)
    return json.dumps(cleaned, ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# Denoising model
# ---------------------------------------------------------------------------


class SpeechDenoisingProcessor:
    """Run the TorchScripted feature extractor and decoder."""

    def __init__(
        self,
        feature_extractor_path: str,
        decoder_path: str,
        device: str,
    ) -> None:
        self.device = torch.device(device)
        self.feature_extractor = torch.jit.load(
            feature_extractor_path, map_location=self.device
        )
        self.decoder = torch.jit.load(decoder_path, map_location=self.device)
        self.feature_extractor.eval()
        self.decoder.eval()

    @torch.inference_mode()
    def process(self, waveform: torch.Tensor, sample_rate: int) -> torch.Tensor:
        return self.process_batch([waveform], [sample_rate])[0]

    @torch.inference_mode()
    def process_batch(
        self,
        waveforms: Sequence[torch.Tensor] | torch.Tensor,
        sample_rates: Optional[Sequence[int]] = None,
        expected_lengths: Optional[Sequence[int]] = None,
    ) -> List[torch.Tensor]:
        if expected_lengths is None:
            expected_lengths: list[int] = []
            for waveform, sample_rate in zip(waveforms, sample_rates):
                duration_seconds = waveform.shape[-1] / float(sample_rate)
                expected_lengths.append(
                    int(round(duration_seconds * SIDON_OUTPUT_SAMPLE_RATE))
                )
        waveforms = torch.nn.functional.pad(waveforms, (0, 24000))

        features = extract_seamless_m4t_features(
            [x for x in waveforms],
            return_tensors="pt",
            padding_value=1.0,
            device=self.device,
        )
        feature_tensor = self.feature_extractor(
            features["input_features"].to(self.device)
        )["last_hidden_state"]
        restored_waveforms = self.decoder(feature_tensor.transpose(1, 2)).cpu()

        results: List[torch.Tensor] = []
        for sample_idx, sample in enumerate(restored_waveforms):
            restored_waveform = sample.view(-1)
            target_length = expected_lengths[sample_idx]
            current_length = restored_waveform.shape[-1]
            if target_length > 0 and current_length != target_length:
                diff = target_length - current_length
                if diff > 0:
                    restored_waveform = torch.nn.functional.pad(
                        restored_waveform, (0, diff)
                    )
                elif diff < 0:
                    restored_waveform = restored_waveform[:target_length]
            results.append(restored_waveform.contiguous())

        return results


# ---------------------------------------------------------------------------
# Batch collation
# ---------------------------------------------------------------------------


class CollateFunction:
    """Collate a list of samples into a padded batch."""

    def __init__(
        self,
        sample_rate: int,
        skip_errors: bool,
    ) -> None:
        self.sample_rate = sample_rate
        self.skip_errors = skip_errors

    def __call__(self, samples: Sequence[dict[str, Any]]) -> CollatedBatch:
        keys: list[str] = []
        waveforms: list[torch.Tensor] = []
        durations: list[float] = []
        metadata: list[dict[str, Any]] = []

        for sample in samples:
            keys.append(sample["label"]["id"])
            waveforms.append(sample["audio"].squeeze(0))
            durations.append(sample["audio"].size(-1) / self.sample_rate)
            metadata.append(sample["label"])
        waveforms = torch.nn.utils.rnn.pad_sequence(waveforms, batch_first=True)

        return CollatedBatch(
            keys=keys, waveforms=waveforms, durations=durations, metadata=metadata
        )


@dataclass
class CollatedBatch:
    """Batch payload returned by the DataLoader collate function."""

    keys: list[str]
    waveforms: list[torch.Tensor]
    durations: list[float]
    metadata: list[dict[str, Any]]

    @property
    def size(self) -> int:
        return len(self.keys)


# ---------------------------------------------------------------------------
# Subprocess-based GPU worker pool
# ---------------------------------------------------------------------------
#
# Problem: PyTorch ≥2.8 caches CUDA device state at import time.  Neither
# forkserver nor spawn lets us change CUDA_VISIBLE_DEVICES *before* the CUDA
# runtime captures the device list.  The only reliable approach is to launch
# each worker as a **subprocess** with CUDA_VISIBLE_DEVICES set in the
# subprocess environment, guaranteeing it takes effect before `import torch`.
#
# Protocol (parent ↔ child, length-prefixed pickle over stdin/stdout):
#   Parent → child:  4-byte LE uint32 length  +  pickle(CollatedBatch)
#   Child  → parent: 4-byte LE uint32 length  +  pickle(result dict)
#   Shutdown signal: 4 zero bytes (length == 0)


def _subprocess_recv():
    """Read a length-prefixed pickled object from stdin.  Returns None on shutdown."""
    raw = sys.stdin.buffer.read(4)
    if len(raw) < 4:
        return None
    (length,) = struct.unpack("<I", raw)
    if length == 0:
        return None
    data = sys.stdin.buffer.read(length)
    return pickle.loads(data)


def _subprocess_send(obj):
    """Send a pickled object with a 4-byte length prefix to stdout."""
    data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    sys.stdout.buffer.write(struct.pack("<I", len(data)))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def subprocess_worker_main():
    """Entry point for a GPU worker subprocess.

    Expected environment: CUDA_VISIBLE_DEVICES already set by the parent.
    Receives initargs via stdin, then processes batches in a loop.
    """
    formatter = "%(asctime)s %(levelname)s [%(filename)s:%(lineno)d] [Worker PID %(process)d] %(message)s"
    logging.basicConfig(format=formatter, level=logging.INFO, force=True)

    initargs = _subprocess_recv()
    feature_extractor_path, decoder_path = initargs

    device = "cpu"
    if torch.cuda.is_available():
        torch.cuda.set_device(0)
        device = "cuda:0"
    else:
        logging.warning("CUDA not available in worker subprocess.")

    logging.info(
        f"Worker PID={os.getpid()}, "
        f"CUDA_VISIBLE_DEVICES={os.environ.get('CUDA_VISIBLE_DEVICES')}, device={device}"
    )

    processor = SpeechDenoisingProcessor(
        feature_extractor_path=feature_extractor_path,
        decoder_path=decoder_path,
        device=device,
    )

    # Process batches until shutdown signal
    while True:
        msg = _subprocess_recv()
        if msg is None:
            break
        req_id = msg["_req_id"]
        batch = msg["_batch"]
        try:
            cleaned_waveforms = processor.process_batch(
                batch.waveforms,
                expected_lengths=[
                    round(d * SIDON_OUTPUT_SAMPLE_RATE) for d in batch.durations
                ],
            )
            cleaned_cpu = [w.cpu() for w in cleaned_waveforms]
            result = {
                "_req_id": req_id,
                "status": "success",
                "keys": batch.keys,
                "results": cleaned_cpu,
                "metadata": batch.metadata,
                "size": batch.size,
            }
        except Exception as e:
            result = {
                "_req_id": req_id,
                "status": "error",
                "keys": batch.keys,
                "error": str(e),
                "size": batch.size,
            }
        _subprocess_send(result)


class _GPUWorker:
    """Handle to a single GPU worker subprocess."""

    def __init__(self, physical_gpu_id, feature_extractor_path, decoder_path):
        env = os.environ.copy()
        if physical_gpu_id is not None:
            env["CUDA_VISIBLE_DEVICES"] = str(physical_gpu_id)
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "omnivoice.scripts.denoise_audio",
                "--_subprocess_worker",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=env,
        )
        # Send init args
        init_data = pickle.dumps(
            (feature_extractor_path, decoder_path), protocol=pickle.HIGHEST_PROTOCOL
        )
        self.proc.stdin.write(struct.pack("<I", len(init_data)))
        self.proc.stdin.write(init_data)
        self.proc.stdin.flush()
        self._lock = threading.Lock()

    def submit(self, batch_with_id):
        """Send a batch dict (containing _req_id + _batch) for processing."""
        with self._lock:
            data = pickle.dumps(batch_with_id, protocol=pickle.HIGHEST_PROTOCOL)
            self.proc.stdin.write(struct.pack("<I", len(data)))
            self.proc.stdin.write(data)
            self.proc.stdin.flush()

    def read_result(self):
        """Blocking read for one result."""
        raw = self.proc.stdout.read(4)
        if len(raw) < 4:
            return None
        (length,) = struct.unpack("<I", raw)
        if length == 0:
            return None
        data = self.proc.stdout.read(length)
        return pickle.loads(data)

    def shutdown(self):
        """Send shutdown signal and wait for process."""
        try:
            with self._lock:
                self.proc.stdin.write(struct.pack("<I", 0))
                self.proc.stdin.flush()
        except Exception:
            pass
        self.proc.wait(timeout=30)


class GPUWorkerPool:
    """Pool of GPU worker subprocesses with round-robin task submission."""

    def __init__(self, pool_specs, feature_extractor_path, decoder_path):
        """
        Args:
            pool_specs: list of (physical_gpu_id, num_workers) tuples.
            feature_extractor_path: path to JIT feature extractor.
            decoder_path: path to JIT decoder.
        """
        self.workers: list[_GPUWorker] = []
        for physical_gpu_id, num_workers in pool_specs:
            for _ in range(num_workers):
                self.workers.append(
                    _GPUWorker(physical_gpu_id, feature_extractor_path, decoder_path)
                )
        self._rr = 0
        self._futures: dict[int, Future] = {}
        self._futures_lock = threading.Lock()
        self._next_id = 0
        # Start reader threads for each worker
        self._reader_threads = []
        for worker in self.workers:
            t = threading.Thread(target=self._reader_loop, args=(worker,), daemon=True)
            t.start()
            self._reader_threads.append(t)

    def _reader_loop(self, worker):
        while True:
            result = worker.read_result()
            if result is None:
                break
            req_id = result.pop("_req_id", None)
            with self._futures_lock:
                fut = self._futures.pop(req_id, None)
            if fut is not None:
                fut.set_result(result)

    def submit(self, batch) -> Future:
        worker = self.workers[self._rr % len(self.workers)]
        self._rr += 1
        with self._futures_lock:
            req_id = self._next_id
            self._next_id += 1
            fut = Future()
            self._futures[req_id] = fut
        batch_dict = {
            "_req_id": req_id,
            "_batch": batch,
        }
        worker.submit(batch_dict)
        return fut

    def shutdown(self):
        for worker in self.workers:
            worker.shutdown()
        for t in self._reader_threads:
            t.join(timeout=5)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    formatter = "%(asctime)s %(levelname)s [%(filename)s:%(lineno)d] %(message)s"
    logging.basicConfig(format=formatter, level=logging.INFO, force=True)
    parser = build_parser()
    args = parser.parse_args()

    # ── Subprocess worker mode ──
    if args._subprocess_worker:
        subprocess_worker_main()
        return

    # Validate input arguments
    assert args.tar_output_pattern is not None, "--tar_output_pattern is required."
    assert args.jsonl_output_pattern is not None, "--jsonl_output_pattern is required."
    assert bool(args.input_manifest) != bool(
        args.input_jsonl
    ), "Exactly one of --input_manifest or --input_jsonl must be provided."

    if args.num_machines > 1:
        assert (
            0 <= args.machine_index < args.num_machines
        ), f"machine_index {args.machine_index} must be in [0, {args.num_machines})"

    # ── Build base dataset and count total samples ──
    if args.input_jsonl:
        logging.info(f"Input mode: raw JSONL ({args.input_jsonl})")
        total_samples = count_lines(args.input_jsonl)
        base_dataset = JsonlDatasetReader(
            args.input_jsonl,
            sample_rate=SIDON_INPUT_SAMPLE_RATE,
            shuffle=args.shuffle,
            shuffle_seed=args.shuffle_seed,
        )
        loader_workers = args.loader_workers
    else:
        logging.info(f"Input mode: WebDataset manifest ({args.input_manifest})")
        manifest_num_lines = count_lines(args.input_manifest)
        loader_workers = min(args.loader_workers, manifest_num_lines)
        total_samples = 0
        manifests = []
        with open(args.input_manifest, "r", encoding="utf-8") as f:
            for line_id, line in tqdm(
                enumerate(f),
                total=manifest_num_lines,
                desc="Calculating dataset length",
            ):
                items = line.strip().split(" ")
                tar_path, jsonl_path, num_items, duration = (
                    items[0],
                    items[1],
                    int(items[2]),
                    float(items[3]),
                )
                assert os.path.exists(tar_path), f"File {tar_path} does not exist."
                assert os.path.exists(jsonl_path), f"File {jsonl_path} does not exist."
                assert jsonl_path.endswith(
                    ".jsonl"
                ), f"File {jsonl_path} is not a .jsonl file."
                if (
                    args.num_machines > 1
                    and line_id % args.num_machines != args.machine_index
                ):
                    continue
                total_samples += num_items
                manifests.append((tar_path, jsonl_path, num_items, duration))
        logging.info(
            f"Total shards: {manifest_num_lines}, "
            f"Shards for current index: {len(manifests)}"
        )
        base_dataset = WebDatasetReader(
            manifests=manifests,
            sample_rate=SIDON_INPUT_SAMPLE_RATE,
            evaluation=True,
        )

    # ── Dynamic batching + DataLoader ──
    batched_dataset = StreamLengthGroupDataset(
        dataset=base_dataset,
        batch_duration=args.batch_duration,
        max_sample=args.max_sample,
        min_length=args.min_length,
        max_length=args.max_length,
    )

    collate_fn = CollateFunction(
        skip_errors=args.skip_errors,
        sample_rate=SIDON_INPUT_SAMPLE_RATE,
    )

    dataloader = DataLoader(
        dataset=batched_dataset,
        batch_size=None,
        collate_fn=collate_fn,
        num_workers=loader_workers,
        prefetch_factor=10 if loader_workers > 0 else None,
        pin_memory=True,
        persistent_workers=loader_workers > 0,
    )

    # ── Multi-GPU process pool ──
    num_devices = torch.cuda.device_count()
    if num_devices == 0:
        logging.warning("No GPUs detected - using CPU for processing")
        num_processes = args.nj_per_gpu
    else:
        num_processes = num_devices * args.nj_per_gpu
    logging.info(
        f"GPU count: {num_devices}, Processes per GPU: {args.nj_per_gpu}, "
        f"Total processes: {num_processes}"
    )

    # Build a list of (physical_gpu_id, num_workers) for each pool.
    # When num_devices == 0 we use a single CPU pool.
    if num_devices == 0:
        pool_specs = [(None, num_processes)]
    else:
        pool_specs = [(gpu_id, args.nj_per_gpu) for gpu_id in range(num_devices)]

    # ── Output paths ──
    tar_output_pattern = str(Path(args.tar_output_pattern).expanduser())
    jsonl_output_pattern = str(Path(args.jsonl_output_pattern).expanduser())
    Path(tar_output_pattern).parent.mkdir(parents=True, exist_ok=True)
    Path(jsonl_output_pattern).parent.mkdir(parents=True, exist_ok=True)

    output_dir = Path(tar_output_pattern).parent.parent
    error_log_path = str(output_dir / "errors.jsonl")
    manifest_path = str(output_dir / "data.lst")

    error_logger = logging.getLogger("error_log")
    error_logger.setLevel(logging.ERROR)
    error_logger.handlers.clear()
    error_fh = logging.FileHandler(error_log_path, mode="w", encoding="utf-8")
    error_fh.setFormatter(logging.Formatter("%(message)s"))
    error_logger.addHandler(error_fh)

    # ── Progress and shard tracking ──
    processed_count = 0
    error_count = 0
    write_error_count = 0
    failed_ids = []
    shard_idx = 0
    shard_sample_count = 0
    shard_duration = 0.0
    samples_per_shard = args.samples_per_shard
    shard_manifest = {}
    target_sample_rate = args.target_sample_rate

    tar_writer = None
    jsonl_file = None

    def open_new_shard():
        nonlocal tar_writer, jsonl_file, shard_idx, shard_sample_count, shard_duration
        if tar_writer is not None:
            tar_writer.close()
        if jsonl_file is not None:
            jsonl_file.close()
        if shard_idx > 0 and shard_sample_count > 0:
            prev_idx = shard_idx - 1
            shard_manifest[prev_idx] = (
                os.path.abspath(tar_output_pattern % prev_idx),
                os.path.abspath(jsonl_output_pattern % prev_idx),
                shard_sample_count,
                shard_duration,
            )
        tar_fname = tar_output_pattern % shard_idx
        jsonl_fname = jsonl_output_pattern % shard_idx
        tar_writer = wds.TarWriter(tar_fname)
        jsonl_file = open(jsonl_fname, "w", encoding="utf-8")
        shard_idx += 1
        shard_sample_count = 0
        shard_duration = 0.0

    def write_sample(key, waveform, metadata):
        nonlocal shard_sample_count, write_error_count, shard_duration
        assert tar_writer is not None and jsonl_file is not None
        try:
            if target_sample_rate != SIDON_OUTPUT_SAMPLE_RATE:
                waveform = torchaudio.functional.resample(
                    waveform,
                    orig_freq=SIDON_OUTPUT_SAMPLE_RATE,
                    new_freq=target_sample_rate,
                )
            waveform = (waveform / (waveform.abs().max() + 1e-7)) * 0.6

            record = serialise_flac(key, waveform, target_sample_rate)
            jsonl_record = _encode_metadata(metadata)
            tar_writer.write(record)
            jsonl_file.write(jsonl_record.decode("utf-8") + "\n")
            shard_sample_count += 1
            shard_duration += metadata.get("audio_duration", 0.0)
        except Exception as exc:
            write_error_count += 1
            failed_ids.append(key)
            error_logger.error(
                json.dumps({"id": key, "reason": str(exc)}, ensure_ascii=False)
            )
            logging.error(f"Write failed for sample {key}: {exc}")

    def handle_result(result):
        nonlocal processed_count, error_count
        if result["status"] == "success":
            for key, cleaned, metadata in zip(
                result["keys"], result["results"], result["metadata"]
            ):
                if tar_writer is None or shard_sample_count >= samples_per_shard:
                    open_new_shard()
                write_sample(key, cleaned, metadata)
                processed_count += 1
        else:
            error_count += result["size"]
            failed_ids.extend(result["keys"])
            for key in result["keys"]:
                error_logger.error(
                    json.dumps(
                        {"id": key, "reason": result["error"]},
                        ensure_ascii=False,
                    )
                )
            if not args.skip_errors:
                raise RuntimeError(
                    f"Batch starting with {result['keys'][0]} failed - terminating"
                )
            logging.warning(
                f"Skipping failed batch starting with {result['keys'][0]}: "
                f"{result['error']}"
            )

    # ── Main processing loop ──
    main_progress = tqdm(total=total_samples, desc="Denoising Audio")

    # Launch subprocess-based GPU workers.  CUDA_VISIBLE_DEVICES is set in the
    # subprocess Popen environment so it takes effect before import torch.
    pool = GPUWorkerPool(pool_specs, args.feature_extractor_path, args.decoder_path)
    logging.info(f"Submitting tasks... ({num_processes} subprocess workers)")
    try:
        futures = set()
        max_pending = num_processes * 2

        def drain_completed():
            nonlocal futures
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for f in done:
                futures.discard(f)
                result = f.result()
                main_progress.update(result["size"])
                handle_result(result)
                main_progress.set_postfix(
                    OK=processed_count,
                    Err=error_count,
                )

        for batch in dataloader:
            if batch.size == 0:
                continue
            if len(futures) >= max_pending:
                drain_completed()
            futures.add(pool.submit(batch))

        logging.info("Processing remaining pending batches...")
        while futures:
            drain_completed()

    except Exception:
        logging.error("Critical error during processing", exc_info=True)
        raise
    finally:
        pool.shutdown()
        main_progress.close()
        if tar_writer is not None:
            tar_writer.close()
        if jsonl_file is not None:
            jsonl_file.close()
        if shard_idx > 0 and shard_sample_count > 0:
            last_idx = shard_idx - 1
            shard_manifest[last_idx] = (
                os.path.abspath(tar_output_pattern % last_idx),
                os.path.abspath(jsonl_output_pattern % last_idx),
                shard_sample_count,
                shard_duration,
            )

    # ── Write manifest (data.lst) ──
    with open(manifest_path, "w", encoding="utf-8") as mf:
        for idx in sorted(shard_manifest.keys()):
            tar_path, jsonl_path, count, duration = shard_manifest[idx]
            mf.write(f"{tar_path} {jsonl_path} {count} {duration:.3f}\n")

    # ── Summary ──
    total_failed = error_count + write_error_count
    filtered_and_skipped = total_samples - processed_count - total_failed
    logging.info(
        f"Processing Complete - Successful: {processed_count}, Failed: {total_failed}, "
        f"Filtered/Skipped: {filtered_and_skipped}, Shards written: {shard_idx}"
    )
    logging.info(f"Manifest written to: {manifest_path} ({len(shard_manifest)} shards)")
    if total_failed > 0:
        logging.info(f"Error details: {error_log_path}")
    if failed_ids and args.skip_errors:
        logging.warning(
            f"Failed sample IDs (count: {len(failed_ids)}): {failed_ids[:100]}..."
        )
    if write_error_count > 0 and not args.skip_errors:
        raise RuntimeError(
            f"{write_error_count} samples failed to write - check logs for details"
        )


if __name__ == "__main__":
    main()
