"""Pure assembly boundary tests; synthetic bytes do not stand in for a passed Blender volume receipt."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock,patch

ADDON=Path(__file__).resolve().parents[1]/'src/babylon_js'
spec=importlib.util.spec_from_file_location('tested_volume_assembly',ADDON/'environment_tools/volume_assembly.py')
assembly=importlib.util.module_from_spec(spec);spec.loader.exec_module(assembly)

class AssemblyBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.stage=self.root/'stage-a';self.stage.mkdir()
        self.source=self.root/'source.blend';self.source.write_bytes(b'synthetic saved source')
        self.hdr=self.root/'capture.exr';self.hdr.write_bytes(b'synthetic capture source')
        self.config=dict(source=self.source.name,source_sha256=assembly.sha(self.source),model='room.babylon',volume_export_route=assembly.ROUTE,
            tools={'ktx':'unused'},preparation={'operator_settings':{}})
        self.config_path=self.root/'recipe.json';assembly.write(self.config_path,self.config)
        self.defaults=dict(version=1,mode='advanced',multiplier=1)
        self.model=dict(meshes=[dict(name='hasenv',metadata={'keep':True,'kadshowEnvironmentLightingDefaults':self.defaults})],materials=[{'name':'unchanged'}])
        assembly.write(self.stage/'room.babylon',self.model);(self.stage/'environment.env').write_bytes(b'synthetic converted ENV')
        selection=dict(source_sha256=assembly.sha(self.source),volume_export_route=assembly.ROUTE,capture_sha256=assembly.sha(self.hdr))
        assembly.write(self.stage/'selection-settings.json',selection)
        report=dict(status='passed',options=dict(environment=dict(image=str(self.hdr),exposure=0,highlight_gain=1,highlight_threshold=1),environment_lighting_defaults=self.defaults),
            environment=dict(source_sha256=assembly.sha(self.hdr)),delivery_files=[dict(path=str(self.stage/name),sha256=assembly.sha(self.stage/name))for name in ('room.babylon','environment.env')])
        assembly.write(self.stage/'room.ktx-report.json',report)
        self.seal()
    def seal(self):
        conversion=assembly.verify_stage_a(self.stage,self.config,assembly.sha(self.source))
        assembly.write(self.stage/'STAGE_A_BOUNDARY.json',dict(config_sha256=assembly.sha(self.config_path),source_sha256=assembly.sha(self.source),conversion=conversion))
    def test_boundary_verifies_actual_model_env_and_report_bytes(self):
        _,proof=assembly.verify_boundary(self.stage,self.config,self.config_path,assembly.sha(self.source))
        self.assertEqual(proof['model_sha256'],assembly.sha(self.stage/'room.babylon'))
    def test_changed_source_is_rejected(self):
        self.source.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'source/config changed'):
            assembly.verify_boundary(self.stage,self.config,self.config_path,assembly.sha(self.source))
    def test_changed_model_or_env_is_rejected(self):
        for name in ('room.babylon','environment.env'):
            path=self.stage/name;old=path.read_bytes();path.write_bytes(b'changed')
            with self.assertRaises(ValueError):assembly.verify_boundary(self.stage,self.config,self.config_path,assembly.sha(self.source))
            path.write_bytes(old)
    def test_rehashed_conversion_report_cannot_rewrite_boundary(self):
        path=self.stage/'environment.env';path.write_bytes(b'changed')
        report=assembly.read(self.stage/'room.ktx-report.json');report['delivery_files'][1]['sha256']=assembly.sha(path)
        assembly.write(self.stage/'room.ktx-report.json',report)
        with self.assertRaisesRegex(ValueError,'conversion report changed'):
            assembly.verify_boundary(self.stage,self.config,self.config_path,assembly.sha(self.source))
    def test_invalid_file_defaults_are_rejected_before_sealing_stage_a(self):
        data=copy.deepcopy(self.model);data['meshes'][0]['metadata']['kadshowEnvironmentLightingDefaults']={'version':2,'mode':'advanced','multiplier':1}
        assembly.write(self.stage/'room.babylon',data)
        report=assembly.read(self.stage/'room.ktx-report.json')
        report['delivery_files'][0]['sha256']=assembly.sha(self.stage/'room.babylon')
        report['options']['environment_lighting_defaults']=data['meshes'][0]['metadata']['kadshowEnvironmentLightingDefaults']
        assembly.write(self.stage/'room.ktx-report.json',report)
        with self.assertRaisesRegex(ValueError,'Invalid Stage A file defaults'):self.seal()

    def test_marker_rejects_duplicate_geometry_and_wrong_metadata(self):
        for data in ({'meshes':[]},{'meshes':[{'name':'hasenv'},{'name':'HASENV'}]},
            {'meshes':[{'name':'hasenv','positions':[1]}]},{'meshes':[{'name':'hasenv','metadata':[]}]},
            {'meshes':[{'name':'hasenv','metadata':{assembly.VOLUME_KEY:{}}}]}):
            with self.subTest(data=data),self.assertRaises(ValueError):assembly.marker(data)
    def test_only_volume_metadata_delta_is_allowed(self):
        after=copy.deepcopy(self.model);descriptor={'strength':1,'advanced':{'strength':1}}
        after['meshes'][0]['metadata'][assembly.VOLUME_KEY]=descriptor
        self.assertEqual(assembly.metadata_only_delta(self.model,after),descriptor)
        for mutate in (lambda v:v['materials'][0].update(name='changed'),lambda v:v['meshes'][0].update(position=[1,0,0]),
                       lambda v:v['meshes'][0]['metadata'].update(kadshowEnvironmentLightingDefaults={'mode':'off'})):
            changed=copy.deepcopy(after);mutate(changed)
            with self.assertRaises(ValueError):assembly.metadata_only_delta(self.model,changed)
    def test_volume_source_guard_failure_is_propagated_without_an_assembly_pass(self):
        api=types.ModuleType('babylon_js.environment_lighting_volume');api.saved_options=Mock(return_value={'config':'actual receipt checked by API'})
        api.export_volume=Mock(side_effect=ValueError('Relocated physical/preview scene state changed'))
        package=types.ModuleType('babylon_js');package.__path__=[]
        bpy=types.SimpleNamespace(app=types.SimpleNamespace(background=True),data=types.SimpleNamespace(filepath=str(self.source),is_dirty=False,images=[]),
            context=types.SimpleNamespace(scene=types.SimpleNamespace(world={'bjs_environment_image':str(self.hdr)})),path=types.SimpleNamespace(abspath=lambda p:p))
        with patch.dict(sys.modules,{'bpy':bpy,'babylon_js':package,api.__name__:api}):
            with self.assertRaisesRegex(ValueError,'physical/preview'):
                assembly.assemble(self.config_path,self.stage,self.root/'stage-b')
        api.export_volume.assert_called_once()
        self.assertFalse((self.root/'stage-b/assembly-report.json').exists())
        self.assertEqual(self.source.read_bytes(),b'synthetic saved source')
    def test_saved_volume_suppression_is_opt_in_and_restores_even_on_error(self):
        bpy=types.SimpleNamespace(app=types.SimpleNamespace(background=True));world={'bjs_environment_lighting_volume_config':'original','keep':1}
        with patch.dict(sys.modules,{'bpy':bpy}):
            with assembly.without_saved_volume(world,{}):self.assertEqual(world['bjs_environment_lighting_volume_config'],'original')
            with self.assertRaisesRegex(RuntimeError,'operation failed'):
                with assembly.without_saved_volume(world,self.config):
                    self.assertNotIn('bjs_environment_lighting_volume_config',world)
                    raise RuntimeError('operation failed')
        self.assertEqual(world,{'bjs_environment_lighting_volume_config':'original','keep':1})
    def test_unknown_route_rejects_without_clearing_saved_values(self):
        world={'bjs_environment_lighting_volume_config':'original'}
        with self.assertRaises(ValueError):
            with assembly.without_saved_volume(world,{'volume_export_route':'unknown'}):pass
        self.assertEqual(world['bjs_environment_lighting_volume_config'],'original')

if __name__=='__main__':unittest.main()
