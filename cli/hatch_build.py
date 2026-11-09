"""Derive release metadata from the repository's single version source."""
import json
from pathlib import Path
from hatchling.metadata.plugin.interface import MetadataHookInterface
from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomMetadataHook(MetadataHookInterface):
    def update(self, metadata):
        root = Path(self.root)
        canonical = root.parent / 'package.json'
        if not canonical.is_file():
            canonical = root / 'package.json'  # Included verbatim in source distributions.
        version = json.loads(canonical.read_text(encoding='utf-8'))['version']
        metadata['version'] = version.replace('-beta.', 'b')


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        root = Path(self.root)
        license_file = root.parent / 'LICENSE'
        if not license_file.is_file():
            license_file = root / 'LICENSE'
        if self.target_name == 'wheel':
            build_data.setdefault('force_include', {})[str(license_file)] = 'voicestudio_cli/licenses/AGPL-3.0.txt'
