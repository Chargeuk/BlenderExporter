"""Focused CPU checks; no Blender import or source-scene changes."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/babylon_js/environment_tools'))
from common import load_config, normalize_metal_diffuse


class MetalDiffuseWeightTests(unittest.TestCase):
    def arrays(self, base_rgb, light=2.0, weight=1.0):
        base = np.zeros((2, 2, 4), np.float32)
        base[0, 0] = [*base_rgb, 1]
        weighted = np.zeros_like(base)
        weighted[..., 3] = 1  # COLOR-bearing alpha is NOT coverage.
        weighted[0, 0, :3] = np.asarray(base_rgb) * light * weight
        weighted[1, 1, :3] = 5  # Off-footprint values must not leak.
        return weighted, base

    def test_nonmetal_unity_and_exact_coverage(self):
        weighted, base = self.arrays([.4, .2, .1])
        result, proof = normalize_metal_diffuse(weighted, base)
        np.testing.assert_allclose(result[0, 0, :3], [2, 2, 2])
        np.testing.assert_array_equal(result[..., 3], base[..., 3])
        np.testing.assert_array_equal(result[base[..., 3] == 0], 0)
        self.assertEqual(proof['owned_pixels'], 1)
        self.assertEqual(proof['off_footprint_pixels'], 3)

    def test_metal_keeps_native_diffuse_weight(self):
        weighted, base = self.arrays([.4, .2, .1], weight=.25)
        result, _ = normalize_metal_diffuse(weighted, base)
        np.testing.assert_allclose(result[0, 0, :3], [.5, .5, .5])
        np.testing.assert_allclose(result[0, 0, :3] * base[0, 0, :3], weighted[0, 0, :3])

    def test_black_base_remains_owned(self):
        weighted, base = self.arrays([0, 0, 0])
        result, proof = normalize_metal_diffuse(weighted, base)
        np.testing.assert_array_equal(result[0, 0], [0, 0, 0, 1])
        self.assertEqual(proof['protected_base_channels'], 3)
        self.assertEqual(proof['protected_residual_max'], 0)

    def test_near_zero_policy_is_explicit_and_finite(self):
        weighted, base = self.arrays([1e-9, 0, .1])
        weighted[0, 0, 0] = 5e-7
        result, proof = normalize_metal_diffuse(weighted, base)
        np.testing.assert_allclose(result[0, 0, :3], [0, 0, 2])
        self.assertEqual(proof['protected_base_channels'], 2)
        self.assertAlmostEqual(proof['protected_residual_max'], 5e-7)
        weighted[0, 0, 0] = 2e-6
        with self.assertRaisesRegex(ValueError, 'zero/near-zero'):
            normalize_metal_diffuse(weighted, base)

    def test_nonfinite_negative_and_nonbinary_inputs_fail(self):
        for channel, value, message in ((0, np.nan, 'Non-finite'), (0, -1, 'Negative'), (3, .5, 'binary')):
            weighted, base = self.arrays([.4, .2, .1])
            base[0, 0, channel] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, message):
                normalize_metal_diffuse(weighted, base)
        weighted, base = self.arrays([.4, .2, .1])
        weighted[0, 0, 0] = np.inf
        with self.assertRaisesRegex(ValueError, 'Non-finite'):
            normalize_metal_diffuse(weighted, base)

    def test_config_legacy_default_and_boolean_opt_in(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'source.blend').write_bytes(b'fixture')
            config = dict(schema_version=1, blend='source.blend', quality='preview', size=16,
                          samples={'DIRECT': 1, 'INDIRECT': 1},
                          images={key: key + '.exr' for key in ('direct', 'indirect', 'island_ids', 'combined', 'colour')})
            path = root / 'config.json'
            path.write_text(json.dumps(config))
            self.assertIs(load_config(path)['preserve_metal_diffuse_weight'], False)
            config['preserve_metal_diffuse_weight'] = True
            path.write_text(json.dumps(config))
            self.assertIs(load_config(path)['preserve_metal_diffuse_weight'], True)
            for invalid in ('false', 0, 1, None):
                config['preserve_metal_diffuse_weight'] = invalid
                path.write_text(json.dumps(config))
                with self.subTest(value=invalid), self.assertRaisesRegex(ValueError, 'must be boolean'):
                    load_config(path)


if __name__ == '__main__':
    unittest.main()
