"""Native read-only-scene image regression: Blender --python TEST -- NEW_OUTPUT."""
import hashlib,json,sys,unittest
from pathlib import Path
import bpy,numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'src/babylon_js/environment_tools')]
from babylon_js.lighting_controls import create_preview_material,validate_preview_resolution
import worker

OUT=Path(sys.argv[sys.argv.index('--')+1]);OUT.mkdir(parents=True,exist_ok=False)
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()

class RelinkTests(unittest.TestCase):
    def setUp(self):
        self.root=OUT/self._testMethodName;self.root.mkdir()
        self.build=self.root/'build';(self.build/'masters').mkdir(parents=True)
        if 'bjs_lighting_controls_material' in bpy.context.scene:del bpy.context.scene['bjs_lighting_controls_material']
        self.colour=self.image('old-colour',1,'sRGB')
        self.old={role:self.image('old-'+role,value,'Non-Color') for role,value in (('direct',2),('indirect',3),('island_ids',1))}
        self.material,self.control=create_preview_material(self.colour,self.old['direct'],self.old['indirect'],self.old['island_ids'],name=self._testMethodName)
        self.control.inputs['Indirect Strength'].default_value=1.75
        self.control.inputs['Shadow Lift'].default_value=.125
        self.values={s.name:s.default_value for s in self.control.inputs}
        self.offsets=[tuple(n.inputs[0].default_value) for n in self.control.node_tree.nodes if 'bjs_pixel_offset' in n]
        for role,image in self.old.items():image['relink_test_role']=role;image.alpha_mode='STRAIGHT'
        self.config=dict(_root=str(self.root),size=32,preview_material=self.material.name,uv='SimpleBake2',
            images={role:'new-logical-targets/'+role+'.exr' for role in ('direct','indirect','island_ids','combined')})

    def image(self,name,value,space='Non-Color',size=32,path=None):
        path=path or self.root/(name+'.exr');path.parent.mkdir(parents=True,exist_ok=True)
        pixels=np.ones((size,size,4),np.float32);pixels[:,:,:3]=value
        worker.write_exr(path,pixels)
        image=bpy.data.images.load(str(path),check_existing=False);image.colorspace_settings.name=space
        return image

    def masters(self,response=False,bad_size=False):
        result={}
        for role,value in (('direct',7),('indirect',11),('island_ids',53)):
            name=role+'_response_processed.exr' if response and role!='island_ids' else ('island_ids.exr' if role=='island_ids' else role+'_final.exr')
            result[role]=self.image('current-'+role,value,path=self.build/'masters'/name,size=64 if bad_size and role=='direct' else 32)
        return result

    def samplers(self):
        return {role:[n for n in self.control.node_tree.nodes if n.type=='TEX_IMAGE' and n.get('bjs_preview_image_role')==role]
                for role in self.old}

    def unchanged_controls(self):
        self.assertEqual(self.values,{s.name:s.default_value for s in self.control.inputs})
        self.assertEqual(self.offsets,[tuple(n.inputs[0].default_value) for n in self.control.node_tree.nodes if 'bjs_pixel_offset' in n])

    def test_stale_donor_binds_every_current_role_without_foreign_remap(self):
        current=self.masters()
        foreign=bpy.data.materials.new('UnrelatedCustom');foreign.use_nodes=True
        foreign_node=foreign.node_tree.nodes.new('ShaderNodeTexImage');foreign_node.image=self.old['direct']
        old_hash=sha(self.old['direct'].filepath)
        worker.relink(self.config,self.build)
        self.assertEqual([len(v) for v in self.samplers().values()],[1,25,25])
        for role,nodes in self.samplers().items():
            for node in nodes:
                self.assertEqual(sha(bpy.path.abspath(node.image.filepath)),sha(current[role].filepath))
                self.assertEqual(node.image.colorspace_settings.name,'Non-Color')
                self.assertEqual(node.image.alpha_mode,'STRAIGHT')
                self.assertEqual(node.image['relink_test_role'],role)
                self.assertEqual(float(node.image.pixels[0]),dict(direct=7,indirect=11,island_ids=53)[role])
        self.assertEqual(sha(bpy.path.abspath(foreign_node.image.filepath)),old_hash)
        self.assertEqual(float(foreign_node.image.pixels[0]),2)
        self.unchanged_controls();self.assertEqual(validate_preview_resolution(self.material,(32,32))['status'],'verified')

    def test_no_masters_is_safe_before_processing(self):
        before={r:[n.image for n in nodes] for r,nodes in self.samplers().items()}
        worker.relink(self.config,self.build)
        self.assertEqual(before,{r:[n.image for n in nodes] for r,nodes in self.samplers().items()});self.unchanged_controls()

    def test_partial_lighting_set_rejected_before_sampler_changes(self):
        self.image('only-direct',7,path=self.build/'masters/direct_final.exr')
        before={r:[n.image for n in nodes] for r,nodes in self.samplers().items()}
        with self.assertRaisesRegex(ValueError,'Incomplete managed lighting masters'):worker.relink(self.config,self.build)
        self.assertEqual(before,{r:[n.image for n in nodes] for r,nodes in self.samplers().items()});self.unchanged_controls()

    def test_response_route_uses_current_processed_response(self):
        self.masters();response=self.masters(response=True);self.config['receiver_response']={'enabled':True}
        worker.relink(self.config,self.build)
        for role,nodes in self.samplers().items():
            self.assertTrue(all(sha(bpy.path.abspath(n.image.filepath))==sha(response[role].filepath) for n in nodes))
        self.unchanged_controls()

    def test_response_ids_only_does_not_bind_partial_response(self):
        self.config['receiver_response']={'enabled':True};self.image('ids-only',53,path=self.build/'masters/island_ids.exr')
        before={r:[n.image for n in nodes] for r,nodes in self.samplers().items()}
        worker.relink(self.config,self.build)
        self.assertEqual(before,{r:[n.image for n in nodes] for r,nodes in self.samplers().items()})

    def test_custom_graph_retains_existing_path_replacement(self):
        current=self.masters();custom=bpy.data.materials.new('LegacyCustom');custom.use_nodes=True
        node=custom.node_tree.nodes.new('ShaderNodeTexImage');node.image=self.old['direct']
        self.config['preview_material']=custom.name
        self.config['images']['direct']=Path(self.old['direct'].filepath).name
        worker.relink(self.config,self.build)
        self.assertEqual(sha(bpy.path.abspath(node.image.filepath)),sha(current['direct'].filepath))

    def test_changed_managed_role_contract_rejected(self):
        self.masters();self.control.node_tree.nodes.remove(self.samplers()['indirect'][-1])
        with self.assertRaisesRegex(ValueError,'samplers changed'):worker.relink(self.config,self.build)

    def test_current_master_dimensions_rejected_without_graph_assignment(self):
        self.masters(bad_size=True);before={r:[n.image for n in nodes] for r,nodes in self.samplers().items()}
        with self.assertRaisesRegex(ValueError,'current-master dimensions differ'):worker.relink(self.config,self.build)
        self.assertEqual(before,{r:[n.image for n in nodes] for r,nodes in self.samplers().items()});self.unchanged_controls()

result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RelinkTests))
(OUT/'result.json').write_text(json.dumps(dict(status='passed' if result.wasSuccessful() else 'failed',tests=result.testsRun,
 failures=len(result.failures),errors=len(result.errors),all_role_hashes_checked=True,no_render_or_bake=True),indent=2))
if not result.wasSuccessful():raise RuntimeError('Managed worker relink regression failed')
