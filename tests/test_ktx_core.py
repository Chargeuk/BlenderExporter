"""Mapping/overwrite regressions plus a small real KTX integration fixture."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import importlib.util
module_path = Path(__file__).resolve().parents[1] / 'src/babylon_js/ktx_conversion.py'
module_spec = importlib.util.spec_from_file_location('ktx_conversion', module_path)
c = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(c)


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        from PIL import Image
        for name in ('a.png', 'b.png', 'light.png'):
            Image.new('RGBA', (32, 32), (31, 113, 209, 128 if name == 'a.png' else 255)).save(self.root / name)
        self.model = dict(materials=[
            dict(name='a/shared.png', albedoTexture=dict(name='a/shared.png', gammaSpace=False),
                 clearCoat=dict(texture=dict(name='b/shared.png'))),
            dict(diffuseTexture=dict(name='a/shared.png'), textures=[dict(name='a/shared.png')])],
            meshes=[dict(name='lightmap_old', id='stable-marker', positions=[1,2,3]),
                    dict(name='a/shared.png', parentId='stable-marker', indices=[0])],
            metadata=dict(note='a/shared.png'), environmentTexture='unconverted.exr')
        for name in ('one.babylon', 'two.babylon'):
            (self.root / name).write_text(json.dumps(self.model))
        self.config = dict(version=1, output_dir='output',
            models=[dict(source='one.babylon'), dict(source='two.babylon')],
            textures=[dict(source='a.png', output='a.ktx2', references=['a/shared.png'], flip_y=True,
                           color_space='srgb', codec='basis-lz', mipmaps=True),
                      dict(source='b.png', output='b.ktx2', references=['b/shared.png'], flip_y=False,
                           color_space='linear', codec='uastc', mipmaps=False, alpha='opaque'),
                      dict(source='light.png', output='lighting.ktx2', lightmap_markers=['lightmap_old'],
                           flip_y=True, color_space='srgb', codec='uastc', mipmaps=False, alpha='discard')])
        self.path = self.root / 'config.json'

    def tearDown(self):
        self.temp.cleanup()

    def plan(self):
        self.path.write_text(json.dumps(self.config))
        return c.load_plan(self.path)

    def test_multiple_models_slots_and_marker_preserve_other_data(self):
        plan = self.plan()
        self.assertEqual(len(plan['models']), 2)
        for entry in plan['models']:
            model = entry['data']
            self.assertEqual(model['materials'][0]['albedoTexture']['name'], 'a.ktx2')
            self.assertTrue(model['materials'][0]['albedoTexture']['gammaSpace'])
            self.assertEqual(model['materials'][0]['clearCoat']['texture']['name'], 'b.ktx2')
            self.assertFalse(model['materials'][0]['clearCoat']['texture']['gammaSpace'])
            self.assertEqual(model['materials'][1]['textures'][0]['name'], 'a.ktx2')
            self.assertEqual(model['meshes'][0]['name'], 'lightmap_lighting')
            self.assertEqual(model['meshes'][0]['id'], 'stable-marker')
            self.assertEqual(model['meshes'][1], self.model['meshes'][1])
            self.assertEqual(model['metadata'], self.model['metadata'])
            self.assertEqual(model['materials'][0]['name'], 'a/shared.png')
            self.assertEqual(model['environmentTexture'], 'unconverted.exr')
        self.assertFalse((self.root / 'output').exists())

    def test_bad_reference_fails_before_any_conversion(self):
        self.config['textures'][0]['references'] = ['missing/shared.png']
        with self.assertRaisesRegex(ValueError, 'not found'):
            self.plan()

    def test_collisions_ambiguous_mapping_and_source_overwrite(self):
        original = copy.deepcopy(self.config)
        self.config['textures'][1]['output'] = 'A.ktx2'
        with self.assertRaisesRegex(ValueError, 'Duplicate output'):
            self.plan()
        self.config = copy.deepcopy(original)
        self.config['textures'][1]['references'] = ['a/shared.png']
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            self.plan()
        self.config = copy.deepcopy(original)
        self.config['output_dir'] = '.'
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            self.plan()

    def test_prepare_keeps_alpha_and_exact_flip(self):
        from PIL import Image
        prepared_dir = self.root / 'prepared'
        prepared_dir.mkdir()
        spec = self.plan()['textures'][0]
        path, _ = c.prepare(spec, prepared_dir)
        with Image.open(path) as image:
            self.assertEqual(image.mode, 'RGBA')
            self.assertEqual(image.getchannel('A').getextrema(), (128,128))
        spec['alpha'] = 'opaque'
        with self.assertRaisesRegex(ValueError, 'Non-opaque'):
            c.prepare(spec, prepared_dir)

    def test_failed_encoder_leaves_delivery_untouched(self):
        plan = self.plan()
        output = self.root / 'output'
        output.mkdir()
        (output / 'one.babylon').write_text('previous')
        import subprocess
        def fake(command, **kwargs):
            return subprocess.CompletedProcess(command, 0 if '--version' in command else 1, stdout='encoder unavailable')
        with patch.object(c.subprocess, 'run', side_effect=fake):
            with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                c.run_conversion(plan, 'fake-ktx', 1)
        self.assertEqual((output / 'one.babylon').read_text(), 'previous')
        self.assertEqual(len(list(output.iterdir())), 1)

    def test_delivery_failure_rolls_back_existing_files(self):
        destination, stage, run = [self.root / name for name in ('dest', 'stage', 'run')]
        for directory in (destination, stage, run):
            directory.mkdir()
        for name in ('one.babylon','two.babylon'):
            (destination/name).write_text('previous')
            (stage/name).write_text('replacement')
        real_copy = c.shutil.copy2
        def failing_copy(src, dst, **kwargs):
            if Path(src) == stage/'two.babylon':
                raise OSError('simulated failed delivery')
            return real_copy(src,dst,**kwargs)
        with patch.object(c.shutil, 'copy2', side_effect=failing_copy):
            with self.assertRaises(OSError):
                c.commit_delivery(list(stage.iterdir()), destination, run)
        self.assertTrue(all(p.read_text() == 'previous' for p in destination.iterdir()))

    def test_real_ktx_multi_model_conversion(self):
        tool = Path('H:/code/texture-tools/KTX-Software-4.4.2/bin/ktx.exe')
        if not tool.is_file():
            self.skipTest('Native Windows integration KTX not installed')
        report = c.run_conversion(self.plan(), str(tool), 2)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(len(report['delivery_files']), 5)
        for name in ('one.babylon','two.babylon'):
            model = c.read_json(self.root/'output'/name)
            for slot in c.inspect_model(model)['texture_references']:
                if slot['reference'].endswith('.ktx2'):
                    self.assertTrue((self.root/'output'/slot['reference']).is_file())


if __name__ == '__main__':
    unittest.main()
