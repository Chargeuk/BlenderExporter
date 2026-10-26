"""Focused pre-HDR native export contracts; no Blender or conversion jobs."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
ENV=ROOT/'src/babylon_js/environment_tools';sys.path.insert(0,str(ENV))
import recipe_cli as recipe
import cli
import test_native_recombine


class NativeExportTests(unittest.TestCase):
    def options(self):
        return dict(schema_version=1,accepted=False,source='source.blend',source_sha256='source',model='scene.babylon',
            native_diagnostic=dict(schema_version=1,native_config=dict(path='native.json',sha256='native'),
                candidate='native',original_tool_source_root='tools',geometry_sha256='geometry',combined_sha256='combined'),
            preparation=dict(visible={'names':['A','B']},helpers={'names':[]},lights={'names':[]},
                lightmap_marker='lightmap_scene',world_settings=dict(inlineTextures=False,usePBRMaterials=True)),
            expected_sampler_counts=dict(colour=1,direct=1,indirect=1,island_ids=1),
            ktx_options=dict(convert_materials=True,lightmap_encoding='rgbd-v1',lightmap_agx=True,
                auto_lightmap=False,colour_adjustment_enabled=False,environment_lighting_volume=None,
                lightmap='native/masters/combined.exr',lightmap_marker='lightmap_scene',lightmap_size=1))

    def test_optin_routes_without_touching_legacy_preparation(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'export.json';recipe.write(path,self.options())
            with patch.object(recipe,'export_native_diagnostic',return_value='diagnostic') as run, \
                 patch.object(recipe,'background_source',side_effect=AssertionError('legacy path')):
                self.assertEqual(recipe.export(path,NS()),'diagnostic');run.assert_called_once()

    def test_default_export_still_reaches_original_guard(self):
        # Reaching the legacy source guard proves the new path is strictly opt-in.
        modules={'bpy':NS(),'babylon_js':NS(__path__=[]),'babylon_js.environment_tools':NS(__path__=[]),
                 'babylon_js.colour_adjustment':NS(settings=lambda s:None),
                 'babylon_js.lighting_profile':NS(scene_lighting_profile=lambda s:None),
                 'babylon_js.environment_tools.image_binding':NS(replace_from_disk=lambda *a:None)}
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'legacy.json';recipe.write(path,{})
            with patch.dict(sys.modules,modules),patch.object(recipe,'background_source',side_effect=ValueError('original source guard')), \
                 patch.object(recipe,'export_native_diagnostic',side_effect=AssertionError('diagnostic route')):
                with self.assertRaisesRegex(ValueError,'original source guard'):recipe.export(path,NS())

    def test_environment_requests_are_rejected_even_when_null(self):
        for key in ('environment','skybox','spatial_lighting'):
            for value in (None,{'enabled':True}):
                opts=self.options();opts['ktx_options'][key]=value
                with self.subTest(key=key,value=value),self.assertRaisesRegex(ValueError,'forbids ENV'):
                    recipe.native_diagnostic_settings(opts)
        for key in ('environment','skybox','spatial_lighting','environment_lighting_volume'):
            opts=self.options();opts[key]=None
            with self.subTest(top_level=key),self.assertRaisesRegex(ValueError,'Environment output'):
                recipe.native_diagnostic_settings(opts)

    def test_saved_volume_fallback_is_never_implicit(self):
        for value in ('missing',{},'saved-volume.json',False):
            opts=self.options()
            if value=='missing':opts['ktx_options'].pop('environment_lighting_volume')
            else:opts['ktx_options']['environment_lighting_volume']=value
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'fallback is forbidden'):
                recipe.native_diagnostic_settings(opts)
        self.assertEqual(recipe.native_diagnostic_settings(self.options())['schema_version'],1)

    def test_no_fake_capture_or_material_flattening(self):
        for key in ('capture_association','candidate_build','source_geometry_association','input_bindings'):
            opts=self.options();opts[key]={}
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'fabricate capture'):
                recipe.native_diagnostic_settings(opts)
        for field,value in (('runtime_material_receivers',{'names':['A']}),('lights',{'names':['Key']}),
                            ('world_settings',{'environmentTexture':'outdoor.hdr'})):
            opts=self.options();opts['preparation'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):recipe.native_diagnostic_settings(opts)

    def test_invalid_optin_and_unsafe_model_cannot_fall_back_to_legacy(self):
        for key,value in (('accepted',True),('native_diagnostic',False),('native_diagnostic',{}),
                          ('model','../accepted.babylon'),('model','scene.json'),('expected_sampler_counts',[])):
            opts=self.options();opts[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):recipe.native_diagnostic_settings(opts)

    def fixture(self,root):
        c,candidate,tools,build=test_native_recombine.NativeRecombineTests().fixture(root)
        for name in ('combined.exr','combined.provenance.json'):
            path=candidate/'masters'/name;path.write_bytes(b'fresh-'+name.encode())
            build['stages']['combine']['outputs'][str(path.relative_to(candidate))]=recipe.sha(path)
        recipe.write(candidate/'build.json',build)
        opts=self.options();opts['source_sha256']=recipe.sha(c['_source'])
        opts['native_diagnostic'].update(native_config=dict(path=c['_config'],sha256=recipe.sha(c['_config'])),
            candidate=str(candidate),original_tool_source_root=str(tools),combined_sha256=recipe.sha(candidate/'masters/combined.exr'))
        opts['ktx_options']['lightmap']=str(candidate/'masters/combined.exr')
        path=root/'export.json';recipe.write(path,opts)
        return c,candidate,tools,path

    def test_real_native_proof_is_required_before_export(self):
        with tempfile.TemporaryDirectory() as temp:
            c,candidate,tools,path=self.fixture(Path(temp))
            with patch('common.load_config',return_value=c):plan=recipe.native_diagnostic_plan(path)
            self.assertEqual(plan[-1]['geometry_sha256'],'geometry')
            self.assertIn(str(path),plan[-1]['inputs'])
            # An output changed after its completed stage may not be relabelled.
            (candidate/'masters/direct_final.exr').write_bytes(b'stale map')
            with patch('common.load_config',return_value=c),self.assertRaisesRegex(ValueError,'completed output changed'):
                recipe.native_diagnostic_plan(path)

    def test_missing_ownership_tool_or_combined_proof_is_rejected(self):
        for missing in ('bake/native_ownership.provenance.json','masters/combined.provenance.json','tools'):
            with self.subTest(missing=missing),tempfile.TemporaryDirectory() as temp:
                c,candidate,tools,path=self.fixture(Path(temp))
                if missing=='tools':
                    (tools/'src/babylon_js/environment_tools/worker.py').write_bytes(b'changed historical helper')
                else:(candidate/missing).unlink()
                with patch('common.load_config',return_value=c),self.assertRaises((ValueError,FileNotFoundError)):
                    recipe.native_diagnostic_plan(path)

    def test_native_source_config_geometry_and_combined_cannot_be_substituted(self):
        for field in ('source_sha256','native_config','geometry_sha256','combined_sha256'):
            with self.subTest(field=field),tempfile.TemporaryDirectory() as temp:
                c,candidate,tools,path=self.fixture(Path(temp));opts=recipe.read(path)
                if field=='source_sha256':opts[field]='another-source'
                elif field=='native_config':opts['native_diagnostic'][field]['sha256']='another-config'
                else:opts['native_diagnostic'][field]='another-identity'
                recipe.write(path,opts)
                with patch('common.load_config',return_value=c),self.assertRaises(ValueError):recipe.native_diagnostic_plan(path)

    def test_diagnostic_outputs_never_enter_the_native_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);retained=root/'retained';retained.mkdir();native=root/'native';native.mkdir()
            self.assertEqual(recipe.native_diagnostic_output(retained,native,root/'diagnostic'),root/'diagnostic')
            for out in (native,native/'new-diagnostic',retained/'new-diagnostic',root):
                with self.subTest(out=out),self.assertRaises(ValueError):recipe.native_diagnostic_output(retained,native,out)

    def registration(self):
        operator=NS(identifier='EXPORT_OT_bjs',properties={k:None for k in ('filepath','export_selected','convert_to_ktx2','ktx_lightmap','material_metallic_multiplier','environment_image')})
        bpy=NS(ops=NS(export=NS(bjs=NS(get_rna_type=lambda:operator))),types=NS(
            World=NS(bl_rna=NS(properties={k:None for k in ('positionsPrecision','normalsPrecision','UVsPrecision','exportTangents','inlineTextures','usePBRMaterials')})),
            Material=NS(bl_rna=NS(properties={'transparencyMode':None,'overloadChannels':None}))))
        addon=NS(__file__=str(ENV.parent/'__init__.py'),bl_info={'version':(3,3,43)})
        utils=NS(check=lambda name:(False,True),enable=lambda *a:(_ for _ in ()).throw(AssertionError('forced registration')))
        return bpy,addon,utils

    def test_cli_enabled_registration_does_not_require_saved_preferences(self):
        record=recipe.native_export_registration(*self.registration())
        self.assertFalse(record['default_enabled']);self.assertTrue(record['actually_loaded'])
        self.assertFalse(record['forced_registration']);self.assertFalse(record['preference_write'])

    def test_missing_registered_operator_loaded_flag_rna_path_or_version_is_rejected(self):
        for field in ('operator','loaded','World','Material','path','version'):
            bpy,addon,utils=self.registration()
            if field=='operator':bpy.ops.export.bjs.get_rna_type=lambda:(_ for _ in ()).throw(RuntimeError('not registered'))
            elif field=='loaded':utils.check=lambda name:(True,False)
            elif field=='World':bpy.types.World.bl_rna.properties={}
            elif field=='Material':bpy.types.Material.bl_rna.properties={}
            elif field=='path':addon.__file__=str(Path(__file__).resolve())
            else:addon.bl_info['version']=(3,3,42)
            with self.subTest(field=field),self.assertRaises(ValueError):recipe.native_export_registration(bpy,addon,utils)

    def test_prepare_export_registers_only_explicit_diagnostic_before_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source.blend';source.write_bytes(b'source')
            for diagnostic in (False,True):
                opts=self.options() if diagnostic else {'source':source.name}
                path=root/('native.json' if diagnostic else 'legacy.json');recipe.write(path,opts)
                with patch.object(cli.subprocess,'run') as run:
                    cli.preparation_command(['prepare-export','--config',str(path),'--output',str(root/'out'),'--blender','blender.exe'])
                argv=run.call_args.args[0]
                if diagnostic:
                    self.assertLess(argv.index('--addons'),argv.index(str(source)))
                    self.assertIn('--factory-startup',argv);self.assertIn('--disable-autoexec',argv)
                else:self.assertEqual(argv[:3],['blender.exe','-b',str(source)]);self.assertNotIn('--addons',argv)


    def appearance_options(self,opts,appearance_config):
        opts['native_diagnostic']['appearance_config']=dict(path=str(appearance_config),sha256=recipe.sha(appearance_config))
        opts['expected_sampler_counts']['colour_adjustment']=1
        opts['ktx_options'].update(colour_adjustment_enabled=True,colour_adjustment_size=512,
            colour_adjustment_codec='basis-lz',colour_adjustment_strength=1.0)
        return opts

    def test_derived_route_requires_explicit_colour_codec_size_and_strength(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'appearance.json';path.write_text('{}')
            opts=self.appearance_options(self.options(),path)
            recipe.native_diagnostic_settings(opts)
            for key,value in [('colour_adjustment_enabled',False),('colour_adjustment_size',1024),
                    ('colour_adjustment_codec','uastc'),('colour_adjustment_strength',float('nan')),
                    ('colour_adjustment_strength',True),('colour_adjustment_strength',1.1)]:
                trial=copy.deepcopy(opts);trial['ktx_options'][key]=value
                with self.subTest(key=key,value=value),self.assertRaises(ValueError):recipe.native_diagnostic_settings(trial)
            trial=copy.deepcopy(opts);trial['expected_sampler_counts'].pop('colour_adjustment')
            with self.assertRaisesRegex(ValueError,'exactly one'):recipe.native_diagnostic_settings(trial)
            # Plain mode still rejects adding a colour overlay and cannot enter derived mode by inference.
            trial=copy.deepcopy(opts);trial['native_diagnostic'].pop('appearance_config')
            with self.assertRaises(ValueError):recipe.native_diagnostic_settings(trial)

    def test_real_single_appearance_derivative_is_verified_before_export(self):
        import test_native_appearance
        with tempfile.TemporaryDirectory() as temp:
            c,original,tools,derived=test_native_appearance.NativeAppearanceTests().derived(Path(temp))
            record=recipe.read(derived/'native_recombine.provenance.json')
            opts=self.options();opts['source_sha256']=recipe.sha(c['_source']);opts['native_diagnostic'].update(
                native_config=dict(path=record['original_config'],sha256=record['original_config_sha256']),
                candidate=str(derived),original_tool_source_root=str(tools),geometry_sha256=record['geometry_sha256'],
                combined_sha256=recipe.sha(derived/'masters/combined.exr'))
            opts['ktx_options']['lightmap']=str(derived/'masters/combined.exr')
            opts=self.appearance_options(opts,c['_config']);path=Path(temp)/'export-appearance.json';recipe.write(path,opts)
            original_c=dict(c,_config=record['original_config']);original_c.pop('appearance_colour_adjustment')
            def load(selected):return c if Path(selected)==Path(c['_config']) else original_c
            with patch('common.load_config',side_effect=load):plan=recipe.native_diagnostic_plan(path)
            self.assertEqual(plan[-1]['kind'],'verified-native-appearance-derivative')
            self.assertEqual(plan[-1]['source_sha256'],recipe.sha(c['_source']))
            # Declared export strength may not silently diverge from the combined appearance.
            opts['ktx_options']['colour_adjustment_strength']=.5;recipe.write(path,opts)
            with patch('common.load_config',side_effect=load),self.assertRaisesRegex(ValueError,'strength differs'):
                recipe.native_diagnostic_plan(path)
            # A missing/stale derived proof must not route through legacy/fresh-native fallback.
            (derived/'native_recombine.provenance.json').unlink()
            with patch('common.load_config',side_effect=load),self.assertRaises((ValueError,FileNotFoundError)):
                recipe.native_diagnostic_plan(path)


if __name__=='__main__':unittest.main()
