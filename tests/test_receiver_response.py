"""Measured response stays separate from native radiance and native ownership."""
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src/babylon_js/environment_tools'))
from receiver_response import (validate_settings, factor_from_captures, apply_response, save_new_array)


class ReceiverResponseTests(unittest.TestCase):
    def setUp(self):
        self.labels = np.zeros((16, 16), np.int32)
        self.labels[2:6, 2:6] = 1
        self.labels[10:14, 10:14] = 2
        self.catalogue = [
            {'id': 1, 'object': 'A', 'triangles': [[[0, 1], [.5, 1], [.5, .5]], [[0, 1], [.5, .5], [0, .5]]]},
            {'id': 2, 'object': 'B', 'triangles': [[[.5, .5], [1, .5], [1, 0]], [[.5, .5], [1, 0], [.5, 0]]]}]
        self.base = np.full((16, 16, 4), [.8, .4, .2, .37], np.float32)
        self.diffuse = np.full((16, 16, 4), [.4, .1, .025, 1], np.float32)
        self.patch = np.full((16, 16, 4), [.16, .12, .08, .123], np.float32)

    def measure(self, patches=None):
        return factor_from_captures(self.base, self.diffuse, self.labels,
            self.catalogue, self.catalogue,
            [{'object': 'B', 'pixels': self.patch}] if patches is None else patches, ['B'])

    def test_opt_in_schema_and_default(self):
        self.assertFalse(validate_settings({}))
        self.assertFalse(validate_settings({'receiver_response': {'enabled': False}}))
        self.assertTrue(validate_settings({'receiver_response': {'enabled': True, 'schema_version': 1}}))
        for settings in (True, {'enabled': 'yes'}, {'enabled': True},
                         {'enabled': True, 'schema_version': 1, 'factor': .2}):
            with self.assertRaises(ValueError): validate_settings({'receiver_response': settings})

    def test_capture_utilities_import_without_scipy(self):
        tools = Path(__file__).resolve().parents[1]/'src/babylon_js/environment_tools'
        code = """import sys, tempfile
from pathlib import Path
class BlockProcessingImports:
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ('ownership', 'reuse') or fullname.startswith('scipy'):
            raise ImportError('Processing dependency unavailable in capture runtime: ' + fullname)
sys.meta_path.insert(0, BlockProcessingImports())
sys.path.insert(0, sys.argv[1])
from receiver_response import validate_settings, save_new_array
import numpy as np
assert validate_settings({'receiver_response': {'enabled': True, 'schema_version': 1}})
with tempfile.TemporaryDirectory() as tmp:
    save_new_array(Path(tmp)/'capture.npy', np.zeros((2, 2, 4), np.float32))
assert not any(n == 'scipy' or n.startswith('scipy.') for n in sys.modules)
"""
        result = subprocess.run([sys.executable, '-B', '-c', code, str(tools)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_known_channels_caps_and_colour_alpha_not_ownership(self):
        factor, response, report = self.measure()
        np.testing.assert_allclose(factor[self.labels == 1, :3], np.tile([.5, .25, .125], (16, 1)))
        np.testing.assert_allclose(factor[self.labels == 2, :3], np.tile([.2, .3, .4], (16, 1)))
        np.testing.assert_array_equal(response[self.labels == 2, :3], self.patch[self.labels == 2, :3])
        np.testing.assert_array_equal(factor[:, :, 3], self.labels > 0)
        self.assertTrue((factor[self.labels == 0] == 0).all())
        self.assertEqual(report['owned_pixels'], 32)

    def test_zero_base_zero_response_is_irrelevant_factor_one(self):
        self.base[2:6, 2:6, 0] = 0
        self.diffuse[2:6, 2:6, 0] = 0
        factor, _, report = self.measure()
        self.assertTrue((factor[self.labels == 1, 0] == 1).all())
        self.assertEqual(report['zero_base_and_diffuse_channels'], 16)

    def test_zero_base_nonzero_response_rejected(self):
        self.base[2:6, 2:6, 0] = 1e-9
        with self.assertRaisesRegex(ValueError, 'near-zero'): self.measure()

    def test_invalid_captures_rejected(self):
        for invalid in (-1, np.nan, np.inf):
            with self.subTest(value=invalid):
                saved = self.diffuse[3, 3, 0]
                self.diffuse[3, 3, 0] = invalid
                with self.assertRaisesRegex(ValueError, 'negative|non-finite'): self.measure()
                self.diffuse[3, 3, 0] = saved
        with self.assertRaisesRegex(ValueError, 'patches differ'): self.measure([])
        with self.assertRaisesRegex(ValueError, 'patches differ'):
            self.measure([{'object': 'B', 'pixels': self.patch}] * 2)

    def test_uv_and_label_mismatches_rejected(self):
        changed = [dict(self.catalogue[0], object='Other'), self.catalogue[1]]
        with self.assertRaisesRegex(ValueError, 'catalogue differs'):
            factor_from_captures(self.base, self.diffuse, self.labels, self.catalogue, changed, [], [])
        changed = [dict(self.catalogue[0], id=2), dict(self.catalogue[1], id=1)]
        with self.assertRaisesRegex(ValueError, 'labels differ'):
            factor_from_captures(self.base, self.diffuse, self.labels, self.catalogue, changed, [], [])
        self.labels[3, 3] = 2
        with self.assertRaisesRegex(ValueError, 'disagrees'): self.measure()

    def test_raw_and_processed_independently_preserved_alpha_and_dilation(self):
        factor, _, _ = self.measure()
        for value in (2., 3.):
            native = np.zeros_like(self.base)
            native[self.labels > 0] = [value, value + 1, value + 2, 1]
            before = native.copy()
            owned, filled = apply_response(native, factor, self.labels)
            np.testing.assert_array_equal(native, before)
            np.testing.assert_array_equal(owned[:, :, 3], native[:, :, 3])
            np.testing.assert_array_equal(filled[self.labels > 0], owned[self.labels > 0])
            np.testing.assert_array_equal(owned[self.labels > 0, :3],
                                          native[self.labels > 0, :3] * factor[self.labels > 0, :3])
            self.assertTrue((filled[:, :, 3] == 1).all())

    def test_native_coverage_mismatch_and_invalid_rgb_rejected(self):
        factor, _, _ = self.measure()
        native = np.zeros_like(self.base)
        native[:, :, 3] = self.labels > 0
        native[0, 0, 3] = 1
        with self.assertRaisesRegex(ValueError, 'coverage differs'): apply_response(native, factor, self.labels)
        native[0, 0, 3] = 0
        native[3, 3, 0] = -1
        with self.assertRaisesRegex(ValueError, 'negative'): apply_response(native, factor, self.labels)

    def test_existing_native_and_derived_files_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'native.npy'
            save_new_array(target, self.base)
            before = target.read_bytes()
            with self.assertRaises(FileExistsError): save_new_array(target, self.diffuse)
            self.assertEqual(target.read_bytes(), before)


if __name__ == '__main__': unittest.main()
