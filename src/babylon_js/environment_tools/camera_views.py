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


def validate_camera_set(c):
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
    modes=('delivery-reference','delivery-decoded') if delivery else ('physical','baked')
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
            raise ValueError('camera_set mode differs from physical/baked or opt-in delivery contract')
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
    if delivery:
        import json
        pairs={}
        for entry in entries:
            key=json.dumps({k:v for k,v in entry.items() if k not in ('id','mode')},sort_keys=True)
            pairs.setdefault(key,[]).append(entry.get('mode',spec.get('mode','baked')))
        if any(sorted(values)!=sorted(modes) for values in pairs.values()):
            raise ValueError('Delivery cameras require exactly one reference/decoded pair with identical camera settings')
    return entries


def _capture_modes(entries, spec, physical_context, baked_context, render_view, delivery_contexts=()):
    """Physical tint enters once for all physical views, before any baked overrides."""
    records = {}
    for mode, context in (('physical', physical_context), ('baked', baked_context))+tuple(delivery_contexts):
        selected = [entry for entry in entries if entry.get('mode', spec.get('mode', 'baked')) == mode]
        if selected:
            with context() as material_record:
                for entry in selected:
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


def capture_views(c, root, stage, *, saved_preview_source_sha256=None):
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
        visible = set(receivers + emitters + (glass if spec.get('include_glass', True) else []) + (contributors if mode == 'physical' else []))
        for obj in scene.objects:
            if obj.type == 'LIGHT':
                obj.hide_render = mode != 'physical' or obj not in lights
            else:
                obj.hide_render = obj not in visible
        eligible = set()
        def walk(layer, blocked=False):
            blocked = blocked or layer.exclude or layer.collection.hide_render
            if not blocked:
                eligible.update(obj for obj in layer.collection.objects if obj.type == 'MESH' and not obj.hide_render)
                for child in layer.children:
                    walk(child, blocked)
        walk(bpy.context.view_layer.layer_collection)
        if not visible <= eligible:
            raise ValueError('Matched view intended geometry is collection-excluded: ' + ', '.join(sorted(obj.name for obj in visible - eligible)))
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
            save_json(stage/(entry['id'] + '.receipt.json'), record)
            return record
        finally:
            scene.camera = original_camera
            bpy.data.objects.remove(camera, do_unlink=True)
            bpy.data.cameras.remove(data)
    delivery_contexts=()
    if delivery:
        from delivery_views import delivery_materials
        delivery_contexts=tuple((mode,lambda mode=mode:delivery_materials(c,receivers,delivery,mode,stage))
                               for mode in ('delivery-reference','delivery-decoded'))
    records = _capture_modes(entries, spec, lambda: physical_materials(c), lambda: _baked_materials(receivers, preview), render_view,delivery_contexts)
    if sha(c['_source']) != source_hash:
        raise RuntimeError('Source changed during matched views')
    result={'kind': 'matched-camera-set', 'views': records, 'source_sha256': source_hash,
            'requested_views': len(entries), 'rendered_views': len(records), 'source_saved': False,
            'physical_overlay_context_count': int(any(record['mode'] == 'physical' for record in records))}
    if delivery:
        result['decoded_delivery_contract_sha256']=delivery['contract_sha256']
        result['limits']=delivery['limits']
        from decoded_delivery import load_contract
        if sha(delivery['contract_path'])!=delivery['contract_sha256']:
            raise ValueError('Decoded delivery contract changed during camera capture')
        load_contract(delivery['contract_path'])
    return result


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


if __name__=='__main__':
    from pathlib import Path
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent.parent))
    saved_preview_main()
