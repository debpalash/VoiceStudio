"""Native maintainer cards retain Electron's identities, links and brand artwork."""
import json
from pathlib import Path
import runpy
import shutil
import pytest
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("bun") is None, reason="needs bun")
def test_mobile_maintainer_catalog_matches_desktop():
    # The resolved path, not 'bun': Windows npm installs only ship bun.cmd.
    result = subprocess.run([shutil.which('bun'), 'scripts/export-mobile-contributors.ts', '--check'], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


def test_mobile_maintainer_catalog_is_localized():
    cards = json.loads((ROOT / 'mobile/contract/maintainer-cards.json').read_text(encoding="utf-8"))
    assert set(cards) == {'debpalash', 'velixio'}
    for card in cards.values():
        assert [link['kind'] for link in card['links']] == ['website', 'github', 'x']
        assert all(link['href'].startswith('https://') for link in card['links'])
        for path in (ROOT / 'electron/src/renderer/src/i18n/locales').glob('*.json'):
            locale = json.loads(path.read_text(encoding="utf-8"))
            for key in [card['name'], card['role'], 'homeUi.website', 'homeUi.follow', 'common.close']:
                value = locale
                for part in key.split('.'):
                    value = value[part]
                assert value


def test_native_brand_icons_are_generated_from_desktop(tmp_path):
    generator = runpy.run_path(str(ROOT / 'scripts/prepare_mobile_contributors.py'))
    generator['prepare_ios_icons'](ROOT, tmp_path / 'ios')
    generator['prepare_android_icons'](ROOT, tmp_path / 'android')
    for kind, path in generator['brand_paths'](ROOT).items():
        name = {'github': 'ContributorGitHub', 'x': 'ContributorX'}[kind]
        image = tmp_path / 'ios' / (name + '.imageset')
        metadata = json.loads((image / 'Contents.json').read_text(encoding="utf-8"))
        assert metadata['properties']['template-rendering-intent'] == 'template'
        svg = ET.parse(image / 'icon.svg').getroot()
        assert svg.attrib['viewBox'] == '0 0 24 24'
        assert svg[0].attrib['d'] == path
        vector = ET.parse(tmp_path / 'android/drawable' / ('contributor_' + kind + '.xml')).getroot()
        assert vector[0].attrib['{http://schemas.android.com/apk/res/android}pathData'] == path
