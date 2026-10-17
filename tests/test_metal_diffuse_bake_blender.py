"""Factory-startup CPU fixture: actual existing bake path, coverage and restoration.

Blender --background --factory-startup --python tests/test_metal_diffuse_bake_blender.py -- OUT
Never open an authoring scene for this test. OUT must be a fresh directory.
"""
from pathlib import Path
import sys
from unittest.mock import patch
import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'src/babylon_js/environment_tools'))
import worker
from common import normalize_metal_diffuse

out = Path(sys.argv[sys.argv.index('--') + 1]).resolve()
out.mkdir(parents=True, exist_ok=False)
scene = bpy.context.scene
for obj in list(scene.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
scene.world.use_nodes = True
next(n for n in scene.world.node_tree.nodes if n.type == 'BACKGROUND').inputs['Color'].default_value = (0, 0, 0, 1)
scene.cycles.seed = 17


def receiver(name, offset, u0, metallic):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    obj = bpy.data.objects.new(name, mesh); scene.collection.objects.link(obj)
    obj.location.x = offset
    uv = mesh.uv_layers.new(name='SimpleBake')
    for corner, co in zip(uv.data, ((u0, .1), (u0+.3, .1), (u0+.3, .9), (u0, .9))):
        corner.uv = co
    mat = bpy.data.materials.new(name); mat.use_nodes = True
    principal = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    principal.inputs['Base Color'].default_value = (.4, 0, .1, 1)
    principal.inputs['Metallic'].default_value = metallic
    principal.inputs['Roughness'].default_value = .5
    mesh.materials.append(mat)
    return obj


metal = receiver('Metal', 0, .1, .75)
nonmetal = receiver('Nonmetal', 2, .6, 0)
twin = metal.copy(); twin.name = 'SharedDataTransport'; twin.location.x = 8; scene.collection.objects.link(twin)
light_data = bpy.data.lights.new('BakeSun', 'SUN'); light_data.energy = 3
light = bpy.data.objects.new('BakeSun', light_data); scene.collection.objects.link(light)
source = out / 'fixture.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(source))
original_data = {obj: obj.data for obj in (metal, nonmetal, twin)}
original_materials = {obj: tuple(obj.data.materials) for obj in original_data}
physical_materials = set(mat for materials in original_materials.values() for mat in materials)


def graph_state(mat):
    tree = mat.node_tree
    return (tuple((node.as_pointer(), node.type) for node in tree.nodes),
            tuple((link.from_node.as_pointer(), link.from_socket.identifier,
                   link.to_node.as_pointer(), link.to_socket.identifier) for link in tree.links),
            tree.nodes.active.as_pointer() if tree.nodes.active else None)


def run(name, option=None):
    root = out / name; (root / 'masters').mkdir(parents=True)
    stage = root / 'bake'; stage.mkdir()
    config = {'_source': str(source), 'size': 32, 'uv': 'SimpleBake', 'device': 'CPU', 'quality': 'preview',
              'samples': {'DIRECT': 1, 'INDIRECT': 1}, 'metallic_cap': .8,
              'receivers': {'names': [metal.name, nonmetal.name]}, 'contributors': {'names': [twin.name]},
              'metal_receivers': {'names': [metal.name]}, 'bake_lights': {'names': [light.name]}}
    if option is not None: config['preserve_metal_diffuse_weight'] = option
    pre_cap = {}
    original_target = worker.target_image
    def capture_target(config, materials):
        image = original_target(config, materials)
        if set(materials) == physical_materials:
            # Legacy joined targets remain in this disposable process. Compare
            # local restoration to this actual pre-cap state, including active nodes.
            pre_cap.update({mat: graph_state(mat) for mat in physical_materials})
        return image
    try:
        with patch.object(worker, 'preflight', return_value={}), patch.object(worker, 'target_image', side_effect=capture_target):
            worker.bake(config, root, stage)
    finally:
        for mat, state in pre_cap.items():
            assert graph_state(mat) == state, 'Local patch changed physical shader: ' + mat.name
        for obj, data in original_data.items():
            assert obj.data == data and tuple(obj.data.materials) == original_materials[obj], obj.name
    for obj, data in original_data.items():
        assert obj.data == data and tuple(obj.data.materials) == original_materials[obj], obj.name
    return stage


legacy = run('legacy')
explicit = run('explicit-false', False)
corrected = run('opt-in', True)
import json
records = json.loads((corrected / 'effective_bake_settings.json').read_text())
assert all(row['pass_filter'] == [row['pass_name']] for row in records if row['phase'] == 'joined')
assert all(set(row['pass_filter']) == {row['pass_name'], 'COLOR'} for row in records if row['phase'] == 'metal_cap')
from babylon_js.agx_lightmap import read_exr
base = read_exr(corrected / 'metal_0_base.exr')
mask = base[..., 3] == 1
assert mask.any() and (~mask).any() and set(np.unique(base[..., 3])) == {0, 1}
for mode in ('direct', 'indirect'):
    # Enabling the correction must not change the joined nonmetal source pass.
    np.testing.assert_array_equal(np.load(legacy / (mode+'_raw.npy')), np.load(corrected / (mode+'_raw.npy')))
    with np.load(legacy / ('metal_0_'+mode+'.npz')) as data: old = data['pixels'].copy()
    with np.load(explicit / ('metal_0_'+mode+'.npz')) as data:
        np.testing.assert_array_equal(old, data['pixels'])
    with np.load(corrected / ('metal_0_'+mode+'_weighted.npz')) as data: weighted = data['pixels'].copy()
    with np.load(corrected / ('metal_0_'+mode+'.npz')) as data: fixed = data['pixels'].copy()
    np.testing.assert_array_equal(fixed[..., 3], base[..., 3])
    np.testing.assert_array_equal(fixed[~mask], 0)
    np.testing.assert_array_equal(fixed[mask, 1], 0)  # A black channel is still owned.
    np.testing.assert_allclose(fixed[mask, :3] * base[mask, :3], weighted[mask, :3], rtol=2e-6, atol=1e-6)
    if mode == 'direct':
        ratio = np.median(fixed[mask, 0] / old[mask, 0])
        assert .1 < ratio < .4, ratio  # Native weighting survives; no fitted multiplier.
    metadata = json.loads((corrected / 'metal_patches.json').read_text())
    row = next(row for row in metadata if row['pass'] == mode)
    assert row['division']['owned_pixels'] == int(mask.sum())
    assert row['division']['protected_base_channels'] == int(mask.sum())
    assert row['base_sha256'] and row['weighted_sha256'] and row['source_blend_sha256']

# Fail deliberately after native bake, while the receiver is using a cloned mesh/material.
with patch.object(worker, 'normalize_metal_diffuse', side_effect=ValueError('fixture division failure')):
    try:
        run('failure-restoration', True)
        raise AssertionError('Injected failure was swallowed')
    except ValueError as error:
        assert str(error) == 'fixture division failure'
for obj, data in original_data.items():
    assert obj.data == data and tuple(obj.data.materials) == original_materials[obj], obj.name
print('METAL_DIFFUSE_BAKE_PASSED', json.dumps({'legacy_default_exact': True, 'joined_nonmetal_exact': True,
      'owned_pixels': int(mask.sum()), 'native_weight_retained': True, 'black_channel_owned': True,
      'failure_restoration': True}))
