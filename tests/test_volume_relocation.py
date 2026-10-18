"""Portable receipt boundaries and immutable lineage guards; no Blender needed."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HELPER=Path(__file__).resolve().parents[1]/'src/babylon_js/environment_tools/volume_relocation.py'
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

    def validate(self,record=None,identity=None,loaded=None):
        fake=types.SimpleNamespace(data=types.SimpleNamespace(filepath=str(loaded or self.loaded)))
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


if __name__=='__main__':unittest.main()
