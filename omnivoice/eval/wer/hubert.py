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
Computes word error rate (WER) with Hubert models for LibriSpeech test sets.
"""
import argparse
import logging
import multiprocessing as mp
import os
import re
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import torch
from tqdm import tqdm

from omnivoice.eval.utils import load_waveform
from omnivoice.eval.wer.common import process_one
from omnivoice.utils.data_utils import read_test_list

# --- Global variables for worker processes ---
worker_pipe = None
worker_device = None


def get_parser():
    parser = argparse.ArgumentParser(
        description="Computes WER with Hubert-based ASR model.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--wav-path",
        type=str,
        required=True,
        help="Path to the directory containing speech files.",
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
        help="Path to the output file where WER information will be saved. "
        "If not provided, results are only printed to console.",
    )
    parser.add_argument(
        "--model-dir",
        type=str,
        required=True,
        help="Local path of our evaluation model repository."
        "Download from https://huggingface.co/k2-fsa/TTS_eval_models."
        "Will use 'tts_eval_models/wer/hubert-large-ls960-ft/'"
        " in this script",
    )
    parser.add_argument(
        "--test-list",
        type=str,
        default="transcript.jsonl",
        help="path of the JSONL test list. Each line is a JSON object "
        "with fields: id, text, ref_audio, ref_text, language_id, language_name.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=16,
        help="Batch size for decoding with the Hugging Face pipeline.",
    )
    parser.add_argument(
        "--nj-per-gpu", type=int, default=1, help="Number of workers per GPU."
    )
    return parser


def process_init(rank_queue, model_dir):
    global worker_pipe, worker_device

    torch.set_num_threads(2)

    try:
        rank = rank_queue.get(timeout=10)
    except Exception:
        raise RuntimeError("Failed to get GPU rank from queue.")

    assert torch.cuda.is_available(), "CUDA is required but not available."
    worker_device = torch.device(f"cuda:{rank}")
    torch.cuda.set_device(rank)

    logging.info(f"Initializing worker on device: {worker_device}")

    try:
        worker_pipe = load_hubert_model(model_dir, worker_device)
        if worker_pipe is None:
            raise RuntimeError("Model loading failed.")
    except Exception as e:
        logging.critical(f"Failed to load model on {worker_device}: {e}")
        raise e


def load_hubert_model(model_dir, device):
    model_path = os.path.join(model_dir, "wer/hubert-large-ls960-ft/")
    if not os.path.exists(model_path):
        logging.error(
            f"Hubert model not found at {model_path}. "
            "Please download from https://huggingface.co/k2-fsa/TTS_eval_models"
        )
        return None

    logging.debug(f"Loading Hubert-based ASR model on {device}...")
    import transformers

    # Suppress transformers logging
    transformers.logging.set_verbosity_error()

    pipe = transformers.pipeline(
        "automatic-speech-recognition",
        model=model_path,
        device=device,
        tokenizer=model_path,
    )
    return pipe


def post_process(text: str) -> str:
    """
    Cleans and normalizes text for WER calculation.
    Args:
        text (str): The input text to be processed.

    Returns:
        str: The cleaned and normalized text.
    """
    text = text.replace("‘", "'").replace("’", "'")
    text = re.sub(r"[^a-zA-Z0-9']", " ", text.lower())
    text = re.sub(r"\s+", " ", text).strip()
    return text


def run_eval_worker(data_chunk, batch_size):
    global worker_pipe
    if worker_pipe is None:
        logging.error("Worker pipeline is not initialized!")
        return []

    metrics_buffer = []
    try:
        dataset = [
            {
                "array": load_waveform(
                    item["wav_path"], sample_rate=16000, return_numpy=True
                ),
                "sampling_rate": 16000,
            }
            for item in data_chunk
        ]
        generate_kwargs = {"language": "english", "task": "transcribe"}

        iterator = worker_pipe(
            dataset, generate_kwargs=generate_kwargs, batch_size=batch_size
        )

        for i, out in enumerate(iterator):
            hypothesis = out["text"].strip()
            ref_item = data_chunk[i]
            truth = ref_item["truth_text"]
            wav_path = ref_item["wav_path"]

            m = process_one(hypothesis, truth, post_process)
            m["wav_path"] = wav_path
            metrics_buffer.append(m)

    except Exception:
        logging.error(f"Worker failed on chunk:\n{traceback.format_exc()}")
        return []

    return metrics_buffer


def main():
    parser = get_parser()
    args = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)s [%(filename)s:%(lineno)d] %(message)s",
        level=logging.INFO,
        force=True,
    )

    logging.info(f"Calculating WER for {args.wav_path}")

    data_list = []
    samples = read_test_list(args.test_list)
    for s in samples:
        wav_full_path = str(Path(args.wav_path) / (s["id"] + "." + args.extension))
        if not os.path.exists(wav_full_path):
            logging.warning(f"File missing: {wav_full_path}")
            continue
        data_list.append(
            {
                "wav_path": wav_full_path,
                "truth_text": s["text"],
            }
        )
    total_files = len(data_list)

    num_gpus = torch.cuda.device_count()
    assert num_gpus > 0, "No GPU found. GPU is required."
    total_workers = num_gpus * args.nj_per_gpu

    mp.set_start_method("spawn", force=True)
    manager = mp.Manager()
    rank_queue = manager.Queue()

    for _ in range(args.nj_per_gpu):
        for rank in range(num_gpus):
            rank_queue.put(rank)

    chunk_size = max(1, args.batch_size)
    tasks = [data_list[i : i + chunk_size] for i in range(0, total_files, chunk_size)]

    logging.info(
        f"Split data into {len(tasks)} chunks (size ~{chunk_size}). "
        f"Spawning {total_workers} workers."
    )

    results = []

    with ProcessPoolExecutor(
        max_workers=total_workers,
        initializer=process_init,
        initargs=(rank_queue, args.model_dir),
    ) as executor:

        futures = []
        for chunk in tasks:
            futures.append(executor.submit(run_eval_worker, chunk, args.batch_size))

        with tqdm(total=total_files, desc="Eval Progress", dynamic_ncols=True) as pbar:
            for future in as_completed(futures):
                chunk_metrics = future.result()
                results.extend(chunk_metrics)
                pbar.update(len(chunk_metrics))

    wers, inses, deles, subses = [], [], [], []
    word_nums = 0

    fout = None
    if args.decode_path:
        parent = os.path.dirname(args.decode_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        fout = open(args.decode_path, "w", encoding="utf8")
        logging.info(f"Saving detailed WER results to: {args.decode_path}")
        fout.write(
            "Name\tWER\tTruth\tHypothesis\tInsertions\tDeletions\tSubstitutions\n"
        )

    for res in results:
        wers.append(float(res["wer"]))
        inses.append(float(res["insertions"]))
        deles.append(float(res["deletions"]))
        subses.append(float(res["substitutions"]))
        word_nums += res["word_num"]

        if fout:
            fout.write(
                f"{res['wav_path']}\t{res['wer']}\t{res['truth']}\t"
                f"{res['hypo']}\t{res['insertions']}\t{res['deletions']}\t"
                f"{res['substitutions']}\n"
            )

    wer_weighted = (
        round(
            (np.sum(subses) + np.sum(deles) + np.sum(inses)) / word_nums * 100, 2
        )
        if word_nums > 0
        else float("nan")
    )

    inse_sum = np.sum(inses)
    dele_sum = np.sum(deles)
    subs_sum = np.sum(subses)

    print("-" * 50)
    logging.info(f"Processed {len(results)}/{total_files} files.")
    wer_info = f"WER: {wer_weighted}%"
    detailed_info = (
        f"Errors: {inse_sum} ins, {dele_sum} del, {subs_sum} sub / {word_nums} words"
    )
    logging.info(wer_info)
    logging.info(detailed_info)
    print("-" * 50)

    if fout:
        fout.write(wer_info + "\n" + detailed_info + "\n")
        fout.close()


if __name__ == "__main__":
    main()
