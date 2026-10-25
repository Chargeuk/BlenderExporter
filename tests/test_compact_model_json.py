"""Model-only compaction through the direct, conversion and metadata export paths."""
import ast
import copy
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ADDON = Path(os.environ.get('BJS_TEST_ADDON_ROOT', Path(__file__).resolve().parents[1] / 'src/babylon_js'))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core = load('compact_model_core', ADDON / 'ktx_conversion.py')


class CompactModelTests(unittest.TestCase):
    def test_layout_only_keeps_numeric_tokens_unicode_strings_and_order(self):
        name = json.dumps('Café  night "quoted" \\ path\t\n', ensure_ascii=False)
        raw = '{\n  "name": ' + name + ''',
          "positions": [-0.000000, 1e-12, 0.1234567890123456789, 12345678901234567890],
          "metadata": {"enabled": true, "optional": null}
        }'''
        result = core.compact_model_json(raw)
        self.assertEqual(json.loads(result), json.loads(raw))
        self.assertIn('[-0.000000,1e-12,0.1234567890123456789,12345678901234567890]', result)
        self.assertIn('Café  night', result)
        self.assertEqual(list(json.loads(result)), ['name', 'positions', 'metadata'])
        self.assertEqual(core.compact_model_json(result), result)

    def test_object_serializer_preserves_values_order_and_callers_json_policies(self):
        model = {'name': 'Café  room', 'positions': [-0.0, 1e-100, 1.2345678901234567],
                 'indices': [0, 1, 2], 'metadata': {'null': None, 'bool': True}}
        before = copy.deepcopy(model)
        result = core.model_json(model)
        self.assertEqual(result, json.dumps(model, ensure_ascii=False, separators=(',', ':')))
        self.assertEqual(json.loads(result), before)
        self.assertEqual(model, before)
        self.assertEqual(math.copysign(1, json.loads(result)['positions'][0]), -1)
        self.assertIn('Caf\\u00e9', core.model_json(model, ensure_ascii=True))
        self.assertEqual(core.model_json({'value': float('nan')}), '{"value":NaN}')
        with self.assertRaises(ValueError):
            core.model_json({'value': float('nan')}, allow_nan=False)

    def test_direct_writer_compacts_actual_method_without_rounding_or_minifying_manifest(self):
        # Execute the production method with data writers, avoiding Blender imports.
        tree = ast.parse((ADDON / 'json_exporter.py').read_text(encoding='utf8'))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'JsonExporter')
        cls.body = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == 'to_json_file']
        namespace = dict(Logger=types.SimpleNamespace(log=lambda *args: None),
                         bpy=types.SimpleNamespace(app=types.SimpleNamespace(version_string='fixture')),
                         format_exporter_version=lambda: 'fixture', compact_model_json=core.compact_model_json,
                         calendar=__import__('calendar'), time=__import__('time'))
        exec(compile(ast.Module(body=[cls], type_ignores=[]), str(ADDON / 'json_exporter.py'), 'exec'), namespace)
        exporter_class = namespace['JsonExporter']
        exporter_class.nameSpace = 'fixture'
        exporter = exporter_class()
        for field in ('materials', 'multiMaterials', 'skeletons', 'meshesAndNodes', 'morphTargetMngrs',
                      'animationGroupers', 'cameras', 'lights', 'shadowGenerators', 'sounds'):
            setattr(exporter, field, [])
        exporter.world = types.SimpleNamespace(to_json_file=lambda stream, _: stream.write(
            '"gravity": [-0.000000, 1e-12, 0.1234567890123456789],\n"name": "Café  room"'))
        exporter.settings = types.SimpleNamespace(writeManifestFile=True)
        with tempfile.TemporaryDirectory() as directory:
            exporter.filepathMinusExtension = str(Path(directory) / 'fixture')
            exporter.to_json_file()
            result = Path(exporter.filepathMinusExtension + '.babylon').read_text(encoding='utf8')
            self.assertIn('[-0.000000,1e-12,0.1234567890123456789]', result)
            self.assertNotIn('\n', result)
            self.assertEqual(json.loads(result)['name'], 'Café  room')
            manifest = Path(exporter.filepathMinusExtension + '.babylon.manifest').read_text(encoding='utf8')
            self.assertIn('\n\t"version" : ', manifest)
            self.assertTrue(json.loads(manifest)['enableSceneOffline'])

    def test_conversion_output_compact_and_reports_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'raw.babylon'
            model = {'name': 'Café  room', 'positions': [-0.0, 1.2345678901234567], 'indices': [0, 1, 2]}
            source.write_text(json.dumps(model, indent=2), encoding='utf8')
            original = source.read_bytes()
            config = root / 'conversion.json'
            config.write_text('{}', encoding='utf8')
            plan = dict(root=str(root), config=str(config), changes=[], textures=[], output_dir=str(root / 'delivery'),
                        models=[dict(data=copy.deepcopy(model), source=str(source), source_sha256=core.sha(source), output='room.babylon')])
            with patch.object(core.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0, stdout='fixture tool')):
                report = core.run_conversion(plan, 'fixture-ktx', 1)
            result = (root / 'delivery/room.babylon').read_text(encoding='utf8')
            self.assertEqual(result, core.model_json(model))
            self.assertEqual(source.read_bytes(), original)
            report_text = (Path(report['run']) / 'report.json').read_text(encoding='utf8')
            self.assertIn('\n  "status": "passed"', report_text)

    def test_staged_optional_metadata_paths_leave_final_model_compact(self):
        name = 'compact_staged_export_test'
        package = types.ModuleType(name)
        package.__path__ = [str(ADDON)]
        modules = {name: package, name + '.ktx_conversion': core, 'bpy': types.SimpleNamespace()}
        def module(suffix, **members):
            value = types.ModuleType(name + '.' + suffix)
            value.__dict__.update(members)
            modules[value.__name__] = value
        def attach(key):
            def export(ctx, model, *args):
                model['meshes'][0].setdefault('metadata', {})[key] = {'value': 1.2345678901234567}
                return {'status': 'passed'}
            return export
        module('environment_lighting_volume', saved_options=lambda world: None, export_volume=attach('volume'))
        module('spatial_lighting', export_spatial=attach('spatial'))
        module('skybox_export', find_basisu=lambda value: ('basis', 'fixture'), export_skybox=attach('skybox'))
        module('env_export', find_converter=lambda value: ('node', 'converter', 'fixture'), export_environment=attach('environment'))
        class World(dict):
            inlineTextures = False
            textureDir = ''
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, modules):
            ktx = load(name + '.ktx_export', ADDON / 'ktx_export.py')
            context = types.SimpleNamespace(scene=types.SimpleNamespace(world=World()))
            for feature in ('spatial_lighting', 'skybox', 'environment', 'environment_lighting_volume', 'environment_lighting_defaults'):
                with self.subTest(feature=feature):
                    target = Path(directory) / feature / 'room.babylon'
                    target.parent.mkdir()
                    original = {'meshes': [{'name': 'hasenv', 'metadata': {'keep': 'Café  room'}}],
                                'positions': [-0.0, 1.2345678901234567], 'indices': [0, 1, 2]}
                    exporter = types.SimpleNamespace(material_options={}, fatalError=None, nErrors=0, nWarnings=0)
                    exporter.execute = Mock(side_effect=lambda ctx, path, objects, **kw: Path(path).write_text(json.dumps(original), encoding='utf8'))
                    value = {'mode': 'advanced', 'multiplier': 1} if feature == 'environment_lighting_defaults' else {'fixture': True}
                    with patch.object(ktx, '_check_texture_collisions'), patch.object(ktx, 'find_ktx', return_value=('ktx', 'fixture')):
                        ktx.execute_with_ktx(exporter, context, str(target), [], {'convert_materials': False, feature: value})
                    self.assertIsNone(exporter.fatalError)
                    result = target.read_text(encoding='utf8')
                    parsed = json.loads(result)
                    self.assertEqual(result, core.model_json(parsed))
                    self.assertEqual(parsed['positions'], original['positions'])
                    self.assertEqual(parsed['indices'], original['indices'])
                    self.assertEqual(parsed['meshes'][0]['metadata']['keep'], 'Café  room')
                    self.assertIn('\n  "status": "passed"', target.with_suffix('.ktx-report.json').read_text(encoding='utf8'))


if __name__ == '__main__':
    unittest.main()
