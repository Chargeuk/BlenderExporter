"""Optional environment lighting file defaults: CPU/fake-bpy contract and staging checks."""
import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import zipfile

REPO = Path(__file__).resolve().parents[1]
ADDON = Path(os.environ.get('BJS_TEST_ADDON_ROOT', REPO / 'src/babylon_js'))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


defaults = load('tested_lighting_defaults', ADDON / 'lighting_defaults.py')


class World(dict):
    inlineTextures = False
    textureDir = ''


def context(world=None):
    return types.SimpleNamespace(scene=types.SimpleNamespace(world=world if world is not None else World()))


def operator(explicit=(), **values):
    fields = dict(environment_lighting_defaults_enabled=False,
                  environment_lighting_default_mode='advanced', environment_lighting_multiplier=1)
    fields.update(values)
    return types.SimpleNamespace(
        properties=types.SimpleNamespace(is_property_set=lambda field: field in explicit), **fields)


class LightingDefaultsTests(unittest.TestCase):
    def test_all_modes_and_endpoints_preserve_literal_zero(self):
        for mode in defaults.MODES:
            for multiplier in (0, 1, 10, 2.5):
                value = dict(version=1, mode=mode, multiplier=multiplier)
                self.assertEqual(defaults.profile(value), value)
                self.assertIsNot(defaults.profile(value), value)

    def test_invalid_profiles_reject_without_mutation(self):
        values = [None, False, [], 'advanced', {}, dict(mode='unknown', multiplier=1),
                  dict(version=2, mode='off', multiplier=1), dict(version=True, mode='off', multiplier=1),
                  dict(version=1.0, mode='off', multiplier=1), dict(mode='off'),
                  dict(mode='simple', multiplier=1, strength=1)]
        values += [dict(mode='advanced', multiplier=value) for value in
                   (True, False, None, '0', -1, 10.01, float('nan'), float('inf'), -float('inf'))]
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                defaults.profile(value)

    def test_absent_or_disabled_saved_preferences_leave_older_exports_unchanged(self):
        for world in (None, World(), World(bjs_environment_lighting_defaults_enabled=False),
                      World(bjs_environment_lighting_default_mode='invalid')):
            self.assertIsNone(defaults.saved_options(world))
        for mode in defaults.MODES:
            world = World(bjs_environment_lighting_defaults_enabled=True,
                          bjs_environment_lighting_default_mode=mode, bjs_environment_lighting_multiplier=0)
            before = dict(world)
            self.assertEqual(defaults.saved_options(world), dict(version=1, mode=mode, multiplier=0))
            self.assertEqual(world, before)
        with self.assertRaises(ValueError):
            defaults.saved_options(World(bjs_environment_lighting_defaults_enabled='true'))

    def test_dialog_prefill_then_explicit_choices_and_prepare_export_saved_fallback(self):
        world = World(bjs_environment_lighting_defaults_enabled=True,
                      bjs_environment_lighting_default_mode='simple', bjs_environment_lighting_multiplier=0)
        ctx = context(world)
        op = operator()
        # prepare-export invokes EXEC_DEFAULT, which does not run invoke/prefill.
        self.assertEqual(defaults.operator_options(op, ctx), dict(version=1, mode='simple', multiplier=0))
        defaults.prefill(op, ctx)
        self.assertTrue(op.environment_lighting_defaults_enabled)
        self.assertEqual(op.environment_lighting_multiplier, 0)
        op.properties.is_property_set = lambda field: True
        op.environment_lighting_default_mode = 'off'
        op.environment_lighting_multiplier = 10
        self.assertEqual(defaults.operator_options(op, ctx), dict(version=1, mode='off', multiplier=10))
        op.environment_lighting_defaults_enabled = False
        self.assertIsNone(defaults.operator_options(op, ctx))
        self.assertEqual(world['bjs_environment_lighting_default_mode'], 'simple')

    def test_operator_queries_explicitness_on_properties_rna_not_operator_struct(self):
        op = operator()
        op.is_property_set = Mock(side_effect=AssertionError('Operator struct does not own these RNA properties'))
        op.properties.is_property_set = Mock(return_value=False)
        self.assertIsNone(defaults.operator_options(op, context()))
        self.assertEqual([call.args[0] for call in op.properties.is_property_set.call_args_list],
                         list(defaults.OPERATOR_FIELDS))
        op.is_property_set.assert_not_called()

    def test_explicit_script_profile_and_null_override_saved_world(self):
        world = World(bjs_environment_lighting_defaults_enabled=True)
        self.assertEqual(defaults.resolve_options(world, {}), dict(version=1, mode='advanced', multiplier=1))
        self.assertIsNone(defaults.resolve_options(world, {defaults.OPTION: None}))
        self.assertEqual(defaults.resolve_options(world, {defaults.OPTION: dict(mode='off', multiplier=0)}),
                         dict(version=1, mode='off', multiplier=0))

    def test_metadata_preserves_existing_volume_and_siblings(self):
        model = dict(metadata={'scene': 'keep'}, meshes=[dict(name='HASENV', metadata={
            'keep': 'marker', 'kadshowEnvironmentLightingVolume': {'strength': 1}})], lights=[dict(name='sun')])
        value = dict(version=1, mode='simple', multiplier=0)
        defaults.apply_defaults(model, value)
        self.assertEqual(model['meshes'][0]['metadata'], {
            'keep': 'marker', 'kadshowEnvironmentLightingVolume': {'strength': 1}, defaults.KEY: value})
        self.assertEqual(model['metadata'], {'scene': 'keep'})
        self.assertNotIn('metadata', model['lights'][0])

    def test_marker_guards_do_not_create_or_mutate_invalid_targets(self):
        models = [{}, {'transformNodes': [{'name': 'hasenv'}]},
                  {'meshes': [{'name': 'hasenv'}, {'name': 'HASENV'}]},
                  {'meshes': [{'name': 'hasenv', 'positions': [1, 2, 3]}]},
                  {'meshes': [{'name': 'hasenv', 'indices': [0]}]},
                  {'meshes': [{'name': 'hasenv', 'metadata': []}]},
                  {'meshes': [{'name': 'hasenv', 'metadata': {defaults.KEY: {}}}]}]
        for model in models:
            before = copy.deepcopy(model)
            with self.subTest(model=model), self.assertRaises(ValueError):
                defaults.apply_defaults(model, dict(mode='advanced', multiplier=1))
            self.assertEqual(model, before)
        model = {}
        self.assertIsNone(defaults.apply_defaults(model, None))
        self.assertEqual(model, {})

    def test_registered_world_controls_have_saved_defaults_and_unregister_cleanly(self):
        class FakeWorld:
            pass
        fake = types.SimpleNamespace(types=types.SimpleNamespace(World=FakeWorld), props=types.SimpleNamespace(
            BoolProperty=lambda **kw: kw, EnumProperty=lambda **kw: kw, FloatProperty=lambda **kw: kw))
        with patch.dict(sys.modules, {'bpy': fake}):
            defaults.register_properties()
            self.assertIs(FakeWorld.bjs_environment_lighting_defaults_enabled['default'], False)
            self.assertEqual(FakeWorld.bjs_environment_lighting_default_mode['default'], 'advanced')
            self.assertEqual(FakeWorld.bjs_environment_lighting_multiplier['default'], 1)
            self.assertEqual(FakeWorld.bjs_environment_lighting_multiplier['min'], 0)
            self.assertEqual(FakeWorld.bjs_environment_lighting_multiplier['max'], 10)
            self.assertNotIn('options', FakeWorld.bjs_environment_lighting_multiplier)  # not SKIP_SAVE
            defaults.unregister_properties()
            self.assertFalse(any(hasattr(FakeWorld, field) for field in defaults.WORLD_FIELDS))

    def test_gui_hooks_and_script_route_use_shared_defaults(self):
        tree = ast.parse((ADDON / '__init__.py').read_text(encoding='utf8'))
        op = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'JsonMain')
        methods = {node.name: ast.unparse(node) for node in op.body if isinstance(node, ast.FunctionDef)}
        self.assertIn('prefill_environment_lighting_defaults(self, context)', methods['invoke'])
        self.assertIn('operator_options(self, context)', methods['execute'])
        self.assertIn("options['environment_lighting_defaults'] = environment_defaults", methods['execute'])
        self.assertIn('draw_operator(self.layout, self)', methods['draw'])
        self.assertIn('draw_world(layout, world)', (ADDON / 'world.py').read_text(encoding='utf8'))


class StagedLightingDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package_name = 'lighting_defaults_stage_test_package'
        package = types.ModuleType(self.package_name)
        package.__path__ = [str(ADDON)]
        core = types.ModuleType(self.package_name + '.ktx_conversion')
        core.sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
        core.read_json = lambda path: json.loads(Path(path).read_text(encoding='utf8'))
        core.texture_fields = lambda model: []
        volume = types.ModuleType(self.package_name + '.environment_lighting_volume')
        volume.saved_options = lambda world: None
        def export_volume(ctx, model, *args):
            model['meshes'][0].setdefault('metadata', {})['kadshowEnvironmentLightingVolume'] = {'strength': 1}
            return {'status': 'passed'}
        volume.export_volume = Mock(side_effect=export_volume)
        self.env = types.ModuleType(self.package_name + '.env_export')
        self.env.find_converter = lambda value: ('node', 'converter', 'test stub')
        def export_environment(ctx, model, *args):
            model.setdefault('meshes', []).append(dict(name='hasenv', metadata={'keep': 'env'}))
            return {'status': 'passed'}
        self.env.export_environment = Mock(side_effect=export_environment)
        self.modules = patch.dict(sys.modules, {
            self.package_name: package, core.__name__: core, volume.__name__: volume,
            self.env.__name__: self.env, self.package_name + '.lighting_defaults': defaults,
            'bpy': types.SimpleNamespace()})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.ktx = load(self.package_name + '.ktx_export', ADDON / 'ktx_export.py')
        self.addCleanup(patch.stopall)
        patch.object(self.ktx, '_check_texture_collisions').start()
        patch.object(self.ktx, 'find_ktx', return_value=('ktx', 'test stub')).start()

    def export(self, world, options, model):
        target = self.root / 'fixture.babylon'
        target.write_text('previous accepted model', encoding='utf8')
        exporter = types.SimpleNamespace(material_options={}, nErrors=0, nWarnings=0, fatalError=None)
        def raw_export(ctx, filepath, objects, **kwargs):
            Path(filepath).write_text(json.dumps(model), encoding='utf8')
        exporter.execute = Mock(side_effect=raw_export)
        self.ktx.execute_with_ktx(exporter, context(world), str(target), [],
                                  dict(convert_materials=False, **options))
        return exporter, target

    def test_defaults_attach_after_env_and_keep_volume_strength_one(self):
        exporter, target = self.export(World(), {
            'environment': {}, 'environment_lighting_volume': {'strength': 1},
            defaults.OPTION: dict(mode='advanced', multiplier=10)}, {})
        self.assertIsNone(exporter.fatalError)
        metadata = json.loads(target.read_text())['meshes'][0]['metadata']
        self.assertEqual(metadata[defaults.KEY], dict(version=1, mode='advanced', multiplier=10))
        self.assertEqual(metadata['kadshowEnvironmentLightingVolume']['strength'], 1)
        self.assertEqual(metadata['keep'], 'env')

    def test_scripted_saved_defaults_and_explicit_zero_are_published(self):
        world = World(bjs_environment_lighting_defaults_enabled=True,
                      bjs_environment_lighting_default_mode='simple', bjs_environment_lighting_multiplier=0)
        exporter, target = self.export(world, {}, {'meshes': [{'name': 'hasenv'}]})
        self.assertIsNone(exporter.fatalError)
        self.assertEqual(json.loads(target.read_text())['meshes'][0]['metadata'][defaults.KEY],
                         dict(version=1, mode='simple', multiplier=0))

    def test_explicit_null_disables_saved_defaults_without_creating_marker(self):
        world = World(bjs_environment_lighting_defaults_enabled=True)
        exporter, target = self.export(world, {defaults.OPTION: None}, {})
        self.assertIsNone(exporter.fatalError)
        self.assertEqual(json.loads(target.read_text()), {})

    def test_invalid_profile_fails_before_raw_export_and_preserves_delivery(self):
        exporter, target = self.export(World(), {defaults.OPTION: dict(mode='off', multiplier=float('nan'))}, {})
        self.assertIn('multiplier must be finite', exporter.fatalError)
        exporter.execute.assert_not_called()
        self.assertEqual(target.read_text(), 'previous accepted model')
        self.assertFalse((self.root / '.bjs-ktx').exists())

    def test_missing_marker_fails_before_publish_and_preserves_delivery(self):
        exporter, target = self.export(World(), {defaults.OPTION: dict(mode='off', multiplier=0)}, {})
        self.assertIn('one existing empty hasenv mesh', exporter.fatalError)
        self.assertEqual(target.read_text(), 'previous accepted model')


class LightingDefaultsPackagingTests(unittest.TestCase):
    def test_new_module_is_packaged_byte_for_byte_and_importable_without_bpy(self):
        builder = load('lighting_defaults_package_builder', REPO / 'tools/package_addon.py')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with zipfile.ZipFile(builder.build(REPO, root / 'addon.zip')) as package:
                self.assertEqual(package.read('babylon_js/lighting_defaults.py'),
                                 (REPO / 'src/babylon_js/lighting_defaults.py').read_bytes())
                package.extract('babylon_js/lighting_defaults.py', root)
            extracted = load('packaged_lighting_defaults', root / 'babylon_js/lighting_defaults.py')
            self.assertEqual(extracted.profile(dict(mode='off', multiplier=0)),
                             dict(version=1, mode='off', multiplier=0))


if __name__ == '__main__':
    unittest.main()
