"""Opt-in native diffuse framing, pass isolation and physical-context contracts."""
import copy
from contextlib import contextmanager
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src/babylon_js/environment_tools'))
from camera_views import validate_camera_set, _capture_modes
spec = importlib.util.spec_from_file_location('native_diffuse_contract', ROOT/'src/babylon_js/workflow_tools/native_view_diagnostics.py')
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class DiffuseContractTests(unittest.TestCase):
    def setUp(self):
        self.config = {'scene': 'Source', 'capture': {'views': {'dimensions': [64, 48],
            'samples': 4, 'mode': 'physical-diffuse', 'denoise': False,
            'source_sha256': 'a'*64, 'camera_set': [{'id': 'front', 'camera': 'Camera'}]}}}

    def test_explicit_opt_in_does_not_mutate_recipe(self):
        before = copy.deepcopy(self.config)
        self.assertEqual(validate_camera_set(self.config), self.config['capture']['views']['camera_set'])
        self.assertEqual(self.config, before)

    def test_reference_requires_hash_no_denoise_and_truthful_png_mode(self):
        views = self.config['capture']['views']
        for key, value in [('source_sha256', None), ('source_sha256', 'A'*64),
                           ('denoise', True), ('color_mode', 'RGBA')]:
            with self.subTest(key=key, value=value):
                changed = copy.deepcopy(self.config)
                changed['capture']['views'][key] = value
                with self.assertRaises(ValueError): validate_camera_set(changed)
        views['delivery_contract'] = 'contract.json'
        with self.assertRaises(ValueError): validate_camera_set(self.config)

    def test_legacy_modes_need_no_new_source_hash(self):
        views = self.config['capture']['views']
        del views['source_sha256']
        for mode in ('physical', 'baked'):
            views['mode'] = mode
            self.assertEqual(len(validate_camera_set(self.config)), 1)

    def test_pass_and_linear_stems_cannot_overwrite_each_other(self):
        entries = self.config['capture']['views']['camera_set']
        entries.append({'id': 'front_passes', 'camera': 'Camera'})
        with self.assertRaisesRegex(ValueError, 'colliding'):
            validate_camera_set(self.config)
        entries[-1]['id'] = 'FRONT'
        with self.assertRaisesRegex(ValueError, 'colliding'):
            validate_camera_set(self.config)

    def test_physical_and_diffuse_share_one_overlay_before_baked(self):
        events = []
        @contextmanager
        def context(name):
            events.append('enter '+name)
            try: yield {'context': name}
            finally: events.append('exit '+name)
        entries = [{'id': 'b', 'mode': 'baked'}, {'id': 'd', 'mode': 'physical-diffuse'},
                   {'id': 'p', 'mode': 'physical'}]
        def render(entry, mode, record):
            events.append(mode)
            return (entry['id'], mode, record)
        result = _capture_modes(entries, {}, lambda: context('physical'), lambda: context('baked'), render)
        self.assertEqual([row[0] for row in result], ['b', 'd', 'p'])
        self.assertIs(result[1][2], result[2][2])
        self.assertEqual(events, ['enter physical', 'physical-diffuse', 'physical', 'exit physical',
                                  'enter baked', 'baked', 'exit baked'])

    def test_failed_diffuse_render_exits_material_context(self):
        events = []
        @contextmanager
        def context():
            try: yield None
            finally: events.append('restored')
        def render(*args): raise RuntimeError('render failure')
        with self.assertRaisesRegex(RuntimeError, 'render failure'):
            _capture_modes([{'id': 'd', 'mode': 'physical-diffuse'}], {}, context, context, render)
        self.assertEqual(events, ['restored'])

    def test_native_pass_transaction_restores_on_failure(self):
        identifiers = lambda values: SimpleNamespace(enum_items=[SimpleNamespace(identifier=value) for value in values])
        fmt = SimpleNamespace(media_type='IMAGE', file_format='PNG', color_mode='RGB', color_depth='8',
                              exr_codec='PIZ', use_exr_interleave=False)
        fmt.bl_rna = SimpleNamespace(properties={'media_type': identifiers(['IMAGE', 'MULTI_LAYER_IMAGE']),
                                                'file_format': identifiers(['PNG', 'OPEN_EXR_MULTILAYER'])})
        scene = SimpleNamespace(render=SimpleNamespace(image_settings=fmt, use_compositing=True, use_sequencer=True),
                                cycles=SimpleNamespace(use_denoising=True))
        layer = SimpleNamespace(use_pass_diffuse_direct=False, use_pass_diffuse_indirect=True,
                                use_pass_diffuse_color=False, cycles=SimpleNamespace(use_denoising=True))
        original_format = native.image_format_state(fmt)
        with self.assertRaisesRegex(RuntimeError, 'fixture failure'):
            with native.diffuse_passes(scene, layer) as receipt:
                self.assertEqual(fmt.file_format, 'OPEN_EXR_MULTILAYER')
                self.assertEqual((fmt.color_depth, fmt.exr_codec), ('32', 'ZIP'))
                self.assertTrue(layer.use_pass_diffuse_direct and layer.use_pass_diffuse_indirect and layer.use_pass_diffuse_color)
                self.assertFalse(scene.render.use_compositing or scene.render.use_sequencer or scene.cycles.use_denoising or layer.cycles.use_denoising)
                self.assertEqual(receipt['formula'], '(Diffuse Direct + Diffuse Indirect) * Diffuse Color')
                raise RuntimeError('fixture failure')
        self.assertEqual(native.image_format_state(fmt), original_format)
        self.assertTrue(scene.render.use_compositing and scene.render.use_sequencer and scene.cycles.use_denoising and layer.cycles.use_denoising)
        self.assertEqual((layer.use_pass_diffuse_direct, layer.use_pass_diffuse_indirect, layer.use_pass_diffuse_color), (False, True, False))


if __name__ == '__main__': unittest.main()
