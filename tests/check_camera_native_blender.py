"""Disposable real Blender fixture for the opt-in native camera helper.

Run background Blender with --python this_file -- --helper <camera_views.py>
--output <fresh fixture directory>. Does not open or alter user authoring scenes.
"""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import struct
import sys
import bpy
from mathutils import Matrix

parser=argparse.ArgumentParser()
parser.add_argument('--helper',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
helper=args.helper.resolve(); root=args.output.resolve()
if root.exists(): raise ValueError('Fixture output must be fresh')
root.mkdir(parents=True)
sys.path.insert(0,str(helper.parent))
sys.path.insert(0,str(helper.parent.parent.parent))
spec=importlib.util.spec_from_file_location('fixture_camera_views',helper)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
from common import sha,save_json

bpy.ops.wm.read_factory_settings(use_empty=True)
scene=bpy.context.scene
scene.world=bpy.data.worlds.new('Fixture world')
bpy.ops.mesh.primitive_cube_add()
cube=bpy.context.object;cube.name='SelectedFixture'
cube.scale=(1,.7,1.2)
uv=cube.data.uv_layers.new(name='FixtureUV')
for loop in uv.data: loop.uv=(.25,.25)
bpy.ops.mesh.primitive_cube_add(location=(3,0,0))
excluded=bpy.context.object;excluded.name='ExcludedFixture'
excluded.hide_render=True;excluded.hide_set(True)
camera_data=bpy.data.cameras.new('Saved orthographic data')
camera_data.type='ORTHO';camera_data.ortho_scale=4;camera_data.shift_x=.1
camera=bpy.data.objects.new('SavedFixtureCamera',camera_data);scene.collection.objects.link(camera)
camera.matrix_world=Matrix.Translation((0,0,6));scene.camera=camera
scene.render.engine='CYCLES'
scene.render.resolution_x=321;scene.render.resolution_y=123
scene.render.resolution_percentage=70
scene.render.filepath='//fixture-original-output'
scene.frame_set(1)
source=root/'fixture.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(source))
bpy.ops.wm.open_mainfile(filepath=str(source))
scene=bpy.context.scene
c={'scene':scene.name,'_source':str(source),'receivers':{'names':['SelectedFixture']},
   'uv':'FixtureUV','size':64}
recipe={'source':str(source),'source_sha256':sha(source),'native_engine':'BLENDER_WORKBENCH',
 'normal_guard':True,'render_aa':'5','frame':1,'dimensions':[96,64],
 'display':{'view_transform':'Standard','look':'None','exposure':0,'gamma':1},
 'presets':{'plain':{'light':'STUDIO','studio_light':'paint.sl','color_type':'SINGLE',
   'single_color':[.65,.65,.65],'show_shadows':False,'show_cavity':True,'cavity_type':'BOTH',
   'background_type':'WORLD','background_color':[.05,.05,.05]}},
 'jobs':[{'id':'saved_camera','camera':'SavedFixtureCamera','objects':['SelectedFixture'],
   'shading':'plain','backface_culling':False},
  {'id':'independent_matrix','matrix_world':[[1,0,0,0],[0,1,0,0],[0,0,1,6],[0,0,0,1]],
   'lens':35,'objects':['SelectedFixture'],'shading':'plain','backface_culling':True}]}
recipe_path=root/'recipe.json';save_json(recipe_path,recipe)
passed=[]

def state():
    s=bpy.context.scene
    return {'engine':s.render.engine,'camera':s.camera.name,'frame':s.frame_current,
      'dimensions':[s.render.resolution_x,s.render.resolution_y,s.render.resolution_percentage],
      'filepath':s.render.filepath,'aa':s.display.render_aa,
      'visibility':[(o.name,o.hide_render,o.hide_get()) for o in s.objects if o.type=='MESH'],
      'objects':sorted(o.name for o in s.objects),
      'meshes':sorted(m.name for m in bpy.data.meshes),
      'cameras':sorted(m.name for m in bpy.data.cameras),
      'selected':sorted(o.name for o in bpy.context.selected_objects),
      'active':bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else None,
      'world':list(s.world.color),'studio':s.display.shading.studio_light,
      'culling':s.display.shading.show_backface_culling,
      'display':{k:getattr(s.view_settings,k) for k in ('view_transform','look','exposure','gamma','use_curve_mapping')}}

before=state();source_hash=sha(source)
result=module.capture_native_diagnostics(c,recipe_path,root/'positive')
assert state()==before, (state(),before)
assert sha(source)==source_hash and result['source_saved'] is False
assert result['job_count']==2 and result['normal_guard']['checked']
assert result['normal_guard']['max_normal_error']<=.001
assert result['normal_guard']['receiver_count']==1
assert [r['backface_culling'] for r in result['receipts']]==[False,True]
assert [r['camera_properties']['type'] for r in result['receipts']]==['ORTHO','PERSP']
assert result['receipts'][1]['camera_properties']['shift_x']==0
for record in result['receipts']:
    png=Path(record['png']);raw=png.read_bytes()
    assert raw[:8]==b'\x89PNG\r\n\x1a\n'
    assert struct.unpack('>II',raw[16:24])==(96,64)
    assert sha(png)==record['png_sha256']
    assert record['visible_mesh_names']==['SelectedFixture']
    assert record['engine']=='BLENDER_WORKBENCH'
    assert record['display_device']=='sRGB' and record['use_curve_mapping'] is False
passed.extend(['two real PNGs and hashes','independent cameras','exact selection and culling',
               'unchanged installed receiver normal guard','state restored','source file unchanged'])

def reject(label,changed,output):
    bpy.ops.wm.open_mainfile(filepath=str(source))
    path=root/(label+'.json');save_json(path,changed)
    try: module.capture_native_diagnostics(c,path,output)
    except (ValueError,RuntimeError): passed.append(label)
    else: raise AssertionError('Invalid native capture accepted: '+label)
    assert sha(source)==source_hash

bad=copy.deepcopy(recipe);bad['source_sha256']='0'*64
reject('stale source',bad,root/'bad-source')
assert not (root/'bad-source').exists()
bad=copy.deepcopy(recipe);bad['jobs'][0]['objects']=['ExcludedFixture']
reject('unconfigured geometry',bad,root/'bad-selection')
assert not (root/'bad-selection').exists()
reject('existing output',recipe,root/'positive')
bad=copy.deepcopy(recipe);bad['presets']['plain']['studio_light']='not-installed.sl'
reject('missing studio light',bad,root/'bad-light')
assert not (root/'bad-light').exists()
bad=copy.deepcopy(recipe);bad['jobs'][0]['camera']='MissingCamera'
reject('missing saved camera',bad,root/'bad-camera')
assert not (root/'bad-camera').exists()
bpy.ops.wm.open_mainfile(filepath=str(source))
scene=bpy.context.scene
# Fail after visibility/render state changes, then prove finally restores them.
hidden=bpy.data.collections.new('ExcludedCollection');scene.collection.children.link(hidden)
obj=scene.objects['SelectedFixture']
for collection in list(obj.users_collection): collection.objects.unlink(obj)
hidden.objects.link(obj);hidden.hide_viewport=True
bpy.ops.wm.save_as_mainfile(filepath=str(root/'hidden-fixture.blend'))
bpy.ops.wm.open_mainfile(filepath=str(root/'hidden-fixture.blend'))
hidden_c=dict(c,_source=str(root/'hidden-fixture.blend'))
hidden_recipe=copy.deepcopy(recipe);hidden_recipe.update(source=hidden_c['_source'],source_sha256=sha(hidden_c['_source']),normal_guard=False)
save_json(root/'hidden-recipe.json',hidden_recipe)
before=state()
try:module.capture_native_diagnostics(hidden_c,root/'hidden-recipe.json',root/'hidden-output')
except ValueError as error: assert 'excluded/hidden' in str(error), str(error)
else:raise AssertionError('View-layer exclusion was ignored')
assert state()==before
passed.append('mid-capture rejection restores state')
save_json(root/'fixture-result.json',{'status':'passed','checks':passed,'count':len(passed),
 'helper_sha256':sha(helper),'original_fixture_source_sha256':source_hash})
print('NATIVE_CAMERA_FIXTURE_PASSED '+json.dumps({'count':len(passed),'checks':passed}),flush=True)
