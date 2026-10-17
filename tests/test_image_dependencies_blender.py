"""Actual CPU preflight with selected node-image closure and retained historical data."""
import json
import sys
from pathlib import Path
import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'src/babylon_js/environment_tools'))
import worker
from common import sha
from babylon_js.environment_controls import create_controls

assert bpy.app.background, 'Never run this fixture in a live session'
out = Path(sys.argv[sys.argv.index('--')+1]).resolve()
out.mkdir(parents=True, exist_ok=False)
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.name = 'Physical dependency fixture'
declared = []
current_images = []
historical_images = []


def file_image(label, *, current=True, packed=False):
    path = out/(label+'.exr')
    worker.write_exr(path, np.ones((16, 16, 4), np.float32))
    image = bpy.data.images.load(str(path), check_existing=False)
    image.name = label
    if packed:
        image.pack()
    if current:
        current_images.append(image)
        if not packed: declared.append(path.name)
    else:
        historical_images.append(image)
    return image


def nested_image(tree, image, label):
    inner = bpy.data.node_groups.new(label+' inner', 'ShaderNodeTree')
    node = inner.nodes.new('ShaderNodeTexImage')
    node.image = image
    outer = bpy.data.node_groups.new(label+' outer', 'ShaderNodeTree')
    group = outer.nodes.new('ShaderNodeGroup')
    group.node_tree = inner
    for _ in range(2):
        node = tree.nodes.new('ShaderNodeGroup')
        node.node_tree = outer
    return inner


def material(label, image, *, nested=False):
    result = bpy.data.materials.new(label)
    result.use_nodes = True
    if nested:
        nested_image(result.node_tree, image, label)
    else:
        node = result.node_tree.nodes.new('ShaderNodeTexImage')
        node.image = image
    return result


def mesh_object(name, material, collection):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    mesh.materials.append(material)
    uv = mesh.uv_layers.new(name='SimpleBake')
    for item, coord in zip(uv.data, ((0, 0), (1, 0), (1, 1), (0, 1))): item.uv = coord
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    return obj


role_images = {}
for role in ('Receiver', 'Contributor', 'Glass', 'Emitter'):
    image = file_image(role+' source')
    role_images[role] = image
    mesh_object(role, material(role+' material', image, nested=role in ('Receiver', 'Glass')), scene.collection)
world_source = file_image('World source')
create_controls(scene.world, world_source, connect=True)
world_nested = file_image('Disconnected nested World source')
nested_image(scene.world.node_tree, world_nested, 'World nested')
preview_source = file_image('Configured preview source')
preview = material('Configured preview material', preview_source, nested=True)
packed = file_image('Packed current source', packed=True)
nested_image(preview.node_tree, packed, 'Packed preview')
generated = bpy.data.images.new('Pending generated direct', 16, 16, float_buffer=True)
generated.source = 'FILE'
generated.filepath = str(out/'pending_direct.exr')
current_images.append(generated)
nested_image(preview.node_tree, generated, 'Pending output')
unused = file_image('Fake-user old image', current=False)
unused.use_fake_user = True
unassigned = file_image('Unassigned old material image', current=False)
material('Unassigned historical material', unassigned, nested=True)
other_scene = bpy.data.scenes.new('Unselected historical scene')
other_material_image = file_image('Other-scene material image', current=False)
mesh_object('Other-scene object', material('Other-scene material', other_material_image), other_scene.collection)
other_world_image = file_image('Other-scene World image', current=False)
other_scene.world = bpy.data.worlds.new('Other-scene World')
other_scene.world.use_nodes = True
nested_image(other_scene.world.node_tree, other_world_image, 'Other World')
metadata_image = file_image('Metadata-only old capture', current=False)
scene.world['bjs_environment_image'] = metadata_image.filepath
source = out/'fixture.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(source))
source_digest = sha(source)
config = {'schema_version': 1, 'size': 16, 'quality': 'preview', 'device': 'CPU',
    'scene': scene.name, '_source': str(source), '_root': str(out), '_candidate': str(out/'candidate'),
    'uv': 'SimpleBake', 'samples': {'DIRECT': 1, 'INDIRECT': 1},
    'receivers': {'names': ['Receiver']}, 'contributors': {'names': ['Contributor']},
    'glass': {'names': ['Glass']}, 'visible_emitters': {'names': ['Emitter']},
    'preview_material': preview.name, 'source_images': declared,
    'images': {'direct': 'pending_direct.exr', 'indirect': 'pending_indirect.exr',
               'island_ids': 'pending_ids.exr', 'combined': 'pending_combined.exr', 'colour': preview_source.filepath}}
worker.setup(config)
failures_checked = []
for label, recipe in (('implicit-all', config), ('explicit-all', dict(config, image_dependency_scope='all'))):
    try:
        worker.preflight(recipe)
        raise AssertionError('Default/all scope must preserve undeclared-history failure')
    except ValueError as error:
        assert 'Declare source image' in str(error), str(error)
        failures_checked.append(label)
scoped = dict(config, image_dependency_scope='participants')
report = worker.preflight(scoped)
dependencies = report['image_dependencies']
(out/'dependency-baseline.json').write_text(json.dumps(report, indent=2), encoding='utf8')
assert dependencies['scope'] == 'participants'
assert {row['name'] for row in dependencies['required_images']} == {image.name for image in current_images}
assert {image.name for image in historical_images} <= {row['name'] for row in dependencies['ignored_images']}
assert all('retained saved data' in row['reason'] for row in dependencies['ignored_images'])
assert 'disconnected nodes' in dependencies['limits']
assert 'scipy' not in sys.modules, 'Native preflight must not require external SciPy'
# Missing declarations and missing files remain errors in every participating root,
# including nested/disconnected images and the configured preview material.
case_diagnostics = []
for label, image in [*role_images.items(), ('World', world_source), ('Nested World', world_nested), ('Preview', preview_source)]:
    # Blender's // paths are not OS paths (Windows may interpret them as UNC).
    # Match declarations by the same resolved source-root identity as preflight.
    resolved = Path(bpy.path.abspath(image.filepath)).resolve()
    relative = resolved.relative_to(out).as_posix()
    remaining = [name for name in declared if (out/name).resolve() != resolved]
    generated_paths = {worker.binding(scoped, name) for name in ('direct', 'indirect', 'island_ids', 'combined')}
    generated_paths.update(Path(scoped['_candidate'])/'masters'/name
                           for name in ('direct_final.exr', 'indirect_final.exr', 'island_ids.exr', 'combined.exr'))
    case = {'label': label, 'image': image.name, 'source': image.source,
        'packed': bool(image.packed_file), 'raw_filepath': image.filepath,
        'raw_os_basename': Path(image.filepath).name, 'resolved_path': str(resolved),
        'source_root_relative': relative, 'declared_before': list(declared),
        'declared_after': remaining,
        'resolved_declared_before': [str((out/name).resolve()) for name in declared],
        'resolved_declared_after': [str((out/name).resolve()) for name in remaining],
        'generated_paths': sorted(map(str, generated_paths))}
    case_diagnostics.append(case)
    (out/'dependency-cases-debug.json').write_text(json.dumps(case_diagnostics, indent=2), encoding='utf8')
    assert image.source == 'FILE' and not image.packed_file, case
    assert len(remaining) == len(declared)-1, 'Fixture must remove exactly this dependency declaration: ' + str(case)
    assert resolved not in {(out/name).resolve() for name in remaining}, case
    assert resolved not in generated_paths, 'Fixture source must not be a generated output: ' + str(case)
    recipe = dict(scoped, source_images=remaining)
    try:
        unexpected = worker.preflight(recipe)
        case['unexpected_preflight_result'] = unexpected
        (out/'dependency-cases-debug.json').write_text(json.dumps(case_diagnostics, indent=2), encoding='utf8')
        raise AssertionError('Undeclared participating dependency must fail: ' + label)
    except ValueError as error:
        assert 'Declare source image' in str(error) and relative in str(error), str(error)
        failures_checked.append(label+'-undeclared')
    original = image.filepath
    missing_name = 'missing-'+relative
    image.filepath = str(out/missing_name)
    try:
        recipe = dict(scoped, source_images=[*declared, missing_name])
        try:
            worker.preflight(recipe)
            raise AssertionError('Missing participating dependency must fail: ' + label)
        except ValueError as error:
            assert 'Missing required source images' in str(error) and missing_name in str(error), str(error)
            failures_checked.append(label+'-missing')
    finally:
        image.filepath = original
assert {image.name for image in historical_images} <= set(bpy.data.images.keys())
assert all(Path(bpy.path.abspath(image.filepath)).is_file() for image in historical_images)
assert sha(source) == source_digest
(out/'result.json').write_text(json.dumps({'status': 'passed', 'scene': scene.name,
    'successful_participant_preflight': report, 'expected_failures_checked': failures_checked,
    'default_all_failure_preserved': True, 'historical_data_retained': True,
    'packed_and_generated_allowances_preserved': True, 'source_sha256': source_digest}, indent=2), encoding='utf8')
print('IMAGE_DEPENDENCY_SCOPE_BLENDER_PASSED', str(out), flush=True)
