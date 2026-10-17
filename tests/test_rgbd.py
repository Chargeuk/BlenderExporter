"""RGBD data contract, legacy preservation and marker naming regressions."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
import numpy as np
import sys
import types
import copy
from unittest.mock import patch

ADDON_ROOT = Path(os.environ.get('BJS_TEST_ADDON_ROOT', Path(__file__).resolve().parents[1] / 'src/babylon_js'))
spec = importlib.util.spec_from_file_location('core', ADDON_ROOT / 'ktx_conversion.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

# Import only the existing pure helper modules, without Blender's package initializer.
package = types.ModuleType('rgbd_normalization_tests')
package.__path__ = [str(Path(__file__).resolve().parents[1] / 'src/babylon_js')]
sys.modules[package.__name__] = package
from rgbd_normalization_tests import agx_lightmap as agx, ktx_export as ktx
from rgbd_normalization_tests.lighting_profile import DEFAULT_LIGHTING


class AgXRangeNormalizationTests(unittest.TestCase):
    def profile(self, **changes):
        return dict(DEFAULT_LIGHTING, **changes)

    def test_strict_default_and_versioned_opt_in(self):
        class Display:
            def applyRGB(self, data):
                data[:] = 1
        colour = np.full((1, 1, 3), .002, np.float32)
        lighting = np.ones_like(colour)
        mask = np.ones((1, 1), bool)
        with self.assertRaisesRegex(ValueError, 'exceeds RGBD range'):
            agx.compensate(colour, lighting, Display(), mask)
        raw, _ = agx.compensate(colour, lighting, Display(), mask,
                                range_normalization=agx.RANGE_NORMALIZATION)
        self.assertGreater(raw.max(), 255)
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            agx.compensate(colour, lighting, Display(), mask, range_normalization=True)
        with self.assertRaisesRegex(ValueError, 'nonnegative'):
            agx.compensate(colour, -lighting, Display(), mask,
                           range_normalization=agx.RANGE_NORMALIZATION)

    def test_gain_one_preserves_pixels_and_profile(self):
        source = np.array([[[0, .01, 254], [2, 3, 4]]], np.float32)
        artist = self.profile()
        output, audit = agx.normalize_compensation(source, artist)
        np.testing.assert_array_equal(output, source)
        self.assertEqual(audit['gain'], 1)
        self.assertEqual(audit['serialized_profile'], artist)
        self.assertEqual(audit['input_array_sha256'], audit['output_array_sha256'])
        self.assertEqual(artist, self.profile())

    def test_full_padding_pipeline_and_rgbd_guard(self):
        # Owned >255 sockets must pass through full gap fill before normalization.
        source = np.zeros((4, 4, 3), np.float32)
        source[0, 0] = [278.67, 291.21857, 220.73]
        source[3, 3] = [290, 100, 20]
        mask = np.zeros((4, 4), bool); mask[0, 0] = mask[3, 3] = True
        filled = agx.fill_gaps(source, mask)
        output, audit = agx.normalize_compensation(filled, self.profile())
        self.assertAlmostEqual(audit['gain'], float(filled.max()) / 254)
        self.assertLessEqual(output.max(), 255)
        self.assertGreater(output.min(), 0)
        np.testing.assert_allclose(output * audit['gain'], filled, rtol=2e-7, atol=1e-5)
        c.encode_rgbd(output)  # Existing final range guard remains authoritative.
        with self.assertRaises(ValueError):
            c.encode_rgbd(filled)

    def test_reject_profile_bounds_modes_and_double_normalization(self):
        source = np.full((1, 1, 3), 508, np.float32)
        profiles = [None, {}, self.profile(mode='original'), self.profile(mode='experimental'),
                    self.profile(version=True), self.profile(extra=1),
                    self.profile(bakedIntensity=3), self.profile(fullyLitThreshold=.01),
                    self.profile(bakedIntensity=np.nan)]
        for profile in profiles:
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                agx.normalize_compensation(source, profile)
        for value in (-1, np.nan, np.inf):
            with self.subTest(value=value), self.assertRaises(ValueError):
                agx.normalize_compensation(np.full((1, 1, 3), value), self.profile())
        with self.assertRaisesRegex(ValueError, 'Already'):
            agx.normalize_compensation(source, self.profile(), already_normalized=True)

    def test_selected_marker_only_and_profile_schema(self):
        artist = self.profile(shadowSuppression=2, bakedDiffusePreservation=.3)
        source = np.full((1, 1, 3), 291.21857, np.float32)
        _, audit = agx.normalize_compensation(source, artist)
        audit['selected_marker'] = 'lightmap_selected'
        model = {'meshes': [dict(name='lightmap_selected', id='keep-id', metadata={
                     'kadshowLighting': artist, 'kadshowColourAdjustment': {'version': 1}, 'keep': 7}),
                 dict(name='lightmap_other', metadata={'kadshowLighting': self.profile()})]}
        before = copy.deepcopy(model)
        ktx.apply_normalization_metadata(model, 'lightmap_selected', audit)
        self.assertEqual(model['meshes'][1], before['meshes'][1])
        self.assertEqual(model['meshes'][0]['id'], 'keep-id')
        self.assertEqual(model['meshes'][0]['metadata']['kadshowColourAdjustment'], {'version': 1})
        self.assertEqual(set(model['meshes'][0]['metadata']['kadshowLighting']), set(DEFAULT_LIGHTING))
        self.assertIn('kadshowAgXRangeNormalization', model['meshes'][0]['metadata'])
        with self.assertRaisesRegex(ValueError, 'already'):
            ktx.apply_normalization_metadata(model, 'lightmap_selected', audit)
        for broken in ({'meshes': []}, {'meshes': [dict(name='lightmap_selected')]},
                       {'meshes': before['meshes'] + [copy.deepcopy(before['meshes'][0])]}):
            with self.assertRaises(ValueError):
                ktx.normalization_marker(broken, 'lightmap_selected')

    def test_linear_diffuse_and_shadow_visibility_equivalence(self):
        source = np.array([[[.001, .01, .05], [278, 291.21857, 220], [.3, 1, 4]]], np.float32)
        for suppression, preservation, no_environment in [(1, 0, False), (3.2, .65, False), (2, 1, True)]:
            artist = self.profile(bakedIntensity=1.2, fullyLitThreshold=1.7,
                                  shadowSuppression=suppression, bakedDiffusePreservation=preservation)
            output, audit = agx.normalize_compensation(source, artist)
            derived = audit['serialized_profile']
            np.testing.assert_allclose(output * derived['bakedIntensity'],
                                       source * artist['bakedIntensity'], rtol=2e-7, atol=1e-5)
            luminance = np.array([.2126, .7152, .0722])
            original_visibility = np.clip((source @ luminance) / artist['fullyLitThreshold'], 0, 1) ** suppression
            derived_visibility = np.clip((output @ luminance) / derived['fullyLitThreshold'], 0, 1) ** suppression
            np.testing.assert_allclose(derived_visibility, original_visibility, rtol=2e-7, atol=1e-7)
            self.assertEqual(derived['bakedDiffusePreservation'], preservation)
            self.assertEqual(derived['reflectionIntensity'], artist['reflectionIntensity'])
            self.assertEqual(derived['highlightPreservation'], artist['highlightPreservation'])
            # No-ENV fallback remains the same linear baked term.
            if no_environment:
                np.testing.assert_allclose(output * derived['bakedIntensity'],
                                           source * artist['bakedIntensity'], rtol=2e-7, atol=1e-5)

    def test_colour_metadata_write_retains_selected_normalized_profile(self):
        # Exercise the actual config write order without Blender or an encoder.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); raw = root/'scene.babylon'; source = root/'source.exr'
            source.write_bytes(b'not decoded by this config-write test')
            artist = self.profile()
            original = {'meshes': [dict(name='lightmap_selected', id='stable', metadata={
                'kadshowLighting': artist, 'keep': 1}), dict(name='lightmap_other', metadata={
                'kadshowLighting': self.profile(shadowSuppression=2)})]}
            raw.write_text(json.dumps(original))
            _, audit = agx.normalize_compensation(np.full((1, 1, 3), 291.21857, np.float32), artist)
            audit['selected_marker'] = 'lightmap_selected'
            prepared = type('PreparedImage', (), {'get': lambda self, name: False})()
            images = []
            fake_bpy = types.SimpleNamespace(data=types.SimpleNamespace(images=images),
                                             path=types.SimpleNamespace(abspath=lambda value: value))
            fake_ca = types.ModuleType(package.__name__ + '.colour_adjustment')
            fake_ca.resolve = lambda scene, options: dict(image=object(), strength=1)
            def prepare(*args):
                images.append(prepared)
                return prepared
            fake_ca.prepare_image = prepare; fake_ca.ENCODING = 'srgb-linear-multiplier2-alpha'
            options = dict(lightmap=str(source), auto_lightmap=False,
                           lightmap_marker='lightmap_selected', lightmap_encoding='rgbd-v1',
                           lightmap_agx=True, lightmap_agx_range_normalization=agx.RANGE_NORMALIZATION)
            context = types.SimpleNamespace(scene=object())
            with patch.dict(sys.modules, {'bpy': fake_bpy, fake_ca.__name__: fake_ca}), \
                 patch.object(agx, 'export_compensated', return_value=(source, {'range_normalization': audit})):
                ktx.make_config(raw, root, options, context, [])
            actual = json.loads(raw.read_text())
            self.assertEqual(actual['meshes'][0]['metadata']['kadshowLighting'], audit['serialized_profile'])
            self.assertIn('kadshowColourAdjustment', actual['meshes'][0]['metadata'])
            self.assertEqual(actual['meshes'][0]['metadata']['keep'], 1)
            self.assertEqual(actual['meshes'][1], original['meshes'][1])
            self.assertEqual(images, [])

    @unittest.skipUnless(os.environ.get('BJS_AGX_ACTUAL_STAGING'), 'Actual scene staging not supplied')
    def test_actual_owned_socket_inputs_without_clipping(self):
        import OpenImageIO as oiio
        import PyOpenColorIO as ocio
        stage = Path(os.environ['BJS_AGX_ACTUAL_STAGING'])
        colour = oiio.ImageBuf(str(stage/'agx/albedo_uv.exr')).get_pixels(oiio.FLOAT)
        lighting = oiio.ImageBuf(str(stage/'agx/combined_linear.exr')).get_pixels(oiio.FLOAT)
        config = ocio.Config.CreateFromFile(os.environ['BJS_OCIO_CONFIG'])
        processor = config.getProcessor(ocio.DisplayViewTransform(
            src='Linear Rec.709', display='sRGB', view='AgX')).getDefaultCPUProcessor()
        mask = colour[..., 3] > .5
        with self.assertRaisesRegex(ValueError, 'exceeds RGBD range'):
            agx.compensate(colour[..., :3], lighting[..., :3], processor, mask)
        raw, _ = agx.compensate(colour[..., :3], lighting[..., :3], processor, mask,
                                range_normalization=agx.RANGE_NORMALIZATION)
        bad = mask & (raw.max(axis=2) > 255)
        labels = np.load(os.environ['BJS_AGX_OWNERSHIP'], allow_pickle=False)
        self.assertEqual(int(bad.sum()), 64)
        self.assertEqual(set(np.unique(labels[bad])), {1600, 1638, 1673, 1711, 1746})
        filled = agx.fill_gaps(raw, mask)
        normalized, audit = agx.normalize_compensation(filled, self.profile())
        self.assertTrue(np.isfinite(normalized).all())
        self.assertLessEqual(float(normalized.max()), 255)
        self.assertAlmostEqual(audit['gain'], float(filled.max()) / 254)
        np.testing.assert_allclose(normalized * audit['gain'], filled, rtol=2e-7, atol=1e-5)
        c.encode_rgbd(normalized)


class RGBDTests(unittest.TestCase):
    def test_hdr_range_and_shadow_precision(self):
        rgb = np.array([[[0, .001, .05], [.25, .5, 1], [2, 10, 63], [255, 64, 1]]], dtype=np.float32)
        pixels, metrics = c.encode_rgbd(rgb)
        restored = (pixels[..., :3] / 255) ** 2.2 / (pixels[..., 3:4] / 255)
        self.assertGreater(restored.max(), 250)
        self.assertTrue(np.all(pixels[..., 3] >= 1))
        self.assertTrue(np.all(pixels[0, :2, 3] == 255))
        np.testing.assert_allclose(restored[:, :3], rgb[:, :3], rtol=.025, atol=.0003)
        # At the format's upper limit, weak channels share a 255x divisor.
        # Their absolute quantization step is larger than in shadow pixels.
        np.testing.assert_allclose(restored[:, 3:], rgb[:, 3:], rtol=.025, atol=.06)
        self.assertGreater(metrics['linear_max'], 1)

    def test_image_wide_divisor_is_derived_from_full_peak(self):
        rgb = np.array([[[0, .001, .05], [2, 10, 47.3232269]]], np.float32)
        default, _ = c.encode_rgbd(rgb)
        explicit, _ = c.encode_rgbd(rgb, 'per-pixel')
        np.testing.assert_array_equal(default, explicit)
        pixels, metrics = c.encode_rgbd(rgb, 'image-wide')
        self.assertTrue(np.all(pixels[..., 3] == 5))
        self.assertEqual(metrics['rgbd_encoded_divisor_mode'], 'image-wide')
        self.assertAlmostEqual(metrics['rgbd_source_peak'], float(rgb.max()))
        self.assertAlmostEqual(metrics['rgbd_image_divisor'], 5 / 255, places=8)
        expected = np.rint(np.power(rgb * np.float32(5 / 255), 1 / 2.2) * 255).astype(np.uint8)
        np.testing.assert_array_equal(pixels[..., :3], expected)
        # Existing metadata floor keeps a lost alpha byte from amplifying energy.
        lost_alpha = pixels.copy(); lost_alpha[..., 3] = 0
        clamped = np.power(lost_alpha[..., :3] / 255, 2.2) / np.maximum(lost_alpha[..., 3:4] / 255, 5 / 255)
        intended = np.power(pixels[..., :3] / 255, 2.2) / (5 / 255)
        np.testing.assert_array_equal(clamped, intended)
        for peak, divisor in [(0, 255), (2, 127), (63, 4), (255, 1)]:
            other, _ = c.encode_rgbd(np.full((2, 2, 3), peak, np.float32), 'image-wide')
            self.assertTrue(np.all(other[..., 3] == divisor))
        with self.assertRaisesRegex(ValueError, 'Unsupported RGBD divisor mode'):
            c.encode_rgbd(rgb, 'unknown')

    def test_fixed_divisor_keyword_preserves_image_wide_bytes_and_metrics(self):
        # Include black, shadows, mixed peaks and the upper format boundary.
        for peak in (0, 1, 2, 47.3232269, 63, 255):
            rgb = np.array([[[0, 0, 0], [peak, peak*.1, peak*.001]],
                            [[peak*.5, 0, peak*.9], [0, peak, peak*.3]]], np.float32)
            expected, metrics = c.encode_rgbd(rgb, 'image-wide')
            for mode in ('per-pixel', 'image-wide'):
                with self.subTest(peak=peak, mode=mode):
                    actual, actual_metrics = c.encode_rgbd(rgb, mode, fixed_divisor=True)
                    np.testing.assert_array_equal(actual, expected)
                    self.assertEqual(actual_metrics, metrics)
            default, _ = c.encode_rgbd(rgb)
            explicit, _ = c.encode_rgbd(rgb, fixed_divisor=False)
            np.testing.assert_array_equal(default, explicit)

    def test_image_wide_preparation_uses_resized_full_image(self):
        from unittest.mock import patch
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'source.exr'; source.write_bytes(b'placeholder')
            spec = dict(source_path=str(source), output='wide.ktx2', size=512,
                        encoding='rgbd-v1', rgbd_divisor_mode='image-wide', color_space='linear', alpha='preserve', flip_y=True)
            # Each 2x2 block averages one64 sample to16 before encoding.
            raw = np.zeros((1024, 1024, 3), np.float32); raw[::2, ::2] = 64
            with patch('cv2.imread', return_value=raw):
                path, metrics = c.prepare(spec, root)
            # The specification and encoding metrics must merge in a real report.
            record = dict(**spec, **metrics)
            self.assertEqual(record['rgbd_divisor_mode'], 'image-wide')
            self.assertEqual(record['rgbd_encoded_divisor_mode'], 'image-wide')
            self.assertEqual(metrics['linear_max'], 16)
            self.assertEqual(metrics['alpha_min'], 15)
            self.assertEqual(metrics['alpha_max'], 15)
            self.assertEqual(np.asarray(Image.open(path)).shape, (512, 512, 4))

    def test_divisor_mode_plan_rejects_non_rgbd_and_unknown_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'source.exr').write_bytes(b'placeholder')
            (root / 'scene.babylon').write_text(json.dumps(dict(meshes=[dict(name='lightmap_old', id='stable')])))
            texture = dict(source='source.exr', output='wide.ktx2', lightmap_markers=['lightmap_old'], flip_y=True,
                           color_space='linear', codec='uastc', mipmaps=False, alpha='preserve', encoding='rgbd-v1', rgbd_divisor_mode='image-wide')
            config = dict(version=1, output_dir='out', models=[dict(source='scene.babylon')], textures=[texture])
            path = root / 'config.json'; path.write_text(json.dumps(config))
            self.assertEqual(c.load_plan(path)['textures'][0]['rgbd_divisor_mode'], 'image-wide')
            texture['rgbd_divisor_mode'] = 'typo'; path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError, 'Unsupported RGBD divisor mode'): c.load_plan(path)
            texture.update(encoding='legacy', rgbd_divisor_mode='image-wide'); path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError, 'requires rgbd-v1'): c.load_plan(path)

    def test_reject_invalid_energy_instead_of_clipping(self):
        for value in [-.01, 256, np.nan, np.inf]:
            with self.assertRaisesRegex(ValueError, 'not clipped'):
                c.encode_rgbd(np.full((1, 1, 3), value))

    def test_marker_metadata_is_opt_in_and_preserves_hierarchy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'source.exr').write_bytes(b'placeholder; planning does not decode')
            original = dict(meshes=[dict(name='lightmap_old', id='stable', metadata={'keep': 1}), dict(name='receiver', parentId='stable', indices=[0,1,2])])
            (root/'scene.babylon').write_text(json.dumps(original))
            texture = dict(source='source.exr', output='room_lightmap_v1.ktx2', lightmap_markers=['lightmap_old'],
                flip_y=True, color_space='linear', codec='uastc', mipmaps=False, alpha='preserve', encoding='rgbd-v1')
            config = dict(version=1, output_dir='out', models=[dict(source='scene.babylon')], textures=[texture])
            path=root/'config.json'
            path.write_text(json.dumps(config)); plan=c.load_plan(path)
            node=plan['models'][0]['data']['meshes'][0]
            self.assertEqual(node['name'],'lightmap_room_v1')
            self.assertEqual(node['metadata'],{'keep':1,'kadshowLightmapEncoding':'rgbd-v1'})
            self.assertEqual(node['id'],'stable')
            self.assertEqual(plan['models'][0]['data']['meshes'][1],original['meshes'][1])
            texture.update(encoding='legacy',color_space='srgb',alpha='discard')
            path.write_text(json.dumps(config)); legacy=c.load_plan(path)
            self.assertEqual(legacy['models'][0]['data']['meshes'][0]['metadata'],{'keep':1})
            texture['encoding']='rgbd-v1'
            path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError,'RGBD requires'):
                c.load_plan(path)

    def test_compressed_alpha_bound_is_per_lightmap(self):
        model = dict(meshes=[dict(name='lightmap_a', metadata={'kadshowLightmapEncoding':'rgbd-v1'}),
                            dict(name='lightmap_legacy', metadata={'keep':1})],
                     transformNodes=[dict(name='lightmap_b', metadata={'kadshowLightmapEncoding':'rgbd-v1'})])
        c.apply_rgbd_bounds(model, [dict(output='a.ktx2', encoding='rgbd-v1', alpha_min=4),
                                   dict(output='b.ktx2', encoding='rgbd-v1', alpha_min=16),
                                   dict(output='legacy.ktx2', encoding='legacy')])
        self.assertEqual(model['meshes'][0]['metadata']['kadshowLightmapMinDivisor'],4/255)
        self.assertEqual(model['transformNodes'][0]['metadata']['kadshowLightmapMinDivisor'],16/255)
        self.assertEqual(model['meshes'][1]['metadata'],{'keep':1})

    def test_canonical_filename_and_collision(self):
        self.assertEqual(c.lightmap_output_name('room_lightmap_v1.ktx2'),'room_v1.ktx2')
        with self.assertRaises(ValueError): c.lightmap_output_name('lightmap_.ktx2')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'x.png').write_bytes(b'not decoded')
            (root/'m.babylon').write_text('{}')
            base=dict(source='x.png',flip_y=False,color_space='srgb',codec='uastc',mipmaps=False)
            config=dict(version=1,models=[dict(source='m.babylon')],textures=[
                dict(base,output='room_lightmap_v1.ktx2',lightmap_markers=['lightmap_a']),
                dict(base,output='room_v1.ktx2',lightmap_markers=['lightmap_b'])])
            path=root/'config.json';path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError,'Duplicate output'):c.load_plan(path)


if __name__ == '__main__': unittest.main()
