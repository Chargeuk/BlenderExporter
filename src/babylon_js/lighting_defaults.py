"""Optional file defaults for KaDshow environment-volume controls, independent of capture."""
import math

KEY = 'kadshowEnvironmentLightingDefaults'
OPTION = 'environment_lighting_defaults'
MODES = ('off', 'simple', 'advanced')
WORLD_FIELDS = ('bjs_environment_lighting_defaults_enabled',
                'bjs_environment_lighting_default_mode', 'bjs_environment_lighting_multiplier')
OPERATOR_FIELDS = ('environment_lighting_defaults_enabled',
                   'environment_lighting_default_mode', 'environment_lighting_multiplier')
MODE_ITEMS = (('off', 'Off', 'Keep native environment lighting'),
              ('simple', 'Simple', 'Prefer the simple environment lighting volume'),
              ('advanced', 'Advanced', 'Prefer the directional environment lighting volume'))


def profile(options):
    """Strict producer validation: bad optional settings cannot publish malformed JSON."""
    if not isinstance(options, dict) or set(options) - {'version', 'mode', 'multiplier'}:
        raise ValueError('Environment lighting defaults require only version, mode and multiplier')
    version = options.get('version', 1)
    mode = options.get('mode')
    multiplier = options.get('multiplier')
    if type(version) is not int or version != 1 or mode not in MODES:
        raise ValueError('Environment lighting defaults require version 1 and mode off, simple or advanced')
    if (isinstance(multiplier, bool) or not isinstance(multiplier, (int, float)) or
            not math.isfinite(multiplier) or not 0 <= multiplier <= 10):
        raise ValueError('Environment lighting defaults multiplier must be finite and between 0 and 10')
    return dict(version=1, mode=mode, multiplier=float(multiplier))


def _world_value(world, name, default):
    # RNA properties expose their registered defaults; dict fallback supports retained custom properties.
    if world is None:
        return default
    return getattr(world, name, world.get(name, default))


def saved_options(world):
    enabled = _world_value(world, WORLD_FIELDS[0], False)
    if type(enabled) is not bool:
        raise ValueError('Saved environment lighting defaults enabled flag must be boolean')
    if not enabled:
        return None
    return profile(dict(mode=_world_value(world, WORLD_FIELDS[1], 'advanced'),
                        multiplier=_world_value(world, WORLD_FIELDS[2], 1)))


def resolve_options(world, options):
    """Explicit null disables saved defaults; omission inherits only an opted-in World."""
    if OPTION in options:
        return None if options[OPTION] is None else profile(options[OPTION])
    return saved_options(world)


def prefill(operator, context):
    """Opening the dialog initializes controls; draw/execute never overwrite user choices."""
    saved = saved_options(context.scene.world)
    operator.environment_lighting_defaults_enabled = saved is not None
    operator.environment_lighting_default_mode = saved['mode'] if saved else 'advanced'
    operator.environment_lighting_multiplier = saved['multiplier'] if saved else 1


def operator_options(operator, context):
    # EXEC_DEFAULT (including prepare-export) bypasses invoke, so inherit the saved World unless overridden.
    # Operator values belong to the OperatorProperties RNA container in Blender 5.2.
    if not any(operator.properties.is_property_set(field) for field in OPERATOR_FIELDS):
        return saved_options(context.scene.world)
    if not operator.environment_lighting_defaults_enabled:
        return None
    return profile(dict(mode=operator.environment_lighting_default_mode,
                        multiplier=operator.environment_lighting_multiplier))


def apply_defaults(model, options):
    """Annotate only the unique geometry-free hasenv mesh after the ENV stage creates it."""
    if options is None:
        return None
    value = profile(options)
    markers = [node for node in model.get('meshes', []) if node.get('name', '').lower() == 'hasenv']
    if len(markers) != 1 or markers[0].get('positions') or markers[0].get('indices'):
        raise ValueError('Environment lighting defaults require one existing empty hasenv mesh marker; export ENV first')
    metadata = markers[0].get('metadata', {})
    if not isinstance(metadata, dict) or KEY in metadata:
        raise ValueError('Invalid or duplicate hasenv environment lighting defaults metadata')
    markers[0].setdefault('metadata', {})[KEY] = value
    return dict(value)


def register_properties():
    import bpy
    bpy.types.World.bjs_environment_lighting_defaults_enabled = bpy.props.BoolProperty(
        name='Export environment lighting defaults', default=False,
        description='Opt in to file defaults for the runtime volume mode and multiplier; older exports stay unchanged')
    bpy.types.World.bjs_environment_lighting_default_mode = bpy.props.EnumProperty(
        name='Environment lighting default mode', default='advanced', items=MODE_ITEMS)
    bpy.types.World.bjs_environment_lighting_multiplier = bpy.props.FloatProperty(
        name='Environment lighting multiplier', default=1, min=0, max=10,
        description='Runtime environment-volume gain only; leaves direct lights, lightmaps and capture masters unchanged')


def unregister_properties():
    import bpy
    for name in WORLD_FIELDS:
        if hasattr(bpy.types.World, name):
            delattr(bpy.types.World, name)


def draw_world(layout, world):
    box = layout.box()
    box.label(text='KaDshow environment lighting file defaults')
    box.prop(world, WORLD_FIELDS[0])
    if world.bjs_environment_lighting_defaults_enabled:
        box.prop(world, WORLD_FIELDS[1])
        box.prop(world, WORLD_FIELDS[2])
        box.label(text='Saved in this World; export dialog can override')


def draw_operator(layout, operator):
    box = layout.box()
    box.label(text='KaDshow environment lighting file defaults')
    box.prop(operator, OPERATOR_FIELDS[0])
    if operator.environment_lighting_defaults_enabled:
        box.prop(operator, OPERATOR_FIELDS[1])
        box.prop(operator, OPERATOR_FIELDS[2])
        box.label(text='Requires an empty hasenv mesh or ENV export')
