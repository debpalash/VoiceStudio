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

"""Batch inference CLI for OmniVoice.

Distributes TTS generation across multiple GPUs for large-scale tasks.
Reads a JSONL test list, generates audio in parallel, and saves results.

Usage:
    omnivoice-infer-batch --model k2-fsa/OmniVoice \
        --test_list test.jsonl --res_dir results/

Test list format (JSONL, one JSON object per line):
    Required fields: "id", "text"
    Voice cloning:   "ref_audio", "ref_text"
    Voice design:    "instruct"
    Optional:        "language_id", "language_name", "duration", "speed"
"""

import argparse
import logging
import multiprocessing as mp
import os
import signal
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import List, Optional, Tuple

import torch
import torchaudio
from tqdm import tqdm

from omnivoice.models.omnivoice import OmniVoice
from omnivoice.utils.audio import load_audio
from omnivoice.utils.common import str2bool
from omnivoice.utils.dtype import tts_dtype_name
from omnivoice.utils.data_utils import read_test_list
from omnivoice.utils.duration import RuleDurationEstimator


def get_best_device():
    """Auto-detect the best available device: CUDA > MPS > CPU."""
    if torch.cuda.is_available():
        return "cuda", torch.cuda.device_count()
    if torch.backends.mps.is_available():
        return "mps", 1
    return "cpu", 1


worker_model = None
SAMPLING_RATE = 24000


def get_parser():
    parser = argparse.ArgumentParser(description="Infer OmniVoice Model")
    parser.add_argument(
        "--model",
        type=str,
        default="k2-fsa/OmniVoice",
        help="Path to the model checkpoint (local dir or HF repo id). "
        "Audio tokenizer is expected at <checkpoint>/audio_tokenizer/.",
    )
    parser.add_argument(
        "--test_list",
        type=str,
        required=True,
        help="Path to the JSONL file containing test samples. "
        'Each line is a JSON object: {"id": "name", "text": "...", '
        '"ref_audio": "/path.wav", "ref_text": "...", '
        '"language_id": "en", "language_name": "English", '
        '"duration": 10.0, "speed": 1.2}. '
        "language_id, language_name, duration, and speed are optional.",
    )
    parser.add_argument(
        "--res_dir",
        type=str,
        required=True,
        help="Directory to save the generated audio files.",
    )
    parser.add_argument(
        "--num_step",
        type=int,
        default=32,
        help="Number of steps for iterative decoding.",
    )
    parser.add_argument(
        "--guidance_scale",
        type=float,
        default=2.0,
        help="Scale for Classifier-Free Guidance.",
    )
    parser.add_argument(
        "--t_shift",
        type=float,
        default=0.1,
        help="Shift t to smaller ones if t_shift < 1.0",
    )
    parser.add_argument(
        "--nj_per_gpu",
        type=int,
        default=1,
        help="Number of worker processes to spawn per GPU.",
    )
    parser.add_argument(
        "--audio_chunk_duration",
        type=float,
        default=15.0,
        help="Maximum duration of audio chunk (in seconds) for splitting. "
        '"Not split" if <= 0.',
    )
    parser.add_argument(
        "--audio_chunk_threshold",
        type=float,
        default=30.0,
        help=(
            "The duration threshold (in seconds) to decide"
            " whether to split audio into chunks."
        ),
    )
    parser.add_argument(
        "--batch_duration",
        type=float,
        default=1000.0,
        help="Maximum total duration (reference + generated) per batch (seconds). "
        "Only effective for parallel_chunk / no chunk mode.",
    )
    parser.add_argument(
        "--batch_size",
        type=int,
        default=0,
        help="Fixed batch size (number of samples per batch). "
        "If > 0, use fixed-size batching instead of duration-based batching.",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=0,
        help="Number of dummy inference runs per worker before real inference "
        "starts, to warm up CUDA kernels and caches.",
    )
    parser.add_argument(
        "--preprocess_prompt",
        type=str2bool,
        default=True,
        help="Whether to preprocess reference audio (silence removal, trimming). "
        "Set to False to keep raw audio.",
    )
    parser.add_argument(
        "--postprocess_output",
        type=str2bool,
        default=True,
        help="Whether to post-process generated audio (remove silence).",
    )
    parser.add_argument(
        "--layer_penalty_factor",
        type=float,
        default=5.0,
        help="The penalty factor for layer-wise sampling.",
    )
    parser.add_argument(
        "--position_temperature",
        type=float,
        default=5.0,
        help="The temperature for position selection.",
    )
    parser.add_argument(
        "--class_temperature",
        type=float,
        default=0.0,
        help="The temperature for class token sampling.",
    )
    parser.add_argument(
        "--denoise",
        type=str2bool,
        default=True,
        help="Whether to add <|denoise|> token in the reference.",
    )
    parser.add_argument(
        "--lang_id",
        type=str,
        default=None,
        help="Language id to use when test_list JSONL entries do not contain "
        "language_id/language_name fields. If provided, both language_id and "
        "language_name will be set to this value.",
    )
    return parser


def process_init(rank_queue, model_checkpoint, warmup=0):
    """Initializer for each worker process.

    Loads model (with tokenizers and duration estimator) onto a specific GPU
    via ``OmniVoice.from_pretrained()``.
    """
    global worker_model

    torch.set_num_threads(2)
    torch.set_num_interop_threads(2)

    formatter = (
        "%(asctime)s %(levelname)s [%(filename)s:%(lineno)d] "
        "[Worker %(process)d] %(message)s"
    )
    logging.basicConfig(format=formatter, level=logging.INFO, force=True)

    rank = rank_queue.get()
    device_type, device_id = rank
    if device_type == "cpu":
        worker_device = "cpu"
    elif device_type == "mps":
        worker_device = "mps"
    else:
        worker_device = f"cuda:{device_id}"

    logging.info(f"Initializing worker on device: {worker_device}")

    worker_model = OmniVoice.from_pretrained(
        model_checkpoint,
        device_map=worker_device,
        dtype=getattr(torch, tts_dtype_name(worker_device)),
    )

    if warmup > 0:
        logging.info(f"Running {warmup} warmup iterations on {worker_device}")
        dummy_ref_audio = (
            torch.randn(1, SAMPLING_RATE),
            SAMPLING_RATE,
        )  # 1s silence
        for i in range(warmup):
            worker_model.generate(
                text=["hello"],
                language=["en"],
                ref_audio=[dummy_ref_audio],
                ref_text=["hello"],
            )
        logging.info(f"Warmup complete on {worker_device}")

    logging.info(f"Worker on {worker_device} initialized successfully.")


def estimate_sample_total_duration(
    duration_estimator: RuleDurationEstimator,
    text: str,
    ref_text: str,
    ref_audio_path: str,
    gen_duration: Optional[float] = None,
) -> float:
    ref_wav = load_audio(ref_audio_path, SAMPLING_RATE)
    ref_duration = ref_wav.shape[-1] / SAMPLING_RATE

    if gen_duration is None:
        gen_duration = duration_estimator.estimate_duration(
            text, ref_text, ref_duration, low_threshold=2.0
        )

    total_duration = ref_duration + gen_duration
    return total_duration


def cluster_samples_by_duration(
    samples: List[Tuple],
    duration_estimator: RuleDurationEstimator,
    batch_duration: float,
) -> List[List[Tuple]]:
    sample_with_duration = []
    for sample in samples:
        save_name, ref_text, ref_audio_path, text, lang_id, lang_name, dur, spd, instruct = sample
        total_duration = estimate_sample_total_duration(
            duration_estimator,
            text,
            ref_text,
            ref_audio_path,
            gen_duration=dur,
        )
        sample_with_duration.append((sample, total_duration))

    sample_with_duration.sort(key=lambda x: x[1], reverse=True)
    batches = []
    current_batch = []
    current_total_duration = 0.0

    for sample, duration in sample_with_duration:
        if duration > batch_duration:
            batches.append([sample])
            continue

        if current_total_duration + duration <= batch_duration:
            current_batch.append(sample)
            current_total_duration += duration
        else:
            batches.append(current_batch)
            current_batch = [sample]
            current_total_duration = duration

    if current_batch:
        batches.append(current_batch)

    logging.info(f"Clustered {len(samples)} samples into {len(batches)} batches")
    return batches


def cluster_samples_by_batch_size(
    samples: List[Tuple],
    duration_estimator: RuleDurationEstimator,
    batch_size: int,
) -> List[List[Tuple]]:
    """Split samples into fixed-size batches, sorted by duration to minimize padding."""
    sample_with_duration = []
    for sample in samples:
        save_name, ref_text, ref_audio_path, text, lang_id, lang_name, dur, spd, instruct = sample
        total_duration = estimate_sample_total_duration(
            duration_estimator,
            text,
            ref_text,
            ref_audio_path,
            gen_duration=dur,
        )
        sample_with_duration.append((sample, total_duration))

    sample_with_duration.sort(key=lambda x: x[1], reverse=True)
    sorted_samples = [s for s, _ in sample_with_duration]

    batches = [
        sorted_samples[i : i + batch_size]
        for i in range(0, len(sorted_samples), batch_size)
    ]
    logging.info(
        f"Split {len(samples)} samples into {len(batches)} batches "
        f"(fixed batch_size={batch_size}, sorted by duration)"
    )
    return batches


def run_inference_batch(
    batch_samples: List[Tuple],
    res_dir: str,
    **gen_kwargs,
) -> List[Tuple]:
    global worker_model

    save_names = []
    ref_texts = []
    ref_audio_paths = []
    texts = []
    langs = []
    durations = []
    speeds = []
    instructs = []

    for sample in batch_samples:
        save_name, ref_text, ref_audio_path, text, lang_id, lang_name, dur, spd, instruct = sample
        save_names.append(save_name)
        ref_texts.append(ref_text)
        ref_audio_paths.append(ref_audio_path)
        texts.append(text)
        langs.append(lang_id)
        durations.append(dur)
        speeds.append(spd)
        instructs.append(instruct)

    start_time = time.time()
    audios = worker_model.generate(
        text=texts,
        language=langs,
        ref_audio=ref_audio_paths,
        ref_text=ref_texts,
        duration=durations if any(d is not None for d in durations) else None,
        speed=speeds if any(s is not None for s in speeds) else None,
        instruct=instructs if any(i is not None for i in instructs) else None,
        **gen_kwargs,
    )
    batch_synth_time = time.time() - start_time

    results = []
    for save_name, audio in zip(save_names, audios):
        save_path = os.path.join(res_dir, save_name + ".wav")
        torchaudio.save(save_path, audio, worker_model.sampling_rate)
        audio_duration = audio.shape[-1] / worker_model.sampling_rate
        results.append(
            (
                save_name,
                batch_synth_time / len(batch_samples),
                audio_duration,
                "success",
            )
        )

    return results


def main():
    formatter = "%(asctime)s %(levelname)s [%(filename)s:%(lineno)d] %(message)s"
    logging.basicConfig(format=formatter, level=logging.INFO, force=True)
    mp.set_start_method("spawn", force=True)

    args = get_parser().parse_args()
    os.makedirs(args.res_dir, exist_ok=True)

    device_type, num_devices = get_best_device()
    if device_type == "cpu":
        logging.warning(
            "No GPU found. Falling back to CPU inference. This might be slow."
        )

    num_processes = num_devices * args.nj_per_gpu
    logging.info(
        f"Using {device_type} ({num_devices} device(s))."
        f" Spawning {num_processes} worker processes."
    )

    manager = mp.Manager()
    rank_queue = manager.Queue()
    for rank in list(range(num_devices)) * args.nj_per_gpu:
        rank_queue.put((device_type, rank))

    samples_raw = read_test_list(args.test_list)
    samples = []
    for s in samples_raw:
        if args.lang_id is not None:
            lang_id = args.lang_id
            lang_name = args.lang_id
        else:
            lang_id = s.get("language_id")
            lang_name = s.get("language_name")
        samples.append(
            (
                s["id"],
                s["ref_text"],
                s["ref_audio"],
                s["text"],
                lang_id,
                lang_name,
                s.get("duration"),
                s.get("speed"),
                s.get("instruct"),
            )
        )

    total_synthesis_time = []
    total_audio_duration = []

    try:
        with ProcessPoolExecutor(
            max_workers=num_processes,
            initializer=process_init,
            initargs=(rank_queue, args.model, args.warmup),
        ) as executor:
            futures = []

            # parallel_chunk / no chunk
            logging.info("Running batch inference")

            duration_estimator = RuleDurationEstimator()
            if args.batch_size > 0:
                batches = cluster_samples_by_batch_size(
                    samples, duration_estimator, args.batch_size
                )
            else:
                batches = cluster_samples_by_duration(
                    samples, duration_estimator, args.batch_duration
                )

            args_dict = vars(args)

            for batch in batches:
                futures.append(
                    executor.submit(
                        run_inference_batch, batch_samples=batch, **args_dict
                    )
                )

            for future in tqdm(
                as_completed(futures), total=len(futures), desc="Processing samples"
            ):
                try:
                    result = future.result()
                    for s_name, synth_time, audio_dur, status in result:
                        total_synthesis_time.append(synth_time)
                        total_audio_duration.append(audio_dur)
                        rtf = synth_time / audio_dur if audio_dur > 0 else float("inf")
                        logging.debug(
                            f"Processed {s_name}: Audio Duration={audio_dur:.2f}s, "
                            f"Synthesis Time={synth_time:.2f}s, RTF={rtf:.4f}"
                        )
                except Exception as e:
                    logging.error(f"Failed to process sample: {e}")
                    detailed_error = traceback.format_exc()
                    logging.error(f"Detailed error: {detailed_error}")

    except (Exception, KeyboardInterrupt) as e:
        logging.critical(
            f"An unrecoverable error occurred: {e}. Terminating all processes."
        )
        detailed_error_info = traceback.format_exc()
        logging.error(f"--- DETAILED TRACEBACK ---\n{detailed_error_info}")
        os.killpg(os.getpgid(os.getpid()), signal.SIGKILL)

    total_synthesis_time = sum(total_synthesis_time)
    total_audio_duration = sum(total_audio_duration)
    logging.info("--- Summary ---")
    logging.info(f"Total audio duration: {total_audio_duration:.2f}s")
    logging.info(f"Total synthesis time: {total_synthesis_time:.2f}s")
    if total_audio_duration > 0:
        average_rtf = total_synthesis_time / total_audio_duration
        logging.info(f"Average RTF: {average_rtf:.4f}")
    else:
        logging.warning("No speech was generated. RTF cannot be computed.")

    logging.info("Done!")


if __name__ == "__main__":
    main()
