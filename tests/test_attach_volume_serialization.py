"""The deferred-volume terminal writer must retain compact runtime JSON."""
import copy
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ADDON = Path(__file__).resolve().parents[1] / 'src/babylon_js'
spec = importlib.util.spec_from_file_location('attach_volume_recipe_contract', ADDON / 'environment_tools/recipe_cli.py')
recipe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recipe)


class AttachVolumeSerializationTests(unittest.TestCase):
    def test_terminal_attachment_stays_compact_and_preserves_model_and_reports(self):
        # Blender and encoding are boundaries; exercise the actual attachment,
        # final inventory and publication code against real temporary files.
        for include_tangents in (False, True):
            with self.subTest(tangents=include_tangents), tempfile.TemporaryDirectory() as temp:
                base = Path(temp).resolve()
                root = base / 'retained'; root.mkdir()
                stage = base / 'intermediate'; stage.mkdir()
                out = base / 'final'
                source = root / 'source.blend'; source.write_bytes(b'unchanged editable source')
                receipt = root / 'volume.json'; receipt.write_bytes(b'unchanged association')
                master = root / 'volume.exr'; master.write_bytes(b'unchanged linear master')
                config_path = root / 'export.json'
                config = dict(model='fixture.babylon', deferred_volume={'enabled': True},
                              tools={'ktx': 'unused-encoder'}, preparation={})
                recipe.write(config_path, config)
                model = dict(meshes=[dict(id='receiver', positions=[-0.0, 1e-40, 1e30],
                                         indices=[0, 1, 2], uvs=[.25, .75], uvs2=[.125, .875])],
                             metadata=dict(text='  room\t"quoted"\n雪  ', integer=9007199254740993,
                                           nested={'enabled': True, 'nothing': None}))
                if include_tangents:
                    model['meshes'][0]['tangents'] = [1.0, 0.0, 0.0, -1.0]
                recipe.write(stage / config['model'], model)
                texture = stage / 'colour.ktx2'; texture.write_bytes(b'unchanged ordinary texture')
                report_name = 'fixture.ktx-report.json'
                report = dict(status='passed', delivery_files=[
                    dict(path=str(path), sha256=recipe.sha(path), bytes=path.stat().st_size)
                    for path in (stage / config['model'], texture)])
                recipe.write(stage / report_name, report)
                selection = dict(deferred_volume={'enabled': True}, source_sha256=recipe.sha(source))
                recipe.write(stage / 'selection-settings.json', selection)
                recipe.write(stage / 'STAGE_RESULT.json', {'kind': 'fixture'})
                stage_hash = recipe.sha(stage / 'STAGE_RESULT.json')
                manifest = dict(artifacts=recipe._stage_inventory(stage))
                before_stage = copy.deepcopy(manifest['artifacts'])
                plan = dict(receipt={'path': str(receipt), 'sha256': recipe.sha(receipt)},
                            master={'path': str(master), 'sha256': recipe.sha(master)},
                            strength=.375, environment={}, scene_identity_sha256='unchanged-scene',
                            config_sha256=recipe.sha(config_path))
                volume_metadata = dict(strength=.375, texture='volume.ktx2',
                                       note='volume metadata with spaces')
                expected = copy.deepcopy(model)
                expected['metadata']['environmentLightingVolume'] = volume_metadata

                def export_volume(context, value, work, package, options, *args):
                    self.assertEqual(value, model)
                    value['metadata']['environmentLightingVolume'] = copy.deepcopy(volume_metadata)
                    (package / 'volume.ktx2').write_bytes(b'encoded volume fixture')
                    return {'status': 'passed', 'strength': options['strength']}

                addon = ModuleType('babylon_js'); addon.__path__ = [str(ADDON)]
                helpers = ModuleType('babylon_js.environment_tools')
                helpers.__path__ = [str(ADDON / 'environment_tools')]
                modules = {'babylon_js': addon, 'babylon_js.environment_tools': helpers,
                           'bpy': SimpleNamespace(context=SimpleNamespace()),
                           'babylon_js.environment_lighting_volume': SimpleNamespace(export_volume=export_volume),
                           'babylon_js.environment_tools.volume_relocation': SimpleNamespace(
                               scene_identity=lambda context: {'sha256': 'unchanged-scene'})}
                # Avoid importing the add-on's Blender registration code. The
                # real compact serializer and publisher still load from source.
                with patch.dict(sys.modules, modules), \
                     patch.object(recipe, 'background_source', return_value=(root, config, source, recipe.sha(source))), \
                     patch.object(recipe, 'deferred_volume_plan', return_value=plan), \
                     patch.object(recipe, 'validate_deferred_stage', return_value=(manifest, report, selection)):
                    for name in ('babylon_js.ktx_conversion', 'babylon_js.ktx_export'):
                        sys.modules.pop(name, None)
                    recipe.attach_volume(config_path, SimpleNamespace(out=out, stage=stage,
                                                                     stage_result_sha256=stage_hash))

                text = (out / config['model']).read_text(encoding='utf8')
                self.assertEqual(text, json.dumps(expected, ensure_ascii=True, separators=(',', ':')) + '\n')
                actual = json.loads(text)
                self.assertEqual(actual, expected)
                self.assertEqual(math.copysign(1, actual['meshes'][0]['positions'][0]), -1)
                self.assertEqual('tangents' in actual['meshes'][0], include_tangents)
                self.assertEqual((out / texture.name).read_bytes(), texture.read_bytes())
                self.assertEqual(recipe._stage_inventory(stage), before_stage)
                self.assertEqual(recipe.sha(stage / 'STAGE_RESULT.json'), stage_hash)
                self.assertEqual(source.read_bytes(), b'unchanged editable source')
                self.assertEqual(receipt.read_bytes(), b'unchanged association')
                self.assertEqual(master.read_bytes(), b'unchanged linear master')
                for name in ('selection-settings.json', report_name, 'STAGE_RESULT.json'):
                    self.assertIn('\n  "', (out / name).read_text(encoding='utf8'))
                published_report = recipe.read(out / report_name)
                entry = next(row for row in published_report['delivery_files'] if Path(row['path']).name == config['model'])
                self.assertEqual(entry['sha256'], recipe.sha(out / config['model']))
                self.assertEqual(entry['bytes'], (out / config['model']).stat().st_size)
                self.assertTrue(recipe.read(out / 'STAGE_RESULT.json')['volume_complete'])
                self.assertNotIn('deferred_volume', recipe.read(out / 'selection-settings.json'))


if __name__ == '__main__':
    unittest.main()
