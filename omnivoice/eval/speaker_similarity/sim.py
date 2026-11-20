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
Computes speaker similarity (SIM-o) using a WavLM-based
    ECAPA-TDNN speaker verification model.
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

from omnivoice.eval.models.ecapa_tdnn_wavlm import ECAPA_TDNN_WAVLM
from omnivoice.eval.utils import load_waveform
from omnivoice.utils.data_utils import read_test_list

warnings.filterwarnings("ignore")

# Global variables for workers
worker_model = None
worker_device = None
worker_sr = 16000


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Calculate speaker similarity (SIM-o) score."
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
        "Will use 'tts_eval_models/speaker_similarity/wavlm_large_finetune.pth'"
        "and 'tts_eval_models/speaker_similarity/wavlm_large/' in this script",
    )
    parser.add_argument(
        "--extension",
        type=str,
        default="wav",
        help="Extension of the speech files.",
    )
    parser.add_argument(
        "--decode-path",
        type=str,
        default=None,
        help="Path to the output file where SIM-o information will be saved. "
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
    sv_model_path,
    ssl_model_path,
):
    """Initialize worker process with model and device."""
    global worker_model, worker_device, worker_sr

    torch.set_num_threads(2)

    formatter = "%(asctime)s %(levelname)s [%(filename)s:%(lineno)d] [Worker %(process)d] %(message)s"
    logging.basicConfig(format=formatter, level=logging.INFO, force=True)

    rank = rank_queue.get() if rank_queue else -1

    worker_device = get_device(rank)
    worker_sr = 16000

    logging.debug(f"Initializing SIM-o worker on {worker_device}")
    # Temporarily suppress INFO logs to hide verbose WavLM config
    logging.disable(logging.INFO)

    # Initialize Model
    try:
        worker_model = ECAPA_TDNN_WAVLM(
            feat_dim=1024,
            channels=512,
            emb_dim=256,
            sr=worker_sr,
            ssl_model_path=ssl_model_path,
        )
        state_dict = torch.load(
            sv_model_path, map_location=lambda storage, loc: storage, weights_only=True
        )
        worker_model.load_state_dict(state_dict["model"], strict=False)
        worker_model.to(worker_device)
        worker_model.eval()
    finally:
        # Restore normal logging
        logging.disable(logging.NOTSET)


@torch.no_grad()
def get_embedding(wav_path: str) -> torch.Tensor:
    """Extract embedding for a single file."""
    speech = load_waveform(wav_path, worker_sr, device=worker_device, max_seconds=120)
    return worker_model([speech])


def run_similarity_worker(line_idx, sample, wav_dir, extension):
    """Worker function to process a single pair."""
    try:
        wav_name = sample["id"]
        ref_wav_path = sample["ref_audio"]
        language_name = sample.get("language_name") or "unknown"
        eval_wav_path = os.path.join(wav_dir, f"{wav_name}.{extension}")

        if not os.path.exists(ref_wav_path):
            return line_idx, f"Reference not found: {ref_wav_path}", None, "error"
        if not os.path.exists(eval_wav_path):
            return line_idx, f"Eval wav not found: {eval_wav_path}", None, "error"

        # Compute embeddings pair-wise
        ref_emb = get_embedding(ref_wav_path)
        eval_emb = get_embedding(eval_wav_path)

        # Cosine Similarity
        similarity = torch.nn.functional.cosine_similarity(ref_emb, eval_emb, dim=-1)

        return (
            line_idx,
            (ref_wav_path, eval_wav_path, language_name),
            similarity.item(),
            "success",
        )

    except Exception as e:
        error_detail = f"Error: {str(e)}\nTraceback:\n{traceback.format_exc()}"
        return line_idx, str(sample), error_detail, "error"


def main():
    parser = get_parser()
    args = parser.parse_args()

    # Main process thread setting
    torch.set_num_threads(2)

    mp.set_start_method("spawn", force=True)

    formatter = "%(asctime)s %(levelname)s [%(filename)s:%(lineno)d] %(message)s"
    logging.basicConfig(format=formatter, level=logging.INFO, force=True)

    # Prepare paths
    sv_model_path = os.path.join(
        args.model_dir, "speaker_similarity/wavlm_large_finetune.pth"
    )
    ssl_model_path = os.path.join(args.model_dir, "speaker_similarity/wavlm_large/")

    if not os.path.exists(sv_model_path) or not os.path.exists(ssl_model_path):
        logging.error("Model files not found. Please check --model-dir.")
        sys.exit(1)

    logging.info(f"Calculating SIM-o for {args.wav_path}")
    # Read list
    samples = read_test_list(args.test_list)

    # Setup Parallel Processing
    num_gpus = torch.cuda.device_count()
    assert num_gpus > 0, "No GPU found. GPU is required."
    total_procs = num_gpus * args.nj_per_gpu

    logging.info(
        f"Starting evaluation with {total_procs} processes " f"on {num_gpus} GPUs."
    )

    manager = mp.Manager()
    rank_queue = manager.Queue()

    for rank in list(range(num_gpus)) * args.nj_per_gpu:
        rank_queue.put(rank)

    scores = []

    fout = None
    if args.decode_path:
        os.makedirs(os.path.dirname(args.decode_path), exist_ok=True)
        fout = open(args.decode_path, "w", encoding="utf8")
        logging.info(f"Saving detailed SIM-o results to: {args.decode_path}")
        fout.write("Prompt-path\tEval-path\tSIM-o\n")

    try:
        with ProcessPoolExecutor(
            max_workers=total_procs,
            initializer=worker_init,
            initargs=(
                rank_queue,
                sv_model_path,
                ssl_model_path,
            ),
        ) as executor:
            futures = []
            for i, sample in enumerate(samples):
                futures.append(
                    executor.submit(
                        run_similarity_worker, i, sample, args.wav_path, args.extension
                    )
                )

            pbar = tqdm(
                as_completed(futures), total=len(samples), desc="Evaluating SIM-o"
            )

            lang_stats = {}

            for future in pbar:
                idx, context, result, status = future.result()
                if status == "success":
                    prompt_path, eval_path, lang = context
                    scores.append(result)

                    # Accumulate per-language
                    if lang not in lang_stats:
                        lang_stats[lang] = []
                    lang_stats[lang].append(result)

                    if fout:
                        if lang == "unknown":
                            fout.write(f"{prompt_path}\t{eval_path}\t{result:.2f}\n")
                        else:
                            fout.write(
                                f"{lang}\t{context[0]}\t{context[1]}\t{result:.2f}\n"
                            )
                else:
                    pbar.write(f"!!! FAILED [Line {idx}]: {context} | Error: {result}")

    except (Exception, KeyboardInterrupt) as e:
        logging.critical(
            f"An unrecoverable error occurred: {e}. " f"Terminating all processes."
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
            logging.info(f"[{lang}] SIM-o score: {l_avg:.3f} ({l_count} pairs)")
            if fout:
                fout.write(f"[{lang}] SIM-o: {l_avg:.3f} ({l_count} pairs)\n")
        logging.info(
            f"Macro-average SIM-o over {len(lang_stats)} languages: "
            f"{np.mean([np.mean(ls) for ls in lang_scores]):.3f}"
        )
        if fout:
            fout.write(
                f"\nMacro-average SIM-o over {len(lang_stats)} languages: "
                f"{np.mean([np.mean(ls) for ls in lang_scores]):.3f}\n"
            )

    if scores:
        avg_score = np.mean(scores)
        logging.info(f"Processed {len(scores)}/{len(samples)} pairs.")
        logging.info(f"SIM-o score: {avg_score:.3f}")
        if fout:
            fout.write(f"\nAverage SIM-o: {avg_score:.3f}\n")
    else:
        logging.error("No valid scores computed.")
    if fout:
        fout.close()
    print("-" * 50)


if __name__ == "__main__":
    main()
