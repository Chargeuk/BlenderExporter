"""Disposable background fixture; never opens or edits a commissioned source."""
import bpy,json,hashlib,sys,argparse
from pathlib import Path
from mathutils import Vector
parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
out=Path(args.output).resolve();assert not out.exists();out.mkdir(parents=True)
import babylon_js
tools=Path(babylon_js.__file__).parent/'environment_tools';sys.path.insert(0,str(tools))
from solid_views import capture
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add();box=bpy.context.object;box.name='Fixture box'
bpy.ops.mesh.primitive_plane_add(size=4,location=(0,0,-1));floor=bpy.context.object;floor.name='Fixture floor'
other=bpy.data.collections.new('Excluded fixture helper');bpy.context.scene.collection.children.link(other)
mesh=box.data.copy();helper=bpy.data.objects.new('Not in explicit delivery',mesh);other.objects.link(helper)
helper.location=(50,0,0)
next(x for x in bpy.context.view_layer.layer_collection.children if x.collection==other).exclude=True
source=out/'fixture.blend';bpy.ops.wm.save_as_mainfile(filepath=str(source));source_hash=sha(source)
def camera(pos):
    p=Vector(pos);m=(-p).to_track_quat('-Z','Y').to_matrix().to_4x4();m.translation=p;return [list(r) for r in m]
config=dict(schema_version=1,source_filename=source.name,source_sha256=source_hash,scene=bpy.context.scene.name,
 delivery_names=['Fixture box','Fixture floor'],expected_delivery_meshes=2,expected_delivery_triangles=14,dimensions=[320,240],
 views=[dict(id='all_plusX',matrix_world=camera((6,2,2)),lens=40,camera_properties={'type':'ORTHO','ortho_scale':6},full_delivery=True,backface_culling=False),
        dict(id='omitted_cap',matrix_world=camera((3,2,4)),lens=40,camera_properties={'type':'ORTHO','ortho_scale':6},omit_faces={'Fixture box':[0]},backface_culling=True)])
recipe=out/'recipe.json';recipe.write_text(json.dumps(config),encoding='utf-8')
checks=[]
def scene_state():
    scene=bpy.context.scene
    return dict(dirty=bpy.data.is_dirty,objects=sorted(bpy.data.objects.keys()),meshes=sorted(bpy.data.meshes.keys()),
      materials=sorted(bpy.data.materials.keys()),worlds=sorted(bpy.data.worlds.keys()),engine=scene.render.engine,
      world=scene.world.name if scene.world else None,camera=scene.camera.name if scene.camera else None,
      object_state=[(o.name,o.hide_render,[list(row) for row in o.matrix_world]) for o in scene.objects])
# A valid imported override must not claim the hash of a different saved recipe.
mismatch=json.loads(recipe.read_text());mismatch['views'][0]['backface_culling']=True
before=scene_state();recipe_hash=sha(recipe);mismatch_output=out/'config-mismatch-unexpected'
try:capture(mismatch,mismatch_output,recipe)
except ValueError as exc:
    assert 'Effective solid diagnostic config differs' in str(exc)
    assert not mismatch_output.exists() and scene_state()==before and sha(recipe)==recipe_hash and sha(source)==source_hash
    checks.append({'case':'effective_config_binding','rejected':True,'scene_unchanged':True,'output_created':False})
else:raise AssertionError('Different effective configuration claimed the saved recipe hash')
# JSON number 1 in a file must not bind to the validated boolean True in memory.
typed=json.loads(json.dumps(mismatch));typed['views'][0]['backface_culling']=1
typed_recipe=out/'typed-mismatch-recipe.json';typed_recipe.write_text(json.dumps(typed),encoding='utf-8')
before=scene_state();typed_hash=sha(typed_recipe);typed_output=out/'config-type-mismatch-unexpected'
try:capture(mismatch,typed_output,typed_recipe)
except ValueError as exc:
    assert 'Effective solid diagnostic config differs' in str(exc)
    assert not typed_output.exists() and scene_state()==before and sha(typed_recipe)==typed_hash and sha(source)==source_hash
    checks.append({'case':'effective_config_type_binding','rejected':True,'scene_unchanged':True,'output_created':False})
else:raise AssertionError('Different JSON types claimed the saved recipe hash')
def reject(label,mutate,message):
    bpy.ops.wm.open_mainfile(filepath=str(source));c=json.loads(recipe.read_text());mutate(c)
    case_recipe=out/(label+'-recipe.json');case_recipe.write_text(json.dumps(c),encoding='utf-8')
    try:capture(c,out/(label+'-unexpected'),case_recipe)
    except (ValueError,RuntimeError) as exc:
        assert message in str(exc),(label,str(exc));checks.append({'case':label,'rejected':True,'message':str(exc)})
    else:raise AssertionError(label+' was not rejected')
reject('unknown',lambda c:c.update(delivery_names=['Fixture box','missing']),'Unknown/nonmesh')
reject('excluded',lambda c:c.update(delivery_names=['Fixture box','Not in explicit delivery']),'excluded')
reject('out_of_range',lambda c:c['views'][1].update(omit_faces={'Fixture box':[100]}),'out of range')
reject('wrong_count',lambda c:c.update(expected_delivery_triangles=100),'count differs')
bpy.ops.wm.open_mainfile(filepath=str(source))
result=capture(config,out/'capture',recipe)
assert result['source_saved'] is False and sha(source)==source_hash
assert result['delivery_triangles']==14 and len(result['delivery_membership'])==2
assert result['views'][0]['displayed_triangles']==14 and result['views'][0]['backface_culling'] is False
assert result['views'][1]['displayed_triangles']==12 and result['views'][1]['omitted_faces'][0]['evaluated_polygon_ids']==[0]
assert result['views'][1]['omitted_faces'][0]['triangle_count']==2
assert all('Not in explicit delivery' not in [r['source_object'] for r in v['displayed']] for v in result['views'])
bpy.ops.wm.open_mainfile(filepath=str(source))
try:capture(config,out/'capture',recipe)
except ValueError as exc:assert 'Fresh output' in str(exc);checks.append({'case':'fresh_output','rejected':True})
else:raise AssertionError('Existing proof overwritten')
# Source-hash identity is enforced before output creation.
wrong=dict(config);wrong['source_sha256']='0'*64
wrong_recipe=out/'wrong-source-hash.json';wrong_recipe.write_text(json.dumps(wrong),encoding='utf-8')
try:capture(wrong,out/'bad-hash',wrong_recipe)
except ValueError as exc:assert 'hash differs' in str(exc);checks.append({'case':'source_hash','rejected':True})
else:raise AssertionError('Wrong source accepted')
# A vertex-instancing mesh cannot silently contribute only its base triangles.
box=bpy.data.objects['Fixture box'];floor=bpy.data.objects['Fixture floor'];floor.parent=box;box.instance_type='VERTS'
instanced=out/'instanced.blend';bpy.ops.wm.save_as_mainfile(filepath=str(instanced));bpy.context.view_layer.update()
ic=dict(config);ic.update(source_filename=instanced.name,source_sha256=sha(instanced))
ip=out/'instanced.json';ip.write_text(json.dumps(ic),encoding='utf-8')
try:capture(ic,out/'instanced-unexpected',ip)
except ValueError as exc:assert 'Generated delivery instances' in str(exc);checks.append({'case':'generated_instances','rejected':True})
else:raise AssertionError('Generated instances silently omitted')
assert sha(source)==source_hash
(out/'fixture-validation.json').write_text(json.dumps({'status':'passed','source_sha256':source_hash,'source_unchanged':True,'negative_guards':checks,'captured_views':2,'selected_triangles':14,'omitted_cap_triangles':2,'helper_path':str(tools/'solid_views.py'),'helper_sha256':sha(tools/'solid_views.py')},indent=2),encoding='utf-8')
print('SOLID_DIAGNOSTIC_BLENDER_FIXTURE_PASSED',len(checks),'negative guards, 2 rendered views')
