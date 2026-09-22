"""Native Blender regression: --python TEST -- NEW_OUTPUT_DIRECTORY."""
import sys,json
from pathlib import Path
import bpy,numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'src/babylon_js/environment_tools')]
from babylon_js.lighting_controls import create_preview_material,validate_preview_resolution,update_preview_resolution
from image_binding import replace_from_disk
import worker

out=Path(sys.argv[sys.argv.index('--')+1]);out.mkdir(parents=True,exist_ok=False)
def make_image(name,n,value,space='Linear Rec.709'):
    pixels=np.ones((n,n,4),np.float32);pixels[:,:,:3]=value
    if name.startswith('indirect'):
        pixels[n//2,n//4,:3]=0;pixels[:,n//2:,:3]=100
    if name.startswith('ids'):pixels[:,n//2:,:3]=2
    path=out/(name+'.exr');worker.write_exr(path,pixels)
    im=bpy.data.images.load(str(path),check_existing=False);im.colorspace_settings.name=space
    return im
def rejected(fn,phrase):
    try:fn()
    except ValueError as e:
        assert phrase in str(e),(phrase,str(e));return
    raise AssertionError('Expected rejection: '+phrase)

colour=make_image('colour',32,1,'sRGB');direct=make_image('direct32',32,2)
indirect=make_image('indirect32',32,3);ids=make_image('ids32',32,1,'Non-Color')
mat,control=create_preview_material(colour,direct,indirect,ids)
control.inputs['Adaptive Smoothing'].default_value=1
control.inputs['Dark Difference'].default_value=0
control.inputs['Shadow Suppression'].default_value=3
values={s.name:s.default_value for s in control.inputs}
assert validate_preview_resolution(mat,(32,32))['status']=='verified'
rejected(lambda:validate_preview_resolution(mat,(64,64)),'configured bake size')
offsets=[n for n in control.node_tree.nodes if 'bjs_pixel_offset' in n]
offsets[0].inputs[0].default_value[0]+=.001
rejected(lambda:validate_preview_resolution(mat),'Stale smoothing')
update_preview_resolution(mat)
large_d=make_image('direct64',64,2);large_i=make_image('indirect64',64,3);large_ids=make_image('ids64',64,1,'Non-Color')
direct=replace_from_disk(direct,large_d.filepath)
before=[tuple(n.inputs[0].default_value) for n in offsets]
rejected(lambda:update_preview_resolution(mat),'dimensions must match')
assert before==[tuple(n.inputs[0].default_value) for n in offsets]
indirect=replace_from_disk(indirect,large_i.filepath);ids=replace_from_disk(ids,large_ids.filepath)
rejected(lambda:validate_preview_resolution(mat,(64,64)),'Stale smoothing')
config={'size':64,'device':'CPU','preview_material':mat.name,'_root':str(out),'images':{'colour':'colour.exr'},'uv':'SimpleBake'}
rejected(lambda:worker.check_preview_resolution(config),'Stale smoothing')
assert update_preview_resolution(mat)['status']=='verified'
assert values=={s.name:s.default_value for s in control.inputs}
assert worker.check_preview_resolution(config)['status']=='verified'
def evaluate(name):
    stage=out/name;stage.mkdir();worker.flatten(config,out,stage)
    return np.load(stage/'evaluated.npy')
updated=evaluate('updated')
del bpy.context.scene['bjs_lighting_controls_material']
fresh,fresh_control=create_preview_material(colour,direct,indirect,ids,name='Fresh64')
for name,value in values.items():fresh_control.inputs[name].default_value=value
config['preview_material']=fresh.name
expected=evaluate('fresh')
error=float(np.max(abs(updated-expected)));assert error<2e-5,error
# Saving must preserve tags, offsets and validation, including the colour atlas
# remaining at 32 while lighting uses 64 (colour resolution is independent).
path=out/'fixture.blend';bpy.ops.wm.save_as_mainfile(filepath=str(path));bpy.ops.wm.open_mainfile(filepath=str(path))
assert validate_preview_resolution(bpy.data.materials['Fresh64'],(64,64))['status']=='verified'
legacy=bpy.data.materials.new('Custom');legacy.use_nodes=True
assert validate_preview_resolution(legacy)['status']=='unverified_custom_graph'
rejected(lambda:update_preview_resolution(legacy),'Custom graph')
(out/'result.json').write_text(json.dumps({'status':'passed','native_updated_vs_fresh_max_error':error,'source_resize':[32,64],
    'stale_offsets_rejected':True,'mixed_dimensions_atomic_rejection':True,'saved_controls_preserved':True,'custom_graph_reported_unverified':True},indent=2))
print('PREVIEW_RESOLUTION_PASSED')
