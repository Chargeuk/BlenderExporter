"""Factory Blender integration: colour coverage, scene restoration and real KTX export."""
import bpy,sys,json,numpy as np
from pathlib import Path
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src'))
import babylon_js
from babylon_js import agx_lightmap as agx
from babylon_js.lighting_controls import create_preview_material,upgrade_runtime_controls
from babylon_js.lighting_profile import scene_lighting_profile,prefill_lighting_options
from babylon_js.json_exporter import JsonExporter
babylon_js.register()
out=Path(sys.argv[sys.argv.index('--')+1]);out.mkdir(parents=True,exist_ok=False)
s=bpy.context.scene;cube=s.objects['Cube'];s.view_settings.view_transform='AgX';s.view_settings.look='None'
s.world.inlineTextures=False;s.world.usePBRMaterials=True
mat=bpy.data.materials.new('Physical');mat.use_nodes=True
p=next(n for n in mat.node_tree.nodes if n.type=='BSDF_PRINCIPLED');p.inputs['Base Color'].default_value=(.4,.2,.1,1)
cube.data.materials.clear();cube.data.materials.append(mat)
uv=cube.data.uv_layers.new(name='SimpleBake')
for src,dst in zip(cube.data.uv_layers[0].data,uv.data):dst.uv=src.uv
marker=bpy.data.objects.new('lightmap_fixture',None);s.collection.objects.link(marker);cube.parent=marker
original_selection=[o.name for o in bpy.context.selected_objects];original_active=bpy.context.object
counts={key:len(getattr(bpy.data,key)) for key in ['objects','meshes','materials','node_groups','images','scenes']}
a=agx.bake_albedo(bpy.context,[cube],out/'albedo.exr',64)
assert bpy.context.scene==s and bpy.context.object==original_active
assert original_selection==[o.name for o in bpy.context.selected_objects]
assert counts=={key:len(getattr(bpy.data,key)) for key in counts}
valid=a[...,3]>.5;assert valid.any() and (~valid).any(),np.unique(a[...,3])
assert np.max(abs(a[valid,:3]-[.4,.2,.1]))<1e-5
# UV-only instanced receivers use the joined fast path without touching shared source data.
twin=cube.copy();s.collection.objects.link(twin);twin.location.x+=3
shared=cube.data
two=agx.bake_albedo(bpy.context,[cube,twin],out/'albedo_instances.exr',64)
assert cube.data==shared and twin.data==shared
assert np.max(abs(two[valid,:3]-[.4,.2,.1]))<1e-5
bpy.data.objects.remove(twin,do_unlink=True)
# Failure must restore the user's scene and remove temporary datablocks too.
extra=mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
counts={key:len(getattr(bpy.data,key)) for key in ['objects','meshes','materials','node_groups','images','scenes']}
try:agx.bake_albedo(bpy.context,[cube],out/'invalid.exr',64);raise AssertionError('ambiguous material accepted')
except ValueError:pass
assert bpy.context.scene==s and counts=={key:len(getattr(bpy.data,key)) for key in counts}
mat.node_tree.nodes.remove(extra)
def image(name,value,space='Non-Color'):
 ar=np.ones((64,64,4),np.float32);ar[...,:3]=value;path=out/(name+'.exr');agx.write_exr(path,ar)
 im=bpy.data.images.load(str(path));im.colorspace_settings.name=space;return im
colour=image('colour',1,'sRGB');direct=image('direct',2);indirect=image('indirect',3);ids=image('ids',1)
preview,node=create_preview_material(colour,direct,indirect,ids)
node.inputs['Baked Diffuse Preservation'].default_value=.15
node.inputs['Shadow Suppression'].default_value=2;node.inputs['Fully Lit Threshold'].default_value=1.4
assert abs(scene_lighting_profile(bpy.context)['bakedDiffusePreservation']-.15)<1e-6
class Op:pass
op=Op();prefill_lighting_options(op,bpy.context);assert abs(op.lighting_baked_diffuse_preservation-.15)<1e-6
# Legacy upgrade must preserve existing controls and not create a shader connection.
interface=next(x for x in node.node_tree.interface.items_tree if x.name=='Baked Diffuse Preservation')
node.node_tree.interface.remove(interface);assert upgrade_runtime_controls(preview)==1
assert node.inputs['Shadow Suppression'].default_value==2
assert abs(node.inputs['Fully Lit Threshold'].default_value-1.4)<1e-6
assert upgrade_runtime_controls(preview)==0
node.inputs['Baked Diffuse Preservation'].default_value=.15
combined=agx.combine_saved_controls(bpy.context,64,out/'combined.exr');assert np.max(abs(combined[...,:3]-5))<1e-5
node.inputs['Direct Strength'].default_value=2
combined=agx.combine_saved_controls(bpy.context,64,out/'changed.exr');assert np.max(abs(combined[...,:3]-7))<1e-5
marker['bjs_lightmap_image']=str(out/'combined.exr')
base_hash=agx.sha(out/'combined.exr')
dest,report=agx.export_compensated(bpy.context,[cube,marker],marker.name,out/'combined.exr',out/'agx')
assert report['source_controls_recombined'] and agx.sha(out/'combined.exr')==base_hash
assert np.isfinite(agx.read_exr(dest)).all()
try:agx.export_compensated(bpy.context,[cube,marker],marker.name,dest,out/'twice');raise AssertionError('double transform accepted')
except ValueError as e:assert 'Already' in str(e)
# Changed albedo must be captured anew, not silently reuse previous data.
p.inputs['Base Color'].default_value=(.2,.1,.05,1)
b=agx.bake_albedo(bpy.context,[cube],out/'albedo_changed.exr',64)
assert np.max(abs(b[valid,:3]-[.2,.1,.05]))<1e-5
options=dict(executable='H:/code/texture-tools/KTX-Software-4.4.2/bin/ktx.exe',auto_lightmap=True,lightmap_encoding='rgbd-v1',lightmap_agx=True,threads=2)
e=JsonExporter();e.execute(bpy.context,str(out/'fixture.babylon'),[cube,marker],ktx_options=options,lighting_options=scene_lighting_profile(bpy.context))
assert not e.fatalError,e.fatalError
d=json.loads((out/'fixture.babylon').read_text());m=next(x for x in d['meshes'] if x['name'].startswith('lightmap_'))
assert '_agx_rgbd' in m['name'];assert (out/(m['name'][9:]+'.ktx2')).is_file()
assert abs(m['metadata']['kadshowLighting']['bakedDiffusePreservation']-.15)<1e-6
assert bpy.ops.export.bjs.get_rna_type().properties['ktx_lightmap_agx'].default is True
assert cube.data.materials[0]==mat and bpy.context.scene==s
print('AGX_EXPORT_TEST_PASSED',json.dumps(report))

# The physical bake entry point must automatically produce the auxiliary albedo.
sys.path.insert(0,str(R/'src/babylon_js/environment_tools'))
import worker
from unittest.mock import patch
job=out/'bake-job';(job/'masters').mkdir(parents=True);stage=job/'bake';stage.mkdir()
config={'size':32,'uv':'SimpleBake','device':'CPU','quality':'pilot','samples':{'DIRECT':1,'INDIRECT':1},
        'receivers':{'names':[cube.name]},'contributors':{},'bake_lights':{},'metal_receivers':{}}
# Preflight has its own tests; exercise the actual native bake and automatic colour step here.
with patch.object(worker,'preflight',return_value={}):
    worker.bake(config,job,stage)
assert (job/'masters/albedo_uv.exr').is_file()
assert (job/'masters/albedo_uv.exr.json').is_file()
assert (stage/'direct_raw.npy').is_file() and (stage/'indirect_raw.npy').is_file()
print('AUTOMATIC_BAKE_ALBEDO_PASSED')
