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
Calculate UTMOS score with automatic Mean Opinion Score (MOS) prediction system
"""
import argparse
import logging
import multiprocessing as mp
import os
import sys
import traceback
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import torch
from tqdm import tqdm

from omnivoice.eval.models.utmos import UTMOS22Strong
from omnivoice.eval.utils import load_waveform
from omnivoice.utils.data_utils import read_test_list

warnings.filterwarnings("ignore")

# Global variables for workers
worker_model = None
worker_device = None
worker_sr = 16000


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Calculate UTMOS score using UTMOS22Strong model."
    )
    parser.add_argument(
        "--wav-path",
        type=str,
        required=True,
        help="Path to the directory containing evaluated speech files.",
    )
    parser.add_argument(
        "--test-list",
        type=str,
        required=True,
        help="Path to the JSONL test list. Each line is a JSON object "
        "with fields: id, text, ref_audio, ref_text, language_id, language_name.",
    )
    parser.add_argument(
        "--model-dir",
        type=str,
        required=True,
        help="Local path of our evaluation model repository."
        "Download from https://huggingface.co/k2-fsa/TTS_eval_models."
        "Will use 'tts_eval_models/mos/utmos22_strong_step7459_v1.pt'"
        " in this script",
    )
    parser.add_argument(
        "--extension",
        type=str,
        default="wav",
        help="Extension of the speech files. Default: wav",
    )
    parser.add_argument(
        "--decode-path",
        type=str,
        default=None,
        help="Path to the output file where UTMOS information will be saved. "
        "If not provided, results are only printed to console.",
    )
    parser.add_argument(
        "--nj-per-gpu",
        type=int,
        default=1,
        help="Number of worker processes to spawn per GPU.",
    )
    return parser


def get_device(rank: int = 0) -> torch.device:
    assert torch.cuda.is_available(), "CUDA is required but not available."
    device = torch.device(f"cuda:{rank}")
    torch.cuda.set_device(rank)
    return device


def worker_init(
    rank_queue,
    model_path,
):
    """Initialize worker process with model and device."""
    global worker_model, worker_device, worker_sr

    # Limit CPU threads per worker
    torch.set_num_threads(2)

    formatter = "%(asctime)s %(levelname)s [%(filename)s:%(lineno)d] [Worker %(process)d] %(message)s"
    logging.basicConfig(format=formatter, level=logging.INFO, force=True)

    rank = rank_queue.get() if rank_queue else -1

    worker_device = get_device(rank)
    worker_sr = 16000

    logging.debug(f"Initializing UTMOS worker on {worker_device}")

    # Initialize Model
    worker_model = UTMOS22Strong()
    try:
        # Load weights to CPU first, then move to device. ``weights_only=True``
        # keeps torch's secure unpickler on: the checkpoint is a downloaded eval
        # artifact, and this mirrors ``omnivoice/models/omnivoice.py``. A
        # checkpoint that needs a pickled global must register it explicitly with
        # ``torch.serialization.add_safe_globals`` rather than disabling this.
        state_dict = torch.load(model_path, map_location="cpu", weights_only=True)
        worker_model.load_state_dict(state_dict)
    except Exception as e:
        logging.error(f"Failed to load model from {model_path}: {e}")
        raise

    worker_model.to(worker_device)
    worker_model.eval()


@torch.no_grad()
def run_utmos_worker(file_idx, wav_path, language_name):
    """Worker function to process a single audio file."""
    try:
        if not os.path.exists(wav_path):
            return file_idx, wav_path, language_name, f"File not found: {wav_path}", "error"

        # Load and preprocess waveform
        speech = load_waveform(wav_path, worker_sr, device=worker_device)

        # Compute score
        # UTMOS expects input shape (Batch, Time)
        score = worker_model(speech.unsqueeze(0), worker_sr)

        return file_idx, wav_path, language_name, score.item(), "success"

    except Exception as e:
        error_detail = (
            f"Error processing {wav_path}: {str(e)}\n"
            f"Traceback:\n{traceback.format_exc()}"
        )
        return file_idx, wav_path, language_name, error_detail, "error"


def main():
    parser = get_parser()
    args = parser.parse_args()

    # Main process thread setting
    torch.set_num_threads(2)

    mp.set_start_method("spawn", force=True)

    formatter = "%(asctime)s %(levelname)s [%(filename)s:%(lineno)d] %(message)s"
    logging.basicConfig(format=formatter, level=logging.INFO, force=True)

    # Validate inputs
    if not os.path.isdir(args.wav_path):
        logging.error(f"Invalid directory: {args.wav_path}")
        sys.exit(1)

    model_path = os.path.join(args.model_dir, "mos/utmos22_strong_step7459_v1.pt")
    if not os.path.exists(model_path):
        logging.error(f"Model file not found at {model_path}")
        sys.exit(1)

    # Scan directory for files
    logging.info(f"Calculating UTMOS for {args.wav_path}")

    wav_files = []
    try:
        samples = read_test_list(args.test_list)
        for s in samples:
            language_name = s.get("language_name") or "unknown"
            eval_wav_path = os.path.join(args.wav_path, f"{s['id']}.{args.extension}")
            wav_files.append((eval_wav_path, language_name))
    except Exception as e:
        raise ValueError(f"Error reading test list {args.test_list}: {e}")

    # Setup Parallel Processing
    num_gpus = torch.cuda.device_count()
    assert num_gpus > 0, "No GPU found. GPU is required."
    total_procs = num_gpus * args.nj_per_gpu

    logging.info(
        f"Starting evaluation with {total_procs} processes on {num_gpus} GPUs."
    )

    manager = mp.Manager()
    rank_queue = manager.Queue()

    for rank in list(range(num_gpus)) * args.nj_per_gpu:
        rank_queue.put(rank)

    scores = []

    fout = None
    if args.decode_path:
        parent = os.path.dirname(args.decode_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        fout = open(args.decode_path, "w", encoding="utf8")
        logging.info(f"Saving detailed UTMOS results to: {args.decode_path}")
        fout.write("Name\tUTMOS\n")

    try:
        with ProcessPoolExecutor(
            max_workers=total_procs,
            initializer=worker_init,
            initargs=(
                rank_queue,
                model_path,
            ),
        ) as executor:
            futures = []
            for i, (wav_path, language_name) in enumerate(wav_files):
                futures.append(
                    executor.submit(run_utmos_worker, i, wav_path, language_name)
                )

            pbar = tqdm(
                as_completed(futures), total=len(wav_files), desc="Evaluating UTMOS"
            )
            lang_stats = {}
            for future in pbar:
                idx, path, language_name, result, status = future.result()
                if status == "success":
                    if language_name not in lang_stats:
                        lang_stats[language_name] = []
                    lang_stats[language_name].append(result)
                    scores.append(result)
                    if fout:
                        if language_name == "unknown":
                            fout.write(f"{os.path.basename(path)}\t{result:.2f}\n")
                        else:
                            fout.write(
                                f"{language_name}\t{os.path.basename(path)}\t{result:.2f}\n"
                            )
                else:
                    pbar.write(f"!!! FAILED [File {idx}]: {path} | {result}")

    except (Exception, KeyboardInterrupt) as e:
        logging.critical(
            f"An unrecoverable error occurred: {e}. Terminating all processes."
        )
        detailed_error_info = traceback.format_exc()
        logging.error(f"--- DETAILED TRACEBACK ---\n{detailed_error_info}")
        sys.exit(1)

    print("-" * 50)

    if len(lang_stats) > 1:
        lang_scores = []
        for lang in sorted(lang_stats.keys()):
            l_scores = lang_stats[lang]
            l_avg = np.mean(l_scores)
            lang_scores.append(l_scores)
            l_count = len(l_scores)
            logging.info(f"[{lang}] UTMOS score: {l_avg:.3f} ({l_count} samples)")
            if fout:
                fout.write(f"[{lang}] UTMOS: {l_avg:.3f} ({l_count} samples)\n")
        logging.info(
            f"Macro-average UTMOS over {len(lang_stats)} languages: "
            f"{np.mean([np.mean(ls) for ls in lang_scores]):.3f}"
        )
        if fout:
            fout.write(
                f"\nMacro-average UTMOS over {len(lang_stats)} languages: "
                f"{np.mean([np.mean(ls) for ls in lang_scores]):.3f}\n"
            )

    if scores:
        avg_score = np.mean(scores)
        logging.info(f"Processed {len(scores)}/{len(wav_files)} files.")
        logging.info(f"UTMOS score: {avg_score:.2f}")
        if fout:
            fout.write(f"\nAverage UTMOS: {avg_score:.2f}\n")
    else:
        logging.error("No valid scores computed.")
    print("-" * 50)

    if fout:
        fout.close()


if __name__ == "__main__":
    main()
