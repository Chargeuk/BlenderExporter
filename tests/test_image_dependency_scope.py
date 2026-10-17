"""Explicit participants scope must not silently change existing recipe validation."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src/babylon_js/environment_tools'))
from common import image_dependency_scope, load_config


class ImageDependencyScopeTests(unittest.TestCase):
    def test_default_preserves_all_datablocks(self):
        self.assertEqual(image_dependency_scope({}), 'all')
        self.assertEqual(image_dependency_scope({'image_dependency_scope': 'all'}), 'all')

    def test_participants_is_explicit(self):
        self.assertEqual(image_dependency_scope({'image_dependency_scope': 'participants'}), 'participants')

    def test_invalid_scope_rejected(self):
        for value in ('active', 'Participants', '', None, True, 1, ['participants']):
            with self.subTest(scope=value), self.assertRaisesRegex(ValueError, 'all or participants'):
                image_dependency_scope({'image_dependency_scope': value})

    def test_loader_validates_scope_and_preserves_source_image_declarations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'source.blend').write_bytes(b'saved scene fixture')
            config = {'schema_version': 1, 'blend': 'source.blend', 'size': 16,
                'images': {name: name+'.exr' for name in ('direct', 'indirect', 'island_ids', 'combined', 'colour')},
                'source_images': ['declared.png'], 'image_dependency_scope': 'participants'}
            path = root/'recipe.json'
            path.write_text(json.dumps(config), encoding='utf8')
            loaded = load_config(path)
            self.assertEqual(loaded['image_dependency_scope'], 'participants')
            self.assertEqual(loaded['source_images'], ['declared.png'])
            config['image_dependency_scope'] = 'automatic'
            path.write_text(json.dumps(config), encoding='utf8')
            with self.assertRaisesRegex(ValueError, 'image_dependency_scope'): load_config(path)


if __name__ == '__main__': unittest.main()
