"""Opt-in intrinsic UV colour for disposable physical transport/material measurements."""
import math
from contextlib import contextmanager
from pathlib import Path


def validate_settings(c):
    settings = c.get('physical_colour_adjustment', {})
    if not isinstance(settings, dict) or not isinstance(settings.get('enabled', False), bool):
        raise ValueError('physical_colour_adjustment requires a boolean enabled flag')
    if not settings.get('enabled'):
        return False
    required = {'enabled', 'schema_version', 'image', 'sha256', 'strength', 'geometry_sha256', 'objects'}
    if set(settings) != required or settings['schema_version'] != 1:
        raise ValueError('physical_colour_adjustment requires explicit schema 1 image/hash/strength/geometry/objects')
    if not isinstance(settings['image'], str) or not settings['image']:
        raise ValueError('Physical colour adjustment needs a saved image path')
    if settings['image'] not in c.get('source_images', []):
        raise ValueError('Declare physical colour adjustment image in source_images')
    for name in ('sha256', 'geometry_sha256'):
        value = settings[name]
        if not isinstance(value, str) or len(value) != 64 or any(x not in '0123456789abcdef' for x in value):
            raise ValueError('Physical colour adjustment needs an explicit ' + name)
    strength = settings['strength']
    if isinstance(strength, bool) or not isinstance(strength, (int, float)) or not math.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError('Invalid physical colour adjustment strength')
    if not isinstance(settings['objects'], dict) or not settings['objects']:
        raise ValueError('Physical colour adjustment requires an explicit object selector')
    return True


def validate_scene(c):
    if 'scene' in c and (not isinstance(c['scene'], str) or not c['scene'].strip()):
        raise ValueError('scene must be an exact nonempty saved scene name')


def select_scene(c):
    """Stage processes select one saved scene; no live scene navigation is allowed."""
    import bpy
    validate_scene(c)
    if not bpy.app.background:
        raise RuntimeError('Saved-scene selection requires a disposable background Blender process')
    if 'scene' in c:
        scene = bpy.data.scenes.get(c['scene'])
        if scene is None:
            raise ValueError('Configured saved scene is missing: ' + c['scene'])
        bpy.context.window.scene = scene
    return bpy.context.scene


def inspect(c):
    """Validate final source/map/UV and explicit untinted material roles before cloning."""
    if not validate_settings(c):
        return None, None, None
    import bpy
    from common import sha
    from worker import select
    from ao import geometry_identity
    from appearance import preview_identity
    from babylon_js import colour_adjustment as ca
    if not bpy.app.background:
        raise RuntimeError('Physical colour adjustment requires a disposable background Blender process')
    if c.get('scene', bpy.context.scene.name) != bpy.context.scene.name:
        raise ValueError('Physical colour adjustment scene differs from configured scene')
    settings = c['physical_colour_adjustment']
    path = (Path(c['_root'])/settings['image']).resolve()
    if sha(path) != settings['sha256']:
        raise ValueError('Physical colour adjustment map hash is stale')
    candidate = Path(c.get('_candidate', c['_root']))/'source-assets'/settings['image']
    if candidate.is_file():
        if sha(candidate) != settings['sha256']:
            raise ValueError('Candidate physical colour adjustment map hash differs')
        path = candidate
    geometry = geometry_identity(c)
    if geometry['geometry_sha256'] != settings['geometry_sha256']:
        raise ValueError('Physical colour adjustment geometry/UV identity is stale')
    objects = select({'physical_tint_objects': settings['objects']}, 'physical_tint_objects')
    allowed = set(select(c, 'receivers') + select(c, 'contributors'))
    excluded = set(select(c, 'glass') + select(c, 'visible_emitters'))
    if not objects or not set(objects) <= allowed or set(objects) & excluded:
        raise ValueError('Physical tint objects must be explicit receivers/contributors, excluding glass and visible emitters')
    materials = {}
    assignments = []
    def has_overlay(tree, seen=None):
        seen = set() if seen is None else seen
        if tree.as_pointer() in seen:
            return False
        seen.add(tree.as_pointer())
        if tree.get('bjs_colour_adjustment_schema') or tree.get('bjs_physical_colour_adjustment'):
            return True
        return any(n.get('bjs_colour_adjustment') or n.get('bjs_physical_colour_adjustment') or
                   (n.type == 'GROUP' and n.node_tree and has_overlay(n.node_tree, seen)) for n in tree.nodes)
    for obj in objects:
        if obj.type != 'MESH' or any(slot.link != 'DATA' for slot in obj.material_slots):
            raise ValueError('Physical tint requires mesh data-linked material slots: ' + obj.name)
        uv = obj.data.uv_layers.get(c.get('uv', 'SimpleBake'))
        if uv is None:
            raise ValueError('Physical tint object lacks configured UV: ' + obj.name)
        slots = list(obj.data.materials)
        if not slots or any(m is None for m in slots):
            raise ValueError('Physical tint object lacks source material: ' + obj.name)
        for material in slots:
            if (material.get('bjs_intrinsic_colour_role') != 'untinted-source'
                    or material.get('bjs_provisional_colour_tint')
                    or material.get('bjs_physical_colour_adjustment')):
                raise ValueError('Physical tint requires untinted-source material; remove provisional tint first: ' + material.name)
            if (not material.use_nodes or has_overlay(material.node_tree)
                    or len([n for n in material.node_tree.nodes if n.type == 'BSDF_PRINCIPLED']) != 1):
                raise ValueError('Physical tint requires one unadjusted Principled source; double application rejected: ' + material.name)
            if material not in materials:
                materials[material] = preview_identity(material)
        assignments.append({'object': obj.name, 'materials': [m.name for m in slots],
                            'polygon_material_indices': [p.material_index for p in obj.data.polygons]})
    image = ca.load_image(path)
    try:
        codes = ca.read_codes(image)
    except Exception:
        bpy.data.images.remove(image)
        raise
    record = {'schema_version': 1, 'kind': 'disposable-physical-intrinsic-colour',
        'scene': bpy.context.scene.name, 'source_sha256': sha(c['_source']),
        'geometry_sha256': geometry['geometry_sha256'], 'uv': c.get('uv', 'SimpleBake'),
        'image': settings['image'], 'image_sha256': settings['sha256'],
        'encoding': ca.ENCODING, 'image_size': list(image.size), 'strength': settings['strength'],
        'objects': assignments, 'source_materials': {m.name: digest for m, digest in materials.items()},
        'source_colour_role': 'untinted-source', 'active': bool(settings['strength'] and codes[:, :, 3].any()),
        'application': 'existing colour_adjustment.group on temporary source Base Color exactly once'}
    return objects, image, record


@contextmanager
def physical_materials(c):
    """Keep shared non-participating meshes/materials and source export PBR unchanged."""
    import bpy
    from babylon_js import colour_adjustment as ca
    objects, image, record = inspect(c)
    if record is None:
        yield None
        return
    originals = []
    clones = {}
    groups = []
    try:
        if record['active']:
            for obj in objects:
                original = obj.data
                mesh = original.copy()
                originals.append((obj, original, mesh))
                obj.data = mesh
                slots = list(mesh.materials)
                mesh.materials.clear()
                for source in slots:
                    if source not in clones:
                        material = source.copy()
                        clones[source] = material
                        material['bjs_physical_colour_adjustment'] = record['image_sha256']
                        material['bjs_intrinsic_colour_role'] = 'temporary-physical-overlay'
                        tree = material.node_tree
                        base = next(n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED').inputs['Base Color']
                        layer = tree.nodes.new('ShaderNodeGroup')
                        layer.node_tree = ca.group(image, record['uv'])
                        groups.append(layer.node_tree)
                        layer['bjs_physical_colour_adjustment'] = 1
                        layer.inputs['Strength'].default_value = record['strength']
                        if base.is_linked:
                            tree.links.new(base.links[0].from_socket, layer.inputs['Colour'])
                        else:
                            layer.inputs['Colour'].default_value = base.default_value
                        tree.links.new(layer.outputs[0], base)
                    mesh.materials.append(clones[source])
        yield record
    finally:
        for obj, original, mesh in reversed(originals):
            obj.data = original
            bpy.data.meshes.remove(mesh)
        for material in clones.values():
            bpy.data.materials.remove(material)
        for tree in groups:
            if tree.users == 0:
                bpy.data.node_groups.remove(tree)
        bpy.data.images.remove(image)


def verify_native_response(native, response):
    """Native transport and material response must describe the same effective source colour."""
    if native.get('physical_colour_adjustment') != response.get('physical_colour_adjustment'):
        raise ValueError('Native transport and receiver response physical colour adjustment differ')
    if native.get('scene') != response.get('scene'):
        raise ValueError('Native transport and receiver response scenes differ')
