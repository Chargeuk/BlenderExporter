"""Pure provenance/framing contracts; no bpy or optional image libraries."""
import copy
from contextlib import contextmanager
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src/babylon_js/environment_tools'))
from camera_views import validate_native_diagnostics, validate_camera_set, _capture_modes


class NativeContractTests(unittest.TestCase):
    def setUp(self):
        self.config = {'scene': 'Scene', '_source': str(Path('fixture.blend').resolve()),'uv':'UVMap','size':64}
        self.recipe = {'source': self.config['_source'], 'source_sha256': 'a'*64,
            'native_engine': 'BLENDER_WORKBENCH', 'normal_guard': True, 'render_aa': '16',
            'frame': 1, 'dimensions': [96, 64],
            'display': {'view_transform': 'Standard', 'look': 'None', 'exposure': 0, 'gamma': 1},
            'presets': {'plain': {'light': 'STUDIO', 'studio_light': 'paint.sl',
                'color_type': 'SINGLE', 'single_color': [.6, .6, .6],
                'show_shadows': False, 'show_cavity': True, 'cavity_type': 'BOTH',
                'background_type': 'WORLD', 'background_color': [.05, .05, .05]}},
            'jobs': [{'id': 'front', 'objects': ['Cube'], 'shading': 'plain',
                'backface_culling': True, 'lens': 35,
                'matrix_world': [[1,0,0,0],[0,1,0,0],[0,0,1,5],[0,0,0,1]]}]}

    def validate(self):
        return validate_native_diagnostics(self.config, self.recipe)

    def test_valid_recipe_is_not_mutated(self):
        before = copy.deepcopy((self.config, self.recipe))
        self.assertEqual(self.validate(), self.recipe['jobs'])
        self.assertEqual((self.config, self.recipe), before)

    def test_source_and_hash_are_explicit(self):
        for key, value in [('source', 'different.blend'), ('source_sha256', 'A'*64),
                           ('source_sha256', ''), ('native_engine', 'CYCLES')]:
            with self.subTest(key=key, value=value):
                old = self.recipe[key]; self.recipe[key] = value
                with self.assertRaises(ValueError): self.validate()
                self.recipe[key] = old

    def test_no_implicit_guard_frame_or_display(self):
        for key in ('normal_guard', 'frame', 'display', 'render_aa'):
            with self.subTest(key=key):
                old = self.recipe.pop(key)
                with self.assertRaises(ValueError): self.validate()
                self.recipe[key] = old

    def test_explicit_membership_rejects_bad_or_duplicate_names(self):
        for names in ([], ['Cube', 'Cube'], [None], [['Cube']], ['']):
            with self.subTest(names=names):
                self.recipe['jobs'][0]['objects'] = names
                with self.assertRaises(ValueError): self.validate()

    def test_culling_is_boolean_and_preset_is_named(self):
        job = self.recipe['jobs'][0]
        job['backface_culling'] = 1
        with self.assertRaises(ValueError): self.validate()
        job['backface_culling'] = False; job['shading'] = 'missing'
        with self.assertRaises(ValueError): self.validate()

    def test_presets_cannot_inherit_missing_settings(self):
        preset = self.recipe['presets']['plain']
        del preset['single_color']
        with self.assertRaises(ValueError): self.validate()

    def test_presets_do_not_accept_arbitrary_rna(self):
        self.recipe['presets']['plain']['show_xray'] = True
        with self.assertRaises(ValueError): self.validate()

    def test_camera_ids_are_safe_and_unique(self):
        self.recipe['jobs'][0]['id'] = '../escape'
        with self.assertRaises(ValueError): self.validate()
        self.recipe['jobs'][0]['id'] = 'front'
        self.recipe['jobs'].append(copy.deepcopy(self.recipe['jobs'][0]))
        with self.assertRaises(ValueError): self.validate()

    def test_native_reuses_matrix_and_clip_contracts(self):
        job = self.recipe['jobs'][0]
        job['matrix_world'][3][0] = .1
        with self.assertRaises(ValueError): self.validate()
        job['matrix_world'][3][0] = 0
        job['camera_properties'] = {'clip_start': 5, 'clip_end': 1}
        with self.assertRaises(ValueError): self.validate()

    def test_nonfinite_and_boolean_dimensions_are_rejected(self):
        self.recipe['dimensions'] = [True, 64]
        with self.assertRaises(ValueError): self.validate()
        self.recipe['dimensions'] = [96, 64]
        self.recipe['jobs'][0]['lens'] = float('nan')
        with self.assertRaises(ValueError): self.validate()

    def test_ordinary_contract_remains_opt_in(self):
        self.assertIsNone(validate_camera_set({'scene': 'Scene'}))
        ordinary = {'scene': 'Scene', 'capture': {'views': {'dimensions': [96,64],
            'samples': 1, 'mode': 'physical', 'camera_set': [{'id':'view','camera':'Camera'}]}}}
        self.assertEqual(validate_camera_set(ordinary)[0]['camera'], 'Camera')
        ordinary['capture']['views']['mode'] = 'native'
        with self.assertRaises(ValueError): validate_camera_set(ordinary)

    def test_existing_physical_baked_context_order(self):
        events=[]
        @contextmanager
        def context(name):
            events.append('enter '+name)
            try: yield name
            finally: events.append('exit '+name)
        entries=[{'id':'b','mode':'baked'},{'id':'p','mode':'physical'}]
        result=_capture_modes(entries, {}, lambda:context('physical'),lambda:context('baked'),
            lambda entry,mode,record: (entry['id'],mode,record))
        self.assertEqual([row[0] for row in result], ['b','p'])
        self.assertEqual(events, ['enter physical','exit physical','enter baked','exit baked'])


if __name__ == '__main__': unittest.main()
