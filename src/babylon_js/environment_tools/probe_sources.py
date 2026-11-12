"""Resolve and temporarily boost Cycles light and authored surface emission."""
from contextlib import ExitStack, contextmanager
import math
from pathlib import Path
import re


def render_layer(scene, active):
    if active is not None and active.name in scene.view_layers and active.use:
        return scene.view_layers[active.name]
    layer = next((layer for layer in scene.view_layers if layer.use), None)
    if layer is None:
        raise ValueError('Advanced capture needs an enabled render view layer')
    return layer


def render_visible_objects(layer):
    """Use render flags, layer exclusion and collection instances, not viewport flags."""
    visible = set()
    instances = set()

    def add_objects(collection):
        for obj in collection.objects:
            if obj.hide_render:
                continue
            if obj.instance_type == 'NONE' or obj.show_instancer_for_render:
                visible.add(obj)
            target = obj.instance_collection if obj.instance_type == 'COLLECTION' else None
            if target is not None and target not in instances:
                instances.add(target)
                add_instance(target)

    def add_instance(collection):
        if not collection.hide_render:
            add_objects(collection)
            for child in collection.children:
                add_instance(child)

    def add_layer(layer_collection):
        if not layer_collection.exclude and not layer_collection.collection.hide_render:
            add_objects(layer_collection.collection)
            for child in layer_collection.children:
                add_layer(child)

    add_layer(layer.layer_collection)
    return visible


def _baked_display(material):
    from babylon_js.lighting_controls import _managed_graphs
    return material is not None and material.use_nodes and bool(_managed_graphs(material))


def _shader_details(material):
    """Trace active Cycles Surface closures, with group-instance socket mapping."""
    if not material.use_nodes or material.node_tree is None:
        return [], True, set()
    root = material.node_tree
    emissions = {}
    responsive = False
    limitations = set()
    seen = set()

    def visit_input(tree, socket, parents):
        for link in socket.links:
            if link.is_valid and not link.is_muted:
                visit_output(tree, link.from_socket, parents)

    def visit_output(tree, socket, parents):
        nonlocal responsive
        node = socket.node
        key = (socket.as_pointer(), tuple(parent[1].as_pointer() for parent in parents))
        if key in seen:
            return
        seen.add(key)
        kind = node.type
        if node.mute:
            for internal in node.internal_links:
                if internal.to_socket == socket:
                    visit_input(tree, internal.from_socket, parents)
        elif kind == 'GROUP' and node.node_tree is not None:
            child = node.node_tree
            output = next((item for item in child.nodes if item.type == 'GROUP_OUTPUT' and item.is_active_output), None)
            if output is None:
                limitations.add('missing active group output')
                return
            match = next((item for item in output.inputs if item.identifier == socket.identifier), None)
            if match is not None:
                visit_input(child, match, parents + ((tree, node),))
        elif kind == 'GROUP_INPUT' and parents:
            parent_tree, parent_node = parents[-1]
            match = next((item for item in parent_node.inputs if item.identifier == socket.identifier), None)
            if match is not None:
                visit_input(parent_tree, match, parents[:-1])
        elif kind in ('REROUTE', 'ADD_SHADER', 'MIX_SHADER'):
            sockets = node.inputs if kind == 'REROUTE' else [item for item in node.inputs if item.type == 'SHADER']
            if kind == 'MIX_SHADER' and not node.inputs[0].is_linked:
                factor = node.inputs[0].default_value
                if factor <= 0:
                    sockets = sockets[:1]
                elif factor >= 1:
                    sockets = sockets[1:]
            for item in sockets:
                visit_input(tree, item, parents)
        elif kind in ('EMISSION', 'BSDF_PRINCIPLED'):
            responsive |= kind == 'BSDF_PRINCIPLED'
            color = node.inputs['Color' if kind == 'EMISSION' else 'Emission Color']
            strength = node.inputs['Strength' if kind == 'EMISSION' else 'Emission Strength']
            if ((color.is_linked or any(color.default_value[:3])) and
                    (strength.is_linked or strength.default_value != 0 or _animated(tree, strength.path_from_id('default_value')))):
                emissions[(tree, node.name)] = (tree, node.name, strength.name, color.is_linked)
        elif kind.startswith('BSDF_') and kind != 'BSDF_TRANSPARENT':
            responsive = True
        elif kind not in ('BSDF_TRANSPARENT', 'HOLDOUT'):
            limitations.add('unsupported active Surface node ' + node.bl_idname)

    output = root.get_output_node('CYCLES')
    if output is not None:
        visit_input(root, output.inputs['Surface'], ())
        if output.inputs['Volume'].is_linked:
            limitations.add('Volume shader emission is outside surface-emitter scaling')
    return list(emissions.values()), responsive, limitations


def _animated(owner, path):
    data = owner.animation_data
    return data is not None and (data.action is not None or bool(data.nla_tracks) or
                                 any(driver.data_path == path for driver in data.drivers))


def _temporary_material(material, emissions, multiplier, cleanup):
    import bpy
    copy = material.copy()
    cleanup.callback(bpy.data.materials.remove, copy)
    copies = {material.node_tree: copy.node_tree}

    def copy_groups(tree):
        for node in tree.nodes:
            if node.type != 'GROUP' or node.node_tree is None:
                continue
            original = node.node_tree
            if original not in copies:
                child = original.copy()
                copies[original] = child
                cleanup.callback(bpy.data.node_groups.remove, child)
                copy_groups(child)
            node.node_tree = copies[original]

    copy_groups(copy.node_tree)
    applied = []
    unsupported = []
    for tree, node_name, socket_name, _ in emissions:
        copied_tree = copies[tree]
        socket = copied_tree.nodes[node_name].inputs[socket_name]
        location = f'{tree.name}/{node_name}/{socket_name}'
        if socket.is_linked:
            link = socket.links[0]
            if link.is_muted or not link.is_valid:
                unsupported.append(location + ': inactive strength link')
                continue
            source = link.from_socket
            # A color-to-float strength link already has a Blender implicit
            # conversion. The Math socket uses the same conversion.
            multiply = copied_tree.nodes.new('ShaderNodeMath')
            multiply.operation = 'MULTIPLY'
            multiply.inputs[1].default_value = multiplier
            copied_tree.links.new(source, multiply.inputs[0])
            copied_tree.links.new(multiply.outputs[0], socket)
            applied.append(dict(socket=location, method='linked-strength-multiply'))
        elif _animated(tree, tree.nodes[node_name].inputs[socket_name].path_from_id('default_value')):
            unsupported.append(location + ': animated unlinked strength')
        else:
            before = socket.default_value
            socket.default_value = before * multiplier
            if not math.isclose(socket.default_value, before * multiplier, rel_tol=1e-6):
                raise ValueError('Advanced emission strength is outside Blender range: ' + location)
            applied.append(dict(socket=location, method='constant-strength', before=before, after=socket.default_value))
    return copy, applied, unsupported


def _set_temporary(cleanup, owner, key, value):
    previous = getattr(owner, key)
    if previous != value:
        setattr(owner, key, value)
        cleanup.callback(setattr, owner, key, previous)


def _assign_slot(cleanup, slot, material):
    # Register in this order so the effective material is restored before its
    # original DATA/OBJECT link; other users of the mesh keep their own slots.
    previous_link, previous_material = slot.link, slot.material

    def restore():
        slot.material = previous_material
        slot.link = previous_link

    slot.link = 'OBJECT'
    cleanup.callback(restore)
    slot.material = material


def source_snapshot(scene, layer, recipe, roles):
    from worker import select
    receivers = set(select(recipe, 'receivers'))
    emitters = set(select(recipe, 'visible_emitters'))
    explicit = set()
    for name, indices in roles.items():
        obj = scene.objects.get(name)
        if obj is None or obj.type != 'MESH' or any(int(index) >= len(obj.material_slots) for index in indices):
            raise ValueError('Advanced material_roles names a missing mesh or material slot: ' + name)
        explicit.add(obj)
    authored = render_visible_objects(layer)
    return dict(authored=authored, receivers=receivers, emitters=emitters,
                contributors=set(select(recipe, 'contributors')), glass=set(select(recipe, 'glass')), explicit=explicit,
                lights={obj for obj in scene.objects if obj.type == 'LIGHT'} | {o for o in authored if o.type == 'LIGHT'},
                originalSlots={obj: [slot.material for slot in obj.material_slots]
                               for obj in authored | receivers | emitters | explicit if obj.type == 'MESH'},
                preview=scene.get('bjs_lighting_controls_material'))


def dependency_hashes(scene, snapshot):
    """Fingerprint external files that can change outside the saved blend."""
    import bpy
    from common import sha
    from babylon_js.workflow_tools.inspect_environment import _images_from_tree
    materials = {material for slots in snapshot['originalSlots'].values() for material in slots if material}
    preview = bpy.data.materials.get(snapshot['preview'] or '')
    if preview is not None:
        materials.add(preview)
    trees = [material.node_tree for material in materials]
    trees.extend(obj.data.node_tree for obj in snapshot['lights'] if obj in snapshot['authored'])
    if scene.world:
        trees.append(scene.world.node_tree)
    images = {image for tree in trees for image in _images_from_tree(tree)}
    paths = {Path(bpy.path.abspath(lib.filepath, library=lib.parent)).resolve() for lib in bpy.data.libraries}
    for image in images:
        if image.packed_file or not image.filepath or image.source not in ('FILE', 'TILED', 'MOVIE', 'SEQUENCE'):
            continue
        path = Path(bpy.path.abspath(image.filepath, library=image.library)).resolve()
        if image.source == 'TILED':
            for tile in image.tiles:
                offset = tile.number - 1001
                name = path.name.replace('<UDIM>', str(tile.number)).replace('<UVTILE>', f'u{offset % 10 + 1}_v{offset // 10 + 1}')
                paths.add(path.with_name(name))
        else:
            paths.add(path)
        if image.source == 'SEQUENCE':
            match = re.fullmatch(r'(.*?)([0-9]+)', path.stem)
            if match and path.parent.is_dir():
                prefix, digits = match.groups()
                pattern = re.compile(re.escape(prefix) + r'[0-9]{' + str(len(digits)) + '}' + re.escape(path.suffix))
                paths.update(item for item in path.parent.iterdir() if pattern.fullmatch(item.name))
    return {str(path): sha(path) if path.is_file() else None for path in sorted(paths)}


def _light_reason(light):
    if light.energy == 0 or not any(light.color):
        return 'zero light energy or color'
    if getattr(getattr(light, 'cycles', None), 'is_portal', False):
        return 'sampling portal has no independent radiance'
    return None


def _ambiguous_emission(emissions, responsive, role, declared):
    return (role is None and not declared and not responsive and
            any(linked_color for _, _, _, linked_color in emissions))


def _boost_light(light, multiplier, cleanup):
    import bpy
    if multiplier == 1:
        return None, dict(reason='source multiplier is 1')
    key, expected = 'energy', light.energy * multiplier
    if _animated(light, 'energy'):
        if not hasattr(light, 'exposure') or _animated(light, 'exposure'):
            return None, dict(reason='animated Light energy and no static exposure control')
        key, expected = 'exposure', light.exposure + math.log2(multiplier)
    copy = light.copy()
    cleanup.callback(bpy.data.lights.remove, copy)
    setattr(copy, key, expected)
    if not math.isclose(getattr(copy, key), expected, rel_tol=1e-6, abs_tol=1e-8):
        raise ValueError('Advanced Light ' + key + ' is outside Blender range: ' + light.name)
    return copy, dict(method='light-' + key, before=getattr(light, key), after=getattr(copy, key))


@contextmanager
def prepared_sources(scene, layer, snapshot, options):
    """Apply source policy around Advanced rendering and restore all touched data."""
    multiplier = options['source_multiplier']
    authored = snapshot['authored']
    light_objects = sorted(snapshot['lights'], key=lambda obj: obj.name)
    enabled = [obj for obj in light_objects if obj in authored and _light_reason(obj.data) is None]
    receiver_mode = 'authored' if enabled else 'baked'
    diagnostics = dict(receiverMode=receiver_mode, multiplier=multiplier, viewLayer=layer.name,
                       boosted=[], skipped=[], ambiguous=[], lightResponsiveObjects=[], limitations=[])
    roles = options['material_roles']
    light_settings = {}
    material_copies = {}
    graphs = {}

    def details(material):
        if material not in graphs:
            graphs[material] = _shader_details(material)
        return graphs[material]

    with ExitStack() as cleanup:
        for obj in light_objects:
            light = obj.data
            row = dict(kind='light', object=obj.name, datablock=light.name)
            reason = 'excluded or hidden in saved render layer' if obj not in authored else _light_reason(light)
            if reason:
                diagnostics['skipped'].append(dict(row, reason=reason))
                continue
            if not obj.is_editable:
                diagnostics['skipped'].append(dict(row, reason='linked Light object is read-only'))
                continue
            _set_temporary(cleanup, obj, 'hide_render', False)
            if light not in light_settings:
                light_settings[light] = _boost_light(light, multiplier, cleanup)
            copy, change = light_settings[light]
            if copy is not None:
                _set_temporary(cleanup, obj, 'data', copy)
            diagnostics['skipped' if 'reason' in change else 'boosted'].append(dict(row, **change))

        # Simple prepares shared mesh DATA slots for receivers. Restore the
        # authored appearance only for Advanced participants that need it.
        unresolved_slots = set()
        prepared_visible = render_visible_objects(layer)
        for obj, original in sorted(snapshot['originalSlots'].items(), key=lambda item: item[0].name):
            for index, (slot, material) in enumerate(zip(obj.material_slots, original)):
                if slot.material == material:
                    continue
                if not enabled and obj in snapshot['receivers']:
                    if obj not in prepared_visible:
                        continue
                    role = roles.get(obj.name, {}).get(str(index))
                    if role == 'baked-display':
                        continue
                    if material is None or _baked_display(material):
                        emissions, responsive, limitations = [], False, set()
                    else:
                        emissions, responsive, limitations = details(material)
                    ambiguous = _ambiguous_emission(emissions, responsive, role, obj in snapshot['emitters'])
                    if ambiguous or (role == 'emitter' and not emissions):
                        row = dict(kind='material', object=obj.name, slot=index, material=material.name if material else None)
                        if ambiguous:
                            diagnostics['ambiguous'].append(dict(row, reason='linked emission color may be baked display or authored emission; declare material_roles'))
                        else:
                            diagnostics['skipped'].append(dict(row, reason='authored receiver slot has no supported active surface emission'))
                            diagnostics['limitations'].extend(dict(row, reason=reason) for reason in sorted(limitations))
                        unresolved_slots.add((obj, index))
                        continue
                    if not emissions:
                        continue
                if obj.is_editable:
                    _assign_slot(cleanup, slot, material)
                else:
                    diagnostics['limitations'].append(dict(object=obj.name, reason='cannot restore a linked receiver material slot'))
        candidates = snapshot['receivers'] | snapshot['emitters'] | snapshot['explicit'] | authored
        for obj in sorted(candidates, key=lambda obj: obj.name):
            if obj.type != 'MESH' or obj in snapshot['glass']:
                continue
            if any(mod.type == 'NODES' and mod.show_render for mod in obj.modifiers) or obj.particle_systems:
                diagnostics['limitations'].append(dict(object=obj.name, reason='procedurally generated lights and material assignments are outside the saved-slot scan'))
            source_visible = not obj.hide_render or obj in authored
            if not source_visible:
                for index in roles.get(obj.name, {}):
                    diagnostics['skipped'].append(dict(kind='material', object=obj.name, slot=int(index),
                        reason='excluded or hidden in saved render layer'))
                continue
            capture_visible = obj in prepared_visible
            if enabled and obj in authored and obj in snapshot['contributors']:
                _set_temporary(cleanup, obj, 'hide_render', False)
                capture_visible = True
            for index, slot in enumerate(obj.material_slots):
                if (obj, index) in unresolved_slots:
                    continue
                material = slot.material
                if material is None:
                    if capture_visible:
                        diagnostics['lightResponsiveObjects'].append(obj.name)
                    continue
                role = roles.get(obj.name, {}).get(str(index))
                row = dict(kind='material', object=obj.name, slot=index, material=material.name)
                if role == 'baked-display' or _baked_display(material):
                    diagnostics['skipped'].append(dict(row, reason='baked display appearance'))
                    if role == 'emitter':
                        diagnostics['ambiguous'].append(dict(row, reason='emitter role conflicts with managed baked preview'))
                    continue
                emissions, responsive, limitations = details(material)
                for limitation in sorted(limitations):
                    diagnostics['limitations'].append(dict(row, reason=limitation))
                if responsive and capture_visible:
                    diagnostics['lightResponsiveObjects'].append(obj.name)
                if not emissions:
                    if role == 'emitter' or obj in snapshot['emitters']:
                        diagnostics['skipped'].append(dict(row, reason='no supported active surface emission'))
                    continue
                if _ambiguous_emission(emissions, responsive, role, obj in snapshot['emitters']):
                    diagnostics['ambiguous'].append(dict(row, reason='linked emission color may be baked display or authored emission; declare material_roles'))
                    continue
                if not capture_visible and obj not in authored:
                    diagnostics['skipped'].append(dict(row, reason='emitter excluded from render layer'))
                    continue
                _set_temporary(cleanup, obj, 'hide_render', False)
                if responsive:
                    diagnostics['lightResponsiveObjects'].append(obj.name)
                if multiplier == 1:
                    diagnostics['skipped'].append(dict(row, reason='source multiplier is 1'))
                    continue
                if not obj.is_editable:
                    diagnostics['skipped'].append(dict(row, reason='linked object material slot is read-only'))
                    continue
                if material not in material_copies:
                    material_copies[material] = _temporary_material(material, emissions, multiplier, cleanup)
                copy, applied, unsupported = material_copies[material]
                if applied:
                    _assign_slot(cleanup, slot, copy)
                    diagnostics['boosted'].append(dict(row, sockets=applied))
                if unsupported:
                    diagnostics['limitations'].append(dict(row, reason='; '.join(unsupported)))
        diagnostics['lightResponsiveObjects'] = sorted(set(diagnostics['lightResponsiveObjects']))
        if enabled and not diagnostics['lightResponsiveObjects']:
            diagnostics['lampResponseError'] = (
                'Render-visible lamps need at least one light-responsive authored capture surface. '
                'Restore authored receiver slots with recipe_cli restore-physical-slots in a sibling source, '
                'then bind that saved source and its matching appearance inputs in the volume recipe.')
        yield diagnostics
