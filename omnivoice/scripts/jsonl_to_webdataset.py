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

"""
Pack a JSONL audio dataset into a customed WebDataset shards
(paired .tar and .jsonl files).

Usage:
    python jsonl_to_webdataset.py \
        --input data.jsonl \
        --output output_dir/ \
        --workers 16 \
        --threads 4 \
        --shard-size 1000 \
        --sr 24000

Input JSONL format (one JSON object per line):
    {"id": "utt_001", "audio_path": "/data/wavs/001.wav", "text": "hello world", ...}

    Required fields: "id", "audio_path", "text"
    All other fields are preserved in the output metadata.

Output structure:
    output_dir/
    ├── audios/           # WebDataset tar shards
    │   ├── shard_000000.tar
    │   ├── shard_000001.tar
    │   └── ...
    ├── txts/             # Per-shard JSONL metadata (with audio_duration added)
    │   ├── shard_000000.jsonl
    │   ├── shard_000001.jsonl
    │   └── ...
    ├── data.lst          # Manifest: <tar_path> <jsonl_path> <sample_count> <total_duration>
    └── errors.jsonl      # Failed samples with error details
"""

import argparse
import io
import json
import logging
import multiprocessing as mp
import os
import random
from concurrent.futures import (
    FIRST_COMPLETED,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    as_completed,
    wait,
)
from itertools import islice
from pathlib import Path

import torchaudio
from omnivoice.utils.audio import read_audio, save_audio
import webdataset as wds
from tqdm import tqdm

from omnivoice.utils.common import str2bool


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pack JSONL audio dataset into WebDataset shards."
    )
    parser.add_argument(
        "--input", type=str, default="data.jsonl", help="Path to input JSONL file"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="emilia",
        help="Path to output directory",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=16,
        help="Number of worker processes (default: 16)",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=4,
        help="Number of threads per worker process.",
    )
    parser.add_argument(
        "--shard-size",
        type=int,
        default=1000,
        help="Number of samples per shard (default: 1000)",
    )
    parser.add_argument(
        "--sr", type=int, default=24000, help="Target sample rate (default: 24000)"
    )
    parser.add_argument(
        "--shuffle",
        type=str2bool,
        default=True,
        help="Shuffle data by default.",
    )
    parser.add_argument(
        "--shuffle-seed",
        type=int,
        default=42,
        help="Random seed for shuffle (default: 42)",
    )
    parser.add_argument(
        "--min-duration",
        type=float,
        default=None,
        help="Filter out samples shorter than this (seconds).",
    )
    parser.add_argument(
        "--max-duration",
        type=float,
        default=None,
        help="Filter out samples >= this duration (seconds).",
    )
    return parser


def read_jsonl(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def chunked_reader(iterator, chunk_size):
    it = iter(iterator)
    while chunk := list(islice(it, chunk_size)):
        yield chunk


def process_audio_item(meta, target_sr):
    key = meta.get("id")
    audio_path = meta.get("audio_path")

    if not key or not audio_path:
        return {
            "error": {
                "id": key,
                "audio_path": audio_path,
                "reason": "missing id or audio_path",
            }
        }

    try:
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"{audio_path} not found")

        waveform, sr = read_audio(audio_path)
        audio_duration = waveform.shape[1] / sr
        meta["audio_duration"] = audio_duration

        if target_sr and sr != target_sr:
            waveform = torchaudio.functional.resample(waveform, sr, target_sr)
            sr = target_sr

        audio_buffer = io.BytesIO()
        save_audio(audio_buffer, waveform, sr, format="flac", bits_per_sample=16)
        audio_bytes = audio_buffer.getvalue()

        sample = {
            "__key__": key,
            "flac": audio_bytes,
        }

        return {"ok": (sample, meta)}

    except Exception as e:
        return {"error": {"id": key, "audio_path": audio_path, "reason": str(e)}}


def process_single_shard(
    shard_idx,
    records,
    output_tar_pattern,
    output_jsonl_pattern,
    target_sr,
    num_threads=4,
    min_duration=None,
    max_duration=None,
):
    tar_fname = output_tar_pattern % shard_idx
    jsonl_fname = output_jsonl_pattern % shard_idx

    processed_count = 0
    filtered_count = 0
    error_count = 0
    total_duration = 0.0
    errors = []

    with wds.TarWriter(tar_fname) as sink, open(
        jsonl_fname, "w", encoding="utf-8"
    ) as jsonl_f:

        with ThreadPoolExecutor(max_workers=num_threads) as thread_pool:
            futures = []

            for meta in records:
                f = thread_pool.submit(process_audio_item, meta, target_sr)
                futures.append(f)

            for f in as_completed(futures):
                result = f.result()

                if "error" in result:
                    error_count += 1
                    errors.append(result["error"])
                    continue

                sample, meta = result["ok"]
                dur = meta.get("audio_duration", 0.0)

                # Duration filtering (based on actual audio_duration computed above)
                if min_duration is not None and dur < min_duration:
                    filtered_count += 1
                    continue
                if max_duration is not None and dur >= max_duration:
                    filtered_count += 1
                    continue

                sink.write(sample)

                jsonl_f.write(json.dumps(meta, ensure_ascii=False) + "\n")

                total_duration += dur
                processed_count += 1

    # Clean up empty shard files
    if processed_count == 0:
        for p in (tar_fname, jsonl_fname):
            if os.path.exists(p):
                os.remove(p)

    return (
        shard_idx,
        processed_count,
        error_count,
        filtered_count,
        total_duration,
        errors,
    )


def count_lines(path):
    with open(path, "rb") as f:
        return sum(buf.count(b"\n") for buf in iter(lambda: f.read(1 << 20), b""))


def pack_dataset(
    input_jsonl,
    output_dir,
    samples_per_shard=5000,
    num_workers=16,
    target_sr=24000,
    threads_per_worker=4,
    shuffle=False,
    shuffle_seed=None,
    min_duration=None,
    max_duration=None,
):
    input_path = Path(input_jsonl)
    output_dir = Path(output_dir)
    output_tar_dir = output_dir / "audios"
    output_tar_dir.mkdir(parents=True, exist_ok=True)
    output_jsonl_dir = output_dir / "txts"
    output_jsonl_dir.mkdir(parents=True, exist_ok=True)

    output_tar_pattern = str(output_tar_dir / "shard-%06d.tar")
    output_jsonl_pattern = str(output_jsonl_dir / "shard-%06d.jsonl")

    error_log_path = str(output_dir / "errors.jsonl")

    # Setup error logger
    error_logger = logging.getLogger("error_log")
    error_logger.setLevel(logging.ERROR)
    error_logger.handlers.clear()
    fh = logging.FileHandler(error_log_path, mode="w", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(message)s"))
    error_logger.addHandler(fh)

    shard_manifest = {}

    print(f"Reading input: {input_path}")
    print(f"Output dir: {output_dir}")
    print(f"Strategy: {num_workers} Processes x {threads_per_worker} Threads")

    if shuffle:
        print("Load input dataset...")
        entries = list(read_jsonl(input_path))
        random.seed(shuffle_seed)
        random.shuffle(entries)
        print(f"Shuffled {len(entries)} entries (seed={shuffle_seed})")
        total_lines = len(entries)
        chunk_gen = chunked_reader(iter(entries), samples_per_shard)
    else:
        print("Calculating total lines...")
        total_lines = count_lines(input_path)
        chunk_gen = chunked_reader(read_jsonl(input_path), samples_per_shard)

    if min_duration is not None or max_duration is not None:
        print(
            f"Duration filter: [{min_duration or 0:.2f}s"
            f", {max_duration or float('inf'):.1f}s) (applied after audio decoding)"
        )

    total_shards_est = (total_lines + samples_per_shard - 1) // samples_per_shard
    print(f"Total samples: {total_lines}, Estimated shards: {total_shards_est}")

    with ProcessPoolExecutor(max_workers=num_workers) as executor:

        futures = set()

        shard_idx = 0
        total_processed = 0
        total_errors = 0
        total_filtered = 0

        pbar = tqdm(
            total=total_shards_est,
            desc="Shards Processed",
            unit="shard",
        )

        def submit_next_chunks(limit):
            """Pull up to `limit` chunks from generator, submit them."""
            nonlocal shard_idx
            submitted = 0
            for chunk in chunk_gen:
                f = executor.submit(
                    process_single_shard,
                    shard_idx,
                    chunk,
                    output_tar_pattern,
                    output_jsonl_pattern,
                    target_sr,
                    threads_per_worker,
                    min_duration,
                    max_duration,
                )
                futures.add(f)
                shard_idx += 1
                submitted += 1
                if submitted >= limit:
                    break

        submit_next_chunks(num_workers * 2)

        while futures:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)

            for f in done:
                futures.remove(f)

                try:
                    s_idx, p_count, e_count, f_count, s_duration, errors = f.result()
                    total_processed += p_count
                    total_errors += e_count
                    total_filtered += f_count

                    # Write error log
                    for err in errors:
                        err["shard_idx"] = s_idx
                        error_logger.error(json.dumps(err, ensure_ascii=False))

                    if p_count > 0:
                        tar_abs = os.path.abspath(output_tar_pattern % s_idx)
                        jsonl_abs = os.path.abspath(output_jsonl_pattern % s_idx)
                        shard_manifest[s_idx] = (
                            tar_abs,
                            jsonl_abs,
                            p_count,
                            s_duration,
                        )

                    pbar.set_postfix(
                        {
                            "Samples": total_processed,
                            "Filtered": total_filtered,
                            "Errors": total_errors,
                        }
                    )
                    pbar.update(1)
                except Exception as e:
                    print(f"Shard task failed: {e}")

                submit_next_chunks(1)

        pbar.close()

    # Write final manifest file (data.lst)
    manifest_path = str(output_dir / "data.lst")
    with open(manifest_path, "w", encoding="utf-8") as mf:
        for idx in sorted(shard_manifest.keys()):
            tar_path, jsonl_path, count, duration = shard_manifest[idx]
            mf.write(f"{tar_path} {jsonl_path} {count} {duration:.3f}\n")

    print(f"\nDone! Output saved to {output_dir}")
    print(f"Successfully packed: {total_processed}")
    print(f"Filtered by duration: {total_filtered}")
    print(f"Failed: {total_errors}")
    print(f"Manifest written to: {manifest_path} ({len(shard_manifest)} shards)")
    if total_errors > 0:
        print(f"Error details: {error_log_path}")


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)

    args = build_parser().parse_args()
    pack_dataset(
        input_jsonl=args.input,
        output_dir=args.output,
        samples_per_shard=args.shard_size,
        num_workers=args.workers,
        target_sr=args.sr,
        threads_per_worker=args.threads,
        shuffle=args.shuffle,
        shuffle_seed=args.shuffle_seed,
        min_duration=args.min_duration,
        max_duration=args.max_duration,
    )
