"""Shared disposable receiver setup for transport and COLOR response bakes."""
from contextlib import contextmanager
import bpy


@contextmanager
def capped_materials(obj, cap, *, strict_principled=True, material_proof=None):
    """Isolate one receiver's cap, including when another object shares its mesh."""
    original_mesh = obj.data
    temporary_mesh = original_mesh.copy()
    clones = []
    was_hidden = obj.hide_get()
    obj.data = temporary_mesh
    try:
        for material in list(temporary_mesh.materials):
            clone = material.copy()
            clones.append(clone)
            principals = [n for n in clone.node_tree.nodes if n.type == 'BSDF_PRINCIPLED']
            if strict_principled and len(principals) != 1:
                raise ValueError('Response requires one Principled node: ' + material.name)
            # Native transport has always accepted the first Principled node.
            principal = next(iter(principals))
            socket = principal.inputs['Metallic']
            linked = socket.links[0].from_socket if socket.is_linked else None
            if material_proof is not None:
                material_proof.append({'source': material.name, 'effective': clone.name,
                    'principled_node': principal.name, 'metallic_default': socket.default_value,
                    'metallic_link': {'node': linked.node.name, 'socket': linked.identifier} if linked else None})
            minimum = clone.node_tree.nodes.new('ShaderNodeMath')
            minimum.operation = 'MINIMUM'
            minimum.inputs[1].default_value = cap
            if linked:
                clone.node_tree.links.new(linked, minimum.inputs[0])
            else:
                minimum.inputs[0].default_value = socket.default_value
            clone.node_tree.links.new(minimum.outputs[0], socket)
        temporary_mesh.materials.clear()
        for material in clones:
            temporary_mesh.materials.append(material)
        yield clones
    finally:
        obj.data = original_mesh
        obj.hide_set(was_hidden)
        bpy.data.meshes.remove(temporary_mesh)
        for material in clones:
            bpy.data.materials.remove(material)


@contextmanager
def joined_receivers(copies, receivers, *, normal_error='Joined receiver normals changed: '):
    """Join disposable evaluated copies and restore their recorded world normals."""
    visibility = [(obj, obj.hide_render) for obj in receivers]
    # Join can invalidate removed-copy RNA references even before it raises.
    # Record identities now and find surviving owned data through bpy afterwards.
    owned_objects = {obj.as_pointer() for obj in copies}
    owned_meshes = {obj.data.as_pointer() for obj in copies}
    try:
        for obj in receivers:
            obj.hide_render = True
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
        if error > .001:
            raise ValueError(normal_error + str(error))
        yield proxy, error
    finally:
        for obj in list(bpy.data.objects):
            if obj.as_pointer() in owned_objects:
                bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in list(bpy.data.meshes):
            if mesh.as_pointer() in owned_meshes and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        for obj, hidden in visibility:
            obj.hide_render = hidden
