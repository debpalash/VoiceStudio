"""Normalize whisper.cpp JSON and merge BPE pieces into timed words."""

from collections.abc import Mapping


def _timestamp(value) -> float:
    if isinstance(value, (float, int)):
        return float(value)
    parts = str(value).strip().replace(',', '.').split(':')
    try:
        result = 0.0
        for part in parts:
            result = result * 60 + float(part)
        return result
    except ValueError:
        return 0.0


def _bounds(item, start=0.0, end=0.0):
    offsets = item.get('offsets') or {}
    stamps = item.get('timestamps') or {}
    return (
        float(offsets['from']) / 1000 if 'from' in offsets else _timestamp(stamps.get('from', start)),
        float(offsets['to']) / 1000 if 'to' in offsets else _timestamp(stamps.get('to', end)),
    )


def _words(tokens, start, end):
    # Older CLIs can split a UTF-8 codepoint across JSON token strings.
    # Keep the complete segment transcript; do not invent corrupted word text.
    if any("\ufffd" in token.get("text", "") for token in tokens):
        return []
    words = []
    probabilities = []
    for token in tokens:
        text = token.get('text', '')
        if not text.strip() or (text.startswith('[_') and text.endswith(']')):
            continue
        begin, finish = _bounds(token, start, end)
        if begin < 0 or finish < begin:
            continue
        probability = float(token.get('p', 1.0))
        if words and not text[0].isspace():
            words[-1]['word'] += text.strip()
            words[-1]['end'] = max(words[-1]['end'], finish)
            probabilities[-1].append(probability)
            words[-1]['probability'] = sum(probabilities[-1]) / len(probabilities[-1])
        else:
            words.append({'word': text.strip(), 'start': begin, 'end': finish, 'probability': probability})
            probabilities.append([probability])
    return words


def parse_transcript(data: dict, word_timestamps: bool) -> dict:
    result = data.get('result')
    language = result.get('language') if isinstance(result, Mapping) else None
    segments, chunks = [], []
    for segment in data.get('transcription') or []:
        text = segment.get('text', '')
        start, end = _bounds(segment)
        words = _words(segment.get('tokens') or [], start, end) if word_timestamps else []
        segments.append({'text': text, 'start': start, 'end': end, 'words': words})
        chunks.append({'text': text, 'timestamp': (start, end)})
    return {
        'text': ' '.join(chunk['text'].strip() for chunk in chunks if chunk['text'].strip()),
        'chunks': chunks,
        'segments': segments,
        'language': language if isinstance(language, str) else None,
    }
