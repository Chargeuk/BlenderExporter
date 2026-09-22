"""RGBD data contract, legacy preservation and marker naming regressions."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np

spec = importlib.util.spec_from_file_location('core', Path(__file__).resolve().parents[1] / 'src/babylon_js/ktx_conversion.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


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
