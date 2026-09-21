"""Background Blender regression: projection, real Basis encoder and staged export."""
import json
import math
import os
from pathlib import Path
import sys
from unittest.mock import patch

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import babylon_js
from babylon_js import skybox_export as sky
from babylon_js import ktx_export as ktx
from babylon_js import ktx_conversion as core
from babylon_js.json_exporter import JsonExporter

args = sys.argv[sys.argv.index('--')+1:]
OUT, BASIS, KTX = Path(args[0]).resolve(), args[1], args[2]
OUT.mkdir(parents=True, exist_ok=False)
babylon_js.register()
props = bpy.ops.export.bjs.get_rna_type().properties
assert props['skybox_size'].default == '1024'
assert {i.identifier for i in props['skybox_size'].enum_items} == {'256','512','1024','2048','4096'}
assert props['export_skybox'].default is False
assert sky.find_basisu(BASIS)[0]
assert not sky.find_basisu(str(OUT/'absent'))[0]
with patch.dict(os.environ, PATH=str(Path(BASIS).parent)):
    assert sky.find_basisu()[0]

# Analytic panorama: RGB encodes the direction of every input pixel. This
# detects swaps/mirrors/pole orientation and the horizontal wrap seam.
w, h = 1024, 512
lon, lat = np.meshgrid((np.arange(w)+.5)/w*2*math.pi, (np.arange(h)+.5)/h*math.pi)
direction = np.stack([np.sin(lon)*np.sin(lat), np.cos(lat), np.cos(lon)*np.sin(lat)], axis=-1)
linear = (direction+1)/2
srgb = np.where(linear <= .0031308, 12.92*linear, 1.055*linear**(1/2.4)-.055)
ktx._write_png(OUT/'directions.png', np.rint(srgb*255).astype(np.uint8))
panorama, hdr = sky.read_panorama(OUT/'directions.png')
assert not hdr
errors = {}
for face in sky.FACE_NAMES:
    d = sky.face_directions(face, 65)
    expected = (d/np.linalg.norm(d,axis=-1,keepdims=True)+1)/2
    actual = sky.sample_panorama(panorama, d)
    error = float(np.max(np.abs(actual-expected)))
    assert error < .005, (face,error)
    errors[face] = error
    # A +90-degree panorama rotation sends +Z to +X, +X to -Z.
    rotated = sky.sample_panorama(panorama, d, rotation=270)
    turned = d[..., [2,1,0]].copy(); turned[...,2] *= -1
    expected = (turned/np.linalg.norm(turned,axis=-1,keepdims=True)+1)/2
    assert np.max(np.abs(rotated-expected)) < .005
seam = sky.sample_panorama(panorama,np.array([[[.00001,0,1],[-.00001,0,1]]]))
assert np.max(np.abs(seam[0,0]-seam[0,1])) < .0001
assert np.allclose(sky.sample_panorama(panorama,np.array([[[0,1,0],[0,-1,0]]]))[0,:,1], [1,0],atol=.005)

# HDR display controls: compressed highlights versus explicit clipping.
test = np.array([[[4.,4.,4.]]])
compressed, clip = sky.display_pixels(test, True, 0, 'REINHARD')
standard, clip2 = sky.display_pixels(test, True, 0, 'STANDARD')
assert clip == 0 and clip2 == 1 and np.all(compressed < standard)
assert sky.display_pixels(test, True, -2, 'STANDARD')[1] == 0
ktx._write_png(OUT/'bad.png',np.zeros((32,32,3),dtype=np.uint8))
try:
    sky.read_panorama(OUT/'bad.png')
    raise AssertionError('Invalid aspect ratio accepted')
except ValueError:
    pass

# Preserve geometry and source marker identities; reject conflicting markers.
scene = bpy.context.scene
cube = scene.objects['Cube']
scene.world.usePBRMaterials = True
scene.world.inlineTextures = False
scene.world.textureDir = 'original_texture_dir'
scene.camera = None
old_marker = bpy.data.objects.new('hasskybox', None)
scene.collection.objects.link(old_marker)
cube.parent = old_marker
objects = [cube, old_marker]
plain = JsonExporter(); plain.execute(bpy.context,str(OUT/'plain.babylon'),objects)
assert not plain.fatalError
before = core.read_json(OUT/'plain.babylon')
state = (set(bpy.data.objects.keys()),set(bpy.data.images.keys()),old_marker.name,scene.world.textureDir)
source_hash = core.sha(OUT/'directions.png')
options = dict(convert_materials=False,skybox=dict(executable=BASIS,image=str(OUT/'directions.png'),size=256))
target = OUT/'delivery/fixture.babylon'
exporter = JsonExporter(); exporter.execute(bpy.context,str(target),objects,ktx_options=options)
assert not exporter.fatalError,exporter.fatalError
after = core.read_json(target)
assert next(n for n in before['meshes'] if n['name']=='Cube') == next(n for n in after['meshes'] if n['name']=='Cube')
marker = next(n for n in after['meshes'] if n['name']=='hasskyboxbasis')
assert marker['id'] == old_marker.name
assert state == (set(bpy.data.objects.keys()),set(bpy.data.images.keys()),old_marker.name,scene.world.textureDir)
assert source_hash == core.sha(OUT/'directions.png')
assert exporter.ktx_report['skybox']['face_size'] == 256
model_hash, basis_hash = core.sha(target), core.sha(target.parent/'cubemap.basis')
with patch.object(sky,'prepare_faces',side_effect=RuntimeError('simulated preparation failure')):
    failed = JsonExporter(); failed.execute(bpy.context,str(target),objects,ktx_options=options)
    assert 'simulated preparation failure' in failed.fatalError
assert core.sha(target) == model_hash and core.sha(target.parent/'cubemap.basis') == basis_hash
# An actual encoder error must likewise retain both previous outputs.
real_run = sky.subprocess.run
def failed_encoder(command, **kwargs):
    if '-output_file' in command:
        return sky.subprocess.CompletedProcess(command, 1, '', 'simulated encoder failure')
    return real_run(command, **kwargs)
with patch.object(sky.subprocess,'run',side_effect=failed_encoder):
    failed = JsonExporter(); failed.execute(bpy.context,str(target),objects,ktx_options=options)
    assert 'encoding failed' in failed.fatalError
assert core.sha(target) == model_hash and core.sha(target.parent/'cubemap.basis') == basis_hash
try:
    sky.set_marker({'meshes':[{'name':'hasskybox'},{'name':'hasskyboxbasis'}]})
    raise AssertionError('Ambiguous markers accepted')
except ValueError:
    pass

# World suggestion traverses only the active graph; never picks disconnected images.
world = scene.world; world.use_nodes=True
nodes = world.node_tree.nodes
env = nodes.new('ShaderNodeTexEnvironment')
env.image = bpy.data.images.load(str(OUT/'directions.png'),check_existing=False)
assert sky.world_panorama(bpy.context) == ''
background = next(n for n in nodes if n.type=='BACKGROUND')
world.node_tree.links.new(env.outputs['Color'],background.inputs['Color'])
assert sky.world_panorama(bpy.context) == str((OUT/'directions.png').resolve())

# Real UI operator, skybox + material conversion, using an existing texture.
material=bpy.data.materials.new('FixtureColour');material.use_nodes=True
bsdf=next(n for n in material.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
tex=material.node_tree.nodes.new('ShaderNodeTexImage');tex.image=env.image
material.node_tree.links.new(tex.outputs['Color'],bsdf.inputs['Base Color'])
cube.data.materials.clear();cube.data.materials.append(material)
bpy.ops.object.select_all(action='DESELECT');cube.select_set(True)
result = bpy.ops.export.bjs(filepath=str(OUT/'operator/fixture.babylon'),export_selected=True,
    convert_to_ktx2=True,ktx_executable=KTX,ktx_threads=2,export_skybox=True,
    basis_executable=BASIS,skybox_size='512',skybox_threads=2)
assert result == {'FINISHED'}
operator_model = core.read_json(OUT/'operator/fixture.babylon')
assert len([n for n in operator_model['meshes'] if n['name']=='hasskyboxbasis']) == 1
assert (OUT/'operator/directions.ktx2').is_file()
assert core.read_json(OUT/'operator/fixture.ktx-report.json')['skybox']['face_size'] == 512

# Missing tool rejects before creating a delivery package.
failed=JsonExporter();failed.execute(bpy.context,str(OUT/'missing/scene.babylon'),objects,
    ktx_options=dict(convert_materials=False,skybox=dict(executable=str(OUT/'absent'))))
assert failed.fatalError and not (OUT/'missing/scene.babylon').exists()
babylon_js.unregister();babylon_js.register();babylon_js.unregister()
(OUT/'result.json').write_text(json.dumps(dict(status='passed',projection_max_errors=errors,
    checks=['size selector/default','PATH and override','all six face directions','rotation','wrap seam and poles',
    'HDR display controls','invalid panorama rejection','real 256/512 Basis validation',
    'source/geometry/hierarchy preserved','delivery-only marker','failure preserves previous package',
    'unique connected World suggestion','combined KTX and skybox UI operator','registration cycle']),indent=2))
print('SKYBOX_BLENDER_TEST_PASSED',str(OUT),flush=True)
