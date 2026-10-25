"""Optional saved/relocated producer policies; pure CPU, no Blender job/save."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock,patch

ROOT=Path(os.environ.get('BJS_PLUGIN_ROOT',Path(__file__).resolve().parents[1]/'src/babylon_js'))
sys.path.insert(0,str(ROOT/'environment_tools'))
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);sys.modules[name]=value;spec.loader.exec_module(value);return value
volume=load('persistence_test_volume',ROOT/'environment_lighting_volume.py')
relocation=load('persistence_test_relocation',ROOT/'environment_tools/volume_relocation.py')

class World(dict):
    def __init__(self,name,values=None):super().__init__(values or {});self.name=name


class VolumePolicyPersistence(unittest.TestCase):
    def test_saved_defaults_and_absent_association_are_unchanged(self):
        self.assertIsNone(volume.saved_options({}))
        self.assertIsNone(volume.saved_options({'bjs_environment_lighting_volume_simple_divisor_mode':'block-4x4'}))
        self.assertEqual(volume.saved_options({'bjs_environment_lighting_volume_config':'//receipt.json'}),
                         dict(config='//receipt.json',image='',strength=1))

    def test_saved_policies_are_passed_to_ordinary_export_together(self):
        world={'bjs_environment_lighting_volume_config':'//receipt.json','bjs_environment_lighting_volume_image':'//simple.exr',
            'bjs_environment_lighting_volume_strength':.5,'bjs_environment_lighting_volume_simple_divisor_mode':'block-4x4',
            'bjs_environment_lighting_volume_advanced_alpha_policy':'source-ceiling'}
        original=copy.deepcopy(world)
        self.assertEqual(volume.saved_options(world),dict(config='//receipt.json',image='//simple.exr',strength=.5,
            simple_divisor_mode='block-4x4',advanced_alpha_policy='source-ceiling'))
        self.assertEqual(world,original)

    def test_saved_explicit_per_pixel_and_omitted_other_policy(self):
        value=volume.saved_options({'bjs_environment_lighting_volume_config':'receipt.json',
            'bjs_environment_lighting_volume_simple_divisor_mode':'per-pixel'})
        self.assertEqual(value['simple_divisor_mode'],'per-pixel');self.assertNotIn('advanced_alpha_policy',value)

    def test_optional_relocation_values_are_exact_and_return_a_copy(self):
        for options in ({},{'simple_divisor_mode':'per-pixel'},{'simple_divisor_mode':'block-4x4'},
            {'advanced_alpha_policy':'source-ceiling'},{'simple_divisor_mode':'block-4x4','advanced_alpha_policy':'source-ceiling'}):
            result=relocation.validate_volume_export_options(options)
            self.assertEqual(result,options);self.assertIsNot(result,options)

    def test_invalid_options_reject_before_any_write_or_save(self):
        invalid=[None,False,True,0,[],['block-4x4'],'source-ceiling',
            {'unknown':'block-4x4'},{1:'block-4x4'},{'simple_divisor_mode':None},{'simple_divisor_mode':False},
            {'simple_divisor_mode':1},{'simple_divisor_mode':'image-wide'},{'simple_divisor_mode':'block4'},
            {'advanced_alpha_policy':None},{'advanced_alpha_policy':False},{'advanced_alpha_policy':'off'},
            {'simple_divisor_mode':'block-4x4','unexpected':'source-ceiling'}]
        configs=[{'volume_export_options':value} for value in invalid]+[None,[],False]
        for config in configs:
            with self.subTest(config=config):
                world=World('World',{'preserve':'unchanged'});previous=dict(world);save=Mock()
                fake_bpy=types.SimpleNamespace(app=types.SimpleNamespace(background=True),data=types.SimpleNamespace(worlds=[world]),
                    ops=types.SimpleNamespace(wm=types.SimpleNamespace(save_as_mainfile=save)))
                fake_volume=types.ModuleType('babylon_js.environment_lighting_volume')
                fake_volume.validate_record=Mock();fake_volume.validate_source=Mock()
                package=types.ModuleType('babylon_js');package.__path__=[]
                with patch.dict(sys.modules,{'bpy':fake_bpy,'babylon_js':package,'babylon_js.environment_lighting_volume':fake_volume}),\
                     patch.object(Path,'read_text',return_value=json.dumps(config)),patch.object(Path,'mkdir') as mkdir,\
                     patch.object(Path,'write_text') as write,patch('shutil.copyfile') as copyfile:
                    with self.assertRaises(ValueError):relocation.relocate('invalid-policy.json')
                    mkdir.assert_not_called();write.assert_not_called();copyfile.assert_not_called();save.assert_not_called()
                    fake_volume.validate_record.assert_not_called();fake_volume.validate_source.assert_not_called()
                    self.assertEqual(world,previous)

    def test_new_policy_proof_binds_all_worlds_and_detects_changed_policy(self):
        options={'simple_divisor_mode':'block-4x4','advanced_alpha_policy':'source-ceiling'}
        worlds=[World(name,{relocation.VOLUME_EXPORT_POLICIES[k][0]:v for k,v in options.items()}) for name in ('WorldA','WorldB')]
        changes=[dict(world=w.name,key=relocation.VOLUME_EXPORT_POLICIES[k][0],previous=None,current=v) for w in worlds for k,v in options.items()]
        proof=dict(volumeExportOptions=options,volumeExportPolicyChanges=changes)
        relocation.validate_saved_export_policies(worlds,proof)
        worlds[0]['bjs_environment_lighting_volume_simple_divisor_mode']='per-pixel'
        with self.assertRaises(ValueError):relocation.validate_saved_export_policies(worlds,proof)

    def test_advanced_policy_without_advanced_receipt_rejects_before_any_write(self):
        config=dict(package_root='unused',source_receipt='unused/receipt.json',volume_export_options={'advanced_alpha_policy':'source-ceiling'})
        fake_bpy=types.SimpleNamespace(app=types.SimpleNamespace(background=True),data=types.SimpleNamespace(worlds=[World('World')]),
            ops=types.SimpleNamespace(wm=types.SimpleNamespace(save_as_mainfile=Mock())))
        fake_volume=types.ModuleType('babylon_js.environment_lighting_volume');fake_volume.validate_record=lambda record:record;fake_volume.validate_source=Mock()
        package=types.ModuleType('babylon_js');package.__path__=[]
        with patch.dict(sys.modules,{'bpy':fake_bpy,'babylon_js':package,'babylon_js.environment_lighting_volume':fake_volume}),\
             patch.object(Path,'read_text',side_effect=[json.dumps(config),json.dumps({'version':1})]),\
             patch.object(Path,'mkdir') as mkdir,patch.object(Path,'write_text') as write,patch('shutil.copyfile') as copyfile:
            with self.assertRaisesRegex(ValueError,'Advanced receipt'):relocation.relocate('advanced-policy-no-advanced.json')
            mkdir.assert_not_called();write.assert_not_called();copyfile.assert_not_called();fake_bpy.ops.wm.save_as_mainfile.assert_not_called()

    def test_incomplete_duplicate_unknown_or_wrong_policy_proof_rejects(self):
        world=World('World',{'bjs_environment_lighting_volume_simple_divisor_mode':'block-4x4'})
        row=dict(world='World',key='bjs_environment_lighting_volume_simple_divisor_mode',previous=None,current='block-4x4')
        valid=dict(volumeExportOptions={'simple_divisor_mode':'block-4x4'},volumeExportPolicyChanges=[row])
        variants=[{'volumeExportOptions':{}},{'volumeExportPolicyChanges':[]},
            dict(valid,volumeExportPolicyChanges=[]),dict(valid,volumeExportPolicyChanges=[row,row]),
            dict(valid,volumeExportOptions={'unknown':'value'}),dict(valid,volumeExportPolicyChanges=[dict(row,current='per-pixel')]),
            dict(valid,volumeExportPolicyChanges=[dict(row,extra=True)])]
        for proof in variants:
            with self.subTest(proof=proof):
                with self.assertRaises(ValueError):relocation.validate_saved_export_policies([world],proof)

    def test_old_policy_free_proofs_remain_unchanged_and_need_no_world_access(self):
        relocation.validate_saved_export_policies(None,{'version':1,'sceneIdentity':{'sha256':'old'}})


if __name__=='__main__':unittest.main()
