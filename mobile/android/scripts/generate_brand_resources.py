#!/usr/bin/env python3
"""Generate Android vectors/adaptive icons from the canonical SVG and theme tokens."""
import argparse
import json
import runpy
from pathlib import Path
import xml.etree.ElementTree as ET

ANDROID = 'http://schemas.android.com/apk/res/android'
AAPT = 'http://schemas.android.com/aapt'
SVG = 'http://www.w3.org/2000/svg'
ET.register_namespace('android', ANDROID)
ET.register_namespace('aapt', AAPT)

def android(**attrs):
    return {f'{{{ANDROID}}}{key}': str(value) for key, value in attrs.items()}

def generate(repository, output):
    contributors = runpy.run_path(str(repository / "scripts/prepare_mobile_contributors.py"))
    contributors["prepare_android_icons"](repository, output)
    logo = ET.parse(repository / 'docs/logo.svg').getroot()
    tokens = json.loads((repository / 'mobile/contract/tokens.json').read_text())
    canvas = next(p for p in tokens['palettes'] if p['id'] == 'default')['colors'][tokens['defaultMode']]['canvas']
    # Contract colors are RGBA; Android resources use ARGB.
    canvas = '#' + canvas[7:9] + canvas[1:7]
    gradients = {node.attrib['id']: node for node in logo.findall(f'.//{{{SVG}}}linearGradient')}

    def write(name, node):
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        ET.indent(node)
        ET.ElementTree(node).write(target, encoding='utf-8', xml_declaration=True)

    def vector(adaptive=False, monochrome=False):
        size = 108 if adaptive else 64
        root = ET.Element('vector', android(width=f'{size}dp', height=f'{size}dp', viewportWidth=size, viewportHeight=size))
        # The complete waveform + spark fit inside the adaptive icon safe zone.
        parent = ET.SubElement(root, 'group', android(scaleX='.9', scaleY='.9', translateX='25.2', translateY='25.2')) if adaptive else root
        for path in logo.findall(f'{{{SVG}}}path'):
            attrs = android(pathData=path.attrib['d'])
            for source, target in [('stroke-width', 'strokeWidth'), ('stroke-linecap', 'strokeLineCap'), ('stroke-linejoin', 'strokeLineJoin')]:
                if source in path.attrib:
                    attrs.update(android(**{target: path.attrib[source]}))
            node = ET.SubElement(parent, 'path', attrs)
            for source, target in [('stroke', 'strokeColor'), ('fill', 'fillColor')]:
                value = path.attrib.get(source)
                if not value:
                    continue
                if value == 'none':
                    # Android vectors need a color, and themed icons must keep
                    # deliberately unpainted SVG paths transparent too.
                    node.attrib.update(android(**{target: '#00000000'}))
                elif monochrome:
                    node.attrib.update(android(**{target: '#FFFFFFFF'}))
                elif value.startswith('url(#'):
                    gradient = gradients[value[5:-1]]
                    attr = ET.SubElement(node, f'{{{AAPT}}}attr', {'name': f'android:{target}'})
                    converted = ET.SubElement(attr, 'gradient', android(type='linear', startX=gradient.attrib['x1'], startY=gradient.attrib['y1'], endX=gradient.attrib['x2'], endY=gradient.attrib['y2']))
                    for stop in gradient:
                        ET.SubElement(converted, 'item', android(offset=stop.attrib.get('offset', '0'), color=stop.attrib['stop-color']))
                else:
                    node.attrib.update(android(**{target: value}))
        return root

    write('drawable/vs_logo.xml', vector())
    write('drawable/vs_launcher_foreground.xml', vector(adaptive=True))
    write('drawable/vs_launcher_monochrome.xml', vector(adaptive=True, monochrome=True))
    colors = ET.Element('resources')
    ET.SubElement(colors, 'color', {'name': 'vs_canvas'}).text = canvas
    write('values/brand_colors.xml', colors)
    # Adaptive icons are available on every supported Android version. Older
    # Android versions ignore the monochrome child; API 33+ uses it for theming.
    icon = ET.Element('adaptive-icon')
    ET.SubElement(icon, 'background', android(drawable='@color/vs_canvas'))
    ET.SubElement(icon, 'foreground', android(drawable='@drawable/vs_launcher_foreground'))
    ET.SubElement(icon, 'monochrome', android(drawable='@drawable/vs_launcher_monochrome'))
    write('mipmap-anydpi/ic_launcher.xml', icon)
    for legacy in ['mipmap-anydpi-v26', 'mipmap-anydpi-v33']:
        stale = output / legacy / 'ic_launcher.xml'
        if stale.exists():
            stale.unlink()
            if not any(stale.parent.iterdir()):
                stale.parent.rmdir()
    splash = ET.Element('layer-list')
    ET.SubElement(splash, 'item', android(drawable='@color/vs_canvas'))
    ET.SubElement(splash, 'item', android(drawable='@drawable/vs_logo', gravity='center', width='96dp', height='96dp'))
    write('drawable/vs_splash.xml', splash)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    generate(Path(__file__).resolve().parents[3], args.output)
