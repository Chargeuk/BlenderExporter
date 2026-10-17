"""Unmocked CPU preflight accepts only the explicitly supported camera-ray World split."""
import json
import math
import sys
from pathlib import Path
import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'src/babylon_js/environment_tools'))
import worker
from common import sha
from babylon_js import environment_controls as world_tools

assert bpy.app.background, 'Never run this fixture in a live session'
out = Path(sys.argv[sys.argv.index('--')+1]).resolve()
out.mkdir(parents=True, exist_ok=False)
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.name = 'Physical World route fixture'
declared = []


def image(name):
    path = out/(name+'.exr')
    worker.write_exr(path, np.ones((16, 16, 4), np.float32))
    declared.append(path.name)
    result = bpy.data.images.load(str(path), check_existing=False)
    result.name = name
    return result


def material(name, source):
    result = bpy.data.materials.new(name)
    result.use_nodes = True
    texture = result.node_tree.nodes.new('ShaderNodeTexImage')
    texture.image = source
    return result


mesh = bpy.data.meshes.new('Receiver mesh')
mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
mesh.materials.append(material('Physical Principled', image('Receiver source')))
uv = mesh.uv_layers.new(name='SimpleBake')
for item, coord in zip(uv.data, ((0, 0), (1, 0), (1, 1), (0, 1))): item.uv = coord
receiver = bpy.data.objects.new('Receiver', mesh)
scene.collection.objects.link(receiver)
preview = material('Configured preview', image('Preview source'))
controls = world_tools.create_controls(scene.world, image('World source'), rotation=math.pi,
    lighting_strength=6, sky_lift=2, lift_rolloff=1, visible_strength=1, connect=True)
tree = scene.world.node_tree
output = next(n for n in tree.nodes if n.type == 'OUTPUT_WORLD' and n.is_active_output)
mix = tree.nodes.new('ShaderNodeMixShader')
ray = tree.nodes.new('ShaderNodeLightPath')
value = tree.nodes.new('ShaderNodeValue')
value.outputs[0].default_value = 1
math_node = tree.nodes.new('ShaderNodeMath')
math_node.operation = 'MULTIPLY'
math_node.inputs[1].default_value = 1
other_controls = tree.nodes.new('ShaderNodeGroup')
other_controls.node_tree = controls.node_tree
other_controls.name = 'Other controls node'
source = out/'fixture.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(source))
source_digest = sha(source)
config = {'schema_version': 1, 'scene': scene.name, '_source': str(source), '_root': str(out),
    '_candidate': str(out/'candidate'), 'size': 16, 'quality': 'preview', 'device': 'CPU',
    'samples': {'DIRECT': 1, 'INDIRECT': 1}, 'uv': 'SimpleBake', 'receivers': {'names': ['Receiver']},
    'preview_material': preview.name, 'image_dependency_scope': 'participants', 'source_images': declared,
    'images': {name: 'pending_'+name+'.exr' for name in ('direct', 'indirect', 'island_ids', 'combined', 'colour')}}
worker.setup(config)
expected_controls = world_tools.settings(controls)
successes = []
rejections = []
nonrealizable_cases = []


def split():
    for node in (controls, output, mix, ray): node.mute = False
    tree.links.new(controls.outputs['Lighting'], mix.inputs[1])
    tree.links.new(controls.outputs['Visible Sky'], mix.inputs[2])
    tree.links.new(ray.outputs['Is Camera Ray'], mix.inputs[0])
    tree.links.new(mix.outputs[0], output.inputs['Surface'])


def snapshot():
    return {'links': sorted((link.from_node.name, list(link.from_node.outputs).index(link.from_socket),
                             link.to_node.name, list(link.to_node.inputs).index(link.to_socket)) for link in tree.links),
            'route_nodes_muted': {node.name: bool(node.mute) for node in (controls, output, mix, ray)},
            'controls': world_tools.settings(controls)}


def preflight_case(name, recipe, *, reject=False, expected_route=None, expected_error=None):
    before = snapshot()
    try:
        report = worker.preflight(recipe)
    except ValueError as error:
        assert reject, (name, str(error))
        if expected_error: assert expected_error in str(error), (name, str(error))
        rejections.append({'case': name, 'error': str(error)})
    else:
        assert not reject, 'Unsupported route accepted: ' + name
        assert report['physical_world_routing']['route'] == expected_route, (name, report)
        successes.append({'case': name, 'report': report})
    assert snapshot() == before, 'Preflight changed the World graph/controls: ' + name
    assert world_tools.settings(controls) == expected_controls
    assert sha(source) == source_digest


preflight_case('default direct', config, expected_route='direct')
preflight_case('explicit direct', dict(config, physical_world_routing='direct'), expected_route='direct')
# Opt-in remains compatible with the exact direct Lighting route as well.
opted = dict(config, physical_world_routing='camera-separated')
preflight_case('opted direct', opted, expected_route='direct')
split()
preflight_case('default rejects camera mix', config, reject=True, expected_error='Physical baking requires the World Lighting output')
preflight_case('explicit direct rejects camera mix', dict(config, physical_world_routing='direct'), reject=True,
               expected_error='Physical baking requires the World Lighting output')
preflight_case('exact camera split', opted, expected_route='camera-separated')
tree.links.new(controls.outputs['Visible Sky'], mix.inputs[1])
tree.links.new(controls.outputs['Lighting'], mix.inputs[2])
preflight_case('swapped shader inputs', opted, reject=True)
split()
tree.links.new(ray.outputs['Is Diffuse Ray'], mix.inputs[0])
preflight_case('noncamera Light Path factor', opted, reject=True)
split()
tree.links.remove(mix.inputs[0].links[0])
mix.inputs[0].default_value = 1
preflight_case('unlinked custom constant factor', opted, reject=True)
split()
tree.links.new(value.outputs[0], mix.inputs[0])
preflight_case('constant Value node factor', opted, reject=True)
split()
tree.links.new(ray.outputs['Is Camera Ray'], math_node.inputs[0])
tree.links.new(math_node.outputs[0], mix.inputs[0])
preflight_case('wrapped camera factor', opted, reject=True)
for node in (controls, output, mix, ray):
    split()
    before = snapshot()
    node.mute = True
    if node == output and not node.mute:
        # Blender 5.2 immediately clears OUTPUT_WORLD.mute despite writable RNA.
        # Record the setter limitation; this did not construct a muted route.
        assert snapshot() == before, 'Unsupported output mute setter changed links/controls'
        nonrealizable_cases.append({'case': 'muted OUTPUT_WORLD', 'requested_mute': True,
            'actual_mute': bool(node.mute), 'rna_readonly': node.bl_rna.properties['mute'].is_readonly,
            'world_links_and_controls_unchanged': True,
            'limit': 'Native setter clears mute; negative route cannot be constructed on this Blender version'})
        continue
    assert node.mute, 'Fixture failed to construct muted node: ' + node.type
    preflight_case('muted '+node.type, opted, reject=True)
split()
tree.links.new(other_controls.outputs['Lighting'], mix.inputs[1])
preflight_case('Lighting from other controls', opted, reject=True)
split()
tree.links.new(other_controls.outputs['Visible Sky'], mix.inputs[2])
preflight_case('Visible Sky from other controls', opted, reject=True)
split()
tree.links.new(controls.outputs['Visible Sky'], output.inputs['Surface'])
preflight_case('Visible Sky direct instead of supported route', opted, reject=True)
split()
preflight_case('unknown route enum', dict(config, physical_world_routing='skip'), reject=True,
               expected_error='physical_world_routing')
preflight_case('exact split still intact', opted, expected_route='camera-separated')
assert 'scipy' not in sys.modules
(out/'result.json').write_text(json.dumps({'status': 'passed', 'successes': successes, 'rejections': rejections,
    'nonrealizable_cases': nonrealizable_cases,
    'source_sha256': source_digest, 'world_graph_and_controls_preserved_per_case': True,
    'final_route': snapshot(), 'cpu_preflight_only_no_bake': True}, indent=2), encoding='utf8')
print('PHYSICAL_WORLD_ROUTING_BLENDER_PASSED', str(out), flush=True)
