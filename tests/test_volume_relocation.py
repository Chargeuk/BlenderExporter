"""Portable receipt boundaries and immutable lineage guards; no Blender needed."""
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
from unittest.mock import patch

ROOT=Path(os.environ.get('BJS_PLUGIN_ROOT',Path(__file__).resolve().parents[1]/'src/babylon_js'))
HELPER=ROOT/'environment_tools/volume_relocation.py'
sys.path.insert(0,str(HELPER.parent))
spec=importlib.util.spec_from_file_location('tested_volume_relocation',HELPER)
relocation=importlib.util.module_from_spec(spec);spec.loader.exec_module(relocation)


class RelocationGuards(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.base=self.root/'blender/textures/environment/lighting_volume';self.base.mkdir(parents=True)
        self.receipt=self.base/'volume_receipt.json'
        self.source=dict(scene='../../../source.blend',sceneSha256='a'*64,scopeRecipe='../../../scope.json',
            scopeRecipeSha256='b'*64,combined='../../lightmaps/combined.exr',combinedSha256='c'*64,
            combinedProvenance='../../../provenance/combined.json',combinedProvenanceSha256='d'*64,
            reference='../capture.exr',referenceSha256='e'*64,previewSha256='f'*64,geometrySha256='1'*64,
            worldImageSha256='2'*64,worldControls={'Rotation':0,'Lighting Strength':1,'Sky Lift':0,'Lift Rolloff':1,'Visible Sky Strength':1,'image':'../world.exr'})
        self.original=dict(version=1,master='simple.exr',source=copy.deepcopy(self.source),statistics={'validCoarseProbes':16})
        self.original['source']['scene']='C:/historical/source.blend'
        self.proof=dict(version=1,originalSourceSha256='a'*64,originalReceiptSha256=self.write('original_volume_receipt.json',self.original),sourceSha256='a'*64,sceneIdentity={'version':1,'sha256':'3'*64})
        self.record=dict(copy.deepcopy(self.original),source=copy.deepcopy(self.source),pathBase='receipt',packageRoot='../../../..',
            relocation=dict(version=1,originalReceipt='original_volume_receipt.json',originalReceiptSha256=self.proof['originalReceiptSha256'],
                proof='relocation_provenance.json',proofSha256=self.write('relocation_provenance.json',self.proof)))
        self.loaded=(self.base/self.source['scene']).resolve()

    def write(self,name,value):
        path=self.base/name;path.write_text(json.dumps(value),encoding='utf8')
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def validate(self,record=None,identity=None,loaded=None,worlds=None):
        fake=types.SimpleNamespace(data=types.SimpleNamespace(filepath=str(loaded or self.loaded),worlds=worlds or []))
        with patch.dict(sys.modules,{'bpy':fake}),patch.object(relocation,'scene_identity',return_value=identity or self.proof['sceneIdentity']):
            relocation.validate_relocation(object(),record or self.record,self.receipt)

    def test_legacy_source_is_unchanged(self):
        self.assertIs(relocation.package_paths(self.original,None),self.original['source'])

    def test_portable_paths_stay_in_declared_package(self):
        resolved=relocation.package_paths(self.record,self.receipt)
        self.assertEqual(Path(resolved['scene']),self.loaded)
        self.assertTrue(all(Path(resolved[k]).is_relative_to(self.root) for k in ('scene','scopeRecipe','combined','combinedProvenance','reference')))
        with self.assertRaises(ValueError):relocation.package_paths(self.record,None)

    def test_absolute_and_escaping_paths_reject(self):
        for key,value in (('scene',str(self.root/'absolute.blend')),('scopeRecipe','../../../../../escape.json')):
            changed=copy.deepcopy(self.record);changed['source'][key]=value
            with self.assertRaises(ValueError):relocation.package_paths(changed,self.receipt)
        changed=copy.deepcopy(self.record);changed['packageRoot']=str(self.root)
        with self.assertRaises(ValueError):relocation.package_paths(changed,self.receipt)

    def test_matching_relocation_passes(self):
        self.validate()

    def test_changed_loaded_source_and_scene_identity_reject(self):
        with self.assertRaises(ValueError):self.validate(loaded=self.root/'wrong.blend')
        with self.assertRaises(ValueError):self.validate(identity={'version':1,'sha256':'4'*64})

    def test_changed_accepted_payload_and_identity_reject(self):
        for mutate in (lambda r:r['statistics'].update(validCoarseProbes=17),lambda r:r['source'].update(combinedSha256='9'*64),lambda r:r['source'].update(sceneSha256='8'*64)):
            changed=copy.deepcopy(self.record);mutate(changed)
            with self.assertRaises(ValueError):self.validate(changed)

    def test_policy_tamper_rejects_even_with_identical_appearance_identity(self):
        class World(dict): name='FixtureWorld'
        world=World(bjs_environment_lighting_volume_simple_divisor_mode='block-4x4')
        proof=dict(self.proof,volumeExportOptions={'simple_divisor_mode':'block-4x4'},
            volumeExportPolicyChanges=[dict(world='FixtureWorld',key='bjs_environment_lighting_volume_simple_divisor_mode',previous=None,current='block-4x4')])
        changed=copy.deepcopy(self.record)
        changed['relocation']['proofSha256']=self.write('relocation_provenance.json',proof)
        self.validate(changed,worlds=[world])
        world['bjs_environment_lighting_volume_simple_divisor_mode']='per-pixel'
        with self.assertRaises(ValueError):self.validate(changed,worlds=[world])

    def test_modified_evidence_rejects(self):
        (self.base/'original_volume_receipt.json').write_text('{}')
        with self.assertRaises(ValueError):self.validate()

    def test_lineage_path_escape_rejects(self):
        changed=copy.deepcopy(self.record);changed['relocation']['proof']='../proof.json'
        with self.assertRaises(ValueError):self.validate(changed)

    def test_wrong_proof_version_and_origin_reject(self):
        for key,value in (('version',2),('originalSourceSha256','9'*64)):
            proof=dict(self.proof,**{key:value})
            changed=copy.deepcopy(self.record)
            changed['relocation']['proofSha256']=self.write('relocation_provenance.json',proof)
            with self.assertRaises(ValueError):self.validate(changed)


class RelocationStrengthSerialization(unittest.TestCase):
    def run_relocation(self, existing=None, export_options=None, existing_policies=None):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);inputs=root/'inputs';inputs.mkdir()
            source=inputs/'source.blend';source.write_bytes(b'unchanged saved source')
            files={key:inputs/name for key,name in dict(scopeRecipe='scope.json',combined='combined.exr',combinedProvenance='combined.json',reference='reference.exr').items()}
            for key,path in files.items():path.write_bytes(key.encode())
            hdri=inputs/'world.exr';hdri.write_bytes(b'world image')
            source_record=dict(scene=str(source),sceneSha256=hashlib.sha256(source.read_bytes()).hexdigest(),worldControls={'image':str(hdri)})
            source_record.update({key:str(path) for key,path in files.items()})
            capture=root/'capture';capture.mkdir()
            original=dict(version=1,master='simple.exr',advanced={'master':'advanced.exr','provenance':'advanced.json'},source=source_record)
            original_receipt=capture/'volume_receipt.json';original_receipt.write_text(json.dumps(original))
            original_bytes=original_receipt.read_bytes()
            for name in ('simple.exr','advanced.exr','advanced.json'):(capture/name).write_bytes(name.encode())
            class World(dict): name='FixtureWorld'
            world=World(existing_policies or {})
            if existing is not None:world['bjs_environment_lighting_volume_strength']=existing
            expected=1 if existing is None else existing
            target=root/'blender/relocated.blend';receipt=root/'blender/volume/volume_receipt.json'
            config=dict(package_root=str(root),source_receipt=str(original_receipt),destination_scene='blender/relocated.blend',
                destination_receipt='blender/volume/volume_receipt.json',path_map={str(p):p.relative_to(root).as_posix() for p in [*files.values(),hdri]})
            if export_options is not None:config['volume_export_options']=export_options
            config_path=root/'relocation.json';config_path.write_text(json.dumps(config))
            data=types.SimpleNamespace(filepath=str(source),is_dirty=False,images=[],scenes=[{}],worlds=[world],objects=[],materials=[],node_groups=[])
            saves=[]
            def save_before_return(**kwargs):
                self.assertIn('bjs_environment_lighting_volume_strength',world)
                self.assertEqual(world['bjs_environment_lighting_volume_strength'],expected)
                self.assertEqual(Path(kwargs['filepath']),target)
                self.assertEqual(Path(world['bjs_environment_lighting_volume_config'][2:]),Path('volume/volume_receipt.json'))
                for name,value in (export_options or {}).items():
                    self.assertEqual(world[relocation.VOLUME_EXPORT_POLICIES[name][0]],value)
                saves.append(kwargs);target.write_bytes(b'new path-only saved scene');data.filepath=str(target)
            fake_bpy=types.SimpleNamespace(app=types.SimpleNamespace(background=True),data=data,context=object(),
                ops=types.SimpleNamespace(wm=types.SimpleNamespace(save_as_mainfile=save_before_return)))
            fake_volume=types.ModuleType('babylon_js.environment_lighting_volume')
            fake_volume.validate_record=lambda value:value
            validated=[]
            fake_volume.validate_source=lambda *args,**kwargs:validated.append((args,kwargs))
            package=types.ModuleType('babylon_js');package.__path__=[]
            identity={'version':1,'sha256':'3'*64}
            with patch.dict(sys.modules,{'bpy':fake_bpy,'babylon_js':package,'babylon_js.environment_lighting_volume':fake_volume}),patch.object(relocation,'scene_identity',return_value=identity):
                relocation.relocate(config_path)
            self.assertEqual(len(saves),1)
            self.assertEqual(world['bjs_environment_lighting_volume_strength'],expected)
            self.assertEqual(source.read_bytes(),b'unchanged saved source')
            self.assertEqual(original_receipt.read_bytes(),original_bytes)
            self.assertEqual((receipt.parent/'original_volume_receipt.json').read_bytes(),original_bytes)
            self.assertEqual(len(validated),2)
            changed_policy_keys={relocation.VOLUME_EXPORT_POLICIES[name][0] for name in (export_options or {})}
            for key,value in (existing_policies or {}).items():
                if key not in changed_policy_keys:self.assertEqual(world[key],value)
            proof=json.loads((receipt.parent/'relocation_provenance.json').read_text())
            if export_options:
                self.assertEqual(proof['volumeExportOptions'],export_options)
                self.assertEqual(len(proof['volumeExportPolicyChanges']),len(export_options))
                relocation.validate_saved_export_policies([world],proof)
                self.assertTrue(all(change['world']=='FixtureWorld' for change in proof['volumeExportPolicyChanges']))
            else:
                self.assertNotIn('volumeExportOptions',proof)
                self.assertNotIn('volumeExportPolicyChanges',proof)
                for key,value in (existing_policies or {}).items():self.assertEqual(world[key],value)

    def test_absent_strength_is_explicit_one_before_single_save(self):
        self.run_relocation()

    def test_existing_strength_is_preserved_before_single_save(self):
        for strength in (0,.35,2):
            with self.subTest(strength=strength):self.run_relocation(strength)

    def test_both_policies_exist_before_exactly_one_save_and_are_proven(self):
        self.run_relocation(export_options={'simple_divisor_mode':'block-4x4','advanced_alpha_policy':'source-ceiling'})

    def test_omitted_options_preserve_existing_policies_and_legacy_proof(self):
        self.run_relocation(existing_policies={'bjs_environment_lighting_volume_simple_divisor_mode':'block-4x4',
            'bjs_environment_lighting_volume_advanced_alpha_policy':'source-ceiling'})

    def test_single_explicit_policy_does_not_change_other_saved_policy(self):
        self.run_relocation(export_options={'simple_divisor_mode':'per-pixel'},
            existing_policies={'bjs_environment_lighting_volume_simple_divisor_mode':'block-4x4',
            'bjs_environment_lighting_volume_advanced_alpha_policy':'source-ceiling'})


if __name__=='__main__':unittest.main()
