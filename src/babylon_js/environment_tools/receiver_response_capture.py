"""COLOR-only measurement in the saved-scene background worker, never a live scene."""
import json
from contextlib import contextmanager
import bpy
import numpy as np

from common import save_json, sha
from receiver_response import validate_settings, save_new_array


@contextmanager
def capped_materials(obj, cap):
    """Clone only this receiving object's data; shared transport materials remain physical."""
    original_mesh = obj.data
    temporary_mesh = original_mesh.copy()
    clones = []
    obj.data = temporary_mesh
    try:
        for material in list(temporary_mesh.materials):
            clone = material.copy()
            clones.append(clone)
            principal = [n for n in clone.node_tree.nodes if n.type == 'BSDF_PRINCIPLED']
            if len(principal) != 1:
                raise ValueError('Response requires one Principled node: ' + material.name)
            socket = principal[0].inputs['Metallic']
            minimum = clone.node_tree.nodes.new('ShaderNodeMath')
            minimum.operation = 'MINIMUM'
            minimum.inputs[1].default_value = cap
            if socket.is_linked:
                clone.node_tree.links.new(socket.links[0].from_socket, minimum.inputs[0])
            else:
                minimum.inputs[0].default_value = socket.default_value
            clone.node_tree.links.new(minimum.outputs[0], socket)
        temporary_mesh.materials.clear()
        for material in clones:
            temporary_mesh.materials.append(material)
        yield clones
    finally:
        obj.data = original_mesh
        bpy.data.meshes.remove(temporary_mesh)
        for material in clones:
            bpy.data.materials.remove(material)


def verify_physical_capture(c, root, tint):
    if c.get('receiver_response',{}).get('enabled'):
        path=root/'response-capture/capture.provenance.json'
        if not path.is_file():raise ValueError('Capture receiver response before native transport')
        record=json.loads(path.read_text())
        if record.get('physical_colour_adjustment')!=tint or record.get('scene')!=bpy.context.scene.name:
            raise ValueError('Receiver response physical tint/scene differs from native transport')


def capture(c, root, stage):
    from worker import setup,preflight
    from physical_colour_adjustment import physical_materials
    setup(c);preflight(c)
    with physical_materials(c) as tint:
        result=_capture(c,root,stage,tint)
        result.update(scene=bpy.context.scene.name,physical_colour_adjustment=tint)
        return result


def _capture(c, root, stage, tint):
    from worker import select, evaluate_receivers, target_image, raw_pixels
    from ao import geometry_identity
    from babylon_js.agx_lightmap import bake_albedo
    if not bpy.app.background:
        raise RuntimeError('Receiver response capture requires a disposable background Blender process')
    if not validate_settings(c):
        raise ValueError('Enable receiver_response before capturing material response')
    scene = bpy.context.scene
    receivers = select(c, 'receivers')
    contributors = select(c, 'contributors')
    caps = select(c, 'metal_receivers')
    uv = c.get('uv', 'SimpleBake')
    record = {'schema_version': 1, 'kind': 'measured-receiver-material-response',
        'source_sha256': sha(c['_source']), 'geometry': geometry_identity(c),
        'scene':scene.name,'physical_colour_adjustment':tint,
        'config_sha256': sha(c['_config']), 'size': c['size'], 'uv': uv,
        'blender_version': bpy.app.version_string,
        'receivers': [o.name for o in receivers], 'contributors': [o.name for o in contributors],
        'cap_objects': [o.name for o in caps], 'metallic_cap': c.get('metallic_cap', .8),
        'materials': [{'object': o.name, 'slots': [m.name for m in o.data.materials],
            'polygon_material_indices': [p.material_index for p in o.data.polygons]} for o in receivers],
        'identity_policy': 'Saved source SHA256 covers material graphs, assignments and source scene; build inputs cover external source-image bytes.',
        'coverage_basis': 'Native COLOR-excluded zero-margin coverage; COLOR alpha is not ownership',
        'base_capture': {'type': 'EMIT', 'samples': 1, 'adaptive': False, 'denoising': False,
                         'margin': 0, 'use_clear': False, 'uv_layer': uv, 'device': 'CPU'},
        'operations': []}
    base = bake_albedo(bpy.context, receivers, stage/'source_base_colour.exr', c['size'], uv)
    save_new_array(stage/'source_base_colour.npy', base)
    for obj in scene.objects:
        if obj.type == 'MESH': obj.hide_render = obj not in receivers + contributors
        if obj.type == 'LIGHT': obj.hide_render = True
    scene.cycles.samples = 1
    scene.cycles.use_adaptive_sampling = False
    scene.cycles.use_denoising = False
    def colour(image, names, phase):
        arguments = {'type': 'DIFFUSE', 'pass_filter': {'COLOR'}, 'margin': 0,
                     'use_clear': True, 'use_selected_to_active': False, 'uv_layer': uv}
        operation = {**arguments, 'pass_filter': ['COLOR'], 'receivers': names, 'phase': phase,
            'samples': scene.cycles.samples, 'adaptive': scene.cycles.use_adaptive_sampling,
            'denoising': scene.cycles.use_denoising, 'device': scene.cycles.device,
            'active_object': bpy.context.view_layer.objects.active.name,
            'scene':scene.name,'physical_colour_adjustment':tint,
            'visible_meshes': sorted(o.name for o in scene.objects if o.type == 'MESH' and not o.hide_render),
            'visible_lights': sorted(o.name for o in scene.objects if o.type == 'LIGHT' and not o.hide_render)}
        record['operations'].append(operation)
        save_json(stage/'capture.provenance.json', record)
        bpy.ops.object.bake(**arguments)
        return raw_pixels(image)
    copies, catalogue = evaluate_receivers(c)
    save_json(stage/'islands.json', catalogue)
    for obj in receivers: obj.hide_render = True
    bpy.ops.object.select_all(action='DESELECT')
    for obj in copies:
        obj.hide_set(False)
        obj.select_set(True)
    bpy.context.view_layer.objects.active = copies[0]
    bpy.ops.object.join()
    proxy = bpy.context.object
    restore = proxy.matrix_world.to_3x3().transposed()
    wanted = [(restore @ v.vector).normalized() for v in proxy.data.attributes['_environment_world_normal'].data]
    proxy.data.normals_split_custom_set(wanted)
    error = max(((a.vector-b).length for a, b in zip(proxy.data.corner_normals, wanted)), default=0)
    if error > .001: raise ValueError('Response proxy normals changed')
    image = target_image(c, list(proxy.data.materials))
    proxy.data.uv_layers.active_index = proxy.data.uv_layers.find(uv)
    save_new_array(stage/'diffuse_colour.npy', colour(image, record['receivers'], 'uncapped'))
    from native_ownership import ownership_method
    if ownership_method(c)=='native-island-id':
        from native_ownership import bake_native_ids, validate_native_ids
        native_ids, native_settings=bake_native_ids(c,proxy,catalogue)
        _, native_report=validate_native_ids(native_ids,catalogue)
        save_new_array(stage/'native_island_ids.npy',native_ids)
        record['native_ownership']=dict(native_report,settings=native_settings)
    bpy.data.objects.remove(proxy, do_unlink=True)
    bpy.data.images.remove(image)
    for obj in receivers: obj.hide_render = False
    patches = []
    for index, obj in enumerate(caps):
        with capped_materials(obj, c.get('metallic_cap', .8)) as materials:
            image = target_image(c, materials)
            try:
                bpy.ops.object.select_all(action='DESELECT')
                obj.hide_set(False)
                obj.select_set(True)
                bpy.context.view_layer.objects.active = obj
                pixels = colour(image, [obj.name], 'metal_cap')
                filename = f'cap_response_{index}.npy'
                save_new_array(stage/filename, pixels)
                patches.append({'object': obj.name, 'file': filename})
            finally:
                bpy.data.images.remove(image)
    save_json(stage/'patches.json', patches)
    record['joined_normal_max_error'] = error
    record['files'] = {p.name: sha(p) for p in stage.iterdir() if p.suffix in ('.npy', '.exr', '.json')
                       and p.name != 'capture.provenance.json'}
    save_json(stage/'capture.provenance.json', record)
    return {'capture_operations': len(record['operations']), 'cap_receiver_count': len(caps),
            'joined_normal_max_error': error, 'physical_transport_performed': False}
