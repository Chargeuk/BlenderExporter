"""Editable illumination grading. All groups execute before material colour.

Upgrades are explicit, idempotent and limited to the known split-lighting graph.
They do not replace existing controls, images or user nodes.
"""
import bpy

SCHEMA = 1


class Builder:
    def __init__(self, tree):
        self.tree = tree

    def wire(self, value, socket):
        if isinstance(value, bpy.types.NodeSocket):
            self.tree.links.new(value, socket)
        else:
            socket.default_value = value

    def math(self, operation, a, b=None):
        n = self.tree.nodes.new('ShaderNodeMath'); n.operation = operation
        self.wire(a, n.inputs[0])
        if b is not None: self.wire(b, n.inputs[1])
        return n.outputs[0]

    def vec(self, operation, a, b=None):
        n = self.tree.nodes.new('ShaderNodeVectorMath'); n.operation = operation
        self.wire(a, n.inputs[0])
        if b is not None: self.wire(b, n.inputs[3] if operation == 'SCALE' else n.inputs[1])
        return n.outputs['Value' if operation == 'DOT_PRODUCT' else 'Vector']

    def mix(self, a, b, amount):
        return self.vec('ADD', self.vec('SCALE', a, self.math('SUBTRACT', 1., amount)), self.vec('SCALE', b, amount))


def socket(tree, name, value=0., low=0., high=1., colour=False):
    s = tree.interface.new_socket(name=name, in_out='INPUT', socket_type='NodeSocketColor' if colour else 'NodeSocketFloat')
    s.default_value = value
    if not colour: s.min_value = low; s.max_value = high
    return s


def create_grade(name):
    """Signed-safe primary grade; optional native curves/gamma are artist controls."""
    tree = bpy.data.node_groups.new(name, 'ShaderNodeTree'); tree['bjs_grade_schema'] = SCHEMA
    socket(tree, 'Colour', (0., 0., 0., 1.), colour=True)
    # Zero guarantees exact legacy identity, including signed intermediate values.
    socket(tree, 'Adjustment Strength', 0.)
    socket(tree, 'Bypass', 0.)
    socket(tree, 'Exposure', 0., -16., 16.)
    socket(tree, 'Contrast', 1., .05, 4.)
    socket(tree, 'Contrast Pivot', 1., .0001, 100.)
    socket(tree, 'Tint', (1., 1., 1., 1.), colour=True)
    socket(tree, 'Saturation', 1., 0., 3.)
    socket(tree, 'Black Level', 0., -10., 10.)
    socket(tree, 'White Level', 1., .0001, 100.)
    socket(tree, 'Gamma', 1., .05, 5.)
    socket(tree, 'Curves Strength', 0.)
    tree.interface.new_socket(name='Colour', in_out='OUTPUT', socket_type='NodeSocketColor')
    ins = tree.nodes.new('NodeGroupInput'); out = tree.nodes.new('NodeGroupOutput'); b = Builder(tree)
    internals = tree.nodes.new('NodeFrame'); internals.label = 'Grading arithmetic (advanced)'; internals.name='Grading arithmetic'
    x = ins.outputs['Colour']
    grade = b.vec('SCALE', x, b.math('POWER', 2., ins.outputs['Exposure']))
    grade = b.vec('MULTIPLY', grade, ins.outputs['Tint'])
    black = b.vec('SCALE', (1., 1., 1.), ins.outputs['Black Level'])
    grade = b.vec('SCALE', b.vec('SUBTRACT', grade, black), b.math('DIVIDE', 1., b.math('MAXIMUM', b.math('SUBTRACT', ins.outputs['White Level'], ins.outputs['Black Level']), .0001)))
    y = b.vec('DOT_PRODUCT', grade, (.2126, .7152, .0722))
    magnitude = b.math('MAXIMUM', b.math('ABSOLUTE', y), 1e-20)
    pivot = b.math('MAXIMUM', ins.outputs['Contrast Pivot'], .0001)
    exponent = b.math('SUBTRACT', b.math('DIVIDE', ins.outputs['Contrast'], b.math('MAXIMUM', ins.outputs['Gamma'], .05)), 1.)
    grade = b.vec('SCALE', grade, b.math('POWER', b.math('DIVIDE', magnitude, pivot), exponent))
    y = b.vec('DOT_PRODUCT', grade, (.2126, .7152, .0722))
    grey = b.vec('SCALE', (1., 1., 1.), y)
    grade = b.mix(grey, grade, ins.outputs['Saturation'])
    curves = tree.nodes.new('ShaderNodeRGBCurve'); curves.name = 'Editable RGB Curves'; curves.label = 'Optional RGB curves (linear lighting)'
    curves.mapping.initialize(); curves.mapping.use_clip = False
    b.wire(grade, curves.inputs['Color']); b.wire(ins.outputs['Curves Strength'], curves.inputs['Fac'])
    grade = curves.outputs['Color']
    amount = b.math('MULTIPLY', ins.outputs['Adjustment Strength'], b.math('SUBTRACT', 1., ins.outputs['Bypass']))
    tree.links.new(b.mix(x, grade, amount), out.inputs['Colour'])
    for index,n in enumerate(n for n in tree.nodes if n.type in ('MATH','VECT_MATH')):
        n.parent=internals;n.location=(-1800+(index%7)*170,-(index//7)*140);n.hide=True
    ins.location = (-1000, 0); curves.location = (200, 0); out.location = (600, 0)
    # A familiar alternative, deliberately disconnected until the artist chooses it.
    native = tree.nodes.new('ShaderNodeBrightContrast'); native.label = 'Advanced alternative — connect explicitly'; native.location = (200, -400)
    return tree


def inspect(material):
    groups = [n for n in material.node_tree.nodes if n.type == 'GROUP' and n.get('bjs_lighting_controls') == 1]
    if len(groups) != 1: raise ValueError('Expected one tagged split-lighting controls group')
    node = groups[0]; tree = node.node_tree
    return node, tree


def upgrade(material, ao_image=None, uv='SimpleBake'):
    node, tree = inspect(material)
    if tree.get('bjs_grading_schema') == SCHEMA:
        if ao_image is not None: bind_ao(material, ao_image)
        return node
    if tree.get('bjs_grading_schema'):
        raise ValueError('Unknown grading version; graph preserved')
    # Validate the complete migration before making a single change.
    clamps = [n for n in tree.nodes if n.type == 'VECT_MATH' and n.operation == 'MAXIMUM' and n.label == 'Clamp negative illumination to black']
    if len(clamps) != 1 or not clamps[0].inputs[0].is_linked: raise ValueError('Custom preview: no recognized final lighting clamp; manual migration required')
    multiply = clamps[0].inputs[0].links[0].from_node
    if multiply.type != 'VECT_MATH' or multiply.operation != 'MULTIPLY' or not multiply.inputs[1].is_linked:
        raise ValueError('Custom colour multiplication: preserve and migrate manually')
    addition = multiply.inputs[1].links[0].from_node
    if addition.type != 'VECT_MATH' or addition.operation != 'ADD' or any(not s.is_linked for s in addition.inputs[:2]):
        raise ValueError('Custom pass combination: preserve and migrate manually')
    direct, indirect = [s.links[0].from_socket for s in addition.inputs[:2]]
    direct_node=direct.node
    if (direct_node.type!='VECT_MATH' or direct_node.operation!='SCALE'
            or not direct_node.inputs[3].is_linked
            or direct_node.inputs[3].links[0].from_socket.name!='Direct Strength'
            or not direct_node.inputs[0].is_linked
            or direct_node.inputs[0].links[0].from_node.get('bjs_preview_image_role')!='direct'):
        raise ValueError('Unrecognized direct/indirect ordering; preserve custom graph and migrate explicitly')
    existing_nodes=set(tree.nodes)
    if ao_image is not None: _validate_ao(tree, ao_image)
    b = Builder(tree); ins = next(n for n in tree.nodes if n.type == 'GROUP_INPUT')
    for name, value, hi in [('AO Strength', 0., 1.), ('AO Contrast', 1., 4.), ('AO Direct Influence', 0., 1.), ('AO Indirect Influence', 1., 1.)]:
        socket(tree, name, value, .05 if name == 'AO Contrast' else 0., hi)
        for owner in [m.node_tree for m in bpy.data.materials if m.use_nodes]+list(bpy.data.node_groups):
            for instance in owner.nodes:
                if instance.type=='GROUP' and instance.node_tree==tree:instance.inputs[name].default_value=value
    ao = tree.nodes.new('ShaderNodeTexImage'); ao.name = 'KaDshow AO'; ao['bjs_preview_image_role'] = 'ao'; ao.extension = 'EXTEND'
    # An unbound image must never turn into an accidental black AO multiplier.
    ao.image = ao_image
    uvnode = tree.nodes.new('ShaderNodeUVMap'); uvnode.uv_map = uv; tree.links.new(uvnode.outputs['UV'], ao.inputs['Vector'])
    available = tree.nodes.new('ShaderNodeValue'); available.name = 'AO Available'; available['bjs_ao_available'] = 1; available.outputs[0].default_value = float(ao_image is not None)
    factor = b.math('POWER', b.math('MINIMUM', b.math('MAXIMUM', ao.outputs['Color'], 0.), 1.), ins.outputs['AO Contrast'])
    def pass_grade(source, label, influence):
        n = tree.nodes.new('ShaderNodeGroup'); n.node_tree = create_grade('KaDshow '+label+' Grade'); n.name = label+' Grade'; n['bjs_grade_role'] = label.lower()
        tree.links.new(source, n.inputs['Colour'])
        weight = b.math('MULTIPLY', b.math('MULTIPLY', ins.outputs['AO Strength'], ins.outputs[influence]), available.outputs[0])
        attenuation = b.math('SUBTRACT', 1., b.math('MULTIPLY', weight, b.math('SUBTRACT', 1., factor)))
        return b.vec('SCALE', n.outputs['Colour'], attenuation)
    tree.links.new(pass_grade(direct, 'Direct', 'AO Direct Influence'), addition.inputs[0])
    tree.links.new(pass_grade(indirect, 'Indirect', 'AO Indirect Influence'), addition.inputs[1])
    final = tree.nodes.new('ShaderNodeGroup'); final.node_tree = create_grade('KaDshow Final Grade'); final.name = 'Final Grade'; final['bjs_grade_role'] = 'final'
    tree.links.new(addition.outputs[0], final.inputs['Colour']); tree.links.new(final.outputs['Colour'], multiply.inputs[1])
    for name, source in [('Illumination', final.outputs['Colour']), ('Direct Lighting', addition.inputs[0].links[0].from_socket), ('Indirect Lighting', addition.inputs[1].links[0].from_socket)]:
        tree.interface.new_socket(name=name, in_out='OUTPUT', socket_type='NodeSocketColor')
        output = next(n for n in tree.nodes if n.type == 'GROUP_OUTPUT' and n.is_active_output)
        tree.links.new(source, output.inputs[name])
    tree['bjs_grading_schema'] = SCHEMA
    # Keep the user's existing layout; put the new artist-facing groups in a clear row.
    right=max((n.location.x+n.width for n in existing_nodes),default=0)+300
    for index,role in enumerate(('direct','indirect','final')):
        grade=next(n for n in tree.nodes if n.get('bjs_grade_role')==role)
        grade.location=(right+index*310,300);grade.width=280
    frame=tree.nodes.new('NodeFrame');frame.label='AO attenuation arithmetic';frame.location=(right,-500)
    for index,n in enumerate(n for n in tree.nodes if n not in existing_nodes and n.type in ('MATH','VECT_MATH')):
        n.parent=frame;n.hide=True;n.location=((index%6)*160,-(index//6)*130)
    ao.location=(right,-1000);uvnode.location=(right-200,-1000);available.location=(right+300,-1000)
    return node


def _validate_ao(tree, image):
    direct = next((n.image for n in tree.nodes if n.get('bjs_preview_image_role') == 'direct'), None)
    if not direct or tuple(direct.size) != tuple(image.size): raise ValueError('AO and lighting dimensions must match')
    if image.colorspace_settings.name not in ('Non-Color', 'Linear Rec.709'): raise ValueError('AO must be linear scalar data')


def bind_ao(material, image):
    _, tree = inspect(material); _validate_ao(tree, image)
    sampler = next(n for n in tree.nodes if n.get('bjs_preview_image_role') == 'ao')
    sampler.image = image
    next(n for n in tree.nodes if n.get('bjs_ao_available')).outputs[0].default_value = 1.


class BJS_OT_lightmap_grading(bpy.types.Operator):
    bl_idname = 'babylon.lightmap_grading'
    bl_label = 'Babylon: Add or Upgrade Lightmap Grading'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        material = bpy.data.materials.get(context.scene.get('bjs_lighting_controls_material', ''))
        if material is None:
            self.report({'ERROR'}, 'Scene has no associated split-lighting preview material'); return {'CANCELLED'}
        try: upgrade(material)
        except ValueError as e:
            self.report({'ERROR'}, str(e)); return {'CANCELLED'}
        self.report({'INFO'}, 'Grading ready; enter the controls group to edit Direct, Indirect and Final Grade')
        return {'FINISHED'}
