"""Generate native template artwork from Electron's CC0 Simple Icons paths."""
import json
import re
import xml.etree.ElementTree as ET

ANDROID = 'http://schemas.android.com/apk/res/android'


def brand_paths(repository):
    source = (repository / 'electron/src/renderer/src/components/brand-icons.tsx').read_text(encoding='utf-8')
    result = {}
    for kind, name in [('github', 'GitHubIcon'), ('x', 'XLogoIcon')]:
        match = re.search(r'export function ' + name + r'\(.*?\n\}', source, re.S)
        paths = re.findall(r'<path d="([^"]+)"\s*/>', match[0]) if match else []
        if len(paths) != 1 or 'viewBox="0 0 24 24"' not in match[0]:
            raise ValueError('Desktop brand icon format changed: ' + name)
        result[kind] = paths[0]
    return result


def prepare_ios_icons(repository, output):
    for kind, path in brand_paths(repository).items():
        name = {'github': 'ContributorGitHub', 'x': 'ContributorX'}[kind]
        directory = output / (name + '.imageset')
        directory.mkdir(parents=True, exist_ok=True)
        svg = ET.Element('svg', {'xmlns': 'http://www.w3.org/2000/svg', 'viewBox': '0 0 24 24'})
        ET.SubElement(svg, 'path', {'d': path, 'fill': '#000000'})
        ET.ElementTree(svg).write(directory / 'icon.svg', encoding='utf-8', xml_declaration=True)
        (directory / 'Contents.json').write_text(json.dumps({
            'images': [{'filename': 'icon.svg', 'idiom': 'universal'}],
            'info': {'version': 1, 'author': 'xcode'},
            'properties': {'preserves-vector-representation': True, 'template-rendering-intent': 'template'},
        }), encoding='utf-8')


def prepare_android_icons(repository, output):
    ET.register_namespace('android', ANDROID)
    def attrs(**values):
        return {f'{{{ANDROID}}}{key}': value for key, value in values.items()}
    directory = output / 'drawable'
    directory.mkdir(parents=True, exist_ok=True)
    for kind, path in brand_paths(repository).items():
        vector = ET.Element('vector', attrs(width='24dp', height='24dp', viewportWidth='24', viewportHeight='24'))
        ET.SubElement(vector, 'path', attrs(pathData=path, fillColor='#FF000000'))
        ET.ElementTree(vector).write(directory / ('contributor_' + kind + '.xml'), encoding='utf-8', xml_declaration=True)
