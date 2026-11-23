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
import traceback
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import List, Union

import numpy as np
import torch
import zhconv
from tqdm import tqdm

from omnivoice.eval.utils import load_waveform
from omnivoice.eval.wer.common import log_metrics, process_one
from omnivoice.eval.wer.text_norm_omni import text_normalize
from omnivoice.utils.data_utils import read_test_list

# --- Global variables for worker processes ---
worker_pipe = None
worker_paraformer = None
worker_device = None


def read_language_mapping_from_tsv(
    mapping_path: Path,
) -> dict[str, Union[str, List[str]]]:
    with open(mapping_path, "r", encoding="utf-8") as f:
        _ = f.readline()  # Skip header
        language_mapping = {}
        for line in f:
            parts = line.strip().split("\t")
            mixed_id, language_name, iso_639_3_id, duration = parts
            language_mapping[mixed_id] = iso_639_3_id
    return language_mapping


mixed_id_to_iso_639_3_id = read_language_mapping_from_tsv(
    Path(f"{os.path.dirname(__file__)}/../../../docs/lang_id_name_map.tsv")
)


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
        "--model-dir",
        type=str,
        required=True,
        help="Local path of evaluation models repository. "
        "Download from https://huggingface.co/k2-fsa/TTS_eval_models. ",
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
        default=16,
        help="Batch size for decoding with the Hugging Face pipeline.",
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


def load_whisper_model(model_dir, device):
    model_path = os.path.join(model_dir, "wer/whisper-large-v3/")
    if not os.path.exists(model_path):
        logging.error(f"Whisper model not found at {model_path}.")
        return None

    import transformers

    # Suppress transformers logging
    transformers.logging.set_verbosity_error()

    logging.info(f"Loading Whisper model on {device}...")
    pipe = transformers.pipeline(
        "automatic-speech-recognition",
        model=model_path,
        chunk_length_s=30,
        dtype=torch.float16 if "cuda" in str(device) else torch.float32,
        device=device,
    )
    return pipe


def load_paraformer_model(model_dir, device):
    model_path = os.path.join(model_dir, "wer/paraformer-zh/")
    if not os.path.exists(model_path):
        logging.error(f"Paraformer model not found at {model_path}.")
        return None

    logging.info(f"Loading Paraformer model on {device}...")

    previous_level = logging.root.manager.disable
    logging.disable(logging.CRITICAL)

    try:
        from funasr import AutoModel

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


def _worker_setup(rank_queue):
    """Common worker setup: get rank, configure device and threads."""
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


def process_init(rank_queue, model_dir):
    """Initializer for Whisper worker processes."""
    global worker_pipe

    _worker_setup(rank_queue)

    try:
        worker_pipe = load_whisper_model(model_dir, worker_device)
        if worker_pipe is None:
            raise RuntimeError("Whisper model loading failed.")
    except Exception as e:
        logging.critical(f"Failed to load Whisper model on {worker_device}: {e}")
        raise e


def process_init_paraformer(rank_queue, model_dir):
    """Initializer for Paraformer worker processes (Chinese evaluation)."""
    global worker_paraformer

    _worker_setup(rank_queue)

    try:
        worker_paraformer = load_paraformer_model(model_dir, worker_device)
        if worker_paraformer is None:
            raise RuntimeError("Paraformer model loading failed.")
    except Exception as e:
        logging.critical(f"Failed to load Paraformer model on {worker_device}: {e}")
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
    if lang != "unknown":

        iso_639_3_code = mixed_id_to_iso_639_3_id[lang]
        text = text_normalize(
            text,
            iso_code=iso_639_3_code,
            lower_case=True,
            remove_numbers=False,
            remove_brackets=False,
        )

    if lang in ["zh", "yue"]:
        text = zhconv.convert(text, "zh-cn")

    # Processing spaces for languages using CER (consistent with the practice
    # in paper Minimax-Speech), specifically: zh, yue, ja, ko, th, arb, vi, hi, el.
    if lang in ("zh", "yue", "ja"):
        # For languages where spaces are not semantically meaningful, remove spaces.
        text = text.replace(" ", "")
        text = " ".join([x for x in text])
    elif lang in ("ko", "th", "arb", "vi", "hi", "el"):
        # For languages where spaces are semantically meaningful, replace spaces with |.
        text = text.replace(" ", "|")
        text = " ".join([x for x in text])
    text = text.lower()
    return text.strip()


class SpeechEvalDataset(torch.utils.data.Dataset):
    def __init__(self, data_list):
        self.data_list = data_list

    def __len__(self):
        return len(self.data_list)

    def __getitem__(self, index):
        item = self.data_list[index]
        waveform = load_waveform(item["wav_path"], sample_rate=16000, return_numpy=True)
        return {
            "array": waveform,
            "sampling_rate": 16000,
            "truth_text": item["truth_text"],
        }


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
        dataset = SpeechEvalDataset(data_chunk)
        if language != "unknown":
            generate_kwargs = {"language": language, "task": "transcribe"}
        else:
            generate_kwargs = {"task": "transcribe"}

        # Use the pipeline to infer batch
        # Note: We iterate through the iterator returned by pipe
        iterator = worker_pipe(
            dataset, generate_kwargs=generate_kwargs, batch_size=batch_size
        )

        for i, out in enumerate(iterator):
            hypothesis = out["text"].strip()

            ref_item = data_chunk[i]
            truth = ref_item["truth_text"]
            wav_path = ref_item["wav_path"]
            lang_id = ref_item.get("lang_id")
            lang_name = ref_item.get("lang_name")

            m = process_one(hypothesis, truth, post_process, lang_id)
            m["wav_path"] = wav_path
            m["lang_name"] = lang_name
            metrics_buffer.append(m)

    except Exception:
        logging.error(
            f"Worker failed on chunk (Lang: {language}):\n{traceback.format_exc()}"
        )
        return []

    return metrics_buffer


def run_eval_worker_paraformer(data_chunk, batch_size):
    """
    Worker function for Chinese evaluation using Paraformer.
    Uses the global worker_paraformer initialized by process_init_paraformer.
    """
    global worker_paraformer
    if worker_paraformer is None:
        logging.error("Paraformer worker pipeline is not initialized!")
        return []

    metrics_buffer = []
    try:
        wav_paths = [item["wav_path"] for item in data_chunk]

        for i in range(0, len(wav_paths), batch_size):
            batch_paths = wav_paths[i : i + batch_size]
            res_batch = worker_paraformer.generate(
                input=batch_paths, batch_size=batch_size, disable_pbar=True
            )

            for j, res in enumerate(res_batch):
                hypothesis = res["text"]
                ref_item = data_chunk[i + j]
                truth = ref_item["truth_text"]
                wav_path = ref_item["wav_path"]
                lang_name = ref_item.get("lang_name")

                m = process_one(hypothesis, truth, post_process, "zh")
                m["wav_path"] = wav_path
                m["lang_name"] = lang_name
                metrics_buffer.append(m)

    except Exception:
        logging.error(f"Paraformer worker failed on chunk:\n{traceback.format_exc()}")
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
        lang_name = s.get("language_name") or "unknown"

        item = {
            "wav_path": wav_path,
            "truth_text": s["text"],
            "lang_id": lang_id,
            "lang_name": lang_name,
        }
        if args.lang and s.get("language_id") != args.lang:
            continue

        data_by_lang[lang_name].append(item)
        total_files += 1

    logging.info(f"Total files: {total_files} in {len(data_by_lang)} languages.")

    # 2. Worker config
    num_gpus = torch.cuda.device_count()
    assert num_gpus > 0, "No GPU found. GPU is required."
    total_workers = num_gpus * args.nj_per_gpu

    mp.set_start_method("spawn", force=True)
    manager = mp.Manager()

    # 3. Scheduling: Split data into Chinese (Paraformer) and non-Chinese (Whisper)
    zh_items = []
    non_zh_items = []
    for lang_name, items in data_by_lang.items():
        lang_id = items[0].get("lang_id", "") if items else ""
        if lang_name == "Chinese" or (lang_id and lang_id.startswith("zh")):
            zh_items.extend(items)
        else:
            non_zh_items.extend(items)

    chunk_size = args.chunk_size

    whisper_tasks = []
    for i in range(0, len(non_zh_items), chunk_size):
        chunk = non_zh_items[i : i + chunk_size]
        lang_name = chunk[0].get("lang_name", "unknown")
        whisper_tasks.append({"chunk": chunk, "lang": lang_name})

    paraformer_tasks = []
    for i in range(0, len(zh_items), chunk_size):
        paraformer_tasks.append(zh_items[i : i + chunk_size])

    logging.info(
        f"Whisper tasks: {len(whisper_tasks)} chunks ({len(non_zh_items)} files). "
        f"Paraformer tasks: {len(paraformer_tasks)} chunks ({len(zh_items)} files). "
        f"Spawning {total_workers} workers per pool."
    )

    # 4. Execution — run Whisper and Paraformer pools sequentially
    results = []

    # 4a. Whisper pool for non-Chinese languages
    if whisper_tasks:
        whisper_rank_queue = manager.Queue()
        for _ in range(args.nj_per_gpu):
            for rank in range(num_gpus):
                whisper_rank_queue.put(rank)

        with ProcessPoolExecutor(
            max_workers=total_workers,
            initializer=process_init,
            initargs=(whisper_rank_queue, args.model_dir),
        ) as executor:

            futures = []
            for task in whisper_tasks:
                futures.append(
                    executor.submit(
                        run_eval_worker, task["chunk"], task["lang"], args.batch_size
                    )
                )

            with tqdm(
                total=len(non_zh_items),
                desc="Whisper Eval",
                dynamic_ncols=True,
            ) as pbar:
                for future in as_completed(futures):
                    try:
                        chunk_metrics = future.result()
                        results.extend(chunk_metrics)
                        pbar.update(len(chunk_metrics))
                    except Exception as e:
                        logging.error(f"Whisper task failed: {e}")

    # 4b. Paraformer pool for Chinese
    if paraformer_tasks:
        para_rank_queue = manager.Queue()
        for _ in range(args.nj_per_gpu):
            for rank in range(num_gpus):
                para_rank_queue.put(rank)

        with ProcessPoolExecutor(
            max_workers=total_workers,
            initializer=process_init_paraformer,
            initargs=(para_rank_queue, args.model_dir),
        ) as executor:

            futures = []
            for chunk in paraformer_tasks:
                futures.append(
                    executor.submit(run_eval_worker_paraformer, chunk, args.batch_size)
                )

            with tqdm(
                total=len(zh_items),
                desc="Paraformer Eval",
                dynamic_ncols=True,
            ) as pbar:
                for future in as_completed(futures):
                    try:
                        chunk_metrics = future.result()
                        results.extend(chunk_metrics)
                        pbar.update(len(chunk_metrics))
                    except Exception as e:
                        logging.error(f"Paraformer task failed: {e}")

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
                ndigits=3,
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

    # Log overall stats
    if word_nums > 0:
        log_metrics(fout, "Overall", inses, deles, subses, word_nums)

    if fout:
        fout.close()


if __name__ == "__main__":
    main()
