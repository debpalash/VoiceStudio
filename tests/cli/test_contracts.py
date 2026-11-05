import importlib.util
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'cli'))
from voicestudio_cli.__main__ import SETTINGS, build_parser
from voicestudio_cli.registry import COMMANDS


class ContractTests(unittest.TestCase):
    def test_request_contract_has_not_drifted(self):
        spec = importlib.util.spec_from_file_location('cli_contract_export', ROOT / 'scripts/export_cli_contract.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        self.assertEqual(module.build_contract(), json.loads((ROOT / 'cli/voicestudio_cli/contracts.json').read_text()))

    def test_upload_fields_match_real_backend_openapi(self):
        contracts = json.loads((ROOT / 'cli/voicestudio_cli/contracts.json').read_text())
        for command in COMMANDS:
            if command.upload and command.upload != '@raw':
                with self.subTest(command=command.name):
                    self.assertIn(command.upload, contracts[command.name]['fields'])

    def test_settings_write_fields_match_backend(self):
        schema = json.loads((ROOT / 'mobile/contract/openapi.json').read_text())
        for route, field in SETTINGS.values():
            with self.subTest(route=route):
                body = schema['paths']['/api/settings/' + route]['put']['requestBody']['content']['application/json']['schema']
                body = schema['components']['schemas'][body['$ref'].rsplit('/', 1)[-1]]
                self.assertIn(field, body['properties'])

    def test_no_duplicate_registered_commands(self):
        names = [command.name for command in COMMANDS]
        self.assertEqual(len(set(names)), len(names))

    def test_help_includes_inherited_options_and_transport_description(self):
        _, nodes = build_parser()
        for command in COMMANDS:
            with self.subTest(command=command.name):
                text = nodes[tuple(command.name.split())].format_help()
                for flag in ['--url', '--json', '--help', '--timeout']:
                    self.assertIn(flag, text)

    def test_every_command_and_group_is_described_for_people(self):
        _, nodes = build_parser()
        for key, parser in nodes.items():
            if not key:
                continue
            name = ' '.join(key)
            with self.subTest(command=name):
                text = parser.description or ''
                self.assertNotIn(text.lower(), {name.lower(), key[-1].lower(), f'{key[-1]} commands'})
                self.assertNotRegex(text, r'^[A-Z][a-z]+: ', 'placeholder "Group: name" description')
                self.assertNotIn('--file JSON', text, 'input format belongs to the --file help')

    def test_no_help_lists_an_empty_commands_section(self):
        _, nodes = build_parser()
        for path, parser in nodes.items():
            text = parser.format_help()
            with self.subTest(command=' '.join(path)):
                if 'Commands:' in text:
                    self.assertRegex(text.split('Commands:', 1)[1], r'\n\s+[a-z][\w-]*\s')
                else:
                    self.assertNotIn('COMMAND', text.splitlines()[0])

    def test_help_hides_options_that_do_nothing(self):
        _, nodes = build_parser()
        for path, hidden in [(('stop',), ['--url', '--timeout', '--output', '--yes']),
                             (('version',), ['--url', '--output']),
                             (('voices',), ['--url', '--json']),
                             (('voices', 'list'), ['--yes'])]:
            with self.subTest(path=path):
                text = nodes[path].format_help()
                for flag in hidden:
                    self.assertNotIn(flag, text)
        self.assertIn('--yes', nodes[('voices', 'delete')].format_help())

    def test_renamed_commands_keep_their_former_names_working_but_unlisted(self):
        from voicestudio_cli.__main__ import RENAMED, HIDDEN
        root, nodes = build_parser()
        for former, current in RENAMED.items():
            with self.subTest(former=former):
                self.assertEqual(nodes[tuple(former.split())].get_default('handler'),
                                 nodes[tuple(current.split())].get_default('handler'))
                parent = nodes[tuple(former.split()[:-1])].format_help()
                self.assertNotRegex(parent, rf'\n\s+{re.escape(former.split()[-1])}\s')
        for former in HIDDEN:
            self.assertNotRegex(nodes[tuple(former.split()[:-1])].format_help(),
                                rf'\n\s+{re.escape(former.split()[-1])}\s')
        self.assertNotIn('stories', root.format_help().split('Commands:')[1].split())


if __name__ == '__main__':
    unittest.main()
