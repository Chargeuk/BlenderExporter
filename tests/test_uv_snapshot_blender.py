"""Read-only evaluated UV snapshot fixture, no UV edits or bake/render invocation."""
import importlib.util
import hashlib
import json
import sys
from pathlib import Path
import bpy

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT/'src/babylon_js/workflow_tools'
sys.path.insert(0, str(TOOLS))
from uv_audit import audit
spec = importlib.util.spec_from_file_location('fixture_inspect_blender', TOOLS/'inspect_blender.py')
inspector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspector)
assert bpy.app.background, 'Never run the fixture in a live scene'
out = Path(sys.argv[sys.argv.index('--')+1]).resolve()
out.mkdir(parents=True, exist_ok=False)
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.name = 'Physical UV fixture'
objects = []
for index, name in enumerate(('A', 'B', 'Explicitly omitted helper')):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    mesh.normals_split_custom_set([(0, 0, 1)]*4)
    for layer_name in ('UVMap', 'SimpleBake'):
        layer = mesh.uv_layers.new(name=layer_name)
        left, right = ((.05, .4) if index == 0 else (.6, .95)) if layer_name == 'SimpleBake' else (0, 1)
        for item, uv in zip(layer.data, ((left, .1), (right, .1), (right, .9), (left, .9))): item.uv = uv
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    obj.location.x = index*2
    obj.rotation_euler = (.1, .2, .3)
    obj.modifiers.new('Evaluate triangulation', 'TRIANGULATE')
    objects.append(obj)
source = out/'fixture.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(source))


def state():
    return {'scene': bpy.context.scene.name, 'active': bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else None,
        'mode': bpy.context.mode, 'selection': sorted(o.name for o in bpy.context.selected_objects),
        'objects': [{'name': o.name, 'matrix': [list(row) for row in o.matrix_world],
            'vertices': [list(v.co) for v in o.data.vertices], 'loops': [loop.vertex_index for loop in o.data.loops],
            'normals': [list(normal.vector) for normal in o.data.corner_normals],
            'uvs': [(layer.name, [list(value.uv) for value in layer.data], layer.active_render) for layer in o.data.uv_layers],
            'active_uv': o.data.uv_layers.active_index,
            'modifiers': [(modifier.name, modifier.type) for modifier in o.modifiers]} for o in objects]}


before = state()
snapshot = inspector.snapshot_uv2(['A', 'B'], uv='SimpleBake', uv1='UVMap',
    expected_uv_order=['UVMap', 'SimpleBake'], expected_scene=scene.name,
    selection_metadata={'names': ['A', 'B'], 'explicitly_omitted': ['Explicitly omitted helper']})
assert state() == before
second = inspector.snapshot_uv2(['B', 'A'], expected_uv_order=['UVMap', 'SimpleBake'])
assert state() == before
assert snapshot['provenance']['geometry_uv1_identity_sha256'] == second['provenance']['geometry_uv1_identity_sha256']
assert snapshot['provenance']['uv2_identity_sha256'] == second['provenance']['uv2_identity_sha256']
assert snapshot['provenance']['receiver_names'] == ['A', 'B']
assert len(snapshot['triangles']) == 4
assert all(row['loops'] and row['vertices'] and 'polygon' in row and 'chart' in row for row in snapshot['triangles'])
assert all(row['evaluated_triangles'] == 2 and row['uv_order'] == ['UVMap', 'SimpleBake'] for row in snapshot['objects'])
report = audit(snapshot, pixels=2048, overlap_area_tolerance=1e-12)
assert report['status'] == 'passed' and report['totals']['charts'] == 2
assert report['totals']['positive_overlap_pairs'] == 0
for kwargs in ({'expected_uv_order': ['SimpleBake', 'UVMap']}, {'expected_scene': 'Wrong scene'}, {'uv1': 'SimpleBake'}):
    try:
        inspector.snapshot_uv2(['A', 'B'], **kwargs)
        raise AssertionError('Invalid snapshot contract must fail')
    except ValueError:
        pass
assert state() == before
assert 'scipy' not in sys.modules
assert hashlib.sha256(source.read_bytes()).hexdigest() == snapshot['provenance']['source_file_sha256']
(out/'snapshot.json').write_text(json.dumps(snapshot, indent=2), encoding='utf8')
(out/'audit.json').write_text(json.dumps(report, indent=2), encoding='utf8')
(out/'result.json').write_text(json.dumps({'status': 'passed', 'source_sha256': snapshot['provenance']['source_file_sha256'],
    'read_only_scene_geometry_normals_uv_order_and_selection': True, 'snapshot_triangles': 4,
    'snapshot_objects': ['A', 'B'], 'evaluated_modifier_triangles_preserved': True,
    'audit_totals': report['totals']}, indent=2), encoding='utf8')
print('UV_SNAPSHOT_BLENDER_PASSED', str(out), flush=True)
