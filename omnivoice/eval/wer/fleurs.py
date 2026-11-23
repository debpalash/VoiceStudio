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

"""Computes word error rate (WER) for FLEURS multilingual evaluation.

Uses omnilingual-asr for ASR transcription across 100+ languages.
Requires a separate environment with ``omnilingual_asr`` installed.

Usage:
    python3 omnivoice/eval/wer/fleurs.py \\
        --wav-path results/fleurs \\
        --test-list test.jsonl \\
        --decode-path results/fleurs.wer.log \\
        --model-card omniASR_LLM_Unlimited_7B_v2 \\
        --chunk-size 100 --batch-size 50
"""
import argparse
import logging
import multiprocessing as mp
import os
import re
import sys
import traceback
import types
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import List, Union

import numpy as np
import torch
from tqdm import tqdm

try:
    from omnilingual_asr.models.inference.pipeline import ASRInferencePipeline
    from omnilingual_asr.models.wav2vec2_llama.lang_ids import supported_langs
except ImportError:
    logging.error("Please install omnilingual_asr first.")
    exit(1)

# omnilingual-asr may pull a transformers version that lacks
# HiggsAudioV2TokenizerModel. Pre-register stubs to bypass
# omnivoice/__init__.py heavy imports.
if "omnivoice" not in sys.modules:
    _root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
    for _name in (
        "omnivoice",
        "omnivoice.eval",
        "omnivoice.eval.wer",
        "omnivoice.utils",
    ):
        if _name not in sys.modules:
            _m = types.ModuleType(_name)
            _m.__path__ = [os.path.join(_root, *_name.split(".")[1:])]
            _m.__package__ = _name
            sys.modules[_name] = _m

from omnivoice.eval.wer.common import log_metrics, process_one
from omnivoice.eval.wer.text_norm_omni import text_normalize
from omnivoice.utils.data_utils import read_test_list

# --- Global variables for worker processes ---
worker_pipe = None
worker_device = None


# fix mismatched language codes between OmniVoice and Omnilingual-ASR model
rename = {
    "et": "ekk",
    "ms": "zsm",
    "sw": "swh",
    "npi": "nep",
}


def read_language_mapping_from_tsv(
    mapping_path: Path,
) -> dict[str, Union[str, List[str]]]:
    with open(mapping_path, "r", encoding="utf-8") as f:
        _ = f.readline()  # Skip header
        language_mapping = {}
        for line in f:
            parts = line.strip().split("\t")
            mixed_id, language_name, iso_639_3_id, duration = parts
            language_mapping[iso_639_3_id] = mixed_id
    return language_mapping


iso_639_3_id_to_mixed_id = read_language_mapping_from_tsv(
    Path(f"{os.path.dirname(__file__)}/../../../docs/lang_id_name_map.tsv")
)

mixed_id_to_omnilingual_asr_lang = {}

for lang in supported_langs:
    if lang in ("cmn_Hant",):
        continue
    iso_639_3_lang_code = lang.split("_")[0]
    if iso_639_3_lang_code in iso_639_3_id_to_mixed_id:
        mixed_id = iso_639_3_id_to_mixed_id[iso_639_3_lang_code]
        mixed_id_to_omnilingual_asr_lang[mixed_id] = lang
    else:
        mixed_id_to_omnilingual_asr_lang[iso_639_3_lang_code] = lang


def clean_cjk_spaces(text):
    """
    Removes spaces adjacent to Chinese and Japanese characters while preserving
    meaningful spaces in English or other languages (like Korean).
    """

    # Define CJK (Chinese, Japanese) Unicode ranges
    # \u4e00-\u9fff: CJK Unified Ideographs (Chinese)
    # \u3040-\u309f: Hiragana (Japanese)
    # \u30a0-\u30ff: Katakana (Japanese)
    # \u3000-\u303f: CJK Symbols and Punctuation
    cjk_range = r"\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\u3000-\u303f"

    # 1. Remove spaces between two CJK characters
    # Example: "我 爱 你" -> "我爱你"
    text = re.sub(f"([{cjk_range}])\\s+([{cjk_range}])", r"\1\2", text)

    # 2. Remove spaces between a CJK character and a non-CJK character (English/Numbers)
    # Example: "我 爱 you" -> "我爱you"
    text = re.sub(f"([{cjk_range}])\\s+", r"\1", text)
    text = re.sub(f"\\s+([{cjk_range}])", r"\1", text)

    # 3. Collapse multiple spaces into one for the remaining parts (e.g., English words)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def get_parser():
    parser = argparse.ArgumentParser(
        description="Computes WER with Whisper.",
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
        "--model-card",
        type=str,
        default="omniASR_LLM_7B",
        help="Model card name for OmniASR (e.g., omniASR_LLM_7B) or local path.",
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
        default=None,
        help="""Language code to evaluate (e.g., 'en' for English, 'zh' for Chinese). 
        If not provided, the script will evaluate all languages found in the test list.
        If specified, only samples of the given language will be evaluated.
        """,
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=8,
        help="Batch size for decoding with the Hugging Face pipeline.",
    )
    parser.add_argument(
        "--nj-per-gpu", type=int, default=1, help="Number of workers per GPU."
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=300,
        help="Number of samples per task chunk sent to workers.",
    )
    return parser


def load_omni_model(model_card, device):
    logging.info(f"Loading OmniASR model ({model_card}) on {device}...")
    try:
        pipeline = ASRInferencePipeline(model_card=model_card, device=str(device))
        return pipeline
    except Exception as e:
        logging.error(f"Failed to load OmniASR pipeline: {e}")
        return None


def process_init(rank_queue, model_card):
    """
    Initializer for each worker process.
    """
    global worker_pipe, worker_device

    # Configure threads constraint
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
        # Using the model_card argument
        worker_pipe = load_omni_model(model_card, worker_device)
        if worker_pipe is None:
            raise RuntimeError("Model loading failed.")
    except Exception as e:
        logging.critical(f"Failed to load model on {worker_device}: {e}")
        raise e


def post_process(text: str, lang: str) -> str:
    """
    Cleans and normalizes text for WER calculation.
    Args:
        text (str): The input text to be processed.
        lang (str): The language of the input text.

    Returns:
        str: The cleaned and normalized text.
    """
    lang_id = lang[:3]  # Extract ISO 639-3 code (e.g., 'eng' from 'eng_Latn')
    text = text_normalize(
        text,
        iso_code=lang_id,
        lower_case=True,
        remove_numbers=False,
        remove_brackets=False,
    )
    text = clean_cjk_spaces(text)
    text = text.replace(" ", "|")
    text = " ".join([x for x in text])
    return text


def run_eval_worker(data_chunk, language, batch_size):
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
        # Prepare batch lists for OmniASR
        audio_paths = [item["wav_path"] for item in data_chunk]

        # OmniASR expects explicit language codes for each file if not auto-detected.
        # Using the language passed to the worker function, or item specific language
        # Assuming item['lang_id'] is compatible (e.g., 'en', 'zh', 'arb_Arab')
        # If the model needs full tokens like 'en_Latn', conversion might be needed here depending on input data.
        lang_list = [item.get("lang_id", language) for item in data_chunk]

        # Use the pipeline to infer batch
        # OmniASR pipeline.transcribe returns a list of strings
        transcriptions = worker_pipe.transcribe(
            audio_paths, lang=lang_list, batch_size=batch_size
        )

        for i, hypo_text in enumerate(transcriptions):
            ref_item = data_chunk[i]
            truth = ref_item["truth_text"]
            wav_path = ref_item["wav_path"]
            lang_id = ref_item.get("lang_id")
            lang_name = ref_item.get("lang_name")

            m = process_one(hypo_text, truth, post_process, lang_id)
            m["wav_path"] = wav_path
            m["lang_name"] = lang_name
            metrics_buffer.append(m)

    except Exception:
        logging.error(
            f"Worker failed on chunk (Lang: {language}):\n{traceback.format_exc()}"
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

    # 1. Prepare Data
    logging.info("Reading test list...")
    data_by_lang = defaultdict(list)
    total_files = 0
    wav_root = Path(args.wav_path)

    samples = read_test_list(args.test_list)
    for s in samples:
        wav_path = str(wav_root / f"{s['id']}.{args.extension}")
        if not os.path.exists(wav_path):
            logging.warning(f"File missing: {wav_path}")
            continue

        lang_id = s.get("language_id") or "unknown"
        if lang_id in rename:
            lang_id = mixed_id_to_omnilingual_asr_lang[rename[lang_id]]
        else:
            lang_id = mixed_id_to_omnilingual_asr_lang[lang_id]
        item = {
            "wav_path": wav_path,
            "truth_text": s["text"],
            "lang_id": lang_id,
            "lang_name": s.get("language_name") or "unknown",
        }
        if args.lang and s.get("language_id") != args.lang:
            continue

        data_by_lang[s.get("language_name") or "unknown"].append(item)

        total_files += 1

    logging.info(f"Total files: {total_files} in {len(data_by_lang)} languages.")

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

    # 3. Scheduling: Split languages into chunks
    # This prevents one huge language from blocking a worker for too long,
    # allows better load balancing across the pool.
    tasks = []
    chunk_size = args.chunk_size

    for lang_name, items in data_by_lang.items():
        # Slicing the list into chunks
        for i in range(0, len(items), chunk_size):
            chunk = items[i : i + chunk_size]
            tasks.append({"chunk": chunk, "lang": lang_name})

    logging.info(
        f"Split data into {len(tasks)} chunks (size ~{chunk_size}). Spawning {total_workers} workers."
    )

    # 4. Execution
    results = []

    with ProcessPoolExecutor(
        max_workers=total_workers,
        initializer=process_init,
        initargs=(rank_queue, args.model_card),
    ) as executor:

        futures = []
        for task in tasks:
            futures.append(
                executor.submit(
                    run_eval_worker, task["chunk"], task["lang"], args.batch_size
                )
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

    # 5. Metrics Aggregation
    wers, inses, deles, subses = [], [], [], []
    word_nums = 0

    # Store metrics per language
    lang_stats = {}

    fout = None
    if args.decode_path:
        parent = os.path.dirname(args.decode_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        logging.info(f"Saving detailed WER results to: {args.decode_path}")
        fout = open(args.decode_path, "w", encoding="utf-8")

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
        lang_name = res["lang_name"]

        # Per language stats
        if lang_name not in lang_stats:
            lang_stats[lang_name] = {
                "inses": [],
                "deles": [],
                "subses": [],
                "word_nums": 0,
            }
        lang_stats[lang_name]["inses"].append(float(res["insertions"]))
        lang_stats[lang_name]["deles"].append(float(res["deletions"]))
        lang_stats[lang_name]["subses"].append(float(res["substitutions"]))
        lang_stats[lang_name]["word_nums"] += res["word_num"]

    print("-" * 50)
    # Log per-language stats
    per_lang_wers = []
    for lang in sorted(lang_stats.keys()):
        stats = lang_stats[lang]
        if stats["word_nums"] > 0:
            lang_wer = log_metrics(
                fout,
                f"[{lang}]",
                stats["inses"],
                stats["deles"],
                stats["subses"],
                stats["word_nums"],
            )
            per_lang_wers.append(lang_wer)
            print("-" * 50)

    # Log Macro-average WER
    if len(per_lang_wers) > 1:
        macro_wer = np.mean(per_lang_wers)
        logging.info(
            f"Macro-average WER over {len(per_lang_wers)} languages: {macro_wer:.2f}%"
        )
        if fout:
            fout.write(
                f"Macro-average WER over {len(per_lang_wers)} languages: {macro_wer:.2f}%\n"
            )
        count_le_5 = sum(1 for w in per_lang_wers if w <= 5.0)
        count_le_10 = sum(1 for w in per_lang_wers if w <= 10.0)
        count_le_20 = sum(1 for w in per_lang_wers if w <= 20.0)

        stats_msg = (
            f"Languages with WER/CER <= 5%: {count_le_5}/{len(per_lang_wers)}\n"
            f"Languages with WER/CER <= 10%: {count_le_10}/{len(per_lang_wers)}\n"
            f"Languages with WER/CER <= 20%: {count_le_20}/{len(per_lang_wers)}"
        )

        logging.info("\n" + stats_msg)
        if fout:
            fout.write(stats_msg + "\n")

    # Log overall stats
    if word_nums > 0:
        log_metrics(fout, "Overall", inses, deles, subses, word_nums)

    if fout:
        fout.close()


if __name__ == "__main__":
    main()
