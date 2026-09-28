"""Native Blender regression: run factory startup, --python this -- output_dir."""
import sys,json
from pathlib import Path
import bpy,numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'src/babylon_js/environment_tools')]
from babylon_js.lighting_controls import create_preview_material
from babylon_js.lightmap_grading import upgrade
from babylon_js.agx_lightmap import combine_saved_controls,write_exr
from appearance import preview_identity
out=Path(sys.argv[sys.argv.index('--')+1]);out.mkdir(parents=True,exist_ok=False)
def image(name,rgb):
    a=np.ones((32,32,4),np.float32);a[:,:,:3]=rgb
    path=out/(name+'.exr');write_exr(path,a)
    im=bpy.data.images.load(str(path));im.colorspace_settings.name='sRGB' if name=='colour' else 'Non-Color'
    return im
mat,node=create_preview_material(image('colour',1),image('direct',[.2,2,64]),image('indirect',0),image('ids',1))
node.inputs['Shadow Lift'].default_value=-.02
def evaluate(name):return combine_saved_controls(bpy.context,32,out/(name+'.exr'))
base=evaluate('baseline'); expected=np.array([.18,1.98,63.98])
assert np.max(abs(base[:,:,:3]-expected))<1e-5
counts=(len(node.node_tree.nodes),len(bpy.data.node_groups));upgrade(mat)
assert counts==(len(node.node_tree.nodes),len(bpy.data.node_groups))
grade=next(n for n in node.node_tree.nodes if n.get('bjs_grade_role')=='final')
grade.inputs['Adjustment Strength'].default_value=1
grade.inputs['Contrast'].default_value=1.2
actual=evaluate('contrast');y=base[:,:,:3]@np.array([.2126,.7152,.0722])
expected=base[:,:,:3]*(y**.2)[:,:,None]
assert np.max(abs(actual[:,:,:3]-expected))<.0001
grade.inputs['Bypass'].default_value=1
assert np.max(abs(evaluate('bypass')-base))<1e-6
grade.inputs['Bypass'].default_value=0;grade.inputs['Contrast'].default_value=1
grade.inputs['Exposure'].default_value=1
grade.inputs['Tint'].default_value=(.5,1,1,1)
assert np.max(abs(evaluate('exposure_tint')[:,:,:3]-base[:,:,:3]*[1,2,2]))<1e-4
grade.inputs['Exposure'].default_value=0;grade.inputs['Tint'].default_value=(1,1,1,1)
grade.inputs['Saturation'].default_value=0
grey=base[:,:,:3]@np.array([.2126,.7152,.0722])
assert np.max(abs(evaluate('saturation')[:,:,:3]-grey[:,:,None]))<1e-4
grade.inputs['Saturation'].default_value=1
grade.inputs['Black Level'].default_value=.1;grade.inputs['White Level'].default_value=2
assert np.max(abs(evaluate('levels')[:,:,:3]-np.maximum((base[:,:,:3]-.1)/1.9,0)))<1e-4
grade.inputs['Black Level'].default_value=0;grade.inputs['White Level'].default_value=1
grade.inputs['Gamma'].default_value=2
assert np.isfinite(evaluate('gamma')).all()
grade.inputs['Adjustment Strength'].default_value=0
upgrade(mat,image('ao_source',.5));node.inputs['AO Strength'].default_value=1
node.inputs['AO Direct Influence'].default_value=1
assert np.max(abs(evaluate('ao')[:,:,:3]-base[:,:,:3]*.5))<1e-4
before=preview_identity(mat)
native=next(n for n in grade.node_tree.nodes if n.type=='BRIGHTCONTRAST')
native.inputs['Contrast'].default_value=.2
assert preview_identity(mat)!=before
native.inputs['Contrast'].default_value=0
assert preview_identity(mat)==before
ramper=grade.node_tree.nodes.new('ShaderNodeValToRGB')
before_ramp=preview_identity(mat);ramper.color_ramp.elements[0].position=.1
assert before_ramp!=preview_identity(mat),'Ramp-only edit not tracked'
grade.node_tree.nodes.remove(ramper)
curve=next(n for n in grade.node_tree.nodes if n.type=='CURVE_RGB')
curve.mapping.curves[3].points.new(.5,.4);curve.mapping.update()
assert before!=preview_identity(mat),'Curve-only edit not tracked'
grade.inputs['Curves Strength'].default_value=1;grade.inputs['Adjustment Strength'].default_value=.3
curved=evaluate('curved');assert np.isfinite(curved).all()
before=preview_identity(mat);node.inputs['Shadow Suppression'].default_value=3
assert before==preview_identity(mat),'Runtime-only metadata changed appearance hash'
path=out/'fixture.blend';bpy.ops.wm.save_as_mainfile(filepath=str(path));bpy.ops.wm.open_mainfile(filepath=str(path))
mat=bpy.data.materials['KaDshow Split Lighting PREVIEW']
assert preview_identity(mat)==before
assert np.max(abs(evaluate('reopened')-curved))<1e-5
import worker
stage=out/'shared-combination';stage.mkdir()
worker.flatten({'size':32,'preview_material':mat.name,'device':'OPTIX'},out,stage)
assert np.array_equal(np.load(stage/'evaluated.npy'),curved),'Preparation differs from export evaluation'
(out/'result.json').write_text(json.dumps({'status':'passed','negative_lift_preserved':True,'HDR_preserved':True,
    'idempotent':True,'curve_only_edit_tracked':True,'runtime_values_excluded':True,'save_reopen_equal':True},indent=2))
print('GRADING_TEST_PASSED')
