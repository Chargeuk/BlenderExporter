"""Real CPU native-diffuse capture and restoration fixture, never a live scene.

Run disposable Blender: --background --factory-startup --python this_file --
--helper <installed environment_tools/camera_views.py> --output <fresh directory>.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch

import bpy
import numpy as np
import OpenImageIO as oiio

assert bpy.app.background, 'Never run this fixture in the live authoring session'
parser = argparse.ArgumentParser()
parser.add_argument('--helper', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
helper, root = args.helper.resolve(), args.output.resolve()
if root.exists(): raise ValueError('Fixture output must be fresh')
root.mkdir(parents=True)
sys.path.insert(0, str(helper.parent))
sys.path.insert(0, str(helper.parent.parent.parent))
spec = importlib.util.spec_from_file_location('fixture_camera_diffuse', helper)
camera_views = importlib.util.module_from_spec(spec)
spec.loader.exec_module(camera_views)
from common import sha, save_json
from babylon_js import environment_controls
from babylon_js.workflow_tools import native_view_diagnostics as native

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.name = 'Physical source'
scene.world = bpy.data.worlds.new('Saved World')
world_image = bpy.data.images.new('Packed fixture World', width=8, height=4)
world_image.pixels[:] = [.03, .03, .03, 1] * 32
world_image.pack()
controls = environment_controls.create_controls(scene.world, world_image, lighting_strength=.1, connect=True)
world_output = next(node for node in scene.world.node_tree.nodes if node.type == 'OUTPUT_WORLD')
scene.world.node_tree.links.new(controls.outputs['Visible Sky'], world_output.inputs['Surface'])
bpy.ops.mesh.primitive_cube_add()
receiver = bpy.context.object
receiver.name = 'Receiver'
material = bpy.data.materials.new('Saved PBR')
material.use_nodes = True
principled = next(node for node in material.node_tree.nodes if node.type == 'BSDF_PRINCIPLED')
principled.inputs['Base Color'].default_value = (.3, .5, .7, 1)
principled.inputs['Metallic'].default_value = .3
principled.inputs['Roughness'].default_value = .4
receiver.data.materials.append(material)
bpy.ops.mesh.primitive_plane_add(size=10, location=(0, 0, -1.1))
contributor = bpy.context.object
contributor.name = 'Contributor'
contributor.data.materials.append(material)
bpy.ops.mesh.primitive_cube_add(size=.2, location=(1.4, 0, .2))
emitter = bpy.context.object
emitter.name = 'Visible emitter'
emission = bpy.data.materials.new('Saved emission')
emission.use_nodes = True
tree = emission.node_tree
tree.nodes.clear()
node = tree.nodes.new('ShaderNodeEmission')
node.inputs['Strength'].default_value = 2
tree.links.new(node.outputs[0], tree.nodes.new('ShaderNodeOutputMaterial').inputs['Surface'])
emitter.data.materials.append(emission)
bpy.ops.mesh.primitive_cube_add(size=.2, location=(-1.4, 0, .2))
glass = bpy.context.object
glass.name = 'Excluded glass'
glass.data.materials.append(material)
for name, watts, location in [('Window key', 500, (-1, 0, 4)), ('Ceiling fill', 90, (2, 0, 3))]:
    data = bpy.data.lights.new(name, 'AREA')
    data.energy = watts
    data.size = 3
    light = bpy.data.objects.new(name, data)
    scene.collection.objects.link(light)
    light.location = location
data = bpy.data.cameras.new('Saved camera data')
data.type = 'ORTHO'
data.ortho_scale = 4
camera = bpy.data.objects.new('Saved camera', data)
scene.collection.objects.link(camera)
camera.location = (0, 0, 6)
scene.camera = camera
scene.render.engine = 'CYCLES'
scene.cycles.samples = 13
scene.cycles.use_denoising = True
scene.cycles.use_adaptive_sampling = True
scene.render.use_compositing = True
scene.render.use_sequencer = True
scene.render.resolution_x, scene.render.resolution_y = 321, 123
scene.render.resolution_percentage = 70
scene.render.filepath = '//original'
scene.frame_set(3, subframe=.25)
source_scene_name = scene.name
other = bpy.data.scenes.new('Original active scene')
bpy.context.window.scene = other
source = root/'source.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(source))

c = {'schema_version': 1, 'scene': source_scene_name, '_source': str(source), '_root': str(root),
    'size': 16, 'quality': 'preview', 'device': 'CPU', 'uv': 'UVMap',
    'receivers': {'names': ['Receiver']}, 'contributors': {'names': ['Contributor']},
    'glass': {'names': ['Excluded glass']}, 'visible_emitters': {'names': ['Visible emitter']},
    'bake_lights': {'names': ['Window key', 'Ceiling fill']},
    'capture': {'views': {'mode': 'physical-diffuse', 'source_sha256': sha(source),
        'dimensions': [64, 48], 'samples': 4, 'denoise': False, 'include_glass': False,
        'world_output': 'Lighting', 'frame': 1,
        'display': {'view_transform': 'Standard', 'look': 'None', 'exposure': 0, 'gamma': 1},
        'camera_set': [{'id': 'diffuse', 'camera': 'Saved camera'}]}}}

def reload_source():
    bpy.ops.wm.open_mainfile(filepath=str(source))
    return bpy.data.scenes[source_scene_name]

def state(s):
    return {'active_scene': bpy.context.scene.name, 'active_layer': bpy.context.view_layer.name,
        'camera': s.camera.name, 'frame': (s.frame_current, s.frame_subframe),
        'render': {key: getattr(s.render, key) for key in ('engine', 'filepath', 'resolution_x', 'resolution_y', 'resolution_percentage',
            'pixel_aspect_x', 'pixel_aspect_y', 'use_border', 'film_transparent', 'use_file_extension', 'use_compositing', 'use_sequencer', 'use_motion_blur') if hasattr(s.render, key)},
        'cycles': {key: getattr(s.cycles, key) for key in ('device', 'samples', 'use_denoising', 'use_adaptive_sampling', 'use_animated_seed',
            'diffuse_bounces', 'max_bounces', 'sample_clamp_direct', 'sample_clamp_indirect', 'time_limit')},
        'display': {key: getattr(s.view_settings, key) for key in ('view_transform', 'look', 'exposure', 'gamma', 'use_curve_mapping')},
        'format': native.image_format_state(s.render.image_settings),
        'color_management': getattr(s.render.image_settings, 'color_management', None),
        'visibility': sorted((obj.name, obj.hide_render) for obj in s.objects),
        'meshes': sorted((obj.name, obj.data.name, [(slot.link, slot.material.name if slot.material else None) for slot in obj.material_slots]) for obj in s.objects if obj.type == 'MESH'),
        'camera_data': sorted(data.name for data in bpy.data.cameras),
        'passes': [(layer.name, layer.use_pass_diffuse_direct, layer.use_pass_diffuse_indirect, layer.use_pass_diffuse_color,
                    getattr(layer.cycles, 'use_denoising', None)) for layer in s.view_layers],
        'links': sorted((link.from_node.name, link.from_socket.name, link.to_node.name, link.to_socket.name) for link in s.world.node_tree.links),
        'compositor': getattr(s, 'compositing_node_group', None)}

scene = reload_source()
before = state(scene)
output = root/'positive'
output.mkdir()
result = camera_views.capture_views(c, root, output)
assert state(scene) == before, (state(scene), before)
assert result['source_saved'] is False and result['source_hash_unchanged'] and result['state_restored']
assert sha(source) == c['capture']['views']['source_sha256']
assert result['physical_overlay_context_count'] == 1 and result['rendered_views'] == 1
record = result['views'][0]
assert record['enabled_lights'] == ['Ceiling fill', 'Window key']
assert record['visible_emitters'] == ['Visible emitter'] and record['glass'] == []
assert record['visible_mesh_count'] == 3
assert not record['denoise'] and not record['compositor_used']
proof = record['native_diffuse']
assert proof['metallic_cap_equivalence'] is False and proof['ungraded_reference'] is True
assert proof['formula'] == '(Diffuse Direct + Diffuse Indirect) * Diffuse Color'
assert proof['multilayer_file_settings']['color_depth'] == '32'
assert proof['multilayer_file_settings']['exr_codec'] == 'ZIP'
assert proof['request_sha256'] == hashlib.sha256(json.dumps(c, sort_keys=True, allow_nan=False, separators=(',', ':')).encode('utf8')).hexdigest()
for filename_key, digest_key in [('linear_file', 'linear_sha256'), ('pass_file', 'pass_file_sha256')]:
    assert sha(output/proof[filename_key]) == proof[digest_key]
passes, channels = native.read_diffuse_passes(output/proof['pass_file'], proof['channels']['layer'])
expected = native.compose_diffuse(passes['DiffDir'], passes['DiffInd'], passes['DiffCol'])
actual = oiio.ImageBuf(str(output/proof['linear_file'])).get_pixels(oiio.FLOAT)
np.testing.assert_array_equal(actual, expected)
assert expected.shape == (48, 64, 3) and float(expected.max()) > .001
exr_spec = oiio.ImageBuf(str(output/proof['linear_file'])).spec()
assert str(exr_spec.format) == 'float', exr_spec.format
assert sha(output/record['file']) == record['sha256']
assert json.loads((output/'diffuse.receipt.json').read_text())['native_diffuse'] == proof

# A failure after rendering/pass readback must restore the complete source state.
scene = reload_source()
before = state(scene)
failed = root/'failed'
failed.mkdir()
with patch.object(native, 'read_diffuse_passes', side_effect=RuntimeError('injected pass read failure')):
    try: camera_views.capture_views(c, root, failed)
    except RuntimeError as error: assert 'injected pass read failure' in str(error)
    else: raise AssertionError('Expected injected failure')
assert state(scene) == before
assert sha(source) == c['capture']['views']['source_sha256']

# Existing retained passes are rejected before render/state changes, even without PNG.
scene = reload_source()
before = state(scene)
with patch.object(native, 'read_diffuse_passes', side_effect=AssertionError('must not render')):
    try: camera_views.capture_views(c, root, failed)
    except ValueError as error: assert 'Preserve previous' in str(error)
    else: raise AssertionError('Overwriting retained outputs was accepted')
assert state(scene) == before
bad = copy.deepcopy(c)
bad['capture']['views']['source_sha256'] = '0'*64
try: camera_views.capture_views(bad, root, root/'bad-source')
except ValueError as error: assert 'immutable' in str(error)
else: raise AssertionError('Stale source hash accepted')
assert state(scene) == before

# Exercise the advertised direct CLI and its common-schema loader in a fresh
# process. These bindings reserve future bake names; no master file is needed.
import subprocess
cli_recipe = copy.deepcopy(c)
for key in list(cli_recipe):
    if key.startswith('_'):
        del cli_recipe[key]
cli_recipe['blend'] = source.name
cli_recipe['source_images'] = []  # Fixture World is packed in the hash-bound source.
cli_recipe['images'] = {key: 'planned-masters/'+key+'.exr'
                        for key in ('direct', 'indirect', 'island_ids', 'combined', 'colour')}
recipe_path = root/'direct-cli.environment.json'
save_json(recipe_path, cli_recipe)
reserved = [root/path for path in cli_recipe['images'].values()]
assert not (root/'planned-masters').exists() and all(not path.exists() for path in reserved)
dependency_hashes = {name: sha(root/name) for name in cli_recipe['source_images']}
input_hashes = {str(path): sha(path) for path in (source, recipe_path, helper, Path(native.__file__))}
cli_output = root/'direct-cli'
command = [bpy.app.binary_path, '--background', '--factory-startup', '--disable-autoexec',
           '--threads', '1', str(source), '--python-exit-code', '1', '--python', str(helper),
           '--', '--physical-diffuse', '--config', str(recipe_path), '--output', str(cli_output)]
options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
with (root/'direct-cli.log').open('w', encoding='utf8') as log:
    completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=180, **options)
assert completed.returncode == 0, 'Direct CLI failed; see '+str(root/'direct-cli.log')
assert all(sha(path) == digest for path, digest in input_hashes.items())
assert {name: sha(root/name) for name in cli_recipe['source_images']} == dependency_hashes
assert not (root/'planned-masters').exists() and all(not path.exists() for path in reserved)
cli_result = json.loads((cli_output/'result.json').read_text())
assert cli_result['source_saved'] is False and cli_result['source_hash_unchanged'] and cli_result['state_restored']
assert cli_result['source_sha256'] == input_hashes[str(source)] and cli_result['rendered_views'] == 1
cli_record = json.loads((cli_output/'diffuse.receipt.json').read_text())
assert cli_result['views'] == [cli_record]
cli_proof = cli_record['native_diffuse']
assert cli_proof['source_images_sha256'] == dependency_hashes
assert cli_proof['camera_helper_sha256'] == input_hashes[str(helper)]
assert cli_proof['helper_sha256'] == input_hashes[str(Path(native.__file__))]
for filename_key, digest_key in [('linear_file', 'linear_sha256'), ('pass_file', 'pass_file_sha256')]:
    assert sha(cli_output/cli_proof[filename_key]) == cli_proof[digest_key]
assert sha(cli_output/cli_record['file']) == cli_record['sha256']
assert cli_record['samples'] == 4 and cli_record['dimensions'] == [64, 48]
assert cli_record['device'] == 'CPU' and cli_record['enabled_lights'] == ['Ceiling fill', 'Window key']
assert state(scene) == before
save_json(root/'result.json', {'status': 'passed', 'checks': [
    'real analytic lights and visible emitter accepted', 'native exact pass composition and float32 EXR',
    'retained passes/PNG/receipt hashes', 'uncapped honest physical reference',
    'success and failure restore scene/camera/lights/materials/format/passes/display/World',
    'existing pass outputs rejected before render', 'stale source hash rejected',
    'advertised direct CLI uses common-schema reserved master paths without creating masters',
    'CLI source/dependency/helper hashes unchanged and reference outputs verified'], 'source_sha256': sha(source)})
