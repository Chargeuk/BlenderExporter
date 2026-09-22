"""Create the saved diffuse-preview graph; never replace physical PBR materials."""
import bpy

CONTROLS = {
    'Direct Strength': (1., 0., 100.), 'Indirect Strength': (1., 0., 100.),
    'Shadow Lift': (0., 0., 1.), 'Adaptive Smoothing': (0., 0., 2.),
    'Patch Size': (3., 0., 16.), 'Dark Difference': (.02, 0., 10.),
    'Shadow Suppression': (2., 0., 8.), 'Fully Lit Threshold': (1., .01, 8.),
    'Highlight Preservation': (1., 0., 1.),
}
RUNTIME_CONTROLS = frozenset(('Shadow Suppression', 'Fully Lit Threshold', 'Highlight Preservation'))


def _managed_graphs(material):
    seen = set()
    def visit(tree):
        if tree is None or tree.as_pointer() in seen:
            return
        seen.add(tree.as_pointer())
        if tree.get('bjs_preview_schema') == 1:
            yield tree
        for node in tree.nodes:
            if node.type == 'GROUP':
                yield from visit(node.node_tree)
    return list(visit(material.node_tree))


def _resolution_plan(tree, expected_size=None):
    roles = {'direct': [], 'indirect': [], 'island_ids': []}
    offsets = {}
    for node in tree.nodes:
        role = node.get('bjs_preview_image_role')
        if role in roles:
            if node.type != 'TEX_IMAGE' or node.image is None:
                raise ValueError('Missing managed lighting image')
            roles[role].append(node.image)
        if 'bjs_pixel_offset' in node:
            xy = tuple(node['bjs_pixel_offset'])
            if node.type != 'VECT_MATH' or node.operation != 'SCALE' or xy in offsets:
                raise ValueError('Invalid managed smoothing offset')
            offsets[xy] = node
    if [len(roles[k]) for k in roles] != [1, 25, 25]:
        raise ValueError('Managed lighting samplers changed; inspect the graph')
    dimensions = {tuple(im.size) for images in roles.values() for im in images}
    if len(dimensions) != 1 or min(next(iter(dimensions))) < 1:
        raise ValueError('Lighting and ownership image dimensions must match')
    size = next(iter(dimensions))
    if expected_size is not None and tuple(expected_size) != size:
        raise ValueError('Preview source dimensions do not match the configured bake size')
    required = {(x, y) for x in range(-2, 3) for y in range(-2, 3) if x or y}
    if set(offsets) != required:
        raise ValueError('Expected 24 managed smoothing offsets')
    return size, [(node, (x / 2 / size[0], y / 2 / size[1], 0.))
                  for (x, y), node in offsets.items()]


def validate_preview_resolution(material, expected_size=None):
    """Check source-pixel offsets, never delivery resizing. Legacy graphs are unverified."""
    graphs = _managed_graphs(material)
    for tree in graphs:
        size, plan = _resolution_plan(tree, expected_size)
        if tuple(tree.get('bjs_source_size', ())) != size or any(
                any(abs(a-b) > 1e-10 for a, b in zip(node.inputs[0].default_value, value))
                or node.inputs[0].is_linked for node, value in plan):
            raise ValueError('Stale smoothing offsets: run update_preview_resolution after replacing all lighting images')
    return {'status': 'verified' if graphs else 'unverified_custom_graph', 'managed_groups': len(graphs)}


def update_preview_resolution(material):
    """Explicitly refresh managed offsets after replacing D/I/IDs. Preserve controls."""
    graphs = _managed_graphs(material)
    if not graphs:
        raise ValueError('Custom graph: inspect its pixel offsets manually; no automatic migration')
    plans = [(tree, *_resolution_plan(tree)) for tree in graphs]
    if any(node.inputs[0].is_linked for _, _, plan in plans for node, _ in plan):
        raise ValueError('Linked smoothing offsets require manual inspection')
    for tree, size, plan in plans:
        for node, value in plan:
            node.inputs[0].default_value = value
        tree['bjs_source_size'] = list(size)
    return validate_preview_resolution(material)


def create_preview_material(colour, direct, indirect, island_ids, *,
                            name='KaDshow Split Lighting PREVIEW',
                            uv1='UVMap', uv2='SimpleBake', scene=None):
    """Use loaded images, return (material, controls node). New names only.

    Inputs must be sRGB colour, decoded linear lightmaps and Non-Color float IDs.
    Images can be blank placeholders at the intended atlas size before first bake.
    The caller assigns this preview only to preview copies, preserving PBR sources.
    """
    if name in bpy.data.materials:
        raise ValueError('Preview material already exists; preserve its controls or choose a new name')
    if any(im is None for im in (colour, direct, indirect, island_ids)):
        raise ValueError('Four loaded image datablocks are required')
    size=tuple(indirect.size)
    if min(size)<1 or any(tuple(im.size)!=size for im in (direct,island_ids)):
        raise ValueError('Lighting and ownership image dimensions must match')
    if colour.colorspace_settings.name!='sRGB' or island_ids.colorspace_settings.name!='Non-Color':
        raise ValueError('Colour must be sRGB and ownership IDs Non-Color')
    if any(im.colorspace_settings.name not in ('Linear Rec.709','Non-Color') for im in (direct,indirect)):
        raise ValueError('Lightmaps must be decoded linear data')
    scene=scene or bpy.context.scene
    if scene.get('bjs_lighting_controls_material'):
        raise ValueError('Scene already has lighting controls; preserve them or explicitly clear the association')
    tree=bpy.data.node_groups.new(name+' Controls','ShaderNodeTree')
    tree['bjs_preview_schema']=1;tree['bjs_source_size']=list(size)
    for key,(value,lo,hi) in CONTROLS.items():
        sock=tree.interface.new_socket(name=key,in_out='INPUT',socket_type='NodeSocketFloat')
        sock.default_value=value;sock.min_value=lo;sock.max_value=hi
    tree.interface.new_socket(name='Shader',in_out='OUTPUT',socket_type='NodeSocketShader')
    nodes=tree.nodes;links=tree.links
    ins=nodes.new('NodeGroupInput');out=nodes.new('NodeGroupOutput')
    def wire(value,socket):
        if isinstance(value,bpy.types.NodeSocket):links.new(value,socket)
        else:socket.default_value=value
    def math(op,a,b=None):
        n=nodes.new('ShaderNodeMath');n.operation=op;wire(a,n.inputs[0])
        if b is not None:wire(b,n.inputs[1])
        return n.outputs[0]
    def vector(op,a,b=None):
        n=nodes.new('ShaderNodeVectorMath');n.operation=op;wire(a,n.inputs[0])
        if b is not None:wire(b,n.inputs[3] if op=='SCALE' else n.inputs[1])
        return n.outputs['Value' if op=='DOT_PRODUCT' else 'Vector']
    def uv(layer):
        n=nodes.new('ShaderNodeUVMap');n.uv_map=layer;return n.outputs['UV']
    def sample(im,coords,interpolation='Linear',extension='EXTEND',role=None):
        n=nodes.new('ShaderNodeTexImage');n.image=im;n.interpolation=interpolation;n.extension=extension
        n['bjs_preview_image_role']=role
        links.new(coords,n.inputs['Vector']);return n.outputs['Color']
    material_uv=uv(uv1);light_uv=uv(uv2)
    colour_value=sample(colour,material_uv,extension='REPEAT',role='colour');direct_value=sample(direct,light_uv,role='direct')
    centre=sample(indirect,light_uv,role='indirect');centre_id=sample(island_ids,light_uv,'Closest',role='island_ids')
    # Replicate the established 25-tap same-island binomial filter.
    total=vector('SCALE',centre,36.);weight=36.
    weights=(1,4,6,4,1)
    for y in range(-2,3):
        for x in range(-2,3):
            if x==0 and y==0:continue
            offset=vector('SCALE',(x/2/size[0],y/2/size[1],0.),ins.outputs['Patch Size'])
            offset.node['bjs_pixel_offset']=[x,y]
            coords=vector('ADD',light_uv,offset)
            sid=sample(island_ids,coords,'Closest',role='island_ids')
            # Scalar conversion is safe: each ID has equal RGB channels.
            difference=math('ABSOLUTE',math('SUBTRACT',sid,centre_id))
            same=math('LESS_THAN',difference,.1)
            w=math('MULTIPLY',same,float(weights[x+2]*weights[y+2]))
            total=vector('ADD',total,vector('SCALE',sample(indirect,coords,'Closest',role='indirect'),w))
            weight=math('ADD',weight,w)
    mean=vector('SCALE',total,math('DIVIDE',1.,weight))
    def adjusted(value):
        scaled=vector('SCALE',value,math('SUBTRACT',1.,ins.outputs['Shadow Lift']))
        lift=vector('SCALE',(1.,1.,1.),ins.outputs['Shadow Lift'])
        return vector('SCALE',vector('ADD',scaled,lift),ins.outputs['Indirect Strength'])
    centre_adjusted=adjusted(centre);mean_adjusted=adjusted(mean)
    delta_vector=vector('SUBTRACT',mean_adjusted,centre_adjusted)
    delta=vector('DOT_PRODUCT',delta_vector,(.2126,.7152,.0722))
    k=ins.outputs['Dark Difference']
    q=math('DIVIDE',math('SUBTRACT',delta,k),math('MAXIMUM',k,.00001))
    q=math('MINIMUM',math('MAXIMUM',q,0.),1.)
    amount=math('MULTIPLY',math('MULTIPLY',q,ins.outputs['Adaptive Smoothing']),math('GREATER_THAN',ins.outputs['Patch Size'],0.))
    indirect_value=vector('ADD',centre_adjusted,vector('SCALE',delta_vector,amount))
    illumination=vector('ADD',vector('SCALE',direct_value,ins.outputs['Direct Strength']),indirect_value)
    emission=nodes.new('ShaderNodeEmission');links.new(vector('MULTIPLY',colour_value,illumination),emission.inputs['Color'])
    emission.inputs['Strength'].default_value=1.;links.new(emission.outputs[0],out.inputs['Shader'])
    mat=bpy.data.materials.new(name);mat.use_nodes=True;mat.node_tree.nodes.clear();mat.use_fake_user=True
    node=mat.node_tree.nodes.new('ShaderNodeGroup');node.node_tree=tree;node.name='KaDshow Lighting Controls';node['bjs_lighting_controls']=1
    output=mat.node_tree.nodes.new('ShaderNodeOutputMaterial');output.location=(340,0)
    mat.node_tree.links.new(node.outputs['Shader'],output.inputs['Surface'])
    scene['bjs_lighting_controls_material']=mat.name
    return mat,node
