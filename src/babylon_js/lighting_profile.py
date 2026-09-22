"""Versioned KaDshow lighting profile, independent of texture encoding and bpy."""
import math

DEFAULT_LIGHTING = dict(version=1, mode='shadow-aware', bakedIntensity=1.0,
                       reflectionIntensity=1.0, shadowSuppression=2.0,
                       fullyLitThreshold=1.0, highlightPreservation=1.0)
RANGES = dict(bakedIntensity=(0, 4), reflectionIntensity=(0, 4),
              shadowSuppression=(0, 8), fullyLitThreshold=(.01, 8), highlightPreservation=(0, 1))


def lighting_profile(options=None):
    result = dict(DEFAULT_LIGHTING)
    if options is not None:
        unknown = set(options) - set(result)
        if unknown:
            raise ValueError('Unknown KaDshow lighting options: ' + ', '.join(sorted(unknown)))
        result.update(options)
    if result['version'] != 1 or result['mode'] not in ('original', 'experimental', 'shadow-aware'):
        raise ValueError('Unsupported KaDshow lighting version or mode')
    for key, (low, high) in RANGES.items():
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError('Invalid KaDshow lighting ' + key)
    return result


def apply_lighting_profile(model, options=None):
    """Explicitly annotate lightmap markers, retaining encoding/other metadata."""
    profile = lighting_profile(options)
    count = 0
    for collection in ('meshes', 'transformNodes'):
        for node in model.get(collection, []):
            if node.get('name', '').startswith('lightmap_'):
                node.setdefault('metadata', {})['kadshowLighting'] = dict(profile)
                count += 1
    return count


NODE_FIELDS = {'Shadow Suppression': 'shadowSuppression',
               'Fully Lit Threshold': 'fullyLitThreshold',
               'Highlight Preservation': 'highlightPreservation'}


def scene_lighting_profile(context, options=None):
    """Read the associated authoring node to initialize export-dialog defaults.

    Scene bjs_lighting_controls_material names a material. Its one group node
    tagged bjs_lighting_controls=1 owns the saved, unlinked runtime controls.
    No association keeps legacy/script/operator options unchanged.
    """
    result = lighting_profile(options)
    material_name = context.scene.get('bjs_lighting_controls_material')
    if not material_name:
        return result
    import bpy
    mat = bpy.data.materials.get(material_name)
    nodes = [n for n in mat.node_tree.nodes if n.type == 'GROUP' and n.get('bjs_lighting_controls') == 1] if mat and mat.use_nodes else []
    if len(nodes) != 1:
        raise ValueError('Expected one tagged lighting-controls node in ' + str(material_name))
    for socket_name, key in NODE_FIELDS.items():
        socket = nodes[0].inputs.get(socket_name)
        if socket is None or socket.is_linked:
            raise ValueError('Saved lighting control must be an unlinked scalar: ' + socket_name)
        result[key] = float(socket.default_value)
    return lighting_profile(result)


def prefill_lighting_options(operator, context):
    """Called only when opening the dialog, never again during execute/check/draw."""
    if not context.scene.get('bjs_lighting_controls_material'):
        return
    profile = scene_lighting_profile(context)
    operator.lighting_shadow_suppression = profile['shadowSuppression']
    operator.lighting_fully_lit_threshold = profile['fullyLitThreshold']
    operator.lighting_highlight_preservation = profile['highlightPreservation']
