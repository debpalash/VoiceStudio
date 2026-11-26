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
Computes word error rate (WER) with Whisper-large-v3 for English and
Paraformer for Chinese. Intended to evaluate WERs on Seed-TTS test sets.
"""
import argparse
import logging
import multiprocessing as mp
import os
import string
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import torch
import zhconv
from tqdm import tqdm
from zhon.hanzi import punctuation

from omnivoice.eval.utils import load_waveform
from omnivoice.eval.wer.common import process_one
from omnivoice.utils.data_utils import read_test_list

# --- Global variables for worker processes ---
worker_pipe = None
worker_device = None


def get_parser():
    parser = argparse.ArgumentParser(
        description="Computes WER with Whisper/Paraformer.",
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
        help="Local path of evaluation models repository. "
        "Download from https://huggingface.co/k2-fsa/TTS_eval_models. "
        "This script expects 'tts_eval_models/wer/whisper-large-v3/' for English "
        "and 'tts_eval_models/wer/paraformer-zh/' for Chinese within this directory.",
    )
    parser.add_argument(
        "--test-list",
        type=str,
        default="test.jsonl",
        help="path of the JSONL test list. Each line is a JSON object "
        "with fields: id, text, ref_audio, ref_text, language_id, language_name.",
    )
    parser.add_argument(
        "--lang",
        type=str,
        choices=["zh", "en"],
        required=True,
        help="Language of the audio and transcripts for "
        "decoding ('zh' for Chinese or 'en' for English).",
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


def load_whisper_model(model_dir, device):
    model_path = os.path.join(model_dir, "wer/whisper-large-v3/")
    if not os.path.exists(model_path):
        logging.error(f"Whisper model not found at {model_path}.")
        return None

    logging.debug(f"Loading Whisper model on {device}...")

    import transformers

    # Suppress transformers logging
    transformers.logging.set_verbosity_error()

    pipe = transformers.pipeline(
        "automatic-speech-recognition",
        model=model_path,
        dtype=torch.float16 if "cuda" in str(device) else torch.float32,
        device=device,
    )
    return pipe


def load_paraformer_model(model_dir, device):
    model_path = os.path.join(model_dir, "wer/paraformer-zh/")
    if not os.path.exists(model_path):
        logging.error(f"Paraformer model not found at {model_path}.")
        return None

    logging.debug(f"Loading Paraformer model on {device}...")

    previous_level = logging.root.manager.disable
    logging.disable(logging.CRITICAL)

    try:
        from funasr import AutoModel

        # FunASR AutoModel accepts "cuda:0" string or torch.device
        model = AutoModel(
            model=model_path,
            device=str(device),
            disable_update=True,
            disable_pbar=True,
            verbose=False,
        )
    finally:
        logging.disable(previous_level)

    return model


def post_process(text: str, lang: str) -> str:
    """
    Cleans and normalizes text for WER calculation.
    Args:
        text (str): The input text to be processed.
        lang (str): The language of the input text.

    Returns:
        str: The cleaned and normalized text.
    """
    punctuation_all = punctuation + string.punctuation
    for x in punctuation_all:
        if x == "'":
            continue
        text = text.replace(x, "")

    text = text.replace("  ", " ")

    if lang == "zh":
        text = " ".join([x for x in text])
    elif lang == "en":
        text = text.lower()
    else:
        raise NotImplementedError
    return text


def process_init(rank_queue, model_dir, lang):
    """
    Initializer for each worker process.
    Loads model onto a specific GPU, once per process.
    """
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
        if lang == "en":
            worker_pipe = load_whisper_model(model_dir, worker_device)
        elif lang == "zh":
            worker_pipe = load_paraformer_model(model_dir, worker_device)
        if worker_pipe is None:
            raise RuntimeError("Model loading failed.")
    except Exception as e:
        logging.critical(f"Failed to load model on {worker_device}: {e}")
        raise e


def run_eval_worker(data_chunk, lang, batch_size):
    """
    Worker function to process a chunk of data.
    Uses the global worker_pipe initialized by process_init.
    """
    global worker_pipe
    if worker_pipe is None:
        logging.error("Worker pipeline is not initialized!")
        return []

    metrics_buffer = []
    try:
        if lang == "en":
            # Load waveforms as arrays, truncating to 30s
            dataset = [
                {
                    "array": load_waveform(
                        item["wav_path"], sample_rate=16000, return_numpy=True
                    )[: 16000 * 30],
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

                m = process_one(hypothesis, truth, post_process, lang)
                m["wav_path"] = wav_path
                metrics_buffer.append(m)

        elif lang == "zh":
            wav_paths = [item["wav_path"] for item in data_chunk]

            for i in range(0, len(wav_paths), batch_size):
                batch_paths = wav_paths[i : i + batch_size]
                res_batch = worker_pipe.generate(
                    input=batch_paths, batch_size=batch_size, disable_pbar=True
                )

                for j, res in enumerate(res_batch):
                    hypothesis = zhconv.convert(res["text"], "zh-cn")
                    ref_item = data_chunk[i + j]
                    truth = ref_item["truth_text"]
                    wav_path = ref_item["wav_path"]

                    m = process_one(hypothesis, truth, post_process, lang)
                    m["wav_path"] = wav_path
                    metrics_buffer.append(m)

    except Exception:
        logging.error(
            f"Worker failed on chunk (Lang: {lang}):\n{traceback.format_exc()}"
        )
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

    # 1. Prepare Data
    logging.info("Reading test list...")
    data_list = []
    samples = read_test_list(args.test_list)
    for s in samples:
        wav_path = str(Path(args.wav_path) / f"{s['id']}.{args.extension}")
        if not os.path.exists(wav_path):
            logging.warning(f"File missing: {wav_path}")
            continue
        data_list.append({"wav_path": wav_path, "truth_text": s["text"]})
    total_files = len(data_list)
    logging.info(f"Total files: {total_files}.")

    # 2. Worker config
    num_gpus = torch.cuda.device_count()
    assert num_gpus > 0, "No GPU found. GPU is required."
    total_workers = num_gpus * args.nj_per_gpu

    mp.set_start_method("spawn", force=True)
    manager = mp.Manager()
    rank_queue = manager.Queue()

    for _ in range(args.nj_per_gpu):
        for rank in range(num_gpus):
            rank_queue.put(rank)

    # 3. Scheduling: Split data into chunks for better load balancing
    chunk_size = max(1, args.batch_size)
    tasks = []
    for i in range(0, total_files, chunk_size):
        tasks.append(data_list[i : i + chunk_size])

    logging.info(
        f"Split data into {len(tasks)} chunks (size ~{chunk_size}). "
        f"Spawning {total_workers} workers."
    )

    # 4. Execution
    results = []

    with ProcessPoolExecutor(
        max_workers=total_workers,
        initializer=process_init,
        initargs=(rank_queue, args.model_dir, args.lang),
    ) as executor:

        futures = []
        for chunk in tasks:
            futures.append(
                executor.submit(run_eval_worker, chunk, args.lang, args.batch_size)
            )

        # Unified progress bar
        with tqdm(total=total_files, desc="Eval Progress", dynamic_ncols=True) as pbar:
            for future in as_completed(futures):
                try:
                    chunk_metrics = future.result()
                    results.extend(chunk_metrics)
                    pbar.update(len(chunk_metrics))
                except Exception as e:
                    logging.error(f"Task failed: {e}")

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

    wer_avg = round(np.mean(wers) * 100, 2) if wers else float("nan")
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
    seedtts_wer_info = f"Seed-TTS WER (Avg of WERs): {wer_avg}%"
    wer_info = f"WER (Weighted): {wer_weighted}%"
    detailed_info = (
        f"Errors: {inse_sum} ins, {dele_sum} del, {subs_sum} sub / {word_nums} words"
    )
    logging.info(seedtts_wer_info)
    logging.info(wer_info)
    logging.info(detailed_info)
    print("-" * 50)

    if fout:
        fout.write(seedtts_wer_info + "\n" + wer_info + "\n" + detailed_info + "\n")
        fout.close()


if __name__ == "__main__":
    main()
