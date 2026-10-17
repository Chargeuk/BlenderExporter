"""Small real CPU COLOR capture; run only with --background --factory-startup."""
import json
import sys
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'src/babylon_js/environment_tools'))
from common import sha
import worker
from receiver_response_capture import capture, capped_materials
from receiver_bake import joined_receivers
import receiver_bake

assert bpy.app.background, 'Never run this fixture in a live session'
args = sys.argv[sys.argv.index('--')+1:]
out = Path(args[0]).resolve()
out.mkdir(parents=True, exist_ok=False)
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
scene.world.use_nodes = True
background = next(n for n in scene.world.node_tree.nodes if n.type == 'BACKGROUND')
background.inputs['Color'].default_value = (.5, .5, .5, 1)
background.inputs['Strength'].default_value = .2
material = bpy.data.materials.new('Shared actual Principled')
material.use_nodes = True
principal = next(n for n in material.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
principal.inputs['Base Color'].default_value = (.8, .4, .2, 1)
# The linked metallic branch must be capped as MIN(original, cap), and restored afterwards.
value = material.node_tree.nodes.new('ShaderNodeValue')
value.outputs[0].default_value = .9
material.node_tree.links.new(value.outputs[0], principal.inputs['Metallic'])
objects = []
for index, name in enumerate(('Uncapped', 'Capped')):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    obj.location.x = index * 2
    mesh.materials.append(material)
    for uv_name in ('UVMap', 'SimpleBake'):
        layer = mesh.uv_layers.new(name=uv_name)
        x0, x1 = (.1, .4) if index == 0 else (.6, .9)
        for data, uv in zip(layer.data, ((x0, .1), (x1, .1), (x1, .9), (x0, .9))): data.uv = uv
    mesh.uv_layers['UVMap'].active_render = True
    objects.append(obj)
# A second object sharing the receiving mesh must retain its physical material.
shared = bpy.data.objects.new('Shared mesh user', objects[1].data)
scene.collection.objects.link(shared)
shared.hide_render = True
original_mesh = objects[1].data
baseline = (len(bpy.data.meshes), len(bpy.data.materials))
objects[1].hide_set(True)
try:
    with capped_materials(objects[1], .8) as clones:
        assert objects[1].data != original_mesh and shared.data == original_mesh
        assert shared.data.materials[0] == material
        capped = next(n for n in clones[0].node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
        minimum = capped.inputs['Metallic'].links[0].from_node
        assert minimum.operation == 'MINIMUM' and minimum.inputs[1].default_value == np.float32(.8)
        assert minimum.inputs[0].links[0].from_node.type == 'VALUE'
        objects[1].hide_set(False)
        raise RuntimeError('injected cap bake failure')
except RuntimeError as exc:
    assert str(exc) == 'injected cap bake failure'
assert objects[1].data == original_mesh and objects[1].hide_get()
assert (len(bpy.data.meshes), len(bpy.data.materials)) == baseline
objects[1].hide_set(False)
# Native transport accepts the first Principled; response still requires exactly one.
extra = material.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
proof = []
with capped_materials(objects[1], .8, strict_principled=False, material_proof=proof):
    assert proof[0]['source'] == material.name and proof[0]['principled_node'] == principal.name
    assert proof[0]['metallic_link'] == {'node': value.name, 'socket': value.outputs[0].identifier}
try:
    with capped_materials(objects[1], .8):
        raise AssertionError('Response must reject multiple Principled nodes')
except ValueError as exc:
    assert 'one Principled' in str(exc)
assert objects[1].data == original_mesh and shared.data.materials[0] == material
assert (len(bpy.data.meshes), len(bpy.data.materials)) == baseline
material.node_tree.nodes.remove(extra)
# The unlinked metallic value follows the same MINIMUM operation and is restored.
material.node_tree.links.remove(principal.inputs['Metallic'].links[0])
principal.inputs['Metallic'].default_value = .9
with capped_materials(objects[1], .8) as clones:
    capped = next(n for n in clones[0].node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    minimum = capped.inputs['Metallic'].links[0].from_node
    assert not minimum.inputs[0].is_linked and minimum.inputs[0].default_value == np.float32(.9)
assert not principal.inputs['Metallic'].is_linked
material.node_tree.links.new(value.outputs[0], principal.inputs['Metallic'])
bpy.data.objects.remove(shared, do_unlink=True)
source = out/'fixture.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(source))
config_path = out/'fixture.json'
config = {'schema_version': 1, 'blend': source.name, 'size': 32, 'quality': 'preview',
    'device': 'CPU', 'samples': {'DIRECT': 1, 'INDIRECT': 1}, 'uv': 'SimpleBake',
    'receivers': {'names': ['Uncapped', 'Capped']}, 'contributors': {},
    'metal_receivers': {'names': ['Capped']}, 'metallic_cap': .8,
    'receiver_response': {'enabled': True, 'schema_version': 1}}
config_path.write_text(json.dumps(config), encoding='utf8')
config.update(_source=str(source), _root=str(out), _candidate=str(out), _config=str(config_path))
stage = out/'response-capture'
stage.mkdir()
source_digest = sha(source)
original_meshes = [o.data for o in objects]
# This fixture tests the actual capture path and native operators. The unrelated supported
# World/preview-binding preflight belongs to the full environment integration fixture.
with patch.object(worker, 'preflight', return_value={'fixture': True}):
    result = capture(config, out, stage)
assert result['capture_operations'] == 2 and result['physical_transport_performed'] is False
assert sha(source) == source_digest
assert [o.data for o in objects] == original_meshes
assert all(o.data.materials[0] == material for o in objects)
assert principal.inputs['Metallic'].links[0].from_node == value
assert value.outputs[0].default_value == np.float32(.9)
assert not any(n.type == 'MATH' and n.operation == 'MINIMUM' for n in material.node_tree.nodes)
record = json.loads((stage/'capture.provenance.json').read_text())
assert all(r['pass_filter'] == ['COLOR'] and r['type'] == 'DIFFUSE' and r['samples'] == 1
           and r['margin'] == 0 and not r['adaptive'] and not r['denoising'] for r in record['operations'])
assert record['operations'][1]['receivers'] == ['Capped']
# Failure before/after the join operator runs must dispose every owned copy and
# mesh, even when the join has invalidated the non-active copy RNA references.
real_join = bpy.ops.object.join
for fail_after_join in (False, True):
    baseline = (len(bpy.data.objects), len(bpy.data.meshes))
    copies, _ = worker.evaluate_receivers(config)
    owned_objects = {obj.as_pointer() for obj in copies}
    owned_meshes = {obj.data.as_pointer() for obj in copies}
    visibility = [obj.hide_render for obj in objects]
    def failing_join():
        if fail_after_join:
            real_join()
        raise RuntimeError('injected join operator failure')
    operations = SimpleNamespace(select_all=bpy.ops.object.select_all, join=failing_join)
    failing_bpy = SimpleNamespace(data=bpy.data, context=bpy.context,
                                  ops=SimpleNamespace(object=operations))
    try:
        with patch.object(receiver_bake, 'bpy', failing_bpy):
            with joined_receivers(copies, objects):
                raise AssertionError('Failed join must never yield a proxy')
    except RuntimeError as exc:
        assert str(exc) == 'injected join operator failure'
    assert not owned_objects & {obj.as_pointer() for obj in bpy.data.objects}
    assert not owned_meshes & {mesh.as_pointer() for mesh in bpy.data.meshes}
    assert (len(bpy.data.objects), len(bpy.data.meshes)) == baseline
    assert [obj.hide_render for obj in objects] == visibility
    assert [obj.data for obj in objects] == original_meshes
    assert all(obj.data.materials[0] == material for obj in objects)
# Failure in a joined bake removes the proxy and restores receiver visibility.
copies, _ = worker.evaluate_receivers(config)
visibility = [obj.hide_render for obj in objects]
try:
    with joined_receivers(copies, objects) as (failed_proxy, normal_error):
        proxy_name = failed_proxy.name
        assert normal_error <= .001 and all(obj.hide_render for obj in objects)
        raise RuntimeError('injected joined bake failure')
except RuntimeError as exc:
    assert str(exc) == 'injected joined bake failure'
assert bpy.data.objects.get(proxy_name) is None
assert [obj.hide_render for obj in objects] == visibility
# Establish ownership with a real native COLOR-excluded bake, never COLOR-pass alpha.
copies, catalogue = worker.evaluate_receivers(config)
for obj in objects: obj.hide_render = True
bpy.ops.object.select_all(action='DESELECT')
for obj in copies: obj.select_set(True)
bpy.context.view_layer.objects.active = copies[0]
bpy.ops.object.join()
proxy = bpy.context.object
image = worker.target_image(config, list(proxy.data.materials))
bpy.ops.object.bake(type='DIFFUSE', pass_filter={'DIRECT'}, margin=0, use_clear=True,
                    use_selected_to_active=False, uv_layer='SimpleBake')
native = worker.raw_pixels(image)
assert np.isin(native[:, :, 3], [0, 1]).all() and (native[:, :, 3] == 1).any()
assert native[:, :, :3][native[:, :, 3] == 1].max() > 0, 'Fixture needs nonzero native radiance'
np.save(out/'native_direct.npy', native, allow_pickle=False)
(out/'native_islands.json').write_text(json.dumps(catalogue, indent=2), encoding='utf8')
assert sha(source) == source_digest
(out/'capture-result.json').write_text(json.dumps({'status': 'capture-passed', 'capture_operations': 2,
    'linked_metallic_restored': True, 'shared_material_preserved': True,
    'native_direct_sha256': sha(out/'native_direct.npy'),
    'native_islands_sha256': sha(out/'native_islands.json'),
    'source_sha256': source_digest, 'fixture_preflight': 'World/preview bindings excluded',
    'verification_pending': 'Run verify_receiver_response_fixture.py in the NumPy/SciPy image-processing runtime'}, indent=2))
print('RECEIVER_RESPONSE_BLENDER_CAPTURE_PASSED', str(out), flush=True)
