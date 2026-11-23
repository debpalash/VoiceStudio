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
Computes Character Error Rate (CER) for Cantonese (yue) using SenseVoiceSmall.
"""

import argparse
import logging
import multiprocessing as mp
import os
import re
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import cn2an
import torch
import zhconv
from tqdm import tqdm

from omnivoice.eval.wer.common import log_metrics, process_one
from omnivoice.eval.wer.text_norm_omni import text_normalize
from omnivoice.utils.data_utils import read_test_list

# --- Global variables for worker processes ---
worker_sensevoice = None
worker_device = None


def get_parser():
    parser = argparse.ArgumentParser(
        description="Computes CER for Cantonese using SenseVoiceSmall.",
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
        help="Path to the output file where CER information will be saved. ",
    )
    parser.add_argument(
        "--model-dir",
        type=str,
        required=True,
        help="Local path of evaluation models repository. ",
    )
    parser.add_argument(
        "--test-list",
        type=str,
        default="test.jsonl",
        help="path of the JSONL test list.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=16,
        help="Batch size for decoding.",
    )
    parser.add_argument(
        "--nj-per-gpu", type=int, default=1, help="Number of workers per GPU."
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=10,
        help="Number of samples per task chunk sent to workers.",
    )
    return parser


def load_sensevoice_model(model_dir, device):
    model_path = os.path.join(model_dir, "wer/SenseVoiceSmall")
    if not os.path.exists(model_path):
        # Fallback if specific sensevoice spelling isn't found
        logging.warning(
            f"SenseVoiceSmall not found at {model_path}. "
            f"Please ensure it is present in eval models."
        )

    logging.info(f"Loading SenseVoice model on {device}...")

    previous_level = logging.root.manager.disable
    logging.disable(logging.CRITICAL)

    try:
        from funasr import AutoModel

        model = AutoModel(
            model="iic/SenseVoiceSmall",
            device=str(device),
            disable_update=True,
            disable_pbar=True,
            verbose=False,
        )
    finally:
        logging.disable(previous_level)

    return model


def _worker_setup(rank_queue):
    global worker_device

    torch.set_num_threads(2)

    try:
        rank = rank_queue.get(timeout=10)
    except Exception:
        raise RuntimeError("Failed to get GPU rank from queue.")

    assert torch.cuda.is_available(), "CUDA is required but not available."
    worker_device = torch.device(f"cuda:{rank}")
    torch.cuda.set_device(rank)

    logging.info(f"Initializing worker on device: {worker_device}")


def process_init_sensevoice(rank_queue, model_dir):
    global worker_sensevoice

    _worker_setup(rank_queue)

    try:
        worker_sensevoice = load_sensevoice_model(model_dir, worker_device)
        if worker_sensevoice is None:
            raise RuntimeError("SenseVoice model loading failed.")
    except Exception as e:
        logging.critical(f"Failed to load SenseVoice model on {worker_device}: {e}")
        raise e


def post_process(text: str, lang: str) -> str:
    """
    Cleans and normalizes text for calculation.
    """
    assert lang == "yue", "this script is designed for Cantonese (yue) evaluation only."
    text = text_normalize(
        text,
        iso_code="yue",
        lower_case=True,
        remove_numbers=False,
        remove_brackets=False,
    )

    text = zhconv.convert(text, "zh-cn")

    text = cn2an.transform(text, "an2cn")

    text = text.replace(" ", "")
    text = " ".join([x for x in text])
    text = text.lower()
    return text.strip()


def run_eval_worker_sensevoice(data_chunk, batch_size):
    global worker_sensevoice
    if worker_sensevoice is None:
        logging.error("SenseVoice worker pipeline is not initialized!")
        return []

    metrics_buffer = []
    try:
        wav_paths = [item["wav_path"] for item in data_chunk]

        for i in range(0, len(wav_paths), batch_size):
            batch_paths = wav_paths[i : i + batch_size]

            # SenseVoice generate call, target lang mapped to yue
            res_batch = worker_sensevoice.generate(
                input=batch_paths,
                batch_size=batch_size,
                language="yue",
                use_itn=False,
                disable_pbar=True,
            )

            for j, res in enumerate(res_batch):
                hypothesis = res["text"]
                # SenseVoice may format output with language tags,
                # cleaning basic tags if any
                hypothesis = re.sub(r"<\|[^|]*\|>", "", hypothesis).strip()

                ref_item = data_chunk[i + j]
                truth = ref_item["truth_text"]
                wav_path = ref_item["wav_path"]
                lang_name = ref_item.get("lang_name")

                m = process_one(hypothesis, truth, post_process, "yue")
                m["wav_path"] = wav_path
                m["lang_name"] = lang_name
                metrics_buffer.append(m)

    except Exception:
        logging.error(f"SenseVoice worker failed on chunk:\n{traceback.format_exc()}")
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

    logging.info("Reading test list and filtering for Cantonese (yue)...")
    yue_items = []
    wav_root = Path(args.wav_path)

    samples = read_test_list(args.test_list)
    for s in samples:
        lang_id = s.get("language_id", "")
        if lang_id != "yue":
            continue

        wav_path = str(wav_root / f"{s['id']}.{args.extension}")
        if not os.path.exists(wav_path):
            logging.warning(f"File missing: {wav_path}")
            continue

        yue_items.append(
            {
                "wav_path": wav_path,
                "truth_text": s["text"],
                "lang_id": "yue",
                "lang_name": s.get("language_name", "Cantonese"),
            }
        )

    logging.info(f"Total Cantonese files found: {len(yue_items)}.")
    if len(yue_items) == 0:
        logging.warning("No files to evaluate. Exiting.")
        return

    num_gpus = torch.cuda.device_count()
    assert num_gpus > 0, "No GPU found. GPU is required."
    total_workers = num_gpus * args.nj_per_gpu

    mp.set_start_method("spawn", force=True)
    manager = mp.Manager()

    chunk_size = args.chunk_size
    tasks = []
    for i in range(0, len(yue_items), chunk_size):
        tasks.append(yue_items[i : i + chunk_size])

    results = []
    rank_queue = manager.Queue()
    for _ in range(args.nj_per_gpu):
        for rank in range(num_gpus):
            rank_queue.put(rank)

    with ProcessPoolExecutor(
        max_workers=total_workers,
        initializer=process_init_sensevoice,
        initargs=(rank_queue, args.model_dir),
    ) as executor:

        futures = []
        for chunk in tasks:
            futures.append(
                executor.submit(run_eval_worker_sensevoice, chunk, args.batch_size)
            )

        with tqdm(
            total=len(yue_items),
            desc="SenseVoice Eval (Cantonese)",
            dynamic_ncols=True,
        ) as pbar:
            for future in as_completed(futures):
                try:
                    chunk_metrics = future.result()
                    results.extend(chunk_metrics)
                    pbar.update(len(chunk_metrics))
                except Exception as e:
                    logging.error(f"Task failed: {e}")

    # Metrics Aggregation
    inses, deles, subses = [], [], []
    word_nums = 0

    fout = None
    if args.decode_path:
        parent = os.path.dirname(args.decode_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        logging.info(f"Saving detailed CER results to: {args.decode_path}")
        fout = open(args.decode_path, "w", encoding="utf-8")

    for res in results:
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

    print("-" * 50)
    if word_nums > 0:
        log_metrics(fout, "[yue] Cantonese", inses, deles, subses, word_nums)

    if fout:
        fout.close()


if __name__ == "__main__":
    main()
