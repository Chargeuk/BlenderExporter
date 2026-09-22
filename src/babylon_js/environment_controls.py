"""Native World group: one HDR source, separate lighting and visible sky.

The shaders live in the .blend and do not require this add-on to evaluate.
Only the managed schema is supported; arbitrary World graphs are not inferred.
"""
from contextlib import contextmanager
import math
from pathlib import Path
import bpy

TAG = 'bjs_environment_controls_version'
VERSION = 1
NAME = 'KaDshow Environment Controls'


def find_controls(world):
    if not world or not world.node_tree:
        return None
    # Follow only the connected active World output (not disconnected experiments).
    pending = [n for n in world.node_tree.nodes if n.type == 'OUTPUT_WORLD' and n.is_active_output]
    seen, found = set(), []
    while pending:
        node = pending.pop()
        if node in seen:
            continue
        seen.add(node)
        if node.type == 'GROUP' and node.node_tree and node.node_tree.get(TAG):
            found.append(node)
        pending.extend(l.from_node for s in node.inputs for l in s.links)
    if len(found) > 1:
        raise ValueError('Multiple connected KaDshow environment groups; select one World source')
    if found and found[0].node_tree.get(TAG) != VERSION:
        raise ValueError('Unsupported KaDshow environment group version')
    return found[0] if found else None


def source_image(node):
    images = [n.image for n in node.node_tree.nodes if n.type == 'TEX_ENVIRONMENT' and n.image]
    if len(images) != 1:
        raise ValueError('KaDshow environment group needs exactly one HDR Environment Texture')
    return images[0]


def settings(node):
    result = {'schema_version': VERSION}
    for socket in node.inputs:
        if socket.is_linked:
            raise ValueError('Environment controls must use saved, unlinked input values')
        value = float(socket.default_value)
        if not math.isfinite(value):
            raise ValueError('Environment controls must be finite')
        result[socket.name] = value
    image = source_image(node)
    result['image'] = bpy.path.abspath(image.filepath, library=image.library)
    return result


def create_controls(world, image, *, rotation=0, lighting_strength=1, sky_lift=0,
                    lift_rolloff=1, visible_strength=1, connect=False):
    """Idempotent for a connected managed group; never replace its accepted values."""
    existing = find_controls(world)
    if existing:
        return existing
    world.use_nodes = True
    tree = bpy.data.node_groups.new(NAME, 'ShaderNodeTree')
    tree[TAG] = VERSION
    for name, value, minimum, maximum in (
            ('Rotation', rotation, -math.tau, math.tau),
            ('Lighting Strength', lighting_strength, 0, 100),
            ('Sky Lift', sky_lift, 0, 100),
            ('Lift Rolloff', lift_rolloff, .001, 100),
            ('Visible Sky Strength', visible_strength, 0, 100)):
        socket = tree.interface.new_socket(name=name, in_out='INPUT', socket_type='NodeSocketFloat')
        socket.default_value, socket.min_value, socket.max_value = value, minimum, maximum
        if name == 'Rotation':
            socket.subtype = 'ANGLE'
    for name in ('Lighting', 'Visible Sky'):
        tree.interface.new_socket(name=name, in_out='OUTPUT', socket_type='NodeSocketShader')
    n, l = tree.nodes, tree.links
    inp = n.new('NodeGroupInput'); inp.location = (-900, -300)
    out = n.new('NodeGroupOutput'); out.location = (700, 100)
    texcoord = n.new('ShaderNodeTexCoord'); texcoord.location = (-900, 300)
    rotation_node = n.new('ShaderNodeCombineXYZ'); rotation_node.location = (-700, -200)
    l.new(inp.outputs['Rotation'], rotation_node.inputs['Z'])
    mapping = n.new('ShaderNodeMapping'); mapping.vector_type = 'POINT'; mapping.location = (-650, 300)
    l.new(texcoord.outputs['Generated'], mapping.inputs['Vector'])
    l.new(rotation_node.outputs[0], mapping.inputs['Rotation'])
    env = n.new('ShaderNodeTexEnvironment'); env.image = image; env.location = (-400, 300)
    l.new(mapping.outputs[0], env.inputs['Vector'])
    lum = n.new('ShaderNodeRGBToBW'); lum.location = (-180, 0)
    l.new(env.outputs['Color'], lum.inputs['Color'])
    denom = n.new('ShaderNodeMath'); denom.operation = 'ADD'; denom.location = (0, -80)
    l.new(lum.outputs[0], denom.inputs[0]); l.new(inp.outputs['Lift Rolloff'], denom.inputs[1])
    divide = n.new('ShaderNodeMath'); divide.operation = 'DIVIDE'; divide.location = (160, -80)
    l.new(inp.outputs['Sky Lift'], divide.inputs[0]); l.new(denom.outputs[0], divide.inputs[1])
    gain = n.new('ShaderNodeMath'); gain.operation = 'ADD'; gain.inputs[1].default_value = 1
    l.new(divide.outputs[0], gain.inputs[0]); gain.location = (320, -80)
    scale = n.new('ShaderNodeVectorMath'); scale.operation = 'SCALE'; scale.location = (320, 250)
    l.new(env.outputs['Color'], scale.inputs[0]); l.new(gain.outputs[0], scale.inputs['Scale'])
    lighting = n.new('ShaderNodeBackground'); lighting.location = (520, 220)
    l.new(scale.outputs[0], lighting.inputs['Color'])
    l.new(inp.outputs['Lighting Strength'], lighting.inputs['Strength'])
    l.new(lighting.outputs[0], out.inputs['Lighting'])
    visible = n.new('ShaderNodeBackground'); visible.location = (520, -180)
    l.new(env.outputs['Color'], visible.inputs['Color'])
    l.new(inp.outputs['Visible Sky Strength'], visible.inputs['Strength'])
    l.new(visible.outputs[0], out.inputs['Visible Sky'])
    group = world.node_tree.nodes.new('ShaderNodeGroup'); group.node_tree = tree
    group.name = NAME; group.label = NAME; group.width = 275
    if connect:
        output = next((n for n in world.node_tree.nodes if n.type == 'OUTPUT_WORLD' and n.is_active_output), None)
        if output is None:
            output = world.node_tree.nodes.new('ShaderNodeOutputWorld')
        world.node_tree.links.new(group.outputs['Lighting'], output.inputs['Surface'])
    return group


@contextmanager
def visible_panorama(context, directory):
    """World-only canonical linear render; remove scratch data/file even on failure.

    Deliberately use the Visible Sky output, never the bake-strength output.
    The occupied-room ENV capture is a separate operation using Lighting.
    """
    node = find_controls(context.scene.world)
    if node is None:
        raise ValueError('Connect a KaDshow Environment Controls group to the active World first')
    values = settings(node)
    image = source_image(node)
    if image.packed_file or not Path(values['image']).is_file():
        raise ValueError('Save the original HDRI externally before skybox export')
    width, height = image.size
    if not width or width != height * 2:
        raise ValueError('World HDRI must be a 2:1 equirectangular image')
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    import tempfile
    with tempfile.TemporaryDirectory(prefix='world-sky-', dir=str(directory)) as temp:
        path = Path(temp) / 'visible.exr'
        scene = bpy.data.scenes.new('TEMP KaDshow visible sky')
        world = camera = obj = None
        try:
            world = bpy.data.worlds.new('TEMP KaDshow visible sky'); world.use_nodes = True
            scene.world = world
            world.node_tree.nodes.clear()
            group = world.node_tree.nodes.new('ShaderNodeGroup'); group.node_tree = node.node_tree
            for s in node.inputs:
                group.inputs[s.name].default_value = s.default_value
            output = world.node_tree.nodes.new('ShaderNodeOutputWorld')
            world.node_tree.links.new(group.outputs['Visible Sky'], output.inputs['Surface'])
            camera = bpy.data.cameras.new('TEMP KaDshow panorama')
            camera.type = 'PANO'; camera.panorama_type = 'EQUIRECTANGULAR'
            obj = bpy.data.objects.new(camera.name, camera); scene.collection.objects.link(obj)
            obj.rotation_euler = (math.pi / 2, 0, 0); scene.camera = obj
            scene.render.engine = 'CYCLES'; scene.cycles.samples = 1
            scene.cycles.use_adaptive_sampling = False; scene.cycles.use_denoising = False
            scene.render.resolution_x, scene.render.resolution_y = width, height
            scene.render.resolution_percentage = 100
            scene.render.image_settings.file_format = 'OPEN_EXR'
            scene.render.image_settings.color_mode = 'RGB'; scene.render.image_settings.color_depth = '32'
            scene.render.image_settings.exr_codec = 'ZIP'; scene.render.filepath = str(path)
            bpy.ops.render.render(scene=scene.name, write_still=True)
            yield path, values
        finally:
            bpy.data.scenes.remove(scene)
            if obj: bpy.data.objects.remove(obj, do_unlink=True)
            if camera: bpy.data.cameras.remove(camera)
            if world: bpy.data.worlds.remove(world)


class BJS_OT_EnvironmentControls(bpy.types.Operator):
    bl_idname = 'world.bjs_environment_controls'
    bl_label = 'Add KaDshow Environment Controls'
    bl_description = 'Create native lighting/visible-sky controls; existing World graphs are kept'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        world = context.scene.world
        if not world:
            self.report({'ERROR'}, 'Create a World and choose an Environment Texture first')
            return {'CANCELLED'}
        try:
            if find_controls(world):
                return {'FINISHED'}
            existing = next((n for n in world.node_tree.nodes if n.type == 'GROUP' and n.node_tree
                             and n.node_tree.get(TAG)) , None) if world.node_tree else None
            if existing:
                self.report({'INFO'}, 'Existing controls retained; connect Lighting to World Output')
                return {'FINISHED'}
            images = {n.image for n in world.node_tree.nodes if n.type == 'TEX_ENVIRONMENT' and n.image} if world.node_tree else set()
            if len(images) != 1:
                raise ValueError('Add exactly one Environment Texture to the World first')
            node = create_controls(world, images.pop())
            # Retain existing shading until the user explicitly connects the new group.
            for n in world.node_tree.nodes: n.select = n == node
            world.node_tree.nodes.active = node
            self.report({'INFO'}, 'Connect Lighting to World Output; set rotation and strength to match your scene')
            return {'FINISHED'}
        except ValueError as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}


def draw_controls(layout, world):
    box = layout.box(); box.label(text=NAME)
    try:
        node = find_controls(world)
        if node is None:
            box.operator(BJS_OT_EnvironmentControls.bl_idname)
            return
        env = next(n for n in node.node_tree.nodes if n.type == 'TEX_ENVIRONMENT')
        box.template_ID(env, 'image', open='image.open')
        for socket in node.inputs:
            box.prop(socket, 'default_value', text=socket.name)
        box.label(text='Lighting: bake / room capture. Visible Sky: Basis export.')
    except ValueError as exc:
        box.label(text=str(exc), icon='ERROR')
