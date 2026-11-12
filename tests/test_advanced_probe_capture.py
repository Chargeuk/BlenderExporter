"""Math, recipe compatibility and checkpoint guards without Blender."""
import copy
import importlib.util
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import types
import unittest

import numpy as np

ADDON = Path(__file__).resolve().parents[1] / 'src/babylon_js'
sys.path.insert(0, str(ADDON / 'environment_tools'))
from advanced_probe_capture import (
    advanced_options, camera_rays, fogged_mean, measure_position, rectilinear_reference,
)

package = types.ModuleType('advanced_probe_test_addon')
package.__path__ = [str(ADDON)]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location(
    package.__name__ + '.environment_lighting_volume', ADDON / 'environment_lighting_volume.py')
volume = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = volume
spec.loader.exec_module(volume)


class AdvancedProbeTests(unittest.TestCase):
    def test_recipe_versions_select_historical_or_native_math(self):
        self.assertEqual(advanced_options(dict(schema_version=1)), (True, None))
        self.assertEqual(advanced_options(dict(schema_version=1, include_advanced=False)), (False, None))
        self.assertEqual(advanced_options(dict(schema_version=2, include_advanced=False)), (False, None))
        _, options = advanced_options(dict(schema_version=2))
        self.assertEqual(options, dict(fog_per_metre=.25, source_multiplier=4., samples=8, material_roles={}))
        with self.assertRaises(ValueError):
            advanced_options(dict(schema_version=2, advanced=dict(material_roles={'screen': {'00': 'emitter'}})))
        with self.assertRaises(ValueError):
            advanced_options(dict(schema_version=2, advanced=dict(source_multiplier=0)))

    def test_matched_rectilinear_reference_uses_world_axes_and_wraps_seam(self):
        directions = volume.panorama_directions(128, 256)
        # R responds to Babylon X, G to Y, B to Z. Bilinear sampling must be
        # continuous across the -Z seam and symmetric around every other axis.
        environment = 2 + directions
        rays, unit_lengths = camera_rays(64, 70)
        self.assertGreater(unit_lengths[0, 0], unit_lengths[31, 31])
        forward = np.array(((0, 0, 1), (0, 0, -1), (-1, 0, 0), (1, 0, 0), (0, 1, 0), (0, -1, 0)))
        rotations = []
        for axis in forward:
            right = np.cross(axis, (0, 0, 1) if axis[2] == 0 else (0, 1, 0))
            rotations.append(np.stack((right, np.cross(right, axis), -axis), axis=1))
        reference = rectilinear_reference(environment, rays, rotations)
        expected_axes = forward[:, [0, 2, 1]]
        axial = np.mean(1 / unit_lengths)
        np.testing.assert_allclose(reference, 2 + expected_axes * axial, atol=.0003)
        with self.assertRaisesRegex(ValueError, 'near-zero'):
            rectilinear_reference(np.zeros_like(environment), rays, rotations)

    def test_fog_is_applied_before_arithmetic_mean_in_metres(self):
        _, ray_lengths = camera_rays(4, 70)
        depth = np.arange(16).reshape(4, 4) / 8
        rgb = np.repeat(depth[..., None] + 1, 3, axis=-1)
        expected = np.mean(rgb * np.exp(-.25 * 2 * depth * ray_lengths)[..., None], axis=(0, 1))
        np.testing.assert_allclose(fogged_mean(rgb, depth, ray_lengths, .25, 2), expected)
        depth[:] = np.inf
        np.testing.assert_array_equal(fogged_mean(rgb, depth, ray_lengths, .25, 2), np.zeros(3))
        np.testing.assert_array_equal(fogged_mean(rgb, depth, ray_lengths, 0, 2), rgb.mean(axis=(0, 1)))
        with self.assertRaisesRegex(ValueError, 'nonnegative Z'):
            fogged_mean(rgb, -depth, ray_lengths, .25, 2)

    def test_checkpoint_reuse_is_bound_to_signature_and_position(self):
        from common import save_json
        calls = []

        def render(position):
            calls.append(position)
            return np.full((6, 3), 1.5).tolist()

        with TemporaryDirectory() as folder:
            path = Path(folder) / 'probe.json'
            first, record = measure_position([1, 2, 3], path, 'a'*64, np.ones((6, 3)), render)
            reused, _ = measure_position([1, 2, 3], path, 'a'*64, np.ones((6, 3)), render)
            np.testing.assert_array_equal(first, reused)
            self.assertEqual(len(calls), 1)
            for position, digest in (([2, 2, 3], 'a'*64), ([1, 2, 3], 'b'*64)):
                with self.assertRaisesRegex(ValueError, 'checkpoint position or capture inputs'):
                    measure_position(position, path, digest, np.ones((6, 3)), render)
            record['ratioRgb'][0][1] = 22
            save_json(path, record)
            with self.assertRaisesRegex(ValueError, 'ratios changed'):
                measure_position([1, 2, 3], path, 'a'*64, np.ones((6, 3)), render)

    def test_historical_and_perspective_provenance_are_distinct(self):
        bounds = {'min': [0, 0, 0], 'max': [1, 1, 1]}
        advanced = dict(version=1, dimensions=volume.DIMENSIONS, bounds=bounds, directionOrder=volume.DIRECTION_ORDER,
                        atlasColumns=4, atlasRows=24, atlasLayout=volume.ADVANCED_LAYOUT, flip_y=False,
                        master='advanced.exr', provenance='proof.json', masterSha256='a'*64, provenanceSha256='b'*64,
                        normalization=dict(method=volume.ADVANCED_HEMISPHERE_NORMALIZATION,
                                           referenceRgb=[[1, 1, 1]] * 6, nearZeroGuard=1e-6),
                        panoramaConvention=volume.PANORAMA_CONVENTION)
        legacy = dict(directionOrder=volume.DIRECTION_ORDER, source={'scene': 'fixture'},
                      panoramaConvention=volume.PANORAMA_CONVENTION, cameraEulerBlender=[np.pi/2, 0, 0])
        volume.validate_advanced(advanced, dict(bounds=bounds))
        volume.validate_advanced_provenance(advanced, legacy, legacy['source'])
        native = copy.deepcopy(advanced)
        native.pop('panoramaConvention')
        native.update(referencePanoramaConvention=volume.PANORAMA_CONVENTION,
                      capture=dict(method=volume.ADVANCED_PERSPECTIVE_METHOD, resolution=[64, 64],
                                   fovDegrees=[70, 70], viewLayer='ViewLayer', samples=8,
                                   blackFogPerMetre=.25, sourceMultiplier=4, metresPerBlenderUnit=1,
                                   depth='cycles-axial-z-times-pixel-centre-ray-length'))
        native['normalization']['method'] = volume.ADVANCED_PERSPECTIVE_NORMALIZATION
        proof = dict(directionOrder=volume.DIRECTION_ORDER, source=legacy['source'],
                     capture=native['capture'], normalization=native['normalization'],
                     referencePanoramaConvention=volume.PANORAMA_CONVENTION)
        volume.validate_advanced(native, dict(bounds=bounds))
        volume.validate_advanced_provenance(native, proof, legacy['source'])
        with self.assertRaisesRegex(ValueError, 'perspective capture provenance'):
            volume.validate_advanced_provenance(native, legacy, legacy['source'])
        native['capture']['fovDegrees'] = [60, 60]
        with self.assertRaisesRegex(ValueError, 'profile'):
            volume.validate_advanced(native, dict(bounds=bounds))


if __name__ == '__main__':
    unittest.main()
