"""Export the CLI request contract from the already generated backend OpenAPI.

Run after scripts/export_openapi.py when backend request contracts change.
"""
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'cli'))
from voicestudio_cli.registry import COMMANDS


def build_contract():
    schema = json.loads((ROOT / 'mobile/contract/openapi.json').read_text())
    result = {}
    def shape(path):
        return re.sub(r'\{[^}]+\}', '{}', path)
    for command in COMMANDS:
        if command.route.startswith('/audio/') and command.name == 'history download':
            continue  # Starlette's static mount is intentionally absent from OpenAPI.
        if command.route == '/openapi.json':
            continue
        path = next(p for p in schema['paths'] if shape(p) == shape(command.route))
        operation = schema['paths'][path][command.method.lower()]
        content = operation.get('requestBody', {}).get('content', {})
        encoding = next(iter(content), '')
        body = content.get(encoding, {}).get('schema', {})
        if '$ref' in body:
            body = schema['components']['schemas'][body['$ref'].rsplit('/', 1)[-1]]
        result[command.name] = {
            'encoding': encoding,
            'required': body.get('required', []),
            'fields': list(body.get('properties', {})),
            'query': [p['name'] for p in operation.get('parameters', []) if p['in'] == 'query'],
            'required_query': [p['name'] for p in operation.get('parameters', [])
                               if p['in'] == 'query' and p.get('required')],
        }
    return result


if __name__ == '__main__':
    target = ROOT / 'cli/voicestudio_cli/contracts.json'
    target.write_text(json.dumps(build_contract(), indent=2, sort_keys=True) + '\n')
