"""Contracts shared by upstream 3.3.31 and the retained installed workflows."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / 'src/babylon_js'
spec = importlib.util.spec_from_file_location('update_compat_core', ADDON / 'ktx_conversion.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
sys.path.insert(0, str(ADDON / 'environment_tools'))
from native_ownership import validate_native_ids
from receiver_response import factor_from_captures


class PluginUpdateCompatibilityTests(unittest.TestCase):
    def test_both_fixed_divisor_interfaces_keep_identical_full_image_bytes(self):
        rgb = np.array([[[.01, .2, 1], [10, 60, 150]], [[.5, 1, 2], [3, 4, 5]]], np.float32)
        upstream, upstream_metrics = core.encode_rgbd(rgb, fixed_divisor=True)
        installed, installed_metrics = core.encode_rgbd(rgb, 'image-wide')
        np.testing.assert_array_equal(upstream, installed)
        self.assertEqual(upstream_metrics, installed_metrics)
        self.assertEqual(len(np.unique(upstream[..., 3])), 1)
        original, _ = core.encode_rgbd(rgb)
        self.assertGreater(len(np.unique(original[..., 3])), 1)

    def test_standalone_hdr_preparation_forwards_upstream_fixed_divisor(self):
        raw = np.array([[[150, 60, 10], [1, .2, .01]], [[5, 4, 3], [2, 1, .5]]], np.float32)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'fixture.exr'
            source.write_bytes(b'fixture backend replaced below')
            config = dict(source_path=str(source), output='fixture.ktx2', flip_y=True,
                          encoding='rgbd-v1', color_space='linear', rgbd_fixed_divisor=True)
            with patch.object(core, 'read_float_hdr', return_value=raw):
                prepared, record = core.prepare(config, root)
            with Image.open(prepared) as image:
                pixels = np.asarray(image)
            self.assertEqual(len(np.unique(pixels[..., 3])), 1)
            self.assertEqual(record['rgbd_encoded_divisor_mode'], 'image-wide')
            self.assertEqual(record['source_dimensions'], [2, 2])

    def test_native_owner_response_uses_sampled_ids_without_uv_reconstruction(self):
        # Two charts share conservative footprint candidates. Actual sampled IDs
        # remain authoritative through the retained response API.
        triangle = [[0, 0], [1, 0], [0, 1]]
        catalogue = [dict(id=i, object='receiver'+str(i), triangles=[triangle]) for i in (1, 2)]
        ids = np.zeros((2, 2, 4), np.float32)
        ids[0, 0] = [2, 2, 2, 1]
        ids[1, 1] = [1, 1, 1, 1]
        labels, proof = validate_native_ids(ids, catalogue)
        base = np.zeros_like(ids); base[labels > 0] = [.2, .4, .5, 1]
        diffuse = base.copy(); diffuse[..., :3] *= .25
        factor, response, report = factor_from_captures(
            base, diffuse, labels, catalogue, catalogue, [], [], native_ids=ids)
        np.testing.assert_array_equal(factor[labels > 0, :3], .25)
        np.testing.assert_array_equal(response[..., 3], labels > 0)
        self.assertFalse(proof['owner_inference'])
        self.assertEqual(report['owned_pixels'], 2)
        changed = ids.copy(); changed[0, 0, :3] = 1
        with self.assertRaisesRegex(ValueError, 'ownership disagrees'):
            factor_from_captures(base, diffuse, labels, catalogue, catalogue, [], [], native_ids=changed)

    def test_packaged_env_converter_keeps_latest_upstream_source_and_lock(self):
        for name in ('convert.cjs', 'runtime.cjs', 'setup.py', 'package.json', 'package-lock.json'):
            self.assertEqual((ADDON/'workflow_tools/env_converter'/name).read_bytes(),
                             (ROOT/'tools/env-converter'/name).read_bytes())
        metadata = json.loads((ADDON/'workflow_tools/env_converter/package.json').read_text())
        self.assertEqual(metadata['dependencies']['babylonjs'], '7.27.0')


if __name__ == '__main__':
    unittest.main()
