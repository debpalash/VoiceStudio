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

"""Text processing utilities for TTS inference.

Provides:
- ``chunk_text_punctuation()``: Splits long text into model-friendly chunks at
  sentence boundaries, with abbreviation-aware punctuation splitting.
- ``add_punctuation()``: Appends missing end punctuation (Chinese or English).
"""

import re
from typing import List, Optional, Tuple


SPLIT_PUNCTUATION = set(".,;:!?。，；：！？")
CLOSING_MARKS = set("\"'""'）]》》>」】")

END_PUNCTUATION = {
    ";",
    ":",
    ",",
    ".",
    "!",
    "?",
    "…",
    ")",
    "]",
    "}",
    '"',
    "'",
    """,
    "'",
    "；",
    "：",
    "，",
    "。",
    "！",
    "？",
    "、",
    "……",
    "）",
    "】",
    """,
    "'",
}


ABBREVIATIONS = {
    "Mr.",
    "Mrs.",
    "Ms.",
    "Dr.",
    "Prof.",
    "Sr.",
    "Jr.",
    "Rev.",
    "Fr.",
    "Hon.",
    "Pres.",
    "Gov.",
    "Capt.",
    "Gen.",
    "Sen.",
    "Rep.",
    "Col.",
    "Maj.",
    "Lt.",
    "Cmdr.",
    "Sgt.",
    "Cpl.",
    "Co.",
    "Corp.",
    "Inc.",
    "Ltd.",
    "Est.",
    "Dept.",
    "St.",
    "Ave.",
    "Blvd.",
    "Rd.",
    "Mt.",
    "Ft.",
    "No.",
    "Jan.",
    "Feb.",
    "Mar.",
    "Apr.",
    "Aug.",
    "Sep.",
    "Sept.",
    "Oct.",
    "Nov.",
    "Dec.",
    "i.e.",
    "e.g.",
    "vs.",
    "Vs.",
    "Etc.",
    "approx.",
    "fig.",
    "def.",
}


def chunk_text_punctuation(
    text: str,
    chunk_len: int,
    min_chunk_len: Optional[int] = None,
) -> List[str]:
    """
    Splits the input tokens list into chunks according to punctuations,
    avoiding splits on common abbreviations (e.g., Mr., No.).
    """

    # 1. Split the tokens according to punctuations.
    sentences = []
    current_sentence = []

    tokens_list = list(text)

    for token in tokens_list:
        # If the first token of current sentence is punctuation,
        # append it to the end of the previous sentence.
        if (
            len(current_sentence) == 0
            and len(sentences) != 0
            and (token in SPLIT_PUNCTUATION or token in CLOSING_MARKS)
        ):
            sentences[-1].append(token)
        # Otherwise, append the current token to the current sentence.
        else:
            current_sentence.append(token)

            # Split the sentence in positions of punctuations.
            if token in SPLIT_PUNCTUATION:
                is_abbreviation = False

                if token == ".":
                    temp_str = "".join(current_sentence).strip()
                    if temp_str:
                        last_word = temp_str.split()[-1]
                        if last_word in ABBREVIATIONS:
                            is_abbreviation = True

                if not is_abbreviation:
                    sentences.append(current_sentence)
                    current_sentence = []
    # Assume the last few tokens are also a sentence
    if len(current_sentence) != 0:
        sentences.append(current_sentence)

    # 2. Merge short sentences.
    merged_chunks = []
    current_chunk = []
    for sentence in sentences:
        if len(current_chunk) + len(sentence) <= chunk_len:
            current_chunk.extend(sentence)
        else:
            if len(current_chunk) > 0:
                merged_chunks.append(current_chunk)
            current_chunk = sentence

    if len(current_chunk) > 0:
        merged_chunks.append(current_chunk)

    # 4. Post-process: Check for undersized chunks and merge them
    #  with the previous chunk or next chunk (if it's the first chunk).
    if min_chunk_len is not None:
        first_chunk_short_flag = (
            len(merged_chunks) > 0 and len(merged_chunks[0]) < min_chunk_len
        )
        final_chunks = []
        for i, chunk in enumerate(merged_chunks):
            if i == 1 and first_chunk_short_flag:
                final_chunks[-1].extend(chunk)
            else:
                if len(chunk) >= min_chunk_len:
                    final_chunks.append(chunk)
                else:
                    if len(final_chunks) == 0:
                        final_chunks.append(chunk)
                    else:
                        final_chunks[-1].extend(chunk)
    else:
        final_chunks = merged_chunks

    chunk_strings = [
        "".join(chunk).strip() for chunk in final_chunks if "".join(chunk).strip()
    ]
    return chunk_strings


def add_punctuation(text: str):
    """Add punctuation if there is not in the end of text"""
    text = text.strip()

    if not text:
        return text

    if text[-1] not in END_PUNCTUATION:
        is_chinese = any("\u4e00" <= char <= "\u9fff" for char in text)

        text += "。" if is_chinese else "."

    return text


# Inline pause marker (issue #276): `[pause]`, `[pause 500ms]`, `[pause 1s]`,
# `[pause 1.5s]`. Case-insensitive; whitespace around the number is tolerated.
# A bare `[pause]` uses PAUSE_DEFAULT_MS.
PAUSE_DEFAULT_MS = 350
PAUSE_MAX_MS = 10_000
# The numeric spec is an atomic group ``(?>…)`` so the engine can't backtrack
# its leading ``\s+`` against the trailing ``\s*`` — that overlap made the
# pattern polynomial-time on adversarial whitespace (ReDoS). Atomic groups are
# behavior-preserving here (no valid ``[pause …]`` needs to backtrack into the
# spec) and require Python ≥3.11, which the project already mandates.
_PAUSE_RE = re.compile(
    r"\[\s*pause(?>\s+(\d+(?:\.\d+)?)\s*(ms|s)?)?\s*\]",
    re.IGNORECASE,
)


def _pause_ms(num, unit):
    """Resolve a parsed (number, unit) pair to a clamped millisecond value."""
    if num is None:
        return PAUSE_DEFAULT_MS
    try:
        value = float(num)
    except ValueError:
        return PAUSE_DEFAULT_MS
    # Bare number or explicit "ms" -> milliseconds; "s" -> seconds.
    ms = value * 1000.0 if (unit and unit.lower() == "s") else value
    # Clamp before integer conversion: valid decimal markers can overflow
    # float parsing or the seconds-to-milliseconds multiplication.
    ms_int = int(round(min(ms, PAUSE_MAX_MS)))
    return max(0, min(ms_int, PAUSE_MAX_MS))


def parse_pause_markers(text):
    """Split ``text`` on inline ``[pause ...]`` markers (issue #276).

    Returns a list of ``(span_text, pause_ms_after)`` tuples, in order, where
    ``pause_ms_after`` is the silence (in milliseconds) to insert AFTER that
    span's synthesized audio. Guarantees:

    - With no markers: ``[(text, 0)]`` -- the original text, no pause.
    - Concatenating every ``span_text`` (markers removed) reproduces the input
      minus the markers.
    - A leading marker yields a first tuple with empty ``span_text`` and the
      pause (rendered as leading silence, no audio).
    - Consecutive markers sum their durations (clamped to ``PAUSE_MAX_MS``).

    The caller synthesizes each non-empty ``span_text`` as usual and stitches a
    silence buffer of the given length between spans -- no model changes needed.
    """
    if not text or "[" not in text:
        return [(text, 0)]

    segments = []
    last = 0
    pending_text = ""
    for m in _PAUSE_RE.finditer(text):
        pending_text += text[last:m.start()]
        last = m.end()
        pause = _pause_ms(m.group(1), m.group(2))
        # When two markers are adjacent (no text between), merge the silence
        # onto the previous segment instead of emitting an empty span.
        if pending_text == "" and segments:
            prev_text, prev_pause = segments[-1]
            segments[-1] = (prev_text, min(prev_pause + pause, PAUSE_MAX_MS))
        else:
            segments.append((pending_text, pause))
        pending_text = ""

    tail = pending_text + text[last:]
    if tail or not segments:
        segments.append((tail, 0))

    return segments
