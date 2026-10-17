"""CPU fixture: final UV tint affects physical copies once, never saved/export PBR."""
import json
import sys
from pathlib import Path
from unittest.mock import patch
import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'src/babylon_js/environment_tools'))
from common import sha
from ao import geometry_identity
from appearance import preview_identity
from physical_colour_adjustment import physical_materials, select_scene, verify_native_response
from receiver_response_capture import capture
from babylon_js import colour_adjustment as ca
from babylon_js.agx_lightmap import bake_albedo, read_exr
import worker

assert bpy.app.background, 'Never run this fixture in a live session'
out = Path(sys.argv[sys.argv.index('--')+1]).resolve()
out.mkdir(parents=True, exist_ok=False)
(out/'masters').mkdir()
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
physical = bpy.context.scene
physical.name = 'Physical fixture'
physical.world.use_nodes = True
background = next(n for n in physical.world.node_tree.nodes if n.type == 'BACKGROUND')
background.inputs['Color'].default_value = (.5, .5, .5, 1)
background.inputs['Strength'].default_value = .2
material = bpy.data.materials.new('White untinted shared source')
material.use_nodes = True
material['bjs_intrinsic_colour_role'] = 'untinted-source'
principal = next(n for n in material.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
principal.inputs['Base Color'].default_value = (1, 1, 1, 1)
metal = material.node_tree.nodes.new('ShaderNodeValue')
metal.outputs[0].default_value = .9
material.node_tree.links.new(metal.outputs[0], principal.inputs['Metallic'])
objects = []
for index, name in enumerate(('Uncapped', 'Capped', 'Contributor')):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    obj = bpy.data.objects.new(name, mesh)
    physical.collection.objects.link(obj)
    obj.location.x = index * 2
    mesh.materials.append(material)
    for uv_name in ('UVMap', 'SimpleBake'):
        layer = mesh.uv_layers.new(name=uv_name)
        x0, x1 = (.1, .4) if index == 0 else (.6, .9)
        for item, uv in zip(layer.data, ((x0, .1), (x1, .1), (x1, .9), (x0, .9))): item.uv = uv
    mesh.uv_layers['UVMap'].active_render = True
    objects.append(obj)
glass = bpy.data.objects.new('Glass shared user', objects[0].data)
physical.collection.objects.link(glass)
glass.location.x = 20
preview = bpy.data.scenes.new('Saved baked preview')
bpy.context.window.scene = preview
source = out/'fixture.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(source))
config_path = out/'fixture.json'
config = {'schema_version': 1, 'blend': source.name, 'scene': physical.name, 'size': 32, 'quality': 'preview',
    'device': 'CPU', 'samples': {'DIRECT': 1, 'INDIRECT': 1}, 'uv': 'SimpleBake',
    'receivers': {'names': ['Uncapped', 'Capped']}, 'contributors': {'names': ['Contributor']},
    'glass': {'names': ['Glass shared user']}, 'metal_receivers': {'names': ['Capped']},
    'metallic_cap': .8, 'receiver_response': {'enabled': True, 'schema_version': 1}}
config.update(_source=str(source), _root=str(out), _candidate=str(out), _config=str(config_path))
assert select_scene(config) == physical
assert select_scene({}) == physical
try:
    select_scene({'scene': 'Missing scene'})
    raise AssertionError('Missing saved scene must fail')
except ValueError:
    pass
codes = np.zeros((512, 512, 4), np.float32)
codes[..., :3] = ca.srgb_encode(np.array(.5))
codes[:, :256, :3] = ca.srgb_encode(np.array([.25, .4, .5]))
codes[:, 256:, :3] = ca.srgb_encode(np.array([.5, .3, .2]))
codes[:, :256, 3] = 1
ca.save_codes(out/'neutral.png', np.concatenate((codes[..., :3], np.zeros_like(codes[..., 3:4])), axis=2))
ca.save_codes(out/'tint.png', codes)
config['source_images'] = ['tint.png', 'neutral.png']
config['physical_colour_adjustment'] = {'enabled': True, 'schema_version': 1, 'image': 'tint.png',
    'sha256': sha(out/'tint.png'), 'strength': 1,
    'geometry_sha256': geometry_identity(config)['geometry_sha256'],
    'objects': {'names': [o.name for o in objects]}}
config_path.write_text(json.dumps({k: v for k, v in config.items() if not k.startswith('_')}), encoding='utf8')
source_digest = sha(source)
source_signature = preview_identity(material)
source_meshes = [o.data for o in objects]
original_glass_mesh = glass.data
untinted = bake_albedo(bpy.context, objects[:2], out/'untinted.exr', 32, 'SimpleBake')
for label, changes in (('zero-strength', {'strength': 0}),
                       ('neutral-alpha', {'image': 'neutral.png', 'sha256': sha(out/'neutral.png')})):
    neutral = dict(config, physical_colour_adjustment=dict(config['physical_colour_adjustment'], **changes))
    with physical_materials(neutral) as record:
        assert record['active'] is False
        assert [o.data for o in objects] == source_meshes
        np.testing.assert_array_equal(bake_albedo(bpy.context, objects[:2], out/(label+'.exr'), 32, 'SimpleBake'), untinted)
with physical_materials(config) as record:
    assert record['active']
    assert all(o.data != old for o, old in zip(objects, source_meshes))
    assert all(o.data.materials[0] != material for o in objects)
    assert glass.data == original_glass_mesh and glass.data.materials[0] == material
    for obj in objects:
        base = next(n for n in obj.data.materials[0].node_tree.nodes if n.type == 'BSDF_PRINCIPLED').inputs['Base Color']
        assert base.is_linked and base.links[0].from_node.get('bjs_physical_colour_adjustment') == 1
    try:
        with physical_materials(config):
            raise AssertionError('Double overlay must fail')
    except ValueError:
        pass
    tinted = bake_albedo(bpy.context, objects[:2], out/'once-tinted.exr', 32, 'SimpleBake')
    assert (tinted[..., :3][tinted[..., 3] == 1] < untinted[..., :3][tinted[..., 3] == 1] * .9).any()
    # The right receiver's painted RGB has zero influence and must remain exactly white.
    np.testing.assert_array_equal(tinted[:, 20:28][tinted[:, 20:28, 3] == 1],
                                  untinted[:, 20:28][untinted[:, 20:28, 3] == 1])
assert [o.data for o in objects] == source_meshes and glass.data == original_glass_mesh
assert preview_identity(material) == source_signature
assert not principal.inputs['Base Color'].is_linked
for key in ('geometry_sha256', 'sha256'):
    bad = dict(config, physical_colour_adjustment=dict(config['physical_colour_adjustment'], **{key: '0'*64}))
    try:
        with physical_materials(bad): raise AssertionError('Stale identity must fail')
    except ValueError:
        pass
material['bjs_provisional_colour_tint'] = True
try:
    with physical_materials(config): raise AssertionError('Provisional scalar tint must fail')
except ValueError:
    pass
del material['bjs_provisional_colour_tint']
bad = dict(config, physical_colour_adjustment=dict(config['physical_colour_adjustment'], objects={'names': ['Glass shared user']}))
try:
    with physical_materials(bad): raise AssertionError('Excluded shared glass must fail')
except ValueError:
    pass
capture_stage = out/'response-capture'
capture_stage.mkdir()
bpy.context.window.scene = preview
with patch.object(worker, 'preflight', return_value={'fixture': True}):
    capture_result = capture(config, out, capture_stage)
assert bpy.context.scene == physical
np.testing.assert_array_equal(np.load(capture_stage/'source_base_colour.npy'), tinted)
assert preview_identity(material) == source_signature
native_stage = out/'bake'
native_stage.mkdir()
with patch.object(worker, 'preflight', return_value={'fixture': True}):
    native_result = worker.bake(config, out, native_stage)
response_record = json.loads((capture_stage/'capture.provenance.json').read_text())
verify_native_response(native_result, response_record)
np.testing.assert_array_equal(read_exr(out/'masters/albedo_uv.exr'), tinted)
assert native_result['scene'] == physical.name
assert preview_identity(material) == source_signature
assert [o.data for o in objects] == source_meshes and glass.data == original_glass_mesh
assert sha(source) == source_digest
# Reuse the external two-phase verifier with actual native COLOR-excluded outputs.
native = np.load(native_stage/'direct_raw.npy')
assert native[..., :3][native[..., 3] == 1].max() > 0
np.save(out/'native_direct.npy', native, allow_pickle=False)
(out/'native_islands.json').write_bytes((native_stage/'islands.json').read_bytes())
native_result.update(source_sha256=source_digest)
(native_stage/'result.json').write_text(json.dumps(native_result, indent=2), encoding='utf8')
(out/'capture-result.json').write_text(json.dumps({'status': 'capture-passed', 'capture_operations': 2,
    'linked_metallic_restored': True, 'shared_material_preserved': True,
    'native_direct_sha256': sha(out/'native_direct.npy'), 'native_islands_sha256': sha(out/'native_islands.json'),
    'source_sha256': source_digest, 'physical_overlay_source_restored': True,
    'saved_preview_scene_routed_to_physical': True, 'native_response_tint_identity_matches': True,
    'neutral_alpha_and_strength_bypass': True, 'stale_and_double_tint_rejected': True,
    'fixture_preflight': 'World/preview bindings excluded'}, indent=2))
print('PHYSICAL_COLOUR_BLENDER_CAPTURE_PASSED', str(out), flush=True)
