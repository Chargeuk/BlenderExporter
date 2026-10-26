"""Capture/derive orchestration with actual volume math, mocked Blender and I/O.

The fixture stores NumPy arrays in .exr-named files. It is not an EXR encoder,
native probe capture, calibration or production source-association proof.
"""
import contextlib
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import numpy as np

ROOT = Path(__file__).resolve().parents[1]/'src/babylon_js'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


class SimpleOnlyCapture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        package = types.ModuleType('babylon_js')
        package.__path__ = [str(ROOT)]
        self.stack.enter_context(patch.dict(sys.modules, {'babylon_js': package}))
        self.volume = module('babylon_js.environment_lighting_volume', ROOT/'environment_lighting_volume.py')
        self.capture = module('tested_simple_volume_capture', ROOT/'environment_tools/volume_capture.py')
        self.source = self.root/'source.blend'; self.source.write_bytes(b'fixture source')
        self.world_image = self.root/'world.exr'; self.world_image.write_bytes(b'fixture World')
        self.combined = self.root/'combined.exr'; self.combined.write_bytes(b'fixture combined')
        self.provenance = self.root/'combined.provenance.json'; self.provenance.write_text('{}')
        self.scope = self.root/'scope.json'; self.scope.write_text(json.dumps({'preview_material': 'Fixture Preview'}))
        self.reference = self.root/'reference.exr'
        self.write_pixels(self.reference, np.ones((16, 32, 4), np.float32))
        self.material = types.SimpleNamespace(name='Fixture Preview')
        self.scene = types.SimpleNamespace(
            world={'bjs_environment_image': str(self.reference)}, objects=[],
            camera=types.SimpleNamespace(location=None), render=types.SimpleNamespace(filepath=''))
        self.scene.get = lambda key, default=None: 'Fixture Preview' if key == 'bjs_lighting_controls_material' else default
        self.render = Mock(side_effect=self.render_probe)
        self.bpy = types.SimpleNamespace(
            app=types.SimpleNamespace(background=True),
            data=types.SimpleNamespace(filepath=str(self.source), is_dirty=False, images=[], materials={'Fixture Preview': self.material}),
            context=types.SimpleNamespace(scene=self.scene),
            path=types.SimpleNamespace(abspath=lambda value: value),
            ops=types.SimpleNamespace(render=types.SimpleNamespace(render=self.render)))
        self.source_validation = Mock()
        self.stack.enter_context(patch.object(self.volume, 'validate_source', self.source_validation))
        self.hemispheres = Mock(wraps=self.volume.hemisphere_means)
        self.stack.enter_context(patch.object(self.volume, 'hemisphere_means', self.hemispheres))
        world_tools = types.SimpleNamespace(find_controls=lambda world: object(), settings=lambda node: {'image': str(self.world_image)})
        package.environment_controls = world_tools
        modules = {
            'bpy': self.bpy,
            'common': types.SimpleNamespace(sha=self.sha, save_json=self.save_json),
            'worker': types.SimpleNamespace(capture=lambda *a, **kw: None, write_exr=self.write_pixels),
            'appearance': types.SimpleNamespace(verify_combination=lambda *a: None, preview_identity=lambda material: 'a'*64),
            'spatial_capture': types.SimpleNamespace(set_exr_format=lambda scene: None, verify_gpu=lambda *a: ['fixture']),
            'ao': types.SimpleNamespace(geometry_identity=lambda recipe: {'geometry_sha256': 'b'*64}),
            'babylon_js.agx_lightmap': types.SimpleNamespace(read_exr=self.read_pixels),
            'babylon_js.environment_controls': world_tools,
        }
        self.stack.enter_context(patch.dict(sys.modules, modules))
        self.stack.enter_context(patch.object(self.capture, 'capture_geometry', return_value=object()))
        self.stack.enter_context(patch.object(self.capture, 'clearance', return_value={'valid': True, 'rays': []}))
        self.stack.enter_context(patch.object(self.capture, 'preview'))

    @staticmethod
    def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    @staticmethod
    def save_json(path, value): Path(path).write_text(json.dumps(value, indent=2), encoding='utf-8')

    @staticmethod
    def write_pixels(path, pixels):
        with Path(path).open('wb') as stream: np.save(stream, pixels, allow_pickle=False)

    @staticmethod
    def read_pixels(path):
        with Path(path).open('rb') as stream: return np.load(stream, allow_pickle=False)

    def render_probe(self, **kwargs):
        pixels = np.ones((16, 32, 4), np.float32)
        pixels[..., :3] = [2., 1., .5]
        self.write_pixels(self.scene.render.filepath, pixels)

    def config(self, name, option=...):
        config = dict(schema_version=1, source_scene=str(self.source), source_sha256=self.sha(self.source),
            scope_recipe=str(self.scope), combined=str(self.combined), combined_provenance=str(self.provenance),
            reference_environment=str(self.reference), output_dir=str(self.root/name),
            bounds={'min': [-1, 0, -1], 'max': [1, 2, 1]}, coarse_dimensions=[2, 2, 2], dimensions=[32, 16, 32],
            minimum_valid_probes=1, clearance=.08, panorama={'size': 32, 'samples': 1}, trial_indices=[[0, 0, 0]])
        if option is not ...: config['include_advanced'] = option
        path = self.root/(name+'.json'); self.save_json(path, config)
        return path, Path(config['output_dir'])

    def capture_all(self, name, option=...):
        config, output = self.config(name, option)
        self.capture.run(config, 'trial')
        self.capture.run(config, 'capture')
        receipt = output/'volume_receipt.json'
        return config, output, receipt, json.loads(receipt.read_text())

    def test_default_still_produces_both_with_one_render_per_probe(self):
        _, output, _, record = self.capture_all('default')
        self.volume.validate_record(record)
        self.assertIn('advanced', record)
        self.assertTrue((output/record['advanced']['master']).is_file())
        self.assertEqual(self.render.call_count, 8)  # trial checkpoint reused

    def test_explicit_true_keeps_default_simple_bytes_and_probe_checkpoints(self):
        _, first, _, default = self.capture_all('default')
        _, second, _, explicit = self.capture_all('explicit', True)
        self.assertEqual(default['source'], explicit['source'])
        self.assertEqual((first/default['master']).read_bytes(), (second/explicit['master']).read_bytes())
        self.assertEqual((first/'trial_00/panorama.exr').read_bytes(), (second/'trial_00/panorama.exr').read_bytes())
        self.assertEqual(self.render.call_count, 16)

    def test_false_produces_untouched_simple_only_receipt_without_directional_work(self):
        _, output, _, record = self.capture_all('simple', False)
        self.volume.validate_record(record)
        self.assertNotIn('advanced', record)
        self.assertEqual(list(output.glob('advanced*')), [])
        self.hemispheres.assert_not_called()
        self.assertEqual(self.render.call_count, 8)

    def test_false_keeps_default_simple_payload_and_clearance_and_panorama_bytes(self):
        _, both, _, a = self.capture_all('both')
        _, simple, _, b = self.capture_all('simple', False)
        self.assertEqual(a, dict(b, advanced=a['advanced']))
        for name in (a['master'], 'coarse_valid_mask.npy', 'coarse_volume.npy', 'volume_rgba.npy',
                     'probe_clearance.json', 'trial_00/probe.json', 'trial_00/panorama.exr'):
            self.assertEqual((both/name).read_bytes(), (simple/name).read_bytes(), name)
        self.assertEqual(self.render.call_count, 16)

    def test_non_boolean_options_fail_before_output_or_render(self):
        for index, value in enumerate((None, 0, 1, 'false', 'true', [], {}, .0)):
            with self.subTest(value=value):
                config, output = self.config('invalid'+str(index), value)
                with self.assertRaisesRegex(ValueError, 'include_advanced must be a boolean'):
                    self.capture.run(config, 'trial')
                self.assertFalse(output.exists())
        self.render.assert_not_called()

    def test_actual_simple_only_output_is_derived_without_render_or_input_rewrite(self):
        config, output, receipt, record = self.capture_all('simple', False)
        master_bytes = (output/record['master']).read_bytes()
        receipt_bytes = receipt.read_bytes()
        orientation = self.root/'orientation.json'
        self.save_json(orientation, dict(status='passed', panoramaConvention=self.volume.PANORAMA_CONVENTION,
            directionOrder=self.volume.DIRECTION_ORDER,
            samples=[{'direction': direction, 'passed': True} for direction in self.volume.DIRECTION_ORDER]))
        before_renders = self.render.call_count
        target = self.root/'derived'
        self.capture.derive(config, receipt, target, orientation)
        derived = json.loads((target/'volume_receipt.json').read_text())
        self.volume.validate_record(derived)
        self.assertIn('advanced', derived)
        self.assertEqual({k: v for k, v in derived.items() if k != 'advanced'}, record)
        self.assertEqual(self.render.call_count, before_renders)
        self.assertEqual(receipt.read_bytes(), receipt_bytes)
        self.assertEqual((target/'simple_volume_receipt.json').read_bytes(), receipt_bytes)
        self.assertEqual((output/record['master']).read_bytes(), master_bytes)
        self.assertEqual((target/record['master']).read_bytes(), master_bytes)
        self.source_validation.assert_called_once()

    def test_existing_advanced_receipt_still_rejects_derivation(self):
        config, _, receipt, _ = self.capture_all('both')
        with self.assertRaisesRegex(ValueError, 'Simple-only'):
            self.capture.derive(config, receipt, self.root/'derived', self.root/'not-read.json')
        self.assertFalse((self.root/'derived').exists())


if __name__ == '__main__': unittest.main()
