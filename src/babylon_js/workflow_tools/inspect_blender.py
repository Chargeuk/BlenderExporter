"""Read-only Blender data audit; execute inside Blender, no third-party imports.

inspect_blender() audits render-eligible meshes in the active view layer.
Pass object_names to audit an explicit base-mesh set. snapshot_uv2() separately
reads evaluated meshes for the explicit intended receiver set. Neither uses
operators, edits scene data, reloads images, reads pixels or imports the parent add-on.
"""
from array import array
import hashlib
import json
import os
import sys

import bpy


def inspect_jobs():
    """Cheap observation, not a completion verdict; absent APIs return None."""
    native = {}
    for job in ("RENDER", "OBJECT_BAKE"):
        try:
            native[job] = bool(bpy.app.is_job_running(job))
        except (AttributeError, TypeError, ValueError):
            native[job] = None
    states = []
    # Read only modules already loaded by the installed add-on.
    for name, module in tuple(sys.modules.items()):
        if "simplebake" not in name.lower() or not name.endswith(".background_and_progress"):
            continue
        state = getattr(module, "BakeInProgress", None)
        if state is not None:
            states.append({"module": name, "is_baking": getattr(state, "is_baking", None),
                           "was_error": getattr(state, "was_error", None)})
    props = getattr(bpy.context.scene, "SimpleBake_Props", None)
    counters = {key: getattr(props, key, None) for key in
                ("current_bake_image_number", "total_bake_images_number", "percent_complete")}
    return {"native": native, "simplebake_states": states, "simplebake_counters": counters}


def _render_candidates(view_layer):
    candidates = set()

    def walk(layer, blocked=False):
        blocked = blocked or layer.exclude or layer.collection.hide_render
        if blocked:
            return
        for obj in layer.collection.objects:
            if obj.type == "MESH" and not obj.hide_render:
                candidates.add(obj.name)
        for child in layer.children:
            walk(child, blocked)

    walk(view_layer.layer_collection)
    return candidates


def _node_images(tree, visited=None):
    visited = set() if visited is None else visited
    if tree is None or tree.as_pointer() in visited:
        return set()
    visited.add(tree.as_pointer())
    images = set()
    for node in tree.nodes:
        image = getattr(node, "image", None)
        if image is not None:
            images.add(image.name)
        images.update(_node_images(getattr(node, "node_tree", None), visited))
    return images


def _uv_record(layer, hash_coordinates):
    result = {"name": layer.name, "loop_count": len(layer.data),
              "active_render": layer.active_render, "sha256": None}
    if hash_coordinates:
        values = array('f', [0.0]) * (2 * len(layer.data))
        layer.data.foreach_get('uv', values)
        if sys.byteorder != 'little':
            values.byteswap()
        result['sha256'] = hashlib.sha256(values.tobytes()).hexdigest()
    return result


def _image_record(image):
    packed = bool(image.packed_file) or bool(getattr(image, 'packed_files', ()))
    stored = image.filepath
    unresolved = bool(stored.startswith('//') and not bpy.data.filepath and image.library is None)
    path = bpy.path.abspath(stored, library=image.library) if stored and not unresolved else None
    checkable = image.source == 'FILE' and bool(path)
    exists = os.path.isfile(path) if checkable else None
    return {"name": image.name, "source": image.source, "size": list(image.size),
            "colour_space": image.colorspace_settings.name, "float": image.is_float,
            "packed": packed, "has_data": image.has_data, "dirty": image.is_dirty,
            "fake_user": image.use_fake_user, "stored_path": stored, "resolved_path": path,
            "external_file_exists": exists,
            "dependency_state": ('external_exists' if exists else
                                 'packed_only' if packed else
                                 'external_missing' if exists is False else
                                 'unresolved_relative_path' if unresolved else 'not_checked'),
            "simplebake_denoised_tag": image.get('SB_denoised')}


def inspect_blender(object_names=None, expected_uv_order=None, hash_uv=True, image_names=None):
    """Return JSON-safe observations. Images default to all loaded datablocks.

    UV hashes use little-endian float32 pairs in mesh-loop order. Busy jobs and
    Edit Mode skip hashing because the read would not be a reliable checkpoint.
    Counts use base mesh n-2 triangulation estimates, not evaluated instances.
    """
    scene = bpy.context.scene
    candidates = _render_candidates(bpy.context.view_layer)
    if object_names is None:
        objects = [scene.objects[name] for name in sorted(candidates)]
    else:
        if isinstance(object_names, str):
            raise TypeError('object_names must be an iterable of names, not a string')
        names = sorted(set(object_names))
        invalid = [n for n in names if n not in scene.objects or scene.objects[n].type != 'MESH']
        if invalid:
            raise ValueError('Unknown or non-mesh scene objects: ' + ', '.join(invalid))
        objects = [scene.objects[name] for name in names]
    jobs = inspect_jobs()
    busy = any(v is True for v in jobs['native'].values()) or any(
        s['is_baking'] is True for s in jobs['simplebake_states'])
    issues, records, materials = [], [], {}
    for obj in objects:
        mesh = obj.data
        order = [u.name for u in mesh.uv_layers]
        reason = 'job_active_or_flag_stale' if busy else 'edit_mode' if mesh.is_editmode else None
        if expected_uv_order is not None and order != list(expected_uv_order):
            issues.append({"code": "uv_order_mismatch", "object": obj.name, "actual": order})
        for slot in obj.material_slots:
            if slot.material:
                materials[slot.material.name] = slot.material
        records.append({"name": obj.name, "mesh": mesh.name, "render_candidate": obj.name in candidates,
                        "base_vertices": len(mesh.vertices),
                        "base_triangle_estimate": sum(max(0, len(p.vertices) - 2) for p in mesh.polygons),
                        "modifiers": [m.type for m in obj.modifiers],
                        "materials": [s.material.name if s.material else None for s in obj.material_slots],
                        "uv_order": order, "active_uv": mesh.uv_layers.active.name if mesh.uv_layers.active else None,
                        "hash_skip_reason": reason if hash_uv else 'disabled',
                        "uv_layers": [_uv_record(u, hash_uv and reason is None) for u in mesh.uv_layers]})
    material_records = [{"name": name, "node_images": sorted(_node_images(mat.node_tree))}
                        for name, mat in sorted(materials.items())]
    surface_images = {name for mat in material_records for name in mat['node_images']}
    world_images = _node_images(scene.world.node_tree if scene.world else None)
    if isinstance(image_names, str):
        raise TypeError('image_names must be an iterable of names, not a string')
    names = sorted(set(image_names)) if image_names is not None else sorted(bpy.data.images.keys())
    missing = [name for name in names if name not in bpy.data.images]
    if missing:
        raise ValueError('Unknown image datablocks: ' + ', '.join(missing))
    images = [_image_record(bpy.data.images[name]) for name in names]
    for im in images:
        im['referenced_by_audited_materials'] = im['name'] in surface_images
        im['referenced_by_world'] = im['name'] in world_images
        if im['dependency_state'] == 'external_missing':
            issues.append({"code": "missing_external_image", "image": im['name']})
    return {"schema_version": 1, "file": bpy.data.filepath, "scene": scene.name,
            "view_layer": bpy.context.view_layer.name, "mode": bpy.context.mode,
            "unsaved_changes": bpy.data.is_dirty, "blender_version": bpy.app.version_string,
            "selection_policy": 'explicit_objects' if object_names is not None else 'render_candidates',
            "summary": {"scene_mesh_objects": sum(o.type == 'MESH' for o in scene.objects),
                        "audited_mesh_objects": len(objects),
                        "base_triangle_estimate": sum(o['base_triangle_estimate'] for o in records),
                        "assigned_materials": len(materials), "material_node_images": len(surface_images),
                        "world_node_images": len(world_images), "inspected_image_datablocks": len(images)},
            "objects": records, "materials": material_records, "images": images,
            "jobs": jobs, "issues": issues,
            "limits": 'Base mesh estimates exclude modifier evaluation and generated instances. Render candidates '
                      'honour view-layer exclusion and render-hide flags, not ray visibility or camera framing. '
                      'Material node images include inactive nodes; all-image records include diagnostics. '
                      'Hashes do not prove UV non-overlap. No pixel, on-disk-versus-packed, appearance or runtime '
                      'validation is performed. Job flags/counters are observations, not proof of completion.'}


def compare_uv_snapshots(before, after):
    """Compare matching audit scopes; missing/skipped hashes never mean unchanged."""
    def signature(obj):
        return (obj['uv_order'], [(u['name'], u['loop_count'], u['sha256']) for u in obj['uv_layers']])
    a = {obj['name']: obj for obj in before['objects']}
    b = {obj['name']: obj for obj in after['objects']}
    common = sorted(a.keys() & b.keys())
    unavailable = [n for n in common if any(u['sha256'] is None for o in (a[n], b[n]) for u in o['uv_layers'])
                   or a[n]['hash_skip_reason'] is not None or b[n]['hash_skip_reason'] is not None]
    changed = [n for n in common if n not in unavailable and signature(a[n]) != signature(b[n])]
    added, removed = sorted(b.keys() - a.keys()), sorted(a.keys() - b.keys())
    return {"unchanged": not (unavailable or changed or added or removed), "changed": changed,
            "unavailable": unavailable, "added": added, "removed": removed}


def snapshot_uv2(object_names, *, uv='SimpleBake', uv1=None, expected_uv_order=None,
                 selection_metadata=None, expected_scene=None):
    """Extract an explicit evaluated receiver set without operators or data edits.

    Caller owns membership: include the connected bake floor and omit its runtime
    replacements/helpers/glass in project selectors. This helper does not infer roles.
    Load uv_audit by file path so inspection works from a packaged installation.
    """
    import importlib.util
    from pathlib import Path
    import numpy as np
    if isinstance(object_names, str):
        raise TypeError('object_names must be an explicit iterable of mesh names')
    supplied = list(object_names)
    if not supplied or len(set(supplied)) != len(supplied):
        raise ValueError('Provide a nonempty, duplicate-free intended receiver set')
    names = sorted(supplied)
    scene = bpy.context.scene
    if expected_scene is not None and scene.name != expected_scene:
        raise ValueError('UV snapshot active scene differs from the expected physical scene')
    invalid = [name for name in names if name not in scene.objects or scene.objects[name].type != 'MESH']
    if invalid:
        raise ValueError('Unknown or non-mesh intended UV participants: '+', '.join(invalid))
    jobs = inspect_jobs()
    if (bpy.context.mode != 'OBJECT' or any(value is True for value in jobs['native'].values())
            or any(state['is_baking'] is True for state in jobs['simplebake_states'])):
        raise ValueError('UV snapshot requires Object Mode and no active/stale bake or render job')
    path = Path(__file__).with_name('uv_audit.py')
    spec = importlib.util.spec_from_file_location('bjs_uv_audit_snapshot', path)
    core = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(core)
    deps = bpy.context.evaluated_depsgraph_get()
    records, triangles = [], []
    def digest(parts):
        result = hashlib.sha256()
        for name, values, dtype in parts:
            values = np.asarray(values, dtype=dtype)
            result.update(name.encode())
            result.update(str(values.shape).encode())
            result.update(values.tobytes())
        return result.hexdigest()
    for name in names:
        obj = scene.objects[name]
        evaluated = obj.evaluated_get(deps)
        mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=deps)
        try:
            order = [layer.name for layer in mesh.uv_layers]
            if expected_uv_order is not None and order != list(expected_uv_order):
                raise ValueError('Evaluated UV channel order differs on '+name+': '+str(order))
            primary = uv1 or (order[0] if order else '')
            if uv not in order or primary not in order or uv == primary:
                raise ValueError('UV snapshot requires distinct existing UV1 and UV2 on '+name)
            mesh.calc_loop_triangles()
            matrix = obj.matrix_world
            normal_matrix = matrix.to_3x3().inverted().transposed()
            world_vertices = [list(matrix @ vertex.co) for vertex in mesh.vertices]
            world_normals = [list((normal_matrix @ normal.vector).normalized()) for normal in mesh.corner_normals]
            loop_vertices = [loop.vertex_index for loop in mesh.loops]
            polygons = [(poly.loop_start, poly.loop_total, poly.use_smooth, poly.material_index) for poly in mesh.polygons]
            primary_values = [list(value.uv) for value in mesh.uv_layers[primary].data]
            uv_values = [list(value.uv) for value in mesh.uv_layers[uv].data]
            if not np.isfinite(np.asarray(uv_values)).all() or not np.isfinite(np.asarray(primary_values)).all():
                raise ValueError('Non-finite evaluated UV coordinates on '+name)
            records.append({'object': name, 'base_mesh': obj.data.name,
                'evaluated_vertices': len(mesh.vertices), 'evaluated_loops': len(mesh.loops),
                'evaluated_triangles': len(mesh.loop_triangles), 'uv_order': order, 'uv1': primary, 'uv2': uv,
                'world_geometry_sha256': digest([('vertices', world_vertices, '<f8'), ('normals', world_normals, '<f8'),
                    ('loops', loop_vertices, '<i4'), ('polygons', polygons, '<i4'), ('matrix', list(matrix), '<f8')]),
                'uv1_sha256': digest([(primary, primary_values, '<f4')]),
                'uv2_sha256': digest([(uv, uv_values, '<f4')])})
            for triangle in mesh.loop_triangles:
                triangles.append({'object': name, 'triangle': triangle.index, 'polygon': triangle.polygon_index,
                    'loops': list(triangle.loops), 'vertices': list(triangle.vertices),
                    'uv': [uv_values[index] for index in triangle.loops]})
        finally:
            evaluated.to_mesh_clear()
    for row, chart in zip(triangles, core.chart_ids(triangles)):
        row['chart'] = chart
    source_path = Path(bpy.data.filepath) if bpy.data.filepath else None
    file_hash = None
    if source_path and source_path.is_file():
        source_hash = hashlib.sha256()
        with source_path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024*1024), b''):
                source_hash.update(block)
        file_hash = source_hash.hexdigest()
    return {'schema_version': 1, 'kind': 'evaluated-uv2-triangle-snapshot', 'triangles': triangles,
        'objects': records, 'provenance': {'file': bpy.data.filepath, 'source_file_sha256': file_hash,
            'scene': scene.name, 'view_layer': bpy.context.view_layer.name, 'blender_version': bpy.app.version_string,
            'receiver_names': names, 'receiver_selector_metadata': selection_metadata,
            'uv2': uv, 'expected_uv_order': list(expected_uv_order) if expected_uv_order is not None else None,
            'geometry_uv1_identity_sha256': hashlib.sha256(json.dumps(
                [(row['object'], row['world_geometry_sha256'], row['uv1_sha256']) for row in records], separators=(',', ':')).encode()).hexdigest(),
            'uv2_identity_sha256': hashlib.sha256(json.dumps(
                [(row['object'], row['uv2_sha256']) for row in records], separators=(',', ':')).encode()).hexdigest(),
            'snapshot_helper_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'uv_audit_helper_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'read_only': True,
            'limits': 'Evaluated object meshes only; unrealized instances are not separately enumerated. Exact UV/geometry edge chart connectivity may differ from bake catalogue seam-rounding. Actual native bake coverage remains separate.'}}


if __name__ == '__main__':
    print(json.dumps(inspect_blender(), indent=2))
