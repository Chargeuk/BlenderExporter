"""Recipe-driven receiver UV2 authoring; no saves, baking or automatic retries.

Pure recipe/density functions can be imported outside Blender. Blender entry
points prepare(), pack_existing(), catalogue() import bpy only when called.
Unexpected modal packing holds its temporary context until finish_pending().
"""
import copy
import hashlib
import importlib
import json
import math
from pathlib import Path
import uuid

SCHEMA = 'babylon_js.receiver_uv_prepare.v1'
_PENDING = {}
LIMITS = ('Geometric triangle audit only. No sampled-pixel ownership, measured '
          'clearance, minimum local chart thickness, appearance, bake or runtime proof.')


class PreparationError(RuntimeError):
    """A rolled-back mutation failure with a serializable diagnostic receipt."""
    def __init__(self, message, report):
        super().__init__(message)
        self.report = report


def _keys(value, allowed, where):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError('Unknown/non-object fields at ' + where)


def _positive(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(label + ' must be finite and positive')
    return float(value)


def validate_recipe(recipe, objects):
    """Resolve exact names against {name: environment_role}; reject silent misses."""
    r = copy.deepcopy(recipe)
    _keys(r, ('schema', 'selector', 'uv1', 'uv2', 'default', 'overrides', 'pack',
              'replace_existing', 'planarity_tolerance_m', 'chart_overrides'), 'recipe')
    if r.get('schema') != SCHEMA or r.get('uv1') != 'UVMap' or r.get('uv2') != 'SimpleBake2':
        raise ValueError('Require schema v1 and UVMap / SimpleBake2 channel order')
    selector = r.get('selector', {})
    _keys(selector, ('role', 'names', 'all_role'), 'selector')
    if selector.get('role') != 'RECEIVER':
        raise ValueError('Selector must explicitly restrict environment_role=RECEIVER')
    if ('names' in selector) == ('all_role' in selector):
        raise ValueError('Choose exact names OR all_role=true')
    if 'all_role' in selector:
        if selector['all_role'] is not True:
            raise ValueError('all_role must be true')
        names = sorted(n for n, role in objects.items() if role == 'RECEIVER')
    else:
        names = selector['names']
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names) or len(set(names)) != len(names):
            raise ValueError('Names must be a unique list of exact strings')
        if any(objects.get(n) != 'RECEIVER' for n in names):
            raise ValueError('Unknown or excluded/non-receiver name')
        names = sorted(names)
    if not names:
        raise ValueError('Empty receiver set')
    default = r.get('default', {})
    overrides = r.get('overrides', {})
    if not isinstance(overrides, dict) or set(overrides) - set(names):
        raise ValueError('Override typo or override outside selected receivers')
    resolved = {}
    for name in names:
        _keys(default, ('projection', 'density_weight', 'angle_degrees', 'seam_edges', 'unwrap_method'), 'default')
        _keys(overrides.get(name, {}), tuple(default) + ('projection', 'density_weight', 'angle_degrees', 'seam_edges', 'unwrap_method'), name)
        rule = dict(default, **overrides.get(name, {}))
        if rule.get('projection') not in ('WORLD_XY', 'SMART_PROJECT', 'UNWRAP'):
            raise ValueError('Unsupported projection: ' + name)
        rule['density_weight'] = _positive(rule.get('density_weight'), name + ' density_weight')
        if rule['projection'] == 'SMART_PROJECT':
            angle = _positive(rule.get('angle_degrees', 66), name + ' angle_degrees')
            if angle >= 90:
                raise ValueError('Smart projection angle must be below90 degrees')
            rule['angle_degrees'] = angle
        if 'seam_edges' in rule and (not isinstance(rule['seam_edges'], list) or
            any(type(i) is not int or i < 0 for i in rule['seam_edges']) or
            len(set(rule['seam_edges'])) != len(rule['seam_edges'])):
            raise ValueError('seam_edges must contain unique nonnegative edge indices')
        resolved[name] = rule
    chart_weights = {}
    if not isinstance(r.get('chart_overrides', []), list):
        raise ValueError('chart_overrides must be an explicit list')
    for entry in r.get('chart_overrides', []):
        _keys(entry, ('object', 'polygon_root', 'density_weight'), 'chart_override')
        name, root = entry.get('object'), entry.get('polygon_root')
        if name not in names or type(root) is not int or root < 0 or (name, root) in chart_weights:
            raise ValueError('Unknown/duplicate chart override identity')
        chart_weights[name, root] = _positive(entry.get('density_weight'), 'chart density_weight')
    p = r.get('pack', {})
    _keys(p, ('pixel_reference', 'gap_pixels', 'border_pixels', 'precision', 'search_seconds', 'rotation_step_degrees'), 'pack')
    if any(type(p.get(k)) is not int for k in ('pixel_reference', 'gap_pixels', 'border_pixels', 'precision', 'search_seconds', 'rotation_step_degrees')):
        raise ValueError('Pack settings must be explicit integers')
    if (p['pixel_reference'], p['gap_pixels'], p['border_pixels'], p['precision']) != (2048, 4, 2, 5000):
        raise ValueError('Require square2048 pixel reference, gap4, border2, precision5000')
    if not 1 <= p['search_seconds'] <= 60 or p['rotation_step_degrees'] not in (90, 180):
        raise ValueError('Require finite1..60 second search and90/180 degree rotation')
    if type(r.get('replace_existing', False)) is not bool:
        raise ValueError('replace_existing must be boolean')
    r['planarity_tolerance_m'] = _positive(r.get('planarity_tolerance_m', 1e-5), 'planarity_tolerance_m')
    return r, resolved


def density_scale(world_area, uv_area, weight):
    """Uniform scale yielding UV area / world area = positive LINEAR weight²."""
    return _positive(weight, 'weight') * math.sqrt(_positive(world_area, 'world_area') / _positive(uv_area, 'uv_area'))


def pack_status(operator_result, engine_retcode):
    result = set(operator_result)
    if 'RUNNING_MODAL' in result:
        return 'PENDING'
    return 'SUCCESS' if 'FINISHED' in result and 'CANCELLED' not in result and engine_retcode == 0 else 'FAILED'


def _resolve(context, recipe):
    r, rules = validate_recipe(recipe, {o.name: o.get('environment_role') for o in context.scene.objects})
    objects = [context.scene.objects[n] for n in rules]
    for o in objects:
        if o.type != 'MESH' or o.library or o.data.library or o.modifiers or not o.data.polygons:
            raise ValueError('Require local nonempty modifier-free receiver: ' + o.name)
        layers = [u.name for u in o.data.uv_layers]
        if layers not in (['UVMap'], ['UVMap', 'SimpleBake2']):
            raise ValueError('Require UVMap first and optional SimpleBake2 second: ' + o.name)
        if o not in context.view_layer.objects.values() or o.hide_viewport:
            raise ValueError('Receiver not editable in current view layer: ' + o.name)
        seams = rules[o.name].get('seam_edges', [])
        if any(i >= len(o.data.edges) for i in seams):
            raise ValueError('Seam edge index out of range: ' + o.name)
    return r, rules, objects


def _uv_values(layer):
    return [tuple(a.uv) for a in layer.data]


def _edit_uv_values(mesh, bm, name='UVMap'):
    """Read extant Edit UVs in canonical Mesh polygon/corner order, without writes."""
    layer = bm.loops.layers.uv.get(name)
    if layer is None:
        raise ValueError('Declared protected UV layer missing from Edit BMesh: ' + name)
    if len(bm.verts) != len(mesh.vertices) or len(bm.edges) != len(mesh.edges) or len(bm.faces) != len(mesh.polygons):
        raise ValueError('Edit BMesh topology does not match canonical Mesh ordering')
    by_index = {}
    for face in bm.faces:
        if face.index < 0 or face.index >= len(mesh.polygons) or face.index in by_index:
            raise ValueError('Edit BMesh polygon indices unavailable or ambiguous')
        by_index[face.index] = face
    values = [None] * len(mesh.loops)
    for polygon in mesh.polygons:
        if polygon.index not in by_index:
            raise ValueError('Canonical Mesh polygon missing from Edit BMesh')
        loops = list(by_index[polygon.index].loops)
        expected = list(polygon.vertices)
        actual = [loop.vert.index for loop in loops]
        if len(loops) != len(expected) or not expected:
            raise ValueError('Edit BMesh corner count does not match canonical Mesh polygon')
        starts = [i for i, index in enumerate(actual) if index == expected[0]]
        if len(starts) != 1:
            raise ValueError('Edit BMesh first corner is unavailable or ambiguous')
        start = starts[0]
        loops = loops[start:] + loops[:start]
        if [loop.vert.index for loop in loops] != expected:
            raise ValueError('Edit BMesh winding/corner order differs from canonical Mesh polygon')
        if len(polygon.loop_indices) != len(loops):
            raise ValueError('Canonical Mesh loop ordering is unavailable')
        for loop, index, vertex in zip(loops, polygon.loop_indices, expected):
            if index < 0 or index >= len(values) or values[index] is not None or mesh.loops[index].vertex_index != vertex:
                raise ValueError('Canonical Mesh corner index is unavailable or ambiguous')
            value = tuple(loop[layer].uv)
            if len(value) != 2 or not all(math.isfinite(v) for v in value):
                raise ValueError('Edit protected UV values are nonfinite or malformed')
            values[index] = value
    if any(v is None for v in values):
        raise ValueError('Edit protected UV values do not cover every canonical Mesh corner')
    return values


def _guard_uv1(mesh):
    # Object Mode remains the original MeshUVLoop read. Other selected meshes
    # with no declared UVMap retain their original None sentinel.
    if 'UVMap' not in mesh.uv_layers:
        return None
    if mesh.is_editmode:
        import bmesh
        return _edit_uv_values(mesh, bmesh.from_edit_mesh(mesh), 'UVMap')
    return _uv_values(mesh.uv_layers['UVMap'])


def _placement_users(mesh):
    import bpy
    return sum(o.type == 'MESH' and o.data == mesh for o in bpy.data.objects)


def _guard(o):
    m = o.data
    values = {'vertices': [tuple(v.co) for v in m.vertices], 'faces': [tuple(p.vertices) for p in m.polygons],
              'edges': [(tuple(e.vertices), e.use_seam) for e in m.edges],
              'normals': [tuple(n.vector) for n in m.corner_normals],
              'smooth_materials': [(p.use_smooth, p.material_index) for p in m.polygons],
              'UV1': _guard_uv1(m), 'matrix': [tuple(row) for row in o.matrix_world],
              'parent': o.parent.name if o.parent else None, 'role': o.get('environment_role'),
              'materials': [a.name if a else None for a in m.materials]}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


# Verified Blender5.2.2 persistent storage, not guessed MeshUVLoopLayer properties.
# Selection is mesh-wide; .pn.<layer> pins remain layer-specific.
_UV_SELECT_STORAGE = {'.uv_select_vert': 'CORNER', '.uv_select_edge': 'CORNER', '.uv_select_face': 'FACE'}
_MESH_SELECT_STORAGE = {'.select_vert': 'POINT', '.select_edge': 'EDGE', '.select_poly': 'FACE'}
_MESH_HIDE_STORAGE = {'.hide_vert': 'POINT', '.hide_edge': 'EDGE', '.hide_poly': 'FACE'}


def _bool_storage_read(mesh, names):
    result = {}
    for name, domain in names.items():
        attr = mesh.attributes.get(name)
        if attr is not None and (attr.data_type != 'BOOLEAN' or attr.domain != domain):
            raise ValueError('Wrong Boolean selection/pin storage schema: ' + name)
        result[name] = None if attr is None else [bool(a.value) for a in attr.data]
    return result


def _bool_storage_write(mesh, names, buffers):
    """Restore both attribute absence and exact buffers; never rewrite geometry."""
    if set(names) != set(buffers):
        raise ValueError('Selection/pin storage snapshot names disagree')
    for name, domain in names.items():
        attr = mesh.attributes.get(name)
        if attr is not None and (attr.data_type != 'BOOLEAN' or attr.domain != domain):
            raise ValueError('Wrong Boolean selection/pin storage schema: ' + name)
        values = buffers[name]
        if values is None:
            if attr is not None:
                mesh.attributes.remove(attr)
            continue
        if attr is None:
            attr = mesh.attributes.new(name=name, type='BOOLEAN', domain=domain)
        if len(attr.data) != len(values):
            raise ValueError('Selection/pin buffer length changed: ' + name)
        for a, value in zip(attr.data, values):
            a.value = value
    if _bool_storage_read(mesh, names) != buffers:
        raise RuntimeError('Selection/pin storage restoration did not preserve exact buffers')


def _modern_uv_selection(mesh):
    import bmesh
    return hasattr(bmesh.types.BMLoop, 'uv_select_vert') and hasattr(bmesh.types.BMFace, 'uv_select')


def _edit_uv_snapshot(bm):
    """Read extant Edit BMesh before teardown, without flushing/synchronising."""
    bm.faces.ensure_lookup_table()
    bm.faces.index_update()
    return {'sync_valid': bool(bm.uv_select_sync_valid),
            'faces': [{'index': f.index, 'selected': bool(f.uv_select),
                       'loops': [(bool(l.uv_select_vert), bool(l.uv_select_edge)) for l in f.loops]} for f in bm.faces]}


def _restore_edit_uv(bm, snapshot):
    """Flags/cache only: no bm.to_mesh/update_edit_mesh or geometry roundtrip."""
    bm.faces.ensure_lookup_table()
    if len(bm.faces) != len(snapshot['faces']):
        raise ValueError('Edit UV selection face count changed')
    for row in snapshot['faces']:
        f = bm.faces[row['index']]
        if len(f.loops) != len(row['loops']):
            raise ValueError('Edit UV selection loop count changed')
        f.uv_select = row['selected']
        for loop, (vert, edge) in zip(f.loops, row['loops']):
            loop.uv_select_vert = vert
            loop.uv_select_edge = edge
    bm.uv_select_sync_valid = snapshot['sync_valid']
    if _edit_uv_snapshot(bm) != snapshot:
        raise RuntimeError('Edit UV selection/runtime validity did not restore exactly')


class _ContextState:
    """Restore temporary selection, active/render UVs, hidden elements and mode."""
    def __init__(self, context, objects):
        import bpy
        self.context = context
        self.active = context.view_layer.objects.active
        self.mode = self.active.mode if self.active else 'OBJECT'
        if self.mode not in ('OBJECT', 'EDIT'):
            raise ValueError('Supported starting modes are OBJECT and mesh EDIT')
        if self.mode == 'EDIT' and self.active.type != 'MESH':
            raise ValueError('Only mesh EDIT context is supported')
        self.selected = list(context.selected_objects)
        self.edit_members = list(context.objects_in_mode) if self.mode == 'EDIT' else []
        self.edit_uv = {}
        if self.mode == 'EDIT':
            import bmesh
            for o in self.edit_members:
                if _modern_uv_selection(o.data):
                    self.edit_uv[o] = _edit_uv_snapshot(bmesh.from_edit_mesh(o.data))
        if self.mode == 'EDIT':
            bpy.ops.object.mode_set(mode='OBJECT')
        self.sync = context.scene.tool_settings.use_uv_select_sync
        self.mesh_select_mode = tuple(context.scene.tool_settings.mesh_select_mode)
        self.meshes = {}
        for o in set(objects + self.selected):
            if o.type != 'MESH':
                continue
            m = o.data
            modern = _modern_uv_selection(m)
            storage_names = dict(_UV_SELECT_STORAGE, **_MESH_SELECT_STORAGE)
            storage_names.update(_MESH_HIDE_STORAGE)
            storage_names.update({'.pn.' + u.name: 'CORNER' for u in m.uv_layers})
            self.meshes[o] = {'hidden': o.hide_get(), 'active': m.uv_layers.active.name if m.uv_layers.active else None,
                'render': next((u.name for u in m.uv_layers if u.active_render), None),
                'vertices': [(a.select, a.hide) for a in m.vertices], 'edges': [(a.select, a.hide) for a in m.edges],
                'faces': [(a.select, a.hide) for a in m.polygons],
                'modern': modern, 'storage_names': storage_names if modern else {},
                'storage_buffers': _bool_storage_read(m, storage_names) if modern else {},
                'uv_flags': {} if modern else {u.name: [(a.select, a.select_edge, a.pin_uv) for a in u.data] for u in m.uv_layers}}
        self.protected = {o: _guard(o) for o in self.meshes}

    def select(self, objects, uv):
        import bpy
        c = self.context
        if c.object and c.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for o in c.selected_objects:
            o.select_set(False)
        for o in objects:
            o.hide_set(False)
            o.select_set(True)
            m = o.data
            m.uv_layers.active_index = m.uv_layers.find(uv)
            modern = _modern_uv_selection(m)
            for seq in (m.vertices, m.edges, m.polygons):
                for a in seq:
                    if a.hide:
                        a.hide = False
                    a.select = True
            if modern:
                selected = {name: [True] * (len(m.polygons) if domain == 'FACE' else len(m.loops)) for name, domain in _UV_SELECT_STORAGE.items()}
                _bool_storage_write(m, _UV_SELECT_STORAGE, selected)
            for a in m.uv_layers[uv].data:
                if not modern:
                    a.select = a.select_edge = True
                a.pin_uv = False
        c.view_layer.objects.active = objects[0]
        c.scene.tool_settings.use_uv_select_sync = True
        c.scene.tool_settings.mesh_select_mode = (False, False, True)

    def restore(self):
        import bpy
        c = self.context
        if c.object and c.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for o, x in self.meshes.items():
            m = o.data
            for seq, key in ((m.vertices, 'vertices'), (m.edges, 'edges'), (m.polygons, 'faces')):
                for a, (selected, hidden) in zip(seq, x[key]):
                    a.select = selected
                    if a.hide != hidden:
                        a.hide = hidden
            if x['modern']:
                names = dict(x['storage_names'])
                buffers = dict(x['storage_buffers'])
                # A newly authored layer had no pre-existing pins. Remove only its
                # temporary pin storage; do not fabricate a zero-valued attribute.
                for u in m.uv_layers:
                    name = '.pn.' + u.name
                    if name not in names:
                        names[name], buffers[name] = 'CORNER', None
                _bool_storage_write(m, names, buffers)
            else:
                for u in m.uv_layers:
                    if u.name in x['uv_flags']:
                        for a, flags in zip(u.data, x['uv_flags'][u.name]):
                            a.select, a.select_edge, a.pin_uv = flags
                    else:
                        for a in u.data:
                            a.select = a.select_edge = a.pin_uv = False
            if x['active']:
                m.uv_layers.active_index = m.uv_layers.find(x['active'])
            for u in m.uv_layers:
                u.active_render = u.name == x['render']
            o.hide_set(x['hidden'])
        c.scene.tool_settings.use_uv_select_sync = self.sync
        c.scene.tool_settings.mesh_select_mode = self.mesh_select_mode
        for o in c.selected_objects:
            o.select_set(False)
        for o in self.selected:
            o.select_set(True)
        c.view_layer.objects.active = self.active
        if self.mode == 'EDIT':
            # Restore the exact prior multi-object edit membership, then selection.
            for o in c.selected_objects:
                o.select_set(o in self.edit_members)
            bpy.ops.object.mode_set(mode='EDIT')
            if self.edit_uv:
                import bmesh
                for o, snapshot in self.edit_uv.items():
                    _restore_edit_uv(bmesh.from_edit_mesh(o.data), snapshot)
            for o in self.selected:
                o.select_set(True)
        # Verify after ALL context work, including returning to multi-object EDIT.
        # Object mode has no extant Edit BMesh sync-valid runtime state to restore.
        changed = [o.name for o, wanted in self.protected.items() if _guard(o) != wanted]
        if changed:
            raise RuntimeError('Protected state changed after context restoration: ' + repr(changed))


def _charts(mesh, uv):
    """Face connectivity through geometric edges whose target UV endpoints agree."""
    parent = list(range(len(mesh.polygons)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    edges = {}
    for p in mesh.polygons:
        ls = list(p.loop_indices)
        for a, b in zip(ls, ls[1:] + ls[:1]):
            key = tuple(sorted((mesh.loops[q].vertex_index, round(uv.data[q].uv.x, 7), round(uv.data[q].uv.y, 7)) for q in (a, b)))
            if key in edges:
                parent[root(p.index)] = root(edges[key])
            else:
                edges[key] = p.index
    groups = {}
    for p in mesh.polygons:
        groups.setdefault(root(p.index), []).append(p.index)
    return {min(faces): faces for faces in groups.values()}


def _chart_weights(recipe, rules, objects):
    available = {(o.name, root) for o in objects for root in _charts(o.data, o.data.uv_layers['SimpleBake2'])}
    overrides = {(v['object'], v['polygon_root']): v['density_weight'] for v in recipe.get('chart_overrides', [])}
    if set(overrides) - available:
        raise ValueError('Chart override root missing from current target catalogue: ' + repr(set(overrides)-available))
    return {key: overrides.get(key, rules[key[0]]['density_weight']) for key in available}


def _weight_charts(recipe, rules, objects):
    weights = _chart_weights(recipe, rules, objects)
    for o in objects:
        m = o.data
        m.calc_loop_triangles()
        uv = m.uv_layers['SimpleBake2']
        for root, faces in _charts(m, uv).items():
            ts = [t for t in m.loop_triangles if t.polygon_index in faces]
            wa = math.fsum(_area3([o.matrix_world @ m.vertices[i].co for i in t.vertices]) for t in ts)
            ua = math.fsum(_area2([uv.data[l].uv for l in t.loops]) for t in ts)
            scale = density_scale(wa, ua, weights[o.name, root])
            loops = [l for fi in faces for l in m.polygons[fi].loop_indices]
            center = tuple(math.fsum(uv.data[l].uv[k] for l in loops)/len(loops) for k in (0,1))
            for l in loops:
                uv.data[l].uv = tuple(center[k]+(uv.data[l].uv[k]-center[k])*scale for k in (0,1))


def _area3(points):
    return (points[1] - points[0]).cross(points[2] - points[0]).length * .5


def _area2(points):
    a, b, c = points
    return abs((b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])) * .5


def normalize_chart_for_audit(entry, original_area_epsilon=1e-12):
    """Pure per-chart copy; translation/uniform scaling preserve aspect/winding.

    Epsilon remains in ORIGINAL weighted UV² through the inverse span² mapping.
    Neither original coordinates nor the final raw PACKED audit are modified.
    """
    epsilon = _positive(original_area_epsilon, 'original chart area epsilon')
    triangles = entry.get('triangles') if isinstance(entry, dict) else None
    if not isinstance(triangles, list) or not triangles:
        raise ValueError('Unpacked chart requires nonempty triangle list')
    points = []
    for triangle in triangles:
        if not isinstance(triangle, (tuple, list)) or len(triangle) != 3:
            raise ValueError('Unpacked chart triangle must have three points')
        for point in triangle:
            if not isinstance(point, (tuple, list)) or len(point) != 2 or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in point):
                raise ValueError('Unpacked chart coordinates must be finite numeric UV pairs')
            points.append(point)
    lower = [min(p[k] for p in points) for k in (0, 1)]
    upper = [max(p[k] for p in points) for k in (0, 1)]
    span = _positive(max(upper[k]-lower[k] for k in (0, 1)), 'chart maximum bounding span')
    scale = _positive(1.0/span, 'chart normalization scale')
    normalized_epsilon = epsilon / span / span
    if not math.isfinite(normalized_epsilon) or normalized_epsilon <= 0:
        raise ValueError('Original chart epsilon cannot be represented after normalization')
    result = copy.deepcopy(entry)
    result['triangles'] = [[[(p[k]-lower[k])/span for k in (0, 1)] for p in t] for t in triangles]
    transform = {'original_entry_identity': {k: entry.get(k) for k in ('id', 'object', 'polygon_root')},
        'original_entry_sha256': hashlib.sha256(json.dumps(entry, sort_keys=True, allow_nan=False).encode()).hexdigest(),
        'original_uv_bounds': [lower, upper], 'translation_original_uv': [-v for v in lower],
        'uniform_divisor_original_uv': span, 'uniform_scale': scale,
        'original_area_epsilon_weighted_uv_squared': epsilon,
        'normalized_area_epsilon_uv_squared': normalized_epsilon,
        'normalized_bounds_tolerance_uv': 1e-6, 'equivalent_bounds_slack_original_uv': 1e-6*span,
        'coordinate_formula': '(original_uv - original_min_uv) / max_bbox_span',
        'area_formula': 'normalized_area = original_weighted_uv_area / max_bbox_span²',
        'scope': 'Intrinsic within this chart only; no global unpacked/packed layout, spacing or minimum local thickness proof.'}
    return result, transform


def _audit_unpacked_chart(entry, kernel):
    normalized, transform = normalize_chart_for_audit(entry)
    report = kernel([normalized], bounds_tolerance=1e-6,
                    area_epsilon=transform['normalized_area_epsilon_uv_squared'])
    report['unpacked_chart_normalization'] = transform
    report['reported_area_units'] = 'Normalized per-chart UV squared; multiply by original max_bbox_span² for original weighted UV squared.'
    return report


def catalogue(recipe, context=None, audit_scope='PACKED'):
    """Read-only raw-mesh catalogue; modifier-free policy makes raw=evaluated."""
    import bpy
    from .uv_geometry import audit_catalogue
    c = context or bpy.context
    r, rules, objects = _resolve(c, recipe)
    if audit_scope not in ('PACKED', 'CHARTS', 'NONE'):
        raise ValueError('Unknown audit scope')
    weights = _chart_weights(r, rules, objects)
    entries, stats = [], []
    for o in objects:
        m = o.data
        if 'SimpleBake2' not in m.uv_layers:
            raise ValueError('Missing prepared target UV2: ' + o.name)
        m.calc_loop_triangles()
        uv = m.uv_layers['SimpleBake2']
        for root, faces in _charts(m, uv).items():
            tris = [t for t in m.loop_triangles if t.polygon_index in faces]
            points = [[list(uv.data[l].uv) for l in t.loops] for t in tris]
            world_area = math.fsum(_area3([o.matrix_world @ m.vertices[i].co for i in t.vertices]) for t in tris)
            uv_area = math.fsum(_area2(t) for t in points)
            values = [a for t in points for a in t]
            weight = weights[o.name, root]
            entries.append({'id': len(entries)+1, 'object': o.name, 'polygon_root': root, 'triangles': points})
            stats.append({'id': len(entries), 'object': o.name, 'polygon_root': root, 'faces': faces, 'world_area_m2': world_area,
                'uv_triangle_area': uv_area, 'linear_density_weight': weight,
                'linear_density_ratio': math.sqrt(uv_area/world_area) if world_area > 0 else None,
                'density_divided_by_weight': math.sqrt(uv_area/world_area)/weight if world_area > 0 else None,
                'bounds': [[min(p[k] for p in values) for k in (0,1)], [max(p[k] for p in values) for k in (0,1)]]})
    if audit_scope == 'PACKED':
        audit = audit_catalogue(entries)
    elif audit_scope == 'CHARTS':
        # Unpacked charts intentionally share space. Check intrinsic within-chart
        # geometry without quadratic across-chart comparisons or normalized-tile claims.
        audit = {'scope': 'Within each unpacked chart only; disclosed translated/uniformly normalized copies, original area threshold preserved. No global layout proof.',
                 'charts': [_audit_unpacked_chart(e, audit_catalogue) for e in entries]}
    else:
        audit = {'scope': 'Not requested; statistics/catalogue only'}
    return {'catalogue': entries, 'charts': stats, 'chart_count': len(entries),
            'geometry_audit': audit, 'audit_scope': audit_scope, 'limits': LIMITS,
            'area_method': 'Sum finite raw triangle areas; modifier-free meshes; world transforms applied. Chart bounding widths are not minimum local thickness.'}


def prepare(recipe, context=None):
    """Author weighted charts transactionally; success does NOT pack/save/bake."""
    import bpy
    c = context or bpy.context
    if _PENDING:
        raise RuntimeError('Finish pending pack before another operation')
    r, rules, objects = _resolve(c, recipe)
    if not r.get('replace_existing', False) and any('SimpleBake2' in o.data.uv_layers for o in objects):
        raise ValueError('Existing UV2; use pack_existing or explicitly authorize replace_existing')
    state = _ContextState(c, objects)
    originals = {o: (o.data, _placement_users(o.data), _guard(o)) for o in objects}
    old_target = {o: _uv_values(o.data.uv_layers['SimpleBake2']) if 'SimpleBake2' in o.data.uv_layers else None for o in objects}
    work = {}
    failure_report = {'status': 'FAILED', 'stage': 'prepare', 'prepared_uvs_restored': False}
    try:
        for o in objects:
            failure_report['object'] = o.name
            failure_report['projection'] = rules[o.name]['projection']
            m = o.data.copy()
            work[o] = m
            o.data = m
            if 'SimpleBake2' not in m.uv_layers:
                m.uv_layers.new(name='SimpleBake2', do_init=False)
            m.uv_layers.active_index = 1
            rule = rules[o.name]
            if rule['projection'] == 'WORLD_XY':
                points = [o.matrix_world @ v.co for v in m.vertices]
                if max(p.z for p in points)-min(p.z for p in points) > r['planarity_tolerance_m']:
                    raise ValueError('WORLD_XY requires coplanar worldZ: ' + o.name)
                for loop, a in zip(m.loops, m.uv_layers['SimpleBake2'].data):
                    p = points[loop.vertex_index]
                    a.uv = (p.x, p.y)
            else:
                if 'seam_edges' in rule:
                    for e in m.edges:
                        e.use_seam = e.index in rule['seam_edges']
                state.select([o], 'SimpleBake2')
                bpy.ops.object.mode_set(mode='EDIT')
                if rule['projection'] == 'SMART_PROJECT':
                    result = bpy.ops.uv.smart_project(angle_limit=math.radians(rule['angle_degrees']), island_margin=0.001,
                        correct_aspect=False, scale_to_bounds=False)
                else:
                    op = bpy.ops.uv.unwrap
                    method = rule.get('unwrap_method', 'ANGLE_BASED')
                    choices = {i.identifier for i in op.get_rna_type().properties['method'].enum_items}
                    if method not in choices:
                        raise ValueError('Unavailable unwrap enum: ' + method)
                    result = op(method=method, margin=0.001, correct_aspect=False)
                if 'FINISHED' not in result:
                    raise RuntimeError('Native projection incomplete: ' + str(result))
                bpy.ops.object.mode_set(mode='OBJECT')
                for edge, oldedge in zip(m.edges, originals[o][0].edges):
                    edge.use_seam = oldedge.use_seam
            if _guard(o) != originals[o][2]:
                raise RuntimeError('Protected geometry/UV1/normal/material state changed: ' + o.name)
        _weight_charts(r, rules, objects)
        result = catalogue(r, c, audit_scope='CHARTS')
        failure_report['diagnostic_catalogue'] = result
        if any(not x['passed'] for x in result['geometry_audit']['charts']):
            raise RuntimeError('Prepared chart geometric audit failed; inspect exact island/triangle receipt')
        # Unpacked charts intentionally overlap/extend outside0..1; record audit, do not mislabel it final.
        for o, (original, users, _) in originals.items():
            if users <= 1:
                target = original.uv_layers.get('SimpleBake2') or original.uv_layers.new(name='SimpleBake2', do_init=False)
                for a, b in zip(target.data, work[o].uv_layers['SimpleBake2'].data):
                    a.uv = b.uv
                o.data = original
        result.update(status='PREPARED_NOT_PACKED', split_linked_objects=[o.name for o, (_, users, _) in originals.items() if users > 1], no_save_or_bake=True)
        return result
    except Exception as error:
        if c.object and c.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for o, (original, _, _) in originals.items():
            o.data = original
            target = original.uv_layers.get('SimpleBake2')
            if old_target[o] is None and target is not None:
                original.uv_layers.remove(target)
            elif target is not None:
                for a, v in zip(target.data, old_target[o]):
                    a.uv = v
        failure_report.update(error=str(error), prepared_uvs_restored=True)
        raise PreparationError(str(error), failure_report) from error
    finally:
        try:
            state.restore()
        except Exception as restore_error:
            # A post-restoration guard failure is still a preparation failure.
            # Roll back committed UV2/copies, then make one explicitly recorded
            # context-restoration attempt; never rerun projection or packing.
            if c.object and c.object.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            for o, (original, _, _) in originals.items():
                o.data = original
                target = original.uv_layers.get('SimpleBake2')
                if old_target[o] is None and target is not None:
                    original.uv_layers.remove(target)
                elif target is not None:
                    for a, v in zip(target.data, old_target[o]):
                        a.uv = v
            failure_report.update(stage='context_restore', context_restore_error=str(restore_error), prepared_uvs_restored=True)
            try:
                state.restore()
            except Exception as final_error:
                failure_report['final_context_restore_error'] = str(final_error)
            raise PreparationError(str(restore_error), failure_report) from restore_error
        finally:
            for m in work.values():
                if m.users == 0:
                    bpy.data.meshes.remove(m)


def discover_uvpm(context=None):
    """Import enabled extension leaf, verify actual API/engine without preference writes."""
    import bpy
    c = context or bpy.context
    names = [n for n in c.preferences.addons.keys() if n.rsplit('.',1)[-1] == 'uvpackmaster4']
    if len(names) != 1:
        raise RuntimeError('Need exactly one enabled UVPackmaster4 package')
    package = names[0]
    interface = importlib.import_module(package + '.app_iface')
    prefs = interface.get_prefs()
    props = interface.get_main_props(c)
    types = importlib.import_module(package + '.spipeline.engine.types')
    utils = importlib.import_module(package + '.utils')
    engine = Path(utils.get_engine_execpath())
    if not engine.is_file():
        raise RuntimeError('UVPackmaster engine missing: ' + str(engine))
    return interface, prefs, props, types, {'package': package, 'module_path': interface.__file__, 'engine_path': str(engine), 'engine_exists': True}


def _set_checked(owner, name, value, restore):
    prop = owner.bl_rna.properties.get(name)
    if prop is None or prop.is_readonly:
        raise ValueError('Missing/wrong UVPM RNA property: ' + name)
    if prop.type == 'ENUM' and value not in {i.identifier for i in prop.enum_items}:
        raise ValueError('Unsupported UVPM enum: ' + name)
    restore.append((owner, name, getattr(owner, name)))
    setattr(owner, name, value)
    if getattr(owner, name) != value:
        raise ValueError('UVPM setting clamped/mismatched: ' + name)


def _capture_uvpm_group_settings(props, restore):
    for group in ('lock_group', 'track_group', 'stack_group'):
        descriptor = getattr(props.numbered_groups_descriptors, group)
        _set_checked(descriptor, 'iparam_name', descriptor.iparam_name, restore)
        _set_checked(descriptor, 'enable', False, restore)
    # UVPM pre_operation initializes this name even when normalize_scale=False.
    # Preserve its string only; do not change norm-group enabled behavior.
    descriptor = props.numbered_groups_descriptors.norm_group
    _set_checked(descriptor, 'iparam_name', descriptor.iparam_name, restore)


def _finish(session, operator_result, retcode):
    import bpy
    status = pack_status(operator_result, retcode)
    report = dict(session['report'], status=status, operator_result=sorted(operator_result), engine_retcode=retcode)
    if status == 'PENDING':
        return report
    c = session['context']
    if c.object and c.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    try:
        if status == 'SUCCESS':
            for o in session['objects']:
                if _guard(o) != session['guards'][o]:
                    raise RuntimeError('Protected state changed during packing: ' + o.name)
            facts = catalogue(session['recipe'], c)
            report['attempted_catalogue'] = facts
            ratios = [s['density_divided_by_weight'] for s in facts['charts']]
            if not ratios or min(ratios) <= 0 or max(ratios)/min(ratios) > 1.0002:
                raise RuntimeError('Packing changed intended relative linear densities')
            if not facts['geometry_audit']['passed']:
                raise RuntimeError('Packed geometric audit failed')
            report.update(facts)
            report.pop('attempted_catalogue')
            report['common_density_scale'] = math.fsum(ratios)/len(ratios)
    except Exception as error:
        status = report['status'] = 'FAILED'
        report['error'] = str(error)
    finally:
        if status != 'SUCCESS':
            for o, values in session['before'].items():
                for a, v in zip(o.data.uv_layers['SimpleBake2'].data, values):
                    a.uv = v
            report['prepared_uvs_restored'] = True
        for owner, name, value in reversed(session['settings_restore']):
            setattr(owner, name, value)
        try:
            session['state'].restore()
        except Exception as restore_error:
            report.update(status='FAILED', context_restore_error=str(restore_error))
            if c.object and c.object.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            try:
                for o, values in session['before'].items():
                    for a, v in zip(o.data.uv_layers['SimpleBake2'].data, values):
                        a.uv = v
                report['prepared_uvs_restored'] = True
                session['state'].restore()
            except Exception as final_error:
                report['final_context_restore_error'] = str(final_error)
        finally:
            _PENDING.pop(session['token'], None)
    return report


def pack_existing(recipe, context=None):
    """One synchronous joint pack. Failed packs restore the prepared layout; never retry."""
    import bpy
    c = context or bpy.context
    if _PENDING:
        raise RuntimeError('Finish pending pack before another operation')
    r, rules, objects = _resolve(c, recipe)
    if any(_placement_users(o.data) != 1 or 'SimpleBake2' not in o.data.uv_layers for o in objects):
        raise ValueError('Every receiver needs independent prepared UV2 data; call prepare first')
    area = next((a for a in c.window.screen.areas if a.type == 'IMAGE_EDITOR' and a.ui_type == 'UV'), None) if c.window else None
    if area is None:
        raise RuntimeError('Open an existing UV editor; helper does not replace user editors')
    region = next((a for a in area.regions if a.type == 'WINDOW'), None)
    _, prefs, props, types, discovery = discover_uvpm(c)
    state = _ContextState(c, objects)
    token = uuid.uuid4().hex
    session = {'context': c, 'recipe': r, 'objects': objects, 'state': state,
        'guards': {o: _guard(o) for o in objects}, 'before': {o: _uv_values(o.data.uv_layers['SimpleBake2']) for o in objects},
        'settings_restore': [], 'token': token, 'prefs': prefs, 'report': {'token': token, 'discovery': discovery,
        'settings': r['pack'], 'normalize_scale': False, 'non_square_packing': False,
        'mode': 'pack.single_tile', 'execution': 'EXEC_DEFAULT', 'limits': LIMITS, 'no_save_or_bake': True}}
    _PENDING[token] = session
    try:
        p = r['pack']
        settings = {'pixel_margin_enable': True, 'pixel_margin_tex_size': p['pixel_reference'], 'pixel_margin': p['gap_pixels'],
            'pixel_border_margin_enable': True, 'pixel_border_margin': p['border_pixels'], 'precision': p['precision'],
            'normalize_scale': False, 'non_square_packing': False, 'pixel_perfect_align': False,
            'custom_target_box_enable': False, 'lock_overlapping_enable': False, 'flipping_enable': False,
            'rotation_enable': True, 'rotation_step': p['rotation_step_degrees'], 'island_rot_step_enable': False,
            'island_normalize_multiplier_enable': False, 'island_tdensity_set_before_pack': False, 'tdensity_set_before_pack': False,
            'scale_mode': types.UvpmScaleMode.MAX_SCALE.value(), 'heuristic_enable': True,
            'heuristic_search_time': p['search_seconds'], 'heuristic_max_wait_time': p['search_seconds'],
            'heuristic_allow_mixed_scales': False, 'fully_inside': True}
        for name, value in settings.items():
            _set_checked(props, name, value, session['settings_restore'])
        _capture_uvpm_group_settings(props, session['settings_restore'])
        for name, value in {'dont_transform_pinned_uvs': False, 'script_allow_execution': False}.items():
            _set_checked(prefs, name, value, session['settings_restore'])
        session['report']['effective_properties'] = settings
        # Recipe may add canonical per-chart overrides after viewing the prepared catalogue.
        # Apply those weights explicitly once before this single pack; rollback uses original prepared buffers.
        _weight_charts(r, rules, objects)
        session['report']['weighted_before_pack'] = catalogue(r, c, audit_scope='CHARTS')
        state.select(objects, 'SimpleBake2')
        bpy.ops.object.mode_set(mode='EDIT')
        with c.temp_override(area=area, region=region):
            op = bpy.ops.uvpackmaster4.pack
            # mode_id is a StringProperty; PackOpType is discovered from enabled package.
            op.get_rna_type()  # fail before invocation if operator is not registered
            result = op('EXEC_DEFAULT', mode_id='pack.single_tile', pack_op_type=types.PackOpType.PACK.value())
        return _finish(session, result, prefs.engine_retcode)
    except Exception as error:
        session['report']['error'] = str(error)
        return _finish(session, {'CANCELLED'}, prefs.engine_retcode)


def finish_pending(token, completed_operator_result):
    """Only after externally observed operator completion; no polling/retry/cancel.

    EXEC_DEFAULT on4.1.2 is synchronous. If another version unexpectedly becomes
    modal, the caller must retain the session/context and supply its actual final
    result after observing completion. Never fabricate FINISHED from retcode alone.
    """
    session = _PENDING[token]
    return _finish(session, set(completed_operator_result), session['prefs'].engine_retcode)
