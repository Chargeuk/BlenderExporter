"""Native Blender regression: --python TEST -- NEW_OUTPUT_DIRECTORY."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'src/babylon_js/environment_tools')]
from babylon_js import environment_controls
from babylon_js.environment_tools import recipe_cli
from babylon_js.lighting_controls import create_preview_material
import worker

if not bpy.app.background:
    raise RuntimeError('Run this fixture in a disposable background process')
out = Path(sys.argv[sys.argv.index('--')+1]).resolve()
out.mkdir(parents=True, exist_ok=False)
retained = out/'retained'
retained.mkdir()
scene = bpy.context.scene
for obj in list(scene.objects):
    bpy.data.objects.remove(obj, do_unlink=True)

def image(name):
    path = retained/(name+'.exr')
    worker.write_exr(path, np.ones((16, 16, 4), np.float32))
    result = bpy.data.images.load(str(path), check_existing=False)
    result.colorspace_settings.name = 'sRGB' if name == 'colour' else 'Non-Color' if name == 'island_ids' else 'Linear Rec.709'
    return result

images = {name: image(name) for name in ('colour', 'direct', 'indirect', 'island_ids', 'combined')}
preview, _ = create_preview_material(images['colour'], images['direct'], images['indirect'], images['island_ids'])
physical = bpy.data.materials.new('Physical source')
physical.use_nodes = True
physical.use_fake_user = True
physical_name = physical.name
scene['physical_material'] = physical.name
mesh = bpy.data.meshes.new('Receiver mesh')
mesh.from_pydata([(0,0,0), (1,0,0), (1,1,0), (0,1,0)], [], [(0,1,2,3)])
mesh.materials.append(preview)
uv = mesh.uv_layers.new(name='SimpleBake')
for item, coord in zip(uv.data, ((0,0), (1,0), (1,1), (0,1))):
    item.uv = coord
receiver = bpy.data.objects.new('Receiver', mesh)
scene.collection.objects.link(receiver)
light = bpy.data.objects.new('Bake light', bpy.data.lights.new('Bake light', 'POINT'))
scene.collection.objects.link(light)
history = bpy.data.collections.new('Excluded history')
scene.collection.children.link(history)
old = bpy.data.objects.new('Historical source', mesh.copy())
history.objects.link(old)
old_light = bpy.data.objects.new('Historical light', bpy.data.lights.new('Historical light', 'POINT'))
history.objects.link(old_light)
bpy.context.view_layer.layer_collection.children[history.name].exclude = True
environment_controls.create_controls(scene.world, images['colour'], connect=True)
bpy.context.preferences.filepaths.save_version = 0
source = retained/'fixture.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(source))
config = retained/'environment-build.json'
base = dict(schema_version=1, blend=source.name, size=16, uv='SimpleBake', preview_material=preview.name,
    images={name: name+'.exr' for name in images}, source_images=['colour.exr'],
    receivers=dict(names=[receiver.name]), contributors=dict(names=[]), bake_lights=dict(names=[light.name]),
    preparation=dict(output_blend='physical.blend', default_material_property='physical_material',
        override_material_property='physical_override', generated_images=['direct','indirect','island_ids','combined'],
        profiles={'16': dict(quality='preview', samples=dict(DIRECT=1,INDIRECT=1))}))
recipe_cli.write(config, base)
source_hash = recipe_cli.sha(source)
bpy.ops.wm.open_mainfile(filepath=str(source))
recipe_cli.physical(config, SimpleNamespace(size=16, reuse_current=False, out=out/'physical'))
if bpy.context.view_layer.layer_collection.children['Excluded history'].exclude is not True:
    raise AssertionError('Historical collection exclusion changed')
if 'Historical source' in bpy.context.view_layer.objects:
    raise AssertionError('Historical source entered the view layer')
if bpy.data.objects['Receiver'].data.materials[0].name != physical_name:
    raise AssertionError('Included receiver was not restored to its physical material')
if bpy.data.objects['Receiver'].hide_get() or bpy.data.objects['Receiver'].hide_render:
    raise AssertionError('Included receiver became hidden')
if not bpy.data.objects['Historical source'].hide_render:
    raise AssertionError('Excluded inert source must remain render-hidden')

for role, excluded in [('receivers','Historical source'), ('contributors','Historical source'), ('bake_lights','Historical light')]:
    bpy.ops.wm.open_mainfile(filepath=str(source))
    changed = json.loads(json.dumps(base))
    changed[role] = dict(names=[excluded])
    recipe_cli.write(config, changed)
    try:
        recipe_cli.physical(config, SimpleNamespace(size=16, reuse_current=False, out=out/('invalid-'+role)))
    except ValueError as exc:
        if 'Physical participants excluded from active view layer' not in str(exc):
            raise
    else:
        raise AssertionError('Excluded configured '+role+' was silently accepted')
if recipe_cli.sha(source) != source_hash:
    raise AssertionError('Retained source changed')
(out/'result.json').write_text(json.dumps(dict(status='passed', excluded_inert_source_preserved=True,
    included_receiver_restored=True, excluded_participant_roles_rejected=['receivers','contributors','bake_lights'],
    source_unchanged=True), indent=2)+'\n')
print('PHYSICAL_PREPARATION_VIEW_LAYER_PASSED')
