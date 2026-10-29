"""Factory Blender package/API regression for clean-source volume preflight.

The tiny synthetic receipt binds real saved fixture bytes; it is test data, not a
captured/accepted lighting volume. No rendering, encoding or user-scene access.
"""
import argparse,copy,hashlib,io,json,sys,unittest
from pathlib import Path
import bpy,numpy as np
p=argparse.ArgumentParser();p.add_argument('--addon',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--require-registered',action='store_true')
a=p.parse_args(sys.argv[sys.argv.index('--')+1:]);assert bpy.app.background and not a.output.exists();a.output.mkdir(parents=True)
sys.path.insert(0,str(a.addon.parent));sys.path.insert(0,str(a.addon/'environment_tools'))
import babylon_js
from babylon_js import environment_controls
from babylon_js.lighting_controls import create_preview_material
from babylon_js.environment_tools.appearance import preview_identity
from babylon_js.environment_tools.ao import geometry_identity
from babylon_js.environment_tools.volume_assembly import resolve_stage_b_options
from babylon_js.environment_lighting_volume import saved_options,validate_record,validate_source
assert Path(babylon_js.__file__).resolve().parent==a.addon.resolve() and tuple(babylon_js.bl_info['version'])==(3,3,58)
if a.require_registered:assert bpy.ops.export.bjs.get_rna_type().identifier
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(path,data):path.write_text(json.dumps(data,indent=2)+'\n');return path

def image(name,value,space='Non-Color',width=8,height=8):
    path=a.output/(name+'.exr');im=bpy.data.images.new(name,width,height,alpha=True,float_buffer=True)
    im.colorspace_settings.name=space;im.pixels.foreach_set(list(value)*(width*height))
    im.filepath_raw=str(path);im.file_format='OPEN_EXR';im.save();bpy.data.images.remove(im)
    im=bpy.data.images.load(str(path),check_existing=False);im.colorspace_settings.name=space;return im

scene=bpy.context.scene;scene.render.engine='CYCLES'
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add();obj=bpy.context.object;obj.name='Fixture receiver'
obj.data.uv_layers.new(name='SimpleBake')
for d,uv in zip(obj.data.uv_layers['SimpleBake'].data,[(0,0),(1,0),(1,1),(0,1)]):d.uv=uv
preview,controls=create_preview_material(image('colour',(.4,.3,.2,1),'sRGB'),image('direct',(1,1,1,1)),image('indirect',(.2,.2,.2,1)),image('ids',(1,1,1,1)),scene=scene)
obj.data.materials.append(preview)
reference=Path(image('reference',(1,1,1,1),width=8,height=4).filepath)
world=scene.world or bpy.data.worlds.new('Fixture World');scene.world=world
environment_controls.create_controls(world,bpy.data.images.load(str(reference),check_existing=False),connect=True)
world['bjs_environment_image']=str(reference)
combined=Path(image('combined',(1.2,1.2,1.2,1)).filepath)
provenance=write(a.output/'combined.json',{'schema_version':1,'combined_sha256':sha(combined),'preview_sha256':preview_identity(preview)})
source=a.output/'s.blend';scope=write(a.output/'scope.json',{'scene':scene.name,'size':8,'uv':'SimpleBake','receivers':{'names':[obj.name]},'contributors':{'names':[]}})
geometry=geometry_identity(json.loads(scope.read_text()))['geometry_sha256']
master=Path(image('master',(1,1,1,1),width=128,height=128).filepath)
bpy.ops.wm.save_as_mainfile(filepath=str(source));source_before=sha(source)
record={'version':1,'dimensions':[32,16,32],'atlasColumns':4,'atlasRows':4,'atlasLayout':'y-slices-row-major-xz','bounds':{'min':[-2,0,-2],'max':[2,2,2]},'normalization':{'method':'solid-angle-rgb-mean-over-env-reference','referenceRgb':[1,1,1]},'flip_y':False,'master':master.name,'masterSha256':sha(master),'source':{'scene':str(source),'sceneSha256':sha(source),'scopeRecipe':str(scope),'scopeRecipeSha256':sha(scope),'combined':str(combined),'combinedSha256':sha(combined),'combinedProvenance':str(provenance),'combinedProvenanceSha256':sha(provenance),'reference':str(reference),'referenceSha256':sha(reference),'previewSha256':preview_identity(preview),'geometrySha256':geometry,'worldControls':environment_controls.settings(environment_controls.find_controls(world)),'worldImageSha256':sha(reference)}}
receipt=write(a.output/'receipt.json',record)
config={'package_root':str(a.output),'source':source.name,'source_sha256':sha(source),'ktx_options':{'environment_lighting_volume':{'config':receipt.name,'image':master.name,'strength':1}},'bound_dependencies':[{'path':x.name,'sha256':sha(x)} for x in (receipt,master)]}
config_path=write(a.output/'export.json',config)
env={'image':str(reference),'exposure':0,'highlight_gain':1,'highlight_threshold':1}
world_before={k:world[k] for k in world.keys()};preview_before=preview_identity(preview)

class ActualPackagePreflightTests(unittest.TestCase):
    def test_clean_source_bound_options_and_real_producer_validation(self):
        self.assertIsNone(saved_options(world));options=resolve_stage_b_options(config,config_path,None)
        self.assertEqual(options['strength'],1);self.assertEqual(options['image'],str(master))
        validate_source(bpy.context,validate_record(copy.deepcopy(record)),env,receipt)
    def test_changed_combined_digest_rejected(self):
        changed=copy.deepcopy(record);changed['source']['combinedSha256']='0'*64
        with self.assertRaisesRegex(ValueError,'input changed'):validate_source(bpy.context,changed,env,receipt)
    def test_nonneutral_ENV_rejected(self):
        with self.assertRaisesRegex(ValueError,'neutral ENV'):validate_source(bpy.context,record,dict(env,exposure=1),receipt)
    def test_wrong_ENV_reference_rejected(self):
        with self.assertRaisesRegex(ValueError,'ENV differs'):validate_source(bpy.context,record,dict(env,image=str(config_path)),receipt)
    def test_unbound_explicit_receipt_rejected(self):
        changed=copy.deepcopy(config);changed['bound_dependencies']=[changed['bound_dependencies'][1]]
        with self.assertRaisesRegex(ValueError,'Unbound or changed'):resolve_stage_b_options(changed,config_path,None)
    def test_changed_current_source_binding_rejected(self):
        changed=copy.deepcopy(config);changed['source_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'receipt source/master differs'):resolve_stage_b_options(changed,config_path,None)
    def test_saved_matching_options_remain_compatible_and_conflicts_reject(self):
        saved={'config':str(receipt),'image':str(master),'strength':1}
        self.assertEqual(resolve_stage_b_options(config,config_path,saved)['image'],str(master))
        with self.assertRaisesRegex(ValueError,'associations conflict'):resolve_stage_b_options(config,config_path,dict(saved,strength=.5))

stream=io.StringIO();r=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ActualPackagePreflightTests))
(a.output/'unittest.log').write_text(stream.getvalue())
unchanged=sha(source)==source_before and preview_identity(preview)==preview_before and {k:world[k] for k in world.keys()}==world_before and saved_options(world) is None
result={'status':'passed' if r.wasSuccessful() and unchanged else 'failed','tests':r.testsRun,'failures':[{'test':str(t),'traceback':v} for t,v in r.failures],'errors':[{'test':str(t),'traceback':v} for t,v in r.errors],'source_unchanged':unchanged,'source':str(source),'source_sha256':sha(source),'addon':babylon_js.__file__,'version':list(babylon_js.bl_info['version']),'source_saved_by_checks':False,'fixture_save_only':True,'rendered':False,'encoded':False,'limits':'Factory synthetic fixture only, not volume capture or quality acceptance.'}
write(a.output/'result.json',result);print(json.dumps(result,indent=2));raise SystemExit(0 if result['status']=='passed' else 1)
