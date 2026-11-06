"""Readable terminal output for any command result, chosen by the data's shape.

No command needs its own formatting code: text prints as is, a result with a
``message`` prints the message, flat objects print as aligned fields, lists of
objects print as tables, an object wrapping one list prints the table plus its
summary fields, and nested objects print as indented sections. ``--json`` skips
all of this and prints the data unchanged.

When stdout is not a terminal, tables print as tab-separated rows without a
header or truncation, so ``voicestudio voices list | cut -f2`` works.
"""
from datetime import datetime
import shutil

# Friendlier names for fields whose raw name reads like an implementation detail.
LABELS = {'method': 'Installed as', 'executable': 'Location', 'arch': 'Architecture',
          'automatic': 'Updates from the app', 'instructions': 'How to update'}
ACRONYMS = {'url': 'URL', 'id': 'ID', 'api': 'API', 'hf': 'HF', 'mcp': 'MCP', 'gpu': 'GPU',
            'cpu': 'CPU', 'asr': 'ASR', 'tts': 'TTS', 'llm': 'LLM', 'vram': 'VRAM', 'ram': 'RAM'}
# Identifying fields lead a table; everything else follows in the order the data gives.
PRIORITY = ['id', 'name', 'title', 'label', 'status', 'state', 'language', 'lang', 'engine',
            'backend_id', 'model_id', 'repo_id', 'type', 'kind', 'family', 'installed', 'active',
            'size_bytes', 'duration', 'created_at', 'updated_at']
# A unit suffix in a field name moves to its value: disk_free_gb=71.8 -> "Disk free: 71.8 GB".
UNITS = {'_gb': 'GB', '_mb': 'MB', '_kb': 'KB', '_ms': 'ms', '_seconds': 's', '_secs': 's', '_s': 's',
         '_pct': '%', '_percent': '%'}
MAX_CELL = 40
INDENT = '  '


def is_scalar(value):
    return value is None or isinstance(value, (str, int, float, bool))


def label(key):
    if key in LABELS:
        return LABELS[key]
    key = str(key)
    for suffix in UNITS:
        if key.endswith(suffix) and key != suffix:
            key = key[:-len(suffix)]  # the unit moves to the value
            break
    else:
        if key.endswith('_bytes') and key != '_bytes':
            key = key[:-6]  # the value is already shown as a size
    words = [ACRONYMS.get(word.lower(), word) for word in str(key).replace('_', ' ').replace('-', ' ').split()]
    text = ' '.join(words)
    return text[:1].upper() + text[1:]


def _size(number):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if abs(number) < 1024 or unit == 'TB':
            return f'{number:.0f} {unit}' if unit == 'B' else f'{number:.1f} {unit}'
        number /= 1024


def value_text(key, value):
    """One value as text; meaning comes from the field name, never the command."""
    if value is True:
        return 'yes'
    if value is False:
        return 'no'
    if value is None or value == '':
        return '—'
    name = str(key).lower()
    if isinstance(value, (int, float)):
        if name.endswith('_at') or name in {'timestamp', 'created', 'updated', 'modified', 'mtime'}:
            seconds = value / 1000 if value > 1e12 else value
            if 1e8 < seconds < 1e10:  # a plausible epoch, not a counter
                return datetime.fromtimestamp(seconds).strftime('%Y-%m-%d %H:%M')
        if name.endswith('_bytes') or name in {'bytes', 'size'}:
            return _size(value)
        for suffix, unit in UNITS.items():
            if name.endswith(suffix) and name != suffix:
                number = f'{value:.2f}'.rstrip('0').rstrip('.') if isinstance(value, float) else str(value)
                return number + ('' if unit == '%' else ' ') + unit
        if isinstance(value, float):
            return f'{value:.2f}'.rstrip('0').rstrip('.')
        return str(value)
    if isinstance(value, list):
        if all(is_scalar(item) for item in value) and len(', '.join(map(str, value))) <= MAX_CELL:
            return ', '.join(value_text(key, item) for item in value) or '—'
        return f'{len(value)} item' + ('' if len(value) == 1 else 's')
    if isinstance(value, dict):
        return f'{len(value)} field' + ('' if len(value) == 1 else 's')
    return str(value).replace('\n', ' ')


def _clip(text, limit):
    return text if len(text) <= limit else text[:max(1, limit - 1)] + '…'


def _columns(rows):
    seen = []
    for row in rows:
        for key in row:
            if key not in seen:
                seen.append(key)
    scalar = [key for key in seen if any(is_scalar(row.get(key)) and row.get(key) is not None for row in rows)]
    nested = [key for key in seen if key not in scalar and not str(key).startswith('_')]
    ranked = [key for key in PRIORITY if key in scalar]
    return ranked + [key for key in scalar if key not in ranked] + nested


def table(rows, *, tty, width):
    if not rows:
        return 'No results.' if tty else ''
    columns = _columns(rows)
    cells = [[value_text(key, row.get(key)) for key in columns] for row in rows]
    if not tty:
        # Scripts get every column untruncated: empty stays empty, nested stays JSON.
        import json

        def raw(key, value):
            if value is None or value == '':
                return ''
            if isinstance(value, (list, dict)):
                return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
            return value_text(key, value)
        return '\n'.join('\t'.join(raw(key, row.get(key)).replace('\t', ' ').replace('\n', ' ')
                                    for key in columns) for row in rows)
    headers = [label(key).upper() for key in columns]
    widths = [min(MAX_CELL, max(len(headers[i]), *(len(line[i]) for line in cells))) for i in range(len(columns))]
    # Keep the leading (most identifying) columns that fit the terminal.
    keep = len(columns)
    while keep > 1 and sum(widths[:keep]) + 2 * (keep - 1) > width:
        keep -= 1
    if keep == 1:
        widths[0] = max(4, min(widths[0], width))
    lines = [headers[:keep], *(line[:keep] for line in cells)]
    return '\n'.join('  '.join(_clip(text, widths[i]).ljust(widths[i]) for i, text in enumerate(line)).rstrip()
                     for line in lines)


def _fields(mapping, *, tty, width, depth):
    """Aligned scalar fields first, then each nested field as an indented section."""
    scalars = {key: value for key, value in mapping.items()
               if not str(key).startswith('_') and (is_scalar(value) or (
                   isinstance(value, list) and all(is_scalar(item) for item in value)
                   and len(value_text(key, value)) <= MAX_CELL))}
    blocks = []
    if scalars:
        pad = max(len(label(key)) for key in scalars) + 1
        blocks.append('\n'.join(f'{label(key) + ":":<{pad}} {value_text(key, value)}'
                                for key, value in scalars.items()))
    for key, value in mapping.items():
        if key in scalars or str(key).startswith('_'):
            continue
        body = render(value, tty=tty, width=width - len(INDENT), depth=depth + 1)
        blocks.append(label(key) + ':\n' + '\n'.join(INDENT + line if line else line for line in body.splitlines()))
    return '\n\n'.join(blocks) if depth == 0 else '\n'.join(blocks)


def render(result, *, tty=None, width=None, depth=0):
    if tty is None:
        import sys
        tty = sys.stdout.isatty()
    if width is None:
        width = shutil.get_terminal_size((100, 24)).columns
    if isinstance(result, str):
        return result
    if is_scalar(result):
        return value_text('', result)
    if isinstance(result, list):
        if all(isinstance(item, dict) for item in result):
            return table(result, tty=tty, width=width)
        if all(is_scalar(item) for item in result):
            return '\n'.join(value_text('', item) for item in result) or ('No results.' if tty else '')
        return '\n'.join(('- ' if tty else '') + render(item, tty=tty, width=width, depth=depth + 1)
                         for item in result)
    if isinstance(result, dict):
        if isinstance(result.get('message'), str):
            return result['message']
        if not result or (depth == 0 and result == {'ok': True}):
            return 'Done.' if depth == 0 else '—'
        lists = [key for key, value in result.items() if isinstance(value, list) and value
                 and all(isinstance(item, dict) for item in value)]
        others = {key: value for key, value in result.items() if key not in lists}
        # A list wrapped with summary fields ({"models": [...], "total": 12}) is the list.
        if depth == 0 and len(lists) == 1 and all(is_scalar(value) for value in others.values()):
            body = table(result[lists[0]], tty=tty, width=width)
            if not tty or not others:
                return body
            return body + '\n\n' + _fields(others, tty=tty, width=width, depth=depth)
        return _fields(result, tty=tty, width=width, depth=depth)
    return str(result)
