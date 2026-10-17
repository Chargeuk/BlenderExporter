"""Small real CPU COLOR capture; run only with --background --factory-startup."""
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
import worker
from receiver_response_capture import capture

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
