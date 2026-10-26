"""Opt-in matched saved-camera PNG views from disposable physical/baked scenes."""
from contextlib import contextmanager
from pathlib import Path
import math
import re


def _number(value, name, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('camera_set requires finite ' + name)
    if (low is not None and value < low) or (high is not None and value > high):
        raise ValueError('camera_set ' + name + ' outside allowed range')


def validate_camera_set(c, *, diagnostic=False):
    """No Blender import; legacy capture recipes without camera_set remain unchanged."""
    capture = c.get('capture', {})
    if 'views' not in capture:
        return None
    spec = capture['views']
    if not isinstance(spec, dict):
        raise ValueError('capture.views must be an object')
    entries = spec.get('camera_set')
    if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
        raise ValueError('camera_set requires 1..100 explicit entries')
    if not isinstance(c.get('scene'), str) or not c['scene'].strip():
        raise ValueError('camera_set requires an exact saved scene name')
    dimensions = spec.get('dimensions')
    if not isinstance(dimensions, list) or len(dimensions) != 2 or any(
            isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 16384 for v in dimensions):
        raise ValueError('camera_set requires explicit [width,height] dimensions')
    samples = spec.get('samples')
    if isinstance(samples, bool) or not isinstance(samples, int) or not 1 <= samples <= 65536:
        raise ValueError('camera_set requires explicit positive samples')
    if spec.get('format', 'PNG') != 'PNG' or spec.get('color_depth', '8') != '8' or spec.get('color_mode', 'RGB') not in ('RGB', 'RGBA'):
        raise ValueError('camera_set supports only PNG8 RGB/RGBA')
    for key in ('include_glass', 'film_transparent', 'denoise'):
        if not isinstance(spec.get(key, key == 'include_glass'), bool):
            raise ValueError('camera_set ' + key + ' must be boolean')
    if spec.get('world_output', 'camera-separated') not in ('camera-separated', 'Lighting', 'Visible Sky'):
        raise ValueError('Invalid camera_set World branch')
    display = spec.get('display', {})
    if not isinstance(display, dict) or set(display) - {'view_transform', 'look', 'exposure', 'gamma'}:
        raise ValueError('Invalid camera_set display settings')
    for key in ('view_transform', 'look'):
        if key in display and (not isinstance(display[key], str) or not display[key]):
            raise ValueError('Invalid camera_set display ' + key)
    _number(display.get('exposure', 0), 'exposure', -128, 128)
    _number(display.get('gamma', 1), 'gamma', .001, 100)
    if 'frame' in spec and (isinstance(spec['frame'], bool) or not isinstance(spec['frame'], int)):
        raise ValueError('camera_set frame must be an integer')
    identities = set()
    delivery=spec.get('delivery_contract')
    if delivery is not None and (not isinstance(delivery,str) or not delivery.strip()):
        raise ValueError('delivery_contract must name a saved decoded-delivery contract')
    if diagnostic and delivery:
        raise ValueError('Solid diagnostics do not use a decoded-delivery contract')
    from delivery_views import delivery_channel_plan
    delivery_plan=delivery_channel_plan(spec)
    modes=('solid',) if diagnostic else (tuple(delivery_plan) if delivery else ('physical','physical-diffuse','baked'))
    allowed = {'id', 'camera', 'matrix_world', 'lens', 'mode', 'camera_properties'}
    camera_properties = {'type', 'sensor_width', 'sensor_fit', 'shift_x', 'shift_y', 'clip_start', 'clip_end', 'ortho_scale', 'dof_use', 'dof_fstop'}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - allowed:
            raise ValueError('Invalid camera_set entry fields')
        name = entry.get('id')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', name) or name in identities:
            raise ValueError('camera_set ids must be unique safe filename stems')
        identities.add(name)
        if entry.get('mode', spec.get('mode', 'baked')) not in modes:
            raise ValueError('camera_set mode differs from physical/physical-diffuse/baked or opt-in delivery contract')
        if not entry.get('camera') and 'matrix_world' not in entry:
            raise ValueError('camera_set requires a saved camera or explicit matrix_world')
        if 'camera' in entry and (not isinstance(entry['camera'], str) or not entry['camera'].strip()):
            raise ValueError('camera_set camera must be a nonempty saved object name')
        if 'matrix_world' in entry:
            matrix = entry['matrix_world']
            if not isinstance(matrix, list) or len(matrix) != 4 or any(not isinstance(row, list) or len(row) != 4 for row in matrix):
                raise ValueError('camera_set matrix_world must be 4x4')
            for row in matrix:
                for value in row:
                    _number(value, 'matrix_world')
            if any(abs(a - b) > 1e-8 for a, b in zip(matrix[3], [0, 0, 0, 1])):
                raise ValueError('camera_set matrix_world must be affine')
            a, b, d = matrix[:3]
            determinant = a[0] * (b[1] * d[2] - b[2] * d[1]) - a[1] * (b[0] * d[2] - b[2] * d[0]) + a[2] * (b[0] * d[1] - b[1] * d[0])
            if abs(determinant) < 1e-12:
                raise ValueError('camera_set matrix_world must be nonsingular')
        if not entry.get('camera') and 'lens' not in entry:
            raise ValueError('Explicit matrix camera requires lens')
        if 'lens' in entry:
            _number(entry['lens'], 'lens', 1, 1000)
        properties = entry.get('camera_properties', {})
        if not isinstance(properties, dict) or set(properties) - camera_properties:
            raise ValueError('Unsupported camera_set camera_properties')
        if properties.get('type', 'PERSP') not in ('PERSP', 'ORTHO') or properties.get('sensor_fit', 'AUTO') not in ('AUTO', 'HORIZONTAL', 'VERTICAL'):
            raise ValueError('Unsupported camera_set camera type/sensor fit')
        if 'dof_use' in properties and not isinstance(properties['dof_use'], bool):
            raise ValueError('camera_set dof_use must be boolean')
        for key in ('sensor_width', 'clip_start', 'clip_end', 'ortho_scale', 'dof_fstop'):
            if key in properties:
                _number(properties[key], key, .000001)
        for key in ('shift_x', 'shift_y'):
            if key in properties:
                _number(properties[key], key, -10, 10)
        if 'clip_start' in properties and 'clip_end' in properties and properties['clip_end'] <= properties['clip_start']:
            raise ValueError('camera_set clip_end must exceed clip_start')
    if any(entry.get('mode', spec.get('mode', 'baked')) == 'physical-diffuse' for entry in entries):
        if (not isinstance(spec.get('source_sha256'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', spec['source_sha256'])):
            raise ValueError('physical-diffuse requires explicit saved source_sha256')
        if spec.get('denoise', False) or spec.get('color_mode', 'RGB') != 'RGB':
            raise ValueError('physical-diffuse requires denoise false and RGB display PNG')
        filenames = set()
        for entry in entries:
            suffixes = ['.png', '.receipt.json']
            if entry.get('mode', spec.get('mode', 'baked')) == 'physical-diffuse':
                suffixes += ['.exr', '_passes.exr']
            for suffix in suffixes:
                filename = entry['id'] + suffix
                if filename.casefold() in filenames:
                    raise ValueError('physical-diffuse camera ids create colliding output filenames')
                filenames.add(filename.casefold())
    if delivery:
        import json
        pairs={}
        if 'delivery_channel_isolation' in spec and len({entry['id'].casefold() for entry in entries})!=len(entries):
            raise ValueError('Delivery isolation camera ids create colliding output filenames')
        for entry in entries:
            key=json.dumps({k:v for k,v in entry.items() if k not in ('id','mode')},sort_keys=True)
            pairs.setdefault(key,[]).append(entry.get('mode',spec.get('mode','baked')))
        if any(sorted(values)!=sorted(modes) for values in pairs.values()):
            if 'delivery_channel_isolation' in spec:
                raise ValueError('Delivery cameras require exactly one reference/decoded pair and each declared isolation variant with identical camera settings')
            raise ValueError('Delivery cameras require exactly one reference/decoded pair with identical camera settings')
    return entries


def _capture_modes(entries, spec, physical_context, baked_context, render_view, delivery_contexts=()):
    """Physical tint enters once for all physical views, before any baked overrides."""
    records = {}
    groups = ((('physical', 'physical-diffuse'), physical_context), (('baked',), baked_context))
    groups += tuple(((mode,), context) for mode, context in delivery_contexts)
    for modes, context in groups:
        selected = [entry for entry in entries if entry.get('mode', spec.get('mode', 'baked')) in modes]
        if selected:
            with context() as material_record:
                for entry in selected:
                    mode = entry.get('mode', spec.get('mode', 'baked'))
                    records[entry['id']] = render_view(entry, mode, material_record)
    return [records[entry['id']] for entry in entries]


@contextmanager
def _baked_materials(receivers, material):
    import bpy
    originals = []
    try:
        for obj in receivers:
            if obj.type != 'MESH':
                raise ValueError('Baked view receivers must be meshes')
            original = obj.data
            slots = [(slot.link, slot.material) for slot in obj.material_slots]
            mesh = original.copy()
            originals.append((obj, original, mesh, slots))
            obj.data = mesh
            mesh.materials.clear()
            mesh.materials.append(material)
            for slot in obj.material_slots:
                slot.link = 'DATA'
            for polygon in mesh.polygons:
                polygon.material_index = 0
        yield {'application': 'Configured current preview assigned once to disposable receiver mesh copies; glass source materials retained',
               'preview_material': material.name}
    finally:
        for obj, original, mesh, slots in reversed(originals):
            obj.data = original
            for slot, (link, saved_material) in zip(obj.material_slots, slots):
                slot.link = link
                if link == 'OBJECT':
                    slot.material = saved_material
            bpy.data.meshes.remove(mesh)


def _camera_data_receipt(data):
    result = {key: getattr(data, key) for key in ('type', 'lens', 'sensor_width', 'sensor_height', 'sensor_fit', 'shift_x', 'shift_y', 'clip_start', 'clip_end', 'ortho_scale')}
    result.update(dof_use=data.dof.use_dof, dof_fstop=data.dof.aperture_fstop,
                  dof_focus_distance=data.dof.focus_distance,
                  dof_focus_object=data.dof.focus_object.name if data.dof.focus_object else None,
                  dof_blades=data.dof.aperture_blades, dof_ratio=data.dof.aperture_ratio,
                  dof_rotation=data.dof.aperture_rotation)
    return result


def _apply_display(scene, display, delivery=False):
    """Apply in the selected scene; dynamic setter/readback owns availability.

    Blender's static RNA can report only NONE for dynamic OCIO enums. Keep that
    list as evidence, never as a substitute for the actual requested transform.
    """
    raw_enum=[]
    expected={'view_transform':'Raw','look':'None','exposure':0,'gamma':1}
    if delivery:
        if display!=expected:raise ValueError('Delivery display requires explicit Raw/None/exposure0/gamma1')
        try:raw_enum=[item.identifier for item in scene.view_settings.bl_rna.properties['view_transform'].enum_items]
        except (AttributeError,KeyError):pass
        scene.view_settings.use_curve_mapping=False
    for key,default in (('view_transform','AgX'),('look','None'),('exposure',0),('gamma',1)):
        try:setattr(scene.view_settings,key,display.get(key,default))
        except (TypeError,ValueError,RuntimeError) as exc:
            raise ValueError('Requested display setting could not be applied: '+key) from exc
    if delivery:
        actual={key:getattr(scene.view_settings,key) for key in expected}
        if actual!=expected or scene.view_settings.use_curve_mapping:
            raise ValueError('Delivery display readback differs from Raw/None/exposure0/gamma1 with curves disabled')
    return raw_enum


def validate_saved_preview_request(c, spec, entries, *, loaded_source, actual_source_sha256, expected_source_sha256):
    """Separate diagnostic contract; ordinary combined-provenance capture is unchanged."""
    if (not isinstance(expected_source_sha256,str)
            or not re.fullmatch(r'[0-9a-f]{64}',expected_source_sha256)
            or Path(loaded_source).resolve()!=Path(c['_source']).resolve()
            or actual_source_sha256!=expected_source_sha256
            or c.get('finishing_overrides')
            or not entries
            or any(entry.get('mode',spec.get('mode','baked'))!='baked' for entry in entries)):
        raise ValueError('Saved-preview diagnostics require exact immutable source, baked-only views and no finishing overrides')


@contextmanager
def _physical_diffuse_state(scene):
    """Restore every setting changed by matched capture/setup, including on failure.

    This transaction is opt-in for native diffuse capture. Legacy disposable
    physical/baked and delivery capture keep their existing lifecycle.
    """
    import bpy
    from babylon_js.workflow_tools.native_view_diagnostics import image_format_state, restore_image_format
    render_keys = ('engine', 'filepath', 'resolution_x', 'resolution_y', 'resolution_percentage',
                   'pixel_aspect_x', 'pixel_aspect_y', 'use_border', 'film_transparent',
                   'use_file_extension', 'use_compositing', 'use_motion_blur')
    cycles_keys = ('device', 'samples', 'use_adaptive_sampling', 'use_denoising', 'use_animated_seed',
                   'diffuse_bounces', 'max_bounces', 'sample_clamp_direct', 'sample_clamp_indirect', 'time_limit')
    display_keys = ('view_transform', 'look', 'exposure', 'gamma', 'use_curve_mapping')
    snapshots = [(owner, {key: getattr(owner, key) for key in keys if hasattr(owner, key)})
                 for owner, keys in ((scene.render, render_keys), (scene.cycles, cycles_keys),
                                     (scene.view_settings, display_keys))]
    image_format = image_format_state(scene.render.image_settings)
    color_management = getattr(scene.render.image_settings, 'color_management', None)
    camera, frame, subframe = scene.camera, scene.frame_current, scene.frame_subframe
    original_scene, original_layer = bpy.context.window.scene, bpy.context.window.view_layer
    visibility = [(obj, obj.hide_render) for obj in scene.objects]
    compositing_group = getattr(scene, 'compositing_node_group', None)
    world_links = []
    if scene.world and scene.world.use_nodes:
        world_links = [(node.inputs['Surface'], [(link.from_socket, link.to_socket) for link in node.inputs['Surface'].links])
                       for node in scene.world.node_tree.nodes if node.type == 'OUTPUT_WORLD']
    preferences = bpy.context.preferences.addons.get('cycles')
    preferences = preferences.preferences if preferences else None
    compute_type = preferences.compute_device_type if preferences else None
    devices = {(getattr(device, 'id', device.name), device.type): device.use for device in preferences.devices} if preferences else {}
    try:
        yield
    finally:
        scene.camera = camera
        for obj, hidden in visibility:
            obj.hide_render = hidden
        for socket, links in world_links:
            for link in list(socket.links):
                scene.world.node_tree.links.remove(link)
            for source, target in links:
                scene.world.node_tree.links.new(source, target)
        if hasattr(scene, 'compositing_node_group'):
            scene.compositing_node_group = compositing_group
        for owner, values in snapshots:
            for key, value in values.items():
                setattr(owner, key, value)
        restore_image_format(scene.render.image_settings, image_format)
        if color_management is not None:
            scene.render.image_settings.color_management = color_management
        if preferences:
            preferences.compute_device_type = compute_type
            for device in preferences.devices:
                key = (getattr(device, 'id', device.name), device.type)
                if key in devices:
                    device.use = devices[key]
        scene.frame_set(frame, subframe=subframe)
        bpy.context.window.scene = original_scene
        bpy.context.window.view_layer = original_layer


def capture_views(c, root, stage, *, saved_preview_source_sha256=None):
    """Matched views; native diffuse opt-in requires exact immutable saved source."""
    entries = validate_camera_set(c)
    spec = c.get('capture', {}).get('views', {})
    if 'delivery_channel_isolation' in spec:
        if (Path(stage)/'delivery-images').exists() or any(
                (Path(stage)/(entry['id']+suffix)).exists() for entry in entries for suffix in ('.png','.receipt.json')):
            raise ValueError('Preserve previous delivery isolation outputs; use a fresh output directory')
    if not any(entry.get('mode', spec.get('mode', 'baked')) == 'physical-diffuse' for entry in entries or []):
        return _capture_views(c, root, stage, saved_preview_source_sha256=saved_preview_source_sha256)
    import bpy
    from common import sha
    source = Path(c['_source']).resolve()
    if (not bpy.app.background or Path(bpy.data.filepath).resolve() != source
            or bpy.data.is_dirty or sha(source) != spec['source_sha256']):
        raise ValueError('physical-diffuse requires freshly loaded exact immutable saved source')
    scene = bpy.data.scenes.get(c['scene'])
    if scene is None:
        raise ValueError('Configured saved scene is missing: ' + c['scene'])
    source_images = {str((Path(c['_root'])/name).resolve()): sha(Path(c['_root'])/name)
                     for name in c.get('source_images', [])}
    # Check the complete mixed-mode output set before changing state or rendering.
    for entry in entries:
        suffixes = ['.png', '.receipt.json']
        if entry.get('mode', spec.get('mode', 'baked')) == 'physical-diffuse':
            suffixes += ['.exr', '_passes.exr']
        if any((Path(stage)/(entry['id'] + suffix)).exists() for suffix in suffixes):
            raise ValueError('Preserve previous matched-view outputs; use fresh filenames/directory')
    try:
        with _physical_diffuse_state(scene):
            result = _capture_views(c, root, stage, saved_preview_source_sha256=saved_preview_source_sha256)
    finally:
        if sha(source) != spec['source_sha256']:
            raise RuntimeError('Source changed during physical-diffuse views')
        if any(sha(path) != digest for path, digest in source_images.items()):
            raise RuntimeError('Declared source image changed during physical-diffuse views')
    result.update(state_restored=True, source_hash_unchanged=True)
    return result


def _capture_views(c, root, stage, *, saved_preview_source_sha256=None):
    """Read saved scene, render PNGs, restore per-mode materials; never save the source."""
    import bpy
    import numpy as np
    from mathutils import Matrix
    from worker import setup, select, check_preview_resolution, check_physical_world_routing
    from appearance import verify_combination, preview_identity
    from common import sha, save_json
    from ao import geometry_identity
    from physical_colour_adjustment import physical_materials
    from babylon_js import environment_controls as world_tools
    entries = validate_camera_set(c)
    if not entries:
        raise ValueError('views stage requires capture.views.camera_set')
    if not bpy.app.background:
        raise RuntimeError('Matched views require disposable background Blender')
    scene = setup(c)
    if scene.name != c['scene']:
        raise ValueError('Matched view scene differs from exact recipe scene')
    spec = c['capture']['views']
    delivery=None
    if spec.get('delivery_contract'):
        from delivery_views import contract_for_views
        delivery=contract_for_views(c)
    if 'frame' in spec:
        scene.frame_set(spec['frame'])
    receivers, contributors, glass, emitters = (select(c, key) for key in ('receivers', 'contributors', 'glass', 'visible_emitters'))
    if set(emitters) & set(receivers + contributors + glass) or set(glass) & set(receivers + contributors):
        raise ValueError('Matched view glass/visible emitters must be separate from receivers/contributors')
    if any(obj.type != 'MESH' for obj in receivers + contributors + glass + emitters):
        raise ValueError('Matched view geometry selectors must name meshes')
    lights = select(c, 'bake_lights')
    if any(obj.type != 'LIGHT' for obj in lights):
        raise ValueError('bake_lights must name lights')
    # Validate every named camera and effective property range before the first render.
    cameras = {}
    for entry in entries:
        original = scene.objects.get(entry.get('camera', ''))
        if entry.get('camera') and (original is None or original.type != 'CAMERA'):
            raise ValueError('Missing saved scene camera: ' + entry['camera'])
        if original and original.data.type not in ('PERSP', 'ORTHO'):
            raise ValueError('Matched views require perspective/orthographic saved cameras')
        cameras[entry['id']] = original
        props = entry.get('camera_properties', {})
        if props.get('clip_end', original.data.clip_end if original else 1000) <= props.get('clip_start', original.data.clip_start if original else .1):
            raise ValueError('Effective matched camera clip range is invalid')
    preview = bpy.data.materials.get(c.get('preview_material', ''))
    combination = None
    if any(entry.get('mode', spec.get('mode', 'baked')) == 'baked' for entry in entries):
        check_preview_resolution(c)
        if saved_preview_source_sha256 is None:
            combination = verify_combination(preview, root/'masters/combined.exr', root/'masters/combined.provenance.json')
        else:
            validate_saved_preview_request(c,spec,entries,loaded_source=bpy.data.filepath,
                                           actual_source_sha256=sha(c['_source']),
                                           expected_source_sha256=saved_preview_source_sha256)
            from babylon_js import lightmap_grading
            ctrl, _ = lightmap_grading.inspect(preview)
            combination = {'kind':'saved-preview-source-diagnostic', 'accepted':False,
                           'source_sha256':saved_preview_source_sha256,
                           'preview_sha256':preview_identity(preview),
                           'actual_saved_controls':{socket.name:float(socket.default_value) for socket in ctrl.inputs if socket.type=='VALUE'},
                           'limits':'Exact saved graph and image samplers; no regeneration or combination-provenance acceptance claim.'}
    geometry = geometry_identity(c)
    if delivery and geometry['geometry_sha256']!=delivery['geometry_sha256']:
        raise ValueError('Delivery views source geometry/UV identity differs from contract')
    controls = world_tools.find_controls(scene.world)
    if controls is None:
        raise ValueError('Matched views require configured World controls')
    output = next(node for node in scene.world.node_tree.nodes if node.type == 'OUTPUT_WORLD' and node.is_active_output)
    branch = spec.get('world_output', 'camera-separated')
    if branch == 'camera-separated':
        routing = check_physical_world_routing(c, controls, output)
    else:
        scene.world.node_tree.links.new(controls.outputs[branch], output.inputs['Surface'])
        routing = {'route': branch, 'explicit_rewire_in_disposable_process': True}
    world = world_tools.settings(controls)
    original_camera = scene.camera
    display = spec.get('display', {})
    raw_enum=_apply_display(scene,display,bool(delivery))
    scene.render.resolution_x, scene.render.resolution_y = spec['dimensions']
    scene.render.resolution_percentage = 100
    scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1
    scene.render.use_border = False
    scene.render.film_transparent = spec.get('film_transparent', False)
    scene.render.use_file_extension = True
    if any(entry.get('mode', spec.get('mode', 'baked')) == 'physical-diffuse' for entry in entries):
        from babylon_js.workflow_tools.native_view_diagnostics import set_image_format
        set_image_format(scene.render.image_settings, 'PNG')
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = spec.get('color_mode', 'RGB')
    scene.render.image_settings.color_depth = '8'
    if hasattr(scene.render.image_settings, 'color_management'):
        scene.render.image_settings.color_management = 'FOLLOW_SCENE'
    scene.cycles.samples = spec['samples']
    scene.cycles.use_denoising = spec.get('denoise', False)
    scene.cycles.time_limit = 0
    if hasattr(scene.render, 'use_motion_blur'):
        scene.render.use_motion_blur = False
    source_hash = sha(c['_source'])

    def render_view(entry, mode, material_record):
        physical = mode in ('physical', 'physical-diffuse')
        visible = set(receivers + emitters + (glass if spec.get('include_glass', True) else []) + (contributors if physical else []))
        for obj in scene.objects:
            if obj.type == 'LIGHT':
                obj.hide_render = not physical or obj not in lights
            else:
                obj.hide_render = obj not in visible
        eligible = set()
        def walk(layer, blocked=False):
            blocked = blocked or layer.exclude or layer.collection.hide_render
            if not blocked:
                eligible.update(obj for obj in layer.collection.objects if obj.type in ('MESH', 'LIGHT') and not obj.hide_render)
                for child in layer.children:
                    walk(child, blocked)
        walk(bpy.context.view_layer.layer_collection)
        intended = visible | (set(lights) if mode == 'physical-diffuse' else set())
        if not intended <= eligible:
            raise ValueError('Matched view intended geometry is collection-excluded: ' + ', '.join(sorted(obj.name for obj in intended - eligible)))
        original = cameras[entry['id']]
        data = original.data.copy() if original else bpy.data.cameras.new('TEMP matched view data')
        data.animation_data_clear()
        camera = bpy.data.objects.new('TEMP matched view ' + entry['id'], data)
        scene.collection.objects.link(camera)
        try:
            camera.matrix_world = Matrix(entry['matrix_world']) if 'matrix_world' in entry else original.evaluated_get(bpy.context.evaluated_depsgraph_get()).matrix_world.copy()
            if 'lens' in entry:
                data.lens = entry['lens']
            for key, value in entry.get('camera_properties', {}).items():
                if key == 'dof_use':
                    data.dof.use_dof = value
                elif key == 'dof_fstop':
                    data.dof.aperture_fstop = value
                else:
                    setattr(data, key, value)
            scene.camera = camera
            deps = bpy.context.evaluated_depsgraph_get()
            meshes = []
            for obj in sorted(visible, key=lambda item: item.name):
                evaluated = obj.evaluated_get(deps)
                mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=deps)
                try:
                    mesh.calc_loop_triangles()
                    meshes.append({'object': obj.name, 'evaluated_vertices': len(mesh.vertices), 'evaluated_triangles': len(mesh.loop_triangles)})
                finally:
                    evaluated.to_mesh_clear()
            generated = [instance for instance in deps.object_instances if instance.is_instance and instance.parent and instance.parent.original in visible]
            if generated:
                raise ValueError('Matched view generated instances require explicit realized geometry selectors')
            if delivery:
                triangles=sum(row['evaluated_triangles'] for row in meshes)
                continuous=delivery.get('continuous_surface_equivalence')
                if continuous:
                    checked=continuous['validation']
                    if (triangles!=checked['native_triangle_count'] or len(meshes)!=checked['native_mesh_count']
                            or checked['runtime_triangle_count']!=delivery['runtime_triangle_count']
                            or checked['runtime_mesh_count']!=delivery['runtime_mesh_count']
                            or checked['native_geometry_sha256']!=geometry['geometry_sha256']
                            or checked['source_sha256']!=source_hash):
                        raise ValueError('Actual native render counts/source/geometry differ from verified surface equivalence')
                else:
                    if triangles!=delivery['runtime_triangle_count']:
                        raise ValueError('Delivery/native selected triangle totals differ')
                    if len(meshes)!=delivery['runtime_mesh_count'] and not delivery.get('geometry_equivalence_receipt'):
                        raise ValueError('Different delivery/native mesh count requires retained geometry/UV parity evidence')
            path = stage/(entry['id'] + '.png')
            native_diffuse = None
            if mode == 'physical-diffuse':
                import hashlib
                import json
                from worker import write_exr
                from babylon_js.workflow_tools import native_view_diagnostics as native
                linear = stage/(entry['id'] + '.exr')
                passfile = stage/(entry['id'] + '_passes.exr')
                with native.diffuse_passes(scene, bpy.context.view_layer) as native_diffuse:
                    scene.render.filepath = str(passfile)
                    bpy.ops.render.render(write_still=True, layer=bpy.context.view_layer.name)
                    passes, channels = native.read_diffuse_passes(passfile, bpy.context.view_layer.name)
                    rgb = native.compose_diffuse(passes['DiffDir'], passes['DiffInd'], passes['DiffCol'])
                    write_exr(linear, rgb)
                    native_diffuse.update(channels=channels)
                native._save_png_from_linear(linear, path, scene)
                native_diffuse.update(linear_file=linear.name, linear_sha256=sha(linear),
                    pass_file=passfile.name, pass_file_sha256=sha(passfile),
                    linear_settings={'channels': 'RGB', 'dtype': 'float32', 'compression': 'ZIP', 'color_space': 'Linear Rec.709'},
                    helper_sha256=sha(native.__file__), camera_helper_sha256=sha(__file__),
                    request_sha256=hashlib.sha256(json.dumps(c, sort_keys=True, allow_nan=False, separators=(',', ':')).encode('utf8')).hexdigest(),
                    source_images_sha256={name: sha(Path(c['_root'])/name) for name in c.get('source_images', [])},
                    engine=scene.render.engine,
                    transport={key: getattr(scene.cycles, key) for key in ('diffuse_bounces', 'max_bounces', 'sample_clamp_direct', 'sample_clamp_indirect')},
                    ungraded_reference=True, metallic_cap_equivalence=False,
                    limits='Actual uncapped physical diffuse reference. No glossy/emission channels, no metallic-cap approximation or equivalence claim; display PNG derives from the retained linear master without a second render or denoise.')
            else:
                scene.render.filepath = str(path)
                bpy.ops.render.render(write_still=True)
            import OpenImageIO as oiio
            pixels = oiio.ImageBuf(str(path)).get_pixels(oiio.FLOAT)
            if pixels is None or pixels.shape[:2] != (spec['dimensions'][1], spec['dimensions'][0]) or not np.isfinite(pixels).all():
                raise ValueError('Matched view PNG dimensions/pixels invalid')
            record = {'schema_version': 1, 'id': entry['id'], 'mode': mode, 'file': path.name, 'sha256': sha(path),
                'scene': scene.name, 'frame': scene.frame_current, 'source_sha256': source_hash,
                'camera': entry.get('camera'), 'matrix_world': [list(row) for row in camera.matrix_world], 'camera_properties': _camera_data_receipt(data),
                'dimensions': list(spec['dimensions']), 'samples': scene.cycles.samples, 'denoise': scene.cycles.use_denoising,
                'adaptive_sampling': scene.cycles.use_adaptive_sampling, 'display': {key: getattr(scene.view_settings, key) for key in ('view_transform', 'look', 'exposure', 'gamma')},
                'seed': scene.cycles.seed, 'animated_seed': scene.cycles.use_animated_seed, 'time_limit': scene.cycles.time_limit,
                'denoiser': getattr(scene.cycles, 'denoiser', None), 'denoising_input_passes': getattr(scene.cycles, 'denoising_input_passes', None),
                'denoising_prefilter': getattr(scene.cycles, 'denoising_prefilter', None), 'display_device': scene.display_settings.display_device,
                'use_curve_mapping': scene.view_settings.use_curve_mapping,
                'device': scene.cycles.device, 'film_transparent': scene.render.film_transparent, 'format': 'PNG', 'color_mode': scene.render.image_settings.color_mode, 'color_depth': '8',
                'motion_blur': bool(getattr(scene.render, 'use_motion_blur', False)),
                'world_output': branch, 'world_routing': routing, 'world_settings': world, 'material_application': material_record,
                'geometry_sha256': geometry['geometry_sha256'], 'visible_mesh_count': len(meshes), 'evaluated_triangle_count': sum(row['evaluated_triangles'] for row in meshes), 'meshes': meshes,
                'enabled_lights': sorted(obj.name for obj in scene.objects if obj.type == 'LIGHT' and not obj.hide_render),
                'excluded_lights': sorted(obj.name for obj in scene.objects if obj.type == 'LIGHT' and obj.hide_render),
                'excluded_meshes': sorted(obj.name for obj in scene.objects if obj.type == 'MESH' and obj.hide_render),
                'glass': sorted(obj.name for obj in glass if obj in visible), 'visible_emitters': sorted(obj.name for obj in emitters),
                'combined_provenance': combination if mode == 'baked' else None,
                'delivery_contract': {'path':delivery['contract_path'],'sha256':delivery['contract_sha256'],'raw_enum':raw_enum,
                    'runtime_mesh_count':delivery['runtime_mesh_count'],'runtime_triangle_count':delivery['runtime_triangle_count'],
                    'geometry_equivalence_receipt':delivery['geometry_equivalence_receipt']} if delivery else None,
                'preview_sha256': preview_identity(preview) if mode == 'baked' else None,
                'limits': delivery['limits'] if delivery else 'Actual selected render geometry is recorded; no runtime geometry equivalence claim. Physical tint is disposable intrinsic colour, baked preview carries its existing overlay once. PNG is display-transformed, not a linear lighting master.'}
            if native_diffuse is not None:
                record.update(native_diffuse=native_diffuse, source_saved=False,
                              source_hash_unchanged=sha(c['_source']) == source_hash,
                              compositor_used=False, limits=native_diffuse['limits'])
            if delivery and 'delivery_channel_isolation' in spec:
                record.update(kind='diagnostic-delivery-channel-isolation-view',accepted=False,source_saved=False,
                              source_hash_unchanged=sha(c['_source'])==source_hash,
                              limits=material_record['limits'])
            save_json(stage/(entry['id'] + '.receipt.json'), record)
            return record
        finally:
            scene.camera = original_camera
            bpy.data.objects.remove(camera, do_unlink=True)
            bpy.data.cameras.remove(data)
    delivery_contexts=()
    if delivery:
        from delivery_views import delivery_materials,delivery_channel_plan
        delivery_contexts=tuple((mode,lambda mode=mode:delivery_materials(c,receivers,delivery,mode,stage))
                               for mode in delivery_channel_plan(spec,delivery))
    records = _capture_modes(entries, spec, lambda: physical_materials(c), lambda: _baked_materials(receivers, preview), render_view,delivery_contexts)
    if sha(c['_source']) != source_hash:
        raise RuntimeError('Source changed during matched views')
    result={'kind': 'matched-camera-set', 'views': records, 'source_sha256': source_hash,
            'requested_views': len(entries), 'rendered_views': len(records), 'source_saved': False,
            'physical_overlay_context_count': int(any(record['mode'] in ('physical', 'physical-diffuse') for record in records))}
    if delivery:
        result['decoded_delivery_contract_sha256']=delivery['contract_sha256']
        result['limits']=delivery['limits']
        if 'delivery_channel_isolation' in spec:
            result.update(kind='diagnostic-delivery-channel-isolation-set',accepted=False,
                          delivery_channel_plan=delivery_channel_plan(spec,delivery),
                          limits='Diagnostic channel isolation only; not final all-decoded validation or production acceptance. '+delivery['limits'])
        from decoded_delivery import load_contract
        if sha(delivery['contract_path'])!=delivery['contract_sha256']:
            raise ValueError('Decoded delivery contract changed during camera capture')
        load_contract(delivery['contract_path'])
    return result


def validate_native_diagnostics(c, recipe):
    """Validate opt-in Workbench data without importing Blender.

    Native geometry views do not enter the physical/baked capture mode contract.
    The existing camera contract validates their common framing fields only.
    """
    if not isinstance(recipe, dict) or recipe.get('native_engine') != 'BLENDER_WORKBENCH':
        raise ValueError('Native diagnostics require explicit BLENDER_WORKBENCH')
    if (not isinstance(c.get('_source'), str) or not c['_source']
            or not isinstance(recipe.get('source'), str)
            or not isinstance(recipe.get('source_sha256'), str)
            or not re.fullmatch(r'[0-9a-f]{64}', recipe['source_sha256'])
            or Path(recipe['source']).resolve() != Path(c['_source']).resolve()):
        raise ValueError('Native diagnostics require exact source path and SHA-256')
    if not isinstance(recipe.get('normal_guard'), bool):
        raise ValueError('Native diagnostics require explicit normal_guard boolean')
    if recipe['normal_guard'] and (not isinstance(c.get('uv'), str) or not c['uv']
            or isinstance(c.get('size'), bool) or not isinstance(c.get('size'), int)
            or not 16 <= c['size'] <= 8192):
        raise ValueError('Native receiver normal guard requires explicit UV layer and atlas size')
    if recipe.get('render_aa') not in ('OFF', '5', '8', '11', '16', '32'):
        raise ValueError('Unsupported native render_aa')
    frame = recipe.get('frame')
    if isinstance(frame, bool) or not isinstance(frame, int):
        raise ValueError('Native diagnostics require explicit integer frame')
    display = recipe.get('display')
    if not isinstance(display, dict) or set(display) != {'view_transform', 'look', 'exposure', 'gamma'}:
        raise ValueError('Native diagnostics require complete display settings')
    if display != {'view_transform': 'Standard', 'look': 'None', 'exposure': 0, 'gamma': 1}:
        raise ValueError('Native geometry diagnostics require Standard/None/exposure0/gamma1')
    presets = recipe.get('presets')
    if not isinstance(presets, dict) or not presets:
        raise ValueError('Native diagnostics require explicit shading presets')
    allowed = {'light', 'studio_light', 'color_type', 'single_color', 'show_shadows',
               'show_cavity', 'cavity_type', 'background_type', 'background_color'}
    for name, preset in presets.items():
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', name):
            raise ValueError('Invalid native shading preset name')
        if not isinstance(preset, dict) or set(preset) != allowed:
            raise ValueError('Native shading presets require all supported fields explicitly')
        if preset.get('light') not in ('STUDIO', 'MATCAP') or preset.get('color_type') != 'SINGLE':
            raise ValueError('Native diagnostics require STUDIO/MATCAP and SINGLE colour')
        if not isinstance(preset.get('studio_light'), str) or not preset['studio_light']:
            raise ValueError('Native diagnostics require explicit studio_light')
        if preset.get('background_type') != 'WORLD':
            raise ValueError('Native diagnostics require explicit WORLD background')
        for key in ('single_color', 'background_color'):
            values = preset.get(key)
            if not isinstance(values, list) or len(values) != 3:
                raise ValueError('Native diagnostics require explicit RGB ' + key)
            for value in values:
                _number(value, key, 0, 1)
        for key in ('show_shadows', 'show_cavity'):
            if not isinstance(preset.get(key), bool):
                raise ValueError('Native preset requires boolean ' + key)
        if 'cavity_type' in preset and preset['cavity_type'] not in ('WORLD', 'SCREEN', 'BOTH'):
            raise ValueError('Invalid native cavity_type')
    jobs = recipe.get('jobs')
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= 100:
        raise ValueError('Native diagnostics require 1..100 jobs')
    entries = []
    for job in jobs:
        if not isinstance(job, dict):
            raise ValueError('Invalid native job')
        names = job.get('objects')
        if (not isinstance(names, list) or not names
                or any(not isinstance(name, str) or not name for name in names)
                or len(set(names)) != len(names)):
            raise ValueError('Native jobs require nonempty unique explicit object names')
        if job.get('shading') not in presets or not isinstance(job.get('backface_culling'), bool):
            raise ValueError('Native jobs require named preset and explicit culling boolean')
        entries.append({key: job[key] for key in ('id', 'camera', 'matrix_world', 'lens', 'camera_properties') if key in job})
    framed = dict(c)
    framed['capture'] = {'views': {'camera_set': entries, 'dimensions': recipe.get('dimensions'),
                                  'samples': 1, 'mode': 'physical'}}
    validate_camera_set(framed)
    return jobs


def capture_native_diagnostics(c, recipe_path, output):
    """Opt-in disposable Workbench inspection; no bake, relink or source save.

    Explicit membership is bounded by the configured geometry selectors. Source
    hashes and actual culling/camera/selection settings accompany every image.
    """
    import bpy
    import json
    from mathutils import Matrix
    from common import sha, save_json
    from worker import select, evaluate_receivers
    from receiver_bake import joined_receivers
    if not bpy.app.background:
        raise RuntimeError('Native diagnostics require disposable background Blender')
    recipe_path, output = Path(recipe_path).resolve(), Path(output).resolve()
    recipe = json.loads(recipe_path.read_text(encoding='utf8'))
    jobs = validate_native_diagnostics(c, recipe)
    source = Path(c['_source']).resolve()
    expected = recipe['source_sha256']
    if (Path(bpy.data.filepath).resolve() != source or sha(source) != expected
            or bpy.data.is_dirty or bpy.context.scene.name != c['scene']
            or bpy.context.scene.frame_current != recipe['frame'] or bpy.context.mode != 'OBJECT'):
        raise ValueError('Native diagnostics require freshly loaded exact immutable source and scene')
    if output.exists() or source == output or output in source.parents:
        raise ValueError('Native diagnostics require a fresh output directory separate from source')
    scene = bpy.context.scene
    permitted = {obj.name: obj for key in ('receivers', 'contributors', 'glass', 'visible_emitters')
                 for obj in select(c, key)}
    for job in jobs:
        for name in job['objects']:
            if name not in permitted or permitted[name].type != 'MESH' or name not in scene.objects:
                raise ValueError('Native job object is outside explicit configured mesh selection: ' + name)
        original = scene.objects.get(job.get('camera', ''))
        if job.get('camera') and (original is None or original.type != 'CAMERA'):
            raise ValueError('Missing native diagnostic camera: ' + job['camera'])
    if scene.world is None:
        raise ValueError('Native diagnostics require a saved World for explicit background')
    # Validate installed studio-light names before creating outputs or rendering.
    available = {light.name for light in bpy.context.preferences.studio_lights}
    if any(preset['studio_light'] not in available for preset in recipe['presets'].values()):
        raise ValueError('Native diagnostic studio_light is unavailable in this Blender installation')
    recipe_hash = sha(recipe_path)
    config_path = Path(c['_config']).resolve() if c.get('_config') else None
    config_hash = sha(config_path) if config_path else None
    output.mkdir(parents=True)
    guard = {'checked': False}
    if recipe['normal_guard']:
        receivers = select(c, 'receivers')
        if not receivers:
            raise ValueError('Native receiver normal guard requires configured receivers')
        selection = list(bpy.context.selected_objects)
        active = bpy.context.view_layer.objects.active
        try:
            copies, catalogue = evaluate_receivers(c)
            loops = sum(len(obj.data.loops) for obj in copies)
            with joined_receivers(copies, receivers) as (proxy, error):
                guard = {'checked': True, 'receiver_count': len(receivers), 'loop_count': loops,
                         'receiver_names': sorted(obj.name for obj in receivers),
                         'max_normal_error': error, 'unchanged_threshold': .001,
                         'uv': c.get('uv', 'SimpleBake'), 'size': c.get('size'),
                         'catalogue_islands': len(catalogue),
                         'helper_sha256': sha(Path(__file__).with_name('receiver_bake.py')),
                         'scope': 'Installed joined-receiver normal proof only; no UV2 uniqueness or bake claim'}
        finally:
            for obj in bpy.context.selected_objects: obj.select_set(False)
            for obj in selection: obj.select_set(True)
            bpy.context.view_layer.objects.active = active
        save_json(output/'normal-guard.json', guard)
    visibility = [(obj, obj.hide_render, obj.hide_get()) for obj in scene.objects
                  if obj.type not in ('CAMERA', 'LIGHT')]
    shading = scene.display.shading
    shading_keys = set().union(*(preset.keys() for preset in recipe['presets'].values())) | {'show_backface_culling'}
    saved_shading = {key: (list(getattr(shading, key)) if key in ('single_color', 'background_color')
                            else getattr(shading, key)) for key in shading_keys}
    saved = dict(engine=scene.render.engine, camera=scene.camera, frame=scene.frame_current,
                 world_color=list(scene.world.color), filepath=scene.render.filepath,
                 width=scene.render.resolution_x, height=scene.render.resolution_y,
                 percentage=scene.render.resolution_percentage, transparent=scene.render.film_transparent,
                 aa=scene.display.render_aa,
                 display={key: getattr(scene.view_settings, key) for key in recipe['display']},
                 display_device=scene.display_settings.display_device,
                 use_curve_mapping=scene.view_settings.use_curve_mapping,
                 format=scene.render.image_settings.file_format,
                 color_mode=scene.render.image_settings.color_mode, depth=scene.render.image_settings.color_depth)
    camera_data = bpy.data.cameras.new('TEMP native diagnostic camera')
    camera = bpy.data.objects.new('TEMP native diagnostic camera', camera_data)
    scene.collection.objects.link(camera)
    records = []
    try:
        scene.render.engine = 'BLENDER_WORKBENCH'
        scene.render.resolution_x, scene.render.resolution_y = recipe['dimensions']
        scene.render.resolution_percentage = 100
        scene.render.film_transparent = False
        scene.render.image_settings.file_format = 'PNG'
        scene.render.image_settings.color_mode = 'RGB'
        scene.render.image_settings.color_depth = '8'
        scene.display.render_aa = recipe['render_aa']
        scene.display_settings.display_device = 'sRGB'
        scene.view_settings.use_curve_mapping = False
        _apply_display(scene, recipe['display'])
        scene.frame_set(recipe['frame'])
        scene.camera = camera
        for job in jobs:
            wanted = set(job['objects'])
            for obj, _, _ in visibility:
                obj.hide_render = obj.name not in wanted
                obj.hide_set(obj.name not in wanted)
            original = scene.objects.get(job.get('camera', ''))
            previous = camera.data
            # Each job starts independently; a matrix-only job must not inherit
            # the preceding saved camera's orthographic/shift/DOF settings.
            if original:
                camera.data = original.data.copy()
                camera.matrix_world = original.matrix_world.copy()
            else:
                camera.data = bpy.data.cameras.new('TEMP native diagnostic camera data')
            bpy.data.cameras.remove(previous)
            camera_data = camera.data
            if 'matrix_world' in job:
                camera.matrix_world = Matrix(job['matrix_world'])
            if 'lens' in job:
                camera_data.lens = job['lens']
            for key, value in job.get('camera_properties', {}).items():
                if key == 'dof_use': camera_data.dof.use_dof = value
                elif key == 'dof_fstop': camera_data.dof.aperture_fstop = value
                else: setattr(camera_data, key, value)
            if camera_data.clip_end <= camera_data.clip_start:
                raise ValueError('Invalid effective native camera clip range')
            preset = recipe['presets'][job['shading']]
            shading.light = preset['light']
            for key, value in preset.items():
                if key != 'light': setattr(shading, key, value)
            scene.world.color = preset['background_color']
            shading.show_backface_culling = job['backface_culling']
            bpy.context.view_layer.update()
            if any(not permitted[name].visible_get() for name in wanted):
                raise ValueError('Native selected object is excluded/hidden by its saved view layer or collection')
            actual = sorted(obj.name for obj in scene.objects if obj.type == 'MESH' and not obj.hide_render)
            if actual != sorted(wanted):
                raise ValueError('Native actual mesh selection differs from explicit job')
            png = output/(job['id']+'.png')
            scene.render.filepath = str(png)
            bpy.ops.render.render(write_still=True)
            if not png.is_file() or png.stat().st_size <= 100:
                raise ValueError('Native diagnostic PNG was not written')
            import struct
            with png.open('rb') as stream: header = stream.read(24)
            if (header[:8] != b'\x89PNG\r\n\x1a\n'
                    or struct.unpack('>II', header[16:24]) != tuple(recipe['dimensions'])):
                raise ValueError('Native diagnostic PNG dimensions differ from recipe')
            record = {'id': job['id'], 'source': str(source), 'source_sha256': expected,
                      'recipe_sha256': recipe_hash, 'engine': scene.render.engine,
                      'config_sha256': config_hash,
                      'dimensions': [scene.render.resolution_x, scene.render.resolution_y],
                      'render_aa': scene.display.render_aa, 'source_saved': False,
                      'camera_matrix': [list(row) for row in camera.matrix_world],
                      'camera_properties': _camera_data_receipt(camera_data),
                      'visible_mesh_names': actual, 'expected_visible_mesh_names': sorted(wanted),
                      'shading_preset': job['shading'], 'studio_light': shading.studio_light,
                      'shading': {key: (list(getattr(shading, key)) if key in ('single_color', 'background_color')
                                       else getattr(shading, key)) for key in preset},
                      'frame': scene.frame_current,
                      'display': {key: getattr(scene.view_settings, key) for key in recipe['display']},
                      'display_device': scene.display_settings.display_device,
                      'use_curve_mapping': scene.view_settings.use_curve_mapping,
                      'backface_culling': shading.show_backface_culling,
                      'purpose': job.get('purpose'), 'axis_mapping': job.get('axis_mapping'),
                      'png': str(png), 'png_sha256': sha(png),
                      'limits': 'Native geometry diagnostic; MATCAP is camera-relative shading, not outward-winding proof'}
            if record['backface_culling'] != job['backface_culling']:
                raise ValueError('Native culling readback differs from recipe')
            save_json(output/(job['id']+'.json'), record)
            records.append(record)
    finally:
        for obj, hidden_render, hidden_view in visibility:
            obj.hide_render = hidden_render
            obj.hide_set(hidden_view)
        scene.camera = saved['camera']
        bpy.data.objects.remove(camera, do_unlink=True)
        bpy.data.cameras.remove(camera_data)
        scene.render.engine = saved['engine']
        scene.render.filepath = saved['filepath']
        scene.render.resolution_x, scene.render.resolution_y = saved['width'], saved['height']
        scene.render.resolution_percentage = saved['percentage']
        scene.render.film_transparent = saved['transparent']
        scene.render.image_settings.file_format = saved['format']
        scene.render.image_settings.color_mode = saved['color_mode']
        scene.render.image_settings.color_depth = saved['depth']
        scene.display.render_aa = saved['aa']
        scene.world.color = saved['world_color']
        # Light type owns the dynamic studio-light enum, so restore it first.
        shading.light = saved_shading.pop('light')
        for key, value in saved_shading.items(): setattr(shading, key, value)
        _apply_display(scene, saved['display'])
        scene.display_settings.display_device = saved['display_device']
        scene.view_settings.use_curve_mapping = saved['use_curve_mapping']
        scene.frame_set(saved['frame'])
    if sha(source) != expected:
        raise ValueError('Native diagnostic source changed during capture')
    if sha(recipe_path) != recipe_hash:
        raise ValueError('Native diagnostic recipe changed during capture')
    if config_path and sha(config_path) != config_hash:
        raise ValueError('Native diagnostic config changed during capture')
    result = {'source': str(source), 'source_sha256': expected, 'source_saved': False,
              'source_hash_unchanged': True, 'recipe_sha256': recipe_hash,
              'config_sha256': config_hash, 'configured_mesh_names': sorted(permitted),
              'helper_sha256': sha(__file__), 'normal_guard': guard,
              'job_count': len(records), 'ids': [record['id'] for record in records],
              'receipts': records, 'engine': 'BLENDER_WORKBENCH'}
    save_json(output/'result.json', result)
    return result


def native_diagnostics_main():
    import argparse, json, sys
    parser = argparse.ArgumentParser()
    parser.add_argument('--native-diagnostics', action='store_true', required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--recipe', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    # This diagnostic has no image/bake dependency. Do not manufacture unused
    # image bindings merely to pass the full bake pipeline's load_config.
    config_path = args.config.resolve()
    c = json.loads(config_path.read_text(encoding='utf8'))
    if c.get('schema_version') != 1 or not isinstance(c.get('blend'), str) or not c['blend']:
        raise ValueError('Native diagnostics require schema_version1 and saved blend path')
    c.update(_source=str((config_path.parent/c['blend']).resolve()),
             _root=str(config_path.parent), _config=str(config_path))
    result = capture_native_diagnostics(c, args.recipe, args.output)
    print(json.dumps({'status': 'passed', 'native_views': result['job_count'], 'source_saved': False}))


def saved_preview_main():
    """Direct background entrypoint: no bake chain, relink, finishing, or source save."""
    import argparse, json, sys
    from pathlib import Path
    import bpy
    from common import load_config, save_json
    p=argparse.ArgumentParser()
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(sys.argv[sys.argv.index('--')+1:])
    if not bpy.app.background:raise RuntimeError('Disposable background Blender required')
    c=load_config(a.config)
    expected=c.get('saved_preview_diagnostic',{}).get('source_sha256')
    if not isinstance(expected,str) or len(expected)!=64:raise ValueError('Explicit saved-preview source hash required')
    output=a.output.resolve()
    if output.exists():raise ValueError('Use new saved-preview output directory')
    output.mkdir(parents=True)
    result=capture_views(c,output,output,saved_preview_source_sha256=expected)
    save_json(output/'result.json',result)
    print(json.dumps({'status':'passed','rendered_views':result['rendered_views'],'source_saved':False}))


def physical_diffuse_main():
    """Direct immutable-source reference capture; no bake chain or relinking."""
    import argparse, json, sys
    from common import load_config, save_json
    parser = argparse.ArgumentParser()
    parser.add_argument('--physical-diffuse', action='store_true', required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    c = load_config(args.config)
    entries = validate_camera_set(c)
    spec = c['capture']['views']
    modes = [entry.get('mode', spec.get('mode', 'baked')) for entry in entries or []]
    if 'physical-diffuse' not in modes or set(modes) - {'physical', 'physical-diffuse'}:
        raise ValueError('Direct physical-diffuse entrypoint requires physical-diffuse and optional physical views only')
    output = args.output.resolve()
    if output.exists():
        raise ValueError('Use a fresh physical-diffuse output directory')
    output.mkdir(parents=True)
    result = capture_views(c, output, output)
    save_json(output/'result.json', result)
    print(json.dumps({'status': 'passed', 'rendered_views': result['rendered_views'], 'source_saved': False}))


if __name__=='__main__':
    from pathlib import Path
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent.parent))
    arguments = sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
    if '--native-diagnostics' in arguments:
        native_diagnostics_main()
    elif '--physical-diffuse' in arguments:
        physical_diffuse_main()
    else:
        saved_preview_main()
