"""World route opt-in is explicit; absent recipes retain direct-only validation."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src/babylon_js/environment_tools'))
from common import physical_world_routing, load_config


class PhysicalWorldRoutingTests(unittest.TestCase):
    def test_default_direct(self):
        self.assertEqual(physical_world_routing({}), 'direct')
        self.assertEqual(physical_world_routing({'physical_world_routing': 'direct'}), 'direct')

    def test_explicit_camera_separated(self):
        self.assertEqual(physical_world_routing({'physical_world_routing': 'camera-separated'}), 'camera-separated')

    def test_unknown_modes_rejected(self):
        for mode in ('automatic', 'camera', '', None, True, 1, ['direct']):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'direct or camera-separated'):
                physical_world_routing({'physical_world_routing': mode})

    def test_config_loader_checks_world_route(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'source.blend').write_bytes(b'fixture source')
            config = {'schema_version': 1, 'blend': 'source.blend', 'size': 16,
                'images': {key: key+'.exr' for key in ('direct', 'indirect', 'island_ids', 'combined', 'colour')},
                'physical_world_routing': 'camera-separated'}
            path = root/'recipe.json'
            path.write_text(json.dumps(config), encoding='utf8')
            self.assertEqual(load_config(path)['physical_world_routing'], 'camera-separated')
            config['physical_world_routing'] = 'skip'
            path.write_text(json.dumps(config), encoding='utf8')
            with self.assertRaisesRegex(ValueError, 'physical_world_routing'): load_config(path)


if __name__ == '__main__': unittest.main()
