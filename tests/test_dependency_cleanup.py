"""Audited cleanup execution and immutable-parent guards without a Blender install."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(os.environ.get('BJS_PLUGIN_ROOT',Path(__file__).resolve().parents[1]/'src/babylon_js'))
sys.path.insert(0,str(ROOT/'environment_tools'))
spec=importlib.util.spec_from_file_location('dependency_cleanup_contract',ROOT/'environment_tools/volume_relocation.py')
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
from common import sha


class Items(list):
    def get(self,name):return next((item for item in self if item.name==name),None)
    def __getitem__(self,key):return self.get(key) if isinstance(key,str) else super().__getitem__(key)


class Scene(dict):
    def __init__(self):
        super().__init__(bjs_lighting_controls_material='Protected')
        self.view_layers=[SimpleNamespace(names=[])]


class Tree:
    def __init__(self,name,nodes=()):self.name=name;self.nodes=list(nodes)
    def as_pointer(self):return id(self)


class Node:
    type='TEX_IMAGE'
    def __init__(self,name,image):self.name=name;self._image=None;self.image=image
    @property
    def image(self):return self._image
    @image.setter
    def image(self,value):
        if self._image is not None:self._image.users-=1
        self._image=value
        if value is not None:value.users+=1


class Images(Items):
    def remove(self,image,do_unlink=False):
        assert not do_unlink and image.users==0
        super().remove(image)


class CleanupContracts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.base=self.root/'blender/textures/volumes';self.base.mkdir(parents=True)
        self.source=self.root/'blender/source.blend';self.source.write_bytes(b'accepted portable parent')
        self.target=self.source.with_name('clean.blend')
        self.receipt=self.base/'volume_receipt.json';self.proof_path=self.base/'relocation_provenance.json'
        original_source=dict(scene='historical.blend',sceneSha256='a'*64,worldControls={})
        source=copy.deepcopy(original_source)
        source.update(scene='../../source.blend',sceneSha256=sha(self.source))
        for key in ('scopeRecipe','combined','combinedProvenance','reference'):
            path=self.root/(key+'.input');path.write_bytes(key.encode())
            source[key]=os.path.relpath(path,self.base).replace('\\','/');source[key+'Sha256']=sha(path)
            original_source[key]='historical/'+key;original_source[key+'Sha256']=sha(path)
        self.original=dict(version=1,master='simple.exr',source=original_source,statistics={'accepted':True})
        self.write('original_volume_receipt.json',self.original)
        self.before={'version':1,'sha256':'b'*64};self.after={'version':1,'sha256':'c'*64}
        self.proof=dict(version=1,originalSourceSha256='a'*64,sourceSha256=sha(self.source),
                        originalReceiptSha256=sha(self.base/'original_volume_receipt.json'),sceneIdentity=self.before)
        self.write(self.proof_path.name,self.proof)
        self.record=dict(copy.deepcopy(self.original),source=source,pathBase='receipt',packageRoot='../../..',
                         relocation=dict(version=1,originalReceipt='original_volume_receipt.json',
                             originalReceiptSha256=sha(self.base/'original_volume_receipt.json'),
                             proof=self.proof_path.name,proofSha256=sha(self.proof_path)))
        self.write(self.receipt.name,self.record)
        self.original_receipt=self.receipt.read_bytes();self.original_proof=self.proof_path.read_bytes()
        def image(name):
            path=self.root/(name+'.png');path.write_bytes(name.encode())
            return SimpleNamespace(name=name,filepath=str(path),source='FILE',packed_file=None,is_dirty=False,
                                   use_fake_user=False,users=0)
        self.old=image('legacy');self.new=image('current');self.images=Images([self.old,self.new])
        self.node=Node('Old colour',self.old)
        self.legacy=SimpleNamespace(name='Legacy',use_nodes=True,node_tree=Tree('Legacy tree',[self.node]))
        self.protected=SimpleNamespace(name='Protected',use_nodes=True,node_tree=Tree('Protected tree'))
        self.world=SimpleNamespace(name='World',node_tree=Tree('World tree'))
        self.materials=Items([self.legacy,self.protected])
        self.scene=Scene()
        self.objects=Items([SimpleNamespace(name='Visible',material_slots=[SimpleNamespace(material=self.legacy)])])
        self.data=SimpleNamespace(filepath=str(self.source),is_dirty=False,images=self.images,materials=self.materials,
                                  worlds=[self.world],scenes=[self.scene],node_groups=[],objects=self.objects)
        self.saved=[];self.bad_state=False
        def save(**kwargs):
            self.saved.append(kwargs);Path(kwargs['filepath']).write_bytes(b'audited cleaned source')
            self.data.filepath=kwargs['filepath'];return {'FINISHED'}
        self.bpy=SimpleNamespace(app=SimpleNamespace(background=True),data=self.data,context=SimpleNamespace(mode='OBJECT'),
                                 path=SimpleNamespace(abspath=lambda path:path),ops=SimpleNamespace(wm=SimpleNamespace(save_as_mainfile=save)))
        self.config=dict(schema_version=1,package_root=str(self.root),source_receipt=str(self.receipt),
                         source_sha256=sha(self.source),destination_scene='blender/clean.blend',
                         protected_materials=['Protected'],image_remaps=[dict(source=self.old.name,target=self.new.name,
                             source_sha256=sha(self.old.filepath),target_sha256=sha(self.new.filepath))])
        self.config_path=self.root/'cleanup.json'

    def write(self,name,value):
        (self.base/name).write_text(json.dumps(value),encoding='utf8')

    def identity(self,context,**kwargs):
        if kwargs:
            self.assertEqual(kwargs['image_substitutions'],{'legacy':self.new} if self.config['image_remaps'][0]['target'] is not None else {})
            self.assertEqual(kwargs['omitted_images'],{'legacy'})
            return self.after
        if self.bad_state and self.old not in self.images:return {'version':1,'sha256':'d'*64}
        return self.before if self.old in self.images else self.after

    def preview(self,owner):
        return [(node.name,node.image.name) for node in owner.node_tree.nodes if node.type=='TEX_IMAGE']

    def validate_source(self,context,record,receipt_path):
        helper.validate_relocation(context,record,receipt_path)
        source=helper.package_paths(record,receipt_path)
        for key in ('scene','scopeRecipe','combined','combinedProvenance','reference'):
            if sha(source[key])!=source[key+'Sha256']:raise ValueError('Capture input changed')

    def run_cleanup(self):
        self.config_path.write_text(json.dumps(self.config),encoding='utf8')
        volume=SimpleNamespace(validate_record=lambda value:value,validate_source=self.validate_source)
        with patch.dict(sys.modules,{'bpy':self.bpy,'appearance':SimpleNamespace(preview_identity=self.preview),
                                     'babylon_js.workflow_tools.inspect_blender':SimpleNamespace(_render_candidates=lambda layer:set(layer.names)),
                                     'babylon_js.environment_lighting_volume':volume}), \
             patch.object(helper,'scene_identity',side_effect=self.identity):
            helper.cleanup_dependencies(self.config_path)

    def test_actual_cleanup_saves_once_and_validates_current_and_parent_lineage(self):
        self.run_cleanup()
        self.assertEqual(len(self.saved),1);self.assertEqual(self.node.image,self.new)
        self.assertEqual([image.name for image in self.images],['current'])
        self.assertEqual(self.source.read_bytes(),b'accepted portable parent')
        proof=json.loads(self.proof_path.read_text());cleanup=proof['dependencyCleanup']
        self.assertEqual((self.base/cleanup['parentReceipt']).read_bytes(),self.original_receipt)
        self.assertEqual((self.base/cleanup['parentProof']).read_bytes(),self.original_proof)
        current=json.loads(self.receipt.read_text())
        self.assertEqual(current['source']['sceneSha256'],sha(self.target))
        helper.validate_dependency_cleanup_lineage(current,proof,self.base)
        (self.base/cleanup['parentProof']).write_text('{}')
        with self.assertRaisesRegex(ValueError,'parent evidence'):
            helper.validate_dependency_cleanup_lineage(current,proof,self.base)

    def test_protected_material_rejects_before_save(self):
        self.protected.node_tree.nodes.append(Node('Protected image',self.old))
        with self.assertRaisesRegex(ValueError,'protected material/World'):self.run_cleanup()
        self.assertFalse(self.saved);self.assertFalse(self.target.exists())

    def test_world_image_user_rejects_before_save(self):
        self.world.node_tree.nodes.append(Node('World image',self.old))
        with self.assertRaisesRegex(ValueError,'remaining image user'):self.run_cleanup()
        self.assertFalse(self.saved)

    def test_unlisted_render_eligible_material_is_automatically_protected(self):
        self.scene.view_layers[0].names=['Visible']
        with self.assertRaisesRegex(ValueError,'protected material/World'):self.run_cleanup()
        self.assertFalse(self.saved)

    def test_unsupported_non_node_image_user_rejects_before_save(self):
        self.old.users+=1
        with self.assertRaisesRegex(ValueError,'remaining image user'):self.run_cleanup()
        self.assertFalse(self.saved)

    def test_wrong_source_hash_or_changed_pixels_reject_before_save(self):
        for mutate in ('source','pixels'):
            with self.subTest(mutate=mutate):
                original=self.config['source_sha256']
                if mutate=='source':self.config['source_sha256']='0'*64
                else:Path(self.old.filepath).write_bytes(b'changed pixels')
                with self.assertRaisesRegex(ValueError,'source hash|image bytes'):self.run_cleanup()
                self.config['source_sha256']=original
                self.assertFalse(self.saved)

    def test_unrelated_scene_mutation_fails_before_save(self):
        self.bad_state=True
        with self.assertRaisesRegex(ValueError,'undeclared scene state'):self.run_cleanup()
        self.assertFalse(self.saved)

    def test_remove_only_requires_no_real_users(self):
        self.config['image_remaps'][0].update(target=None,target_sha256=None)
        with self.assertRaisesRegex(ValueError,'Removal-only image'):self.run_cleanup()
        self.assertFalse(self.saved)
        self.node.image=self.new
        self.run_cleanup()
        self.assertIs(self.node.image,self.new)
        self.assertEqual([image.name for image in self.images],['current'])

    def test_declared_sources_cannot_target_each_other_or_drop_protection(self):
        invalid=copy.deepcopy(self.config);invalid['protected_materials']=[]
        with self.assertRaises(ValueError):helper.validate_dependency_cleanup_config(invalid)
        invalid=copy.deepcopy(self.config);invalid['image_remaps'].append(dict(source='current',target='retained',source_sha256='1'*64,target_sha256='2'*64))
        with self.assertRaisesRegex(ValueError,'retained images'):helper.validate_dependency_cleanup_config(invalid)

    def test_preview_counterfactual_changes_only_fingerprint_not_image_binding(self):
        for image in self.images:
            image.colorspace_settings=SimpleNamespace(name='sRGB');image.alpha_mode='STRAIGHT'
        node=self.node
        node.bl_rna=SimpleNamespace(properties=[]);node.inputs=[];node.bl_idname='ShaderNodeTexImage'
        node.get=lambda key:None
        self.legacy.node_tree.links=[]
        appearance_spec=importlib.util.spec_from_file_location('cleanup_appearance_contract',ROOT/'environment_tools/appearance.py')
        appearance=importlib.util.module_from_spec(appearance_spec)
        with patch.dict(sys.modules,{'bpy':self.bpy}):
            appearance_spec.loader.exec_module(appearance)
            original=appearance.preview_identity(self.legacy,diagnostic=True)
            canonical=appearance.preview_identity(self.legacy,diagnostic=True,image_substitutions={'legacy':self.new})
            self.assertEqual(original['nodes'][0]['image']['sha256'],sha(self.old.filepath))
            self.assertEqual(canonical['nodes'][0]['image']['sha256'],sha(self.new.filepath))
            self.assertIs(self.node.image,self.old)
            self.assertEqual(appearance.preview_identity(self.legacy,diagnostic=True),original)


if __name__=='__main__':unittest.main()
