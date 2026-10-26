"""Combined local/remote export contracts reject conflicting ownership before work."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / 'src/babylon_js/environment_tools'
sys.path.insert(0, str(ENV))
import recipe_cli as recipe
import cli
import volume_assembly as assembly


class VolumeRouteIntegrationTests(unittest.TestCase):
    def options(self):
        return {'volume_export_route': 'separate-source-v1',
                'deferred_volume': {'receipt': {}, 'master': {}, 'strength': 1}}

    def test_both_selected_routes_reject(self):
        with self.assertRaisesRegex(ValueError, 'Choose one volume export route'):
            recipe.validate_volume_export_routes(self.options())

    def test_conflict_guard_does_not_replace_individual_option_validators(self):
        # False/null are not two selected routes, but legacy route/declaration
        # validators may still reject those values as invalid configurations.
        for options in ({}, {'volume_export_route': 'separate-source-v1'},
                        {'deferred_volume': {'receipt': 'bound'}},
                        {'volume_export_route': 'separate-source-v1', 'deferred_volume': None},
                        {'volume_export_route': 'separate-source-v1', 'deferred_volume': False},
                        {'volume_export_route': None, 'deferred_volume': {'receipt': 'bound'}},
                        {'volume_export_route': False, 'deferred_volume': {'receipt': 'bound'}}):
            with self.subTest(options=options):
                recipe.validate_volume_export_routes(options)

    def test_public_separate_source_dispatches_only_to_assembly_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'recipe.json'
            config.write_text(json.dumps({'volume_export_route': 'separate-source-v1'}), encoding='utf-8')
            output = root / 'output'
            with patch.object(assembly, 'run_pipeline') as pipeline, \
                 patch.object(cli.subprocess, 'run', side_effect=AssertionError('legacy process started')):
                cli.preparation_command(['prepare-export', '--config', str(config),
                    '--output', str(output), '--blender', 'unused-blender'])
            pipeline.assert_called_once_with(config, output, Path('unused-blender'))
            self.assertFalse(output.exists())

    def test_public_deferred_dispatches_regular_export_when_other_route_absent_or_none(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.blend'
            source.write_bytes(b'dispatch fixture only, never opened by Blender')
            for include_disabled in (False, True):
                with self.subTest(explicit_none=include_disabled):
                    options = {'source': source.name, 'deferred_volume': self.options()['deferred_volume']}
                    if include_disabled:
                        options['volume_export_route'] = None
                    config = root / 'recipe.json'
                    config.write_text(json.dumps(options), encoding='utf-8')
                    output = root / 'output'
                    with patch.object(assembly, 'run_pipeline', side_effect=AssertionError('assembly started')), \
                         patch.object(cli.subprocess, 'run') as launch:
                        cli.preparation_command(['prepare-export', '--config', str(config),
                            '--output', str(output), '--blender', 'unused-blender'])
                    launch.assert_called_once()
                    argv = launch.call_args.args[0]
                    self.assertIn(str(source), argv)
                    self.assertIn('export', argv)
                    self.assertFalse(output.exists())

    def test_public_invalid_assembly_values_keep_legacy_error_before_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'recipe.json'
            output = root / 'output'
            for value in (False, True, 0, 'unknown-route'):
                with self.subTest(route=value):
                    options = self.options()
                    options['volume_export_route'] = value
                    config.write_text(json.dumps(options), encoding='utf-8')
                    with patch.object(cli.subprocess, 'run', side_effect=AssertionError('process started')):
                        with self.assertRaisesRegex(ValueError, 'Unsupported separate-source volume route'):
                            cli.preparation_command(['prepare-export', '--config', str(config),
                                '--output', str(output), '--blender', 'unused-blender'])
                    self.assertFalse(output.exists())

    def test_public_preparation_rejects_before_pipeline_or_subprocess(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'recipe.json'
            config.write_text(json.dumps(self.options()), encoding='utf-8')
            output = root / 'output'
            with patch.object(assembly, 'run_pipeline', side_effect=AssertionError('pipeline started')), \
                 patch.object(cli.subprocess, 'run', side_effect=AssertionError('process started')):
                with self.assertRaisesRegex(ValueError, 'Choose one volume export route'):
                    cli.preparation_command(['prepare-export', '--config', str(config),
                        '--output', str(output), '--blender', 'unused-blender'])
            self.assertFalse(output.exists())

    def test_direct_export_and_pipeline_reject_before_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'recipe.json'
            config.write_text(json.dumps(self.options()), encoding='utf-8')
            with patch.object(recipe, 'background_source', side_effect=AssertionError('scene staging started')):
                with self.assertRaisesRegex(ValueError, 'Choose one volume export route'):
                    recipe.export(config, SimpleNamespace())
            output = root / 'output'
            with self.assertRaisesRegex(ValueError, 'Choose one volume export route'):
                assembly.run_pipeline(config, output, 'unused-blender')
            self.assertFalse(output.exists())

    def test_glass_rgb_and_alpha_override_combine_on_actual_export_helper(self):
        alpha = SimpleNamespace(default_value=.43, is_linked=False)
        colour = SimpleNamespace(default_value=(.7, .8, .9, 1))
        node = SimpleNamespace(type='BSDF_PRINCIPLED', inputs={'Alpha': alpha, 'Base Color': colour})
        material = SimpleNamespace(name='Glass', node_tree=SimpleNamespace(nodes=[node]),
                                   overloadChannels=False, transparencyMode='0')
        facts = recipe.prepare_glass_material(material, {'alpha': .18, 'preserve_rgb': True,
                                                        'transparency_mode': 2})
        self.assertEqual(colour.default_value, (.7, .8, .9, .18))
        self.assertEqual(alpha.default_value, .18)
        self.assertEqual(facts['original_alpha'], .43)
        self.assertTrue(facts['override_applied'])


if __name__ == '__main__':
    unittest.main()
