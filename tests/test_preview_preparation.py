"""Run with factory-startup Blender: --python this_file -- OUTPUT_DIRECTORY."""
import json,sys
from pathlib import Path
import bpy,numpy as np,OpenImageIO as oiio
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'src/babylon_js/environment_tools')]
from babylon_js.lighting_controls import create_preview_material
from babylon_js.lighting_profile import scene_lighting_profile
from appearance import preview_identity,verify_combination
from image_binding import replace_from_disk
from common import sha,save_json
import worker

out=Path(sys.argv[sys.argv.index('--')+1]);out.mkdir(parents=True,exist_ok=False)
def image(name,rgb,colour_space='Linear Rec.709'):
    a=np.ones((32,32,4),np.float32);a[:,:,:3]=rgb
    path=out/(name+'.exr');worker.write_exr(path,a)
    im=bpy.data.images.load(str(path),check_existing=False);im.colorspace_settings.name=colour_space
    return im
colour=image('colour',1.,'sRGB');direct=image('direct',2.);indirect=image('indirect',3.);ids=image('ids',1.,'Non-Color')
mat,node=create_preview_material(colour,direct,indirect,ids)
assert scene_lighting_profile(bpy.context)['shadowSuppression']==2
try:create_preview_material(colour,direct,indirect,ids);raise AssertionError('overwrote preview')
except ValueError:pass
identity=preview_identity(mat)
save_json(out/'identity-before.json',preview_identity(mat,diagnostic=True))
node.inputs['Shadow Suppression'].default_value=4
node.location=(50,30)
save_json(out/'identity-after.json',preview_identity(mat,diagnostic=True))
assert preview_identity(mat)==identity
node.inputs['Indirect Strength'].default_value=2
assert preview_identity(mat)!=identity
node.inputs['Indirect Strength'].default_value=1
assert preview_identity(mat)==identity
save_json(out/'combined.provenance.json',{'schema_version':1,'preview_sha256':identity,'combined_sha256':sha(direct.filepath)})
verify_combination(mat,direct.filepath,out/'combined.provenance.json')
node.inputs['Shadow Lift'].default_value=.1
try:verify_combination(mat,direct.filepath,out/'combined.provenance.json');raise AssertionError('accepted stale map')
except ValueError as e:assert 'recombine' in str(e)
node.inputs['Shadow Lift'].default_value=0

# Replacing a packed image must update all nested samplers without stale pixels.
indirect.pack()
replacement=image('replacement',7.)
fresh=replace_from_disk(indirect,replacement.filepath)
assert not fresh.packed_file and abs(fresh.pixels[0]-7)<1e-6
samplers=[n for n in node.node_tree.nodes if n.type=='TEX_IMAGE' and n.image==fresh]
assert len(samplers)==25,len(samplers)
indirect=replace_from_disk(fresh,out/'indirect.exr')

config={'size':32,'device':'CPU','preview_material':mat.name,'_root':str(out),
        'images':{'colour':'colour.exr'},'uv':'SimpleBake'}
def evaluate(tag):
    stage=out/tag;stage.mkdir();worker.flatten(config,out,stage)
    return np.load(stage/'evaluated.npy')[:,:,:3]
base=evaluate('base');assert np.max(abs(base-5))<2e-5
node.inputs['Direct Strength'].default_value=.5
node.inputs['Indirect Strength'].default_value=2
node.inputs['Shadow Lift'].default_value=.25
adjusted=evaluate('adjusted');assert np.max(abs(adjusted-6))<2e-5

# Foreign bright islands cannot brighten a dark neighbour; within-island smoothing works.
node.inputs['Direct Strength'].default_value=0;node.inputs['Indirect Strength'].default_value=1;node.inputs['Shadow Lift'].default_value=0
a=np.ones((32,32,4),np.float32);a[:,:16,:3]=1;a[:,16:,:3]=100
worker.write_exr(out/'two-islands.exr',a);indirect=replace_from_disk(indirect,out/'two-islands.exr')
a[:,:16,:3]=1;a[:,16:,:3]=2
worker.write_exr(out/'two-ids.exr',a);ids=replace_from_disk(ids,out/'two-ids.exr')
node.inputs['Adaptive Smoothing'].default_value=1;node.inputs['Patch Size'].default_value=3;node.inputs['Dark Difference'].default_value=0
separated=evaluate('separated');assert np.max(abs(separated[:,:15]-1))<2e-4
a=np.ones((32,32,4),np.float32);a[:,:,:3]=3;a[16,8,:3]=0
worker.write_exr(out/'spot.exr',a);indirect=replace_from_disk(indirect,out/'spot.exr')
smooth=evaluate('smooth');node.inputs['Adaptive Smoothing'].default_value=0
raw=evaluate('raw');assert smooth[16,8,0]>raw[16,8,0]+.1
node.inputs['Adaptive Smoothing'].default_value=1;node.inputs['Patch Size'].default_value=0
assert np.max(abs(evaluate('radius-zero')-raw))<2e-5
saved_identity=preview_identity(mat)
saved_name=mat.name
bpy.ops.wm.save_as_mainfile(filepath=str(out/'fixture.blend'))
bpy.ops.wm.open_mainfile(filepath=str(out/'fixture.blend'))
assert preview_identity(bpy.data.materials[saved_name])==saved_identity
save_json(out/'result.json',{'status':'passed','packed_samplers_rebound':25,'runtime_metadata_does_not_invalidate':True,
    'changed_preview_rejected':True,'affine_math_max_error':float(np.max(abs(adjusted-6))),
    'island_boundary_max_error':float(np.max(abs(separated[:,:15]-1))),
    'dark_spot_before':float(raw[16,8,0]),'dark_spot_after':float(smooth[16,8,0])})
print('PREVIEW_PREPARATION_PASSED')
