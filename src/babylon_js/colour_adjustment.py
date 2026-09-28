"""Optional UV2 colour finishing layer. RGB=sRGB(linear multiplier/2), A=influence.

This layer changes albedo, never transparency or the physical lighting master.
See COLOUR_ADJUSTMENT.md for authoring, export and rebake boundaries.
"""
from pathlib import Path
import math
import numpy as np
import bpy

ENCODING = 'srgb-linear-multiplier2-alpha'
STRENGTH = 'Colour Adjustment Strength'

def controls(scene):
    mat=bpy.data.materials.get(scene.get('bjs_lighting_controls_material',''))
    if not mat or not mat.use_nodes: return None
    nodes=[n for n in mat.node_tree.nodes if n.get('bjs_lighting_controls')]
    if len(nodes)!=1: raise ValueError('One managed lighting controls node is required')
    return nodes[0]

def settings(scene):
    node=controls(scene)
    if not node: return None
    layers=[n for n in node.node_tree.nodes if n.get('bjs_colour_adjustment')]
    if not layers: return None
    if len(layers)!=1: raise ValueError('Ambiguous colour adjustment layers')
    image=next(n.image for n in layers[0].node_tree.nodes if n.type=='TEX_IMAGE')
    if image is None: raise ValueError('Colour adjustment image is missing')
    value=node.inputs[STRENGTH]
    if value.is_linked: raise ValueError('Colour adjustment strength must be a saved scalar')
    return dict(image=image, strength=float(value.default_value))

def srgb_encode(rgb):
    return np.where(rgb<=.0031308,rgb*12.92,1.055*np.maximum(rgb,0)**(1/2.4)-.055)

def srgb_decode(rgb):
    return np.where(rgb<=.04045,rgb/12.92,((rgb+.055)/1.055)**2.4)

def save_codes(path, array):
    from .ktx_export import _write_png
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    _write_png(path,np.rint(np.clip(array,0,1)*255).astype(np.uint8))

def load_image(path):
    image=bpy.data.images.load(str(path),check_existing=False)
    image.colorspace_settings.name='sRGB';image.alpha_mode='CHANNEL_PACKED'
    image.use_fake_user=True
    return image

def create_image(path, size=512):
    if size not in (512,1024): raise ValueError('Colour map size must be 512 or 1024')
    if Path(path).exists(): raise ValueError('Preserve the existing map; choose a new filename')
    a=np.zeros((size,size,4),np.float32);a[...,:3]=srgb_encode(np.array(.5))
    save_codes(path,a)
    return load_image(path)

def read_codes(image):
    if image.is_dirty or image.packed_file or not image.filepath:
        raise ValueError('Save/unpack the colour adjustment image before exporting')
    path=Path(bpy.path.abspath(image.filepath))
    if not path.is_file(): raise ValueError('Missing colour adjustment source: '+str(path))
    if image.colorspace_settings.name!='sRGB' or image.alpha_mode!='CHANNEL_PACKED':
        raise ValueError('Colour adjustment needs sRGB and Channel Packed alpha')
    copy=bpy.data.images.load(str(path),check_existing=False)
    try:
        copy.colorspace_settings.name='Non-Color';copy.alpha_mode='CHANNEL_PACKED'
        w,h=copy.size
        if w!=h or w<512 or copy.channels!=4: raise ValueError('Colour adjustment must be square RGBA, at least 512 pixels')
        a=np.empty(w*h*4,np.float32);copy.pixels.foreach_get(a)
        a=a.reshape(h,w,4)[::-1].copy()
        if not np.isfinite(a).all() or (a<0).any() or (a>1).any(): raise ValueError('Invalid colour adjustment pixels')
        return a
    finally: bpy.data.images.remove(copy)

def prepare_image(image,path,size):
    from .ktx_conversion import resize_lightmap
    if size not in (512,1024): raise ValueError('Colour adjustment export supports 512 or 1024')
    a=read_codes(image)
    if size>a.shape[0]: raise ValueError('Create a higher-resolution source before exporting at this size')
    # Resample alpha-weighted multipliers so neutral transparent pixels do not
    # dilute a painted colour at island edges. Alpha remains a separate scalar.
    linear=a.copy();linear[...,:3]=srgb_decode(a[...,:3])*a[...,3:4]
    linear=resize_lightmap(linear,dict(size=size,color_space='linear'),True)
    rgb=np.divide(linear[...,:3],linear[...,3:4],out=np.full_like(linear[...,:3],.5),where=linear[...,3:4]>1e-8)
    linear[...,:3]=srgb_encode(rgb);save_codes(path,linear)
    result=load_image(path);result['bjs_export_colour_temporary']=True
    return result

def group(image,uv='SimpleBake'):
    tree=bpy.data.node_groups.new('KaDshow Colour Adjustment','ShaderNodeTree')
    tree['bjs_colour_adjustment_schema']=1
    for name,kind in [('Colour','NodeSocketColor'),('Strength','NodeSocketFloat')]:
        sock=tree.interface.new_socket(name=name,in_out='INPUT',socket_type=kind)
        if name=='Strength': sock.default_value=1;sock.min_value=0;sock.max_value=1
    tree.interface.new_socket(name='Colour',in_out='OUTPUT',socket_type='NodeSocketColor')
    ins=tree.nodes.new('NodeGroupInput');out=tree.nodes.new('NodeGroupOutput')
    uvnode=tree.nodes.new('ShaderNodeUVMap');uvnode.uv_map=uv
    tex=tree.nodes.new('ShaderNodeTexImage');tex.image=image;tex.extension='EXTEND';tex.interpolation='Linear'
    tex['bjs_preview_image_role']='colour_adjustment';tree.links.new(uvnode.outputs['UV'],tex.inputs['Vector'])
    mul=tree.nodes.new('ShaderNodeVectorMath');mul.operation='SCALE';mul.inputs[3].default_value=2
    tree.links.new(tex.outputs['Color'],mul.inputs[0])
    amount=tree.nodes.new('ShaderNodeMath');amount.operation='MULTIPLY';amount.use_clamp=True
    tree.links.new(tex.outputs['Alpha'],amount.inputs[0]);tree.links.new(ins.outputs['Strength'],amount.inputs[1])
    mix=tree.nodes.new('ShaderNodeMixRGB');mix.blend_type='MIX';mix.inputs[1].default_value=(1,1,1,1)
    tree.links.new(amount.outputs[0],mix.inputs[0]);tree.links.new(mul.outputs[0],mix.inputs[2])
    product=tree.nodes.new('ShaderNodeVectorMath');product.operation='MULTIPLY'
    tree.links.new(ins.outputs['Colour'],product.inputs[0]);tree.links.new(mix.outputs[0],product.inputs[1]);tree.links.new(product.outputs[0],out.inputs[0])
    for i,n in enumerate(tree.nodes): n.location=(i*190,0)
    return tree

def attach(scene,image,uv='SimpleBake',strength=1):
    node=controls(scene)
    if node is None: raise ValueError('Create the managed lighting preview first')
    tree=node.node_tree
    if any(n.get('bjs_colour_adjustment') for n in tree.nodes): raise ValueError('Colour adjustment already exists; edit the saved image/strength')
    sources=[n for n in tree.nodes if n.get('bjs_preview_image_role')=='colour']
    if len(sources)!=1: raise ValueError('Expected one tagged material colour source')
    sock=tree.interface.new_socket(name=STRENGTH,in_out='INPUT',socket_type='NodeSocketFloat')
    sock.default_value=strength;sock.min_value=0;sock.max_value=1
    layer=tree.nodes.new('ShaderNodeGroup');layer.node_tree=group(image,uv);layer['bjs_colour_adjustment']=1
    layer.name='KaDshow Colour Adjustment';layer.label='UV2 colour variation (alpha is influence)'
    source=sources[0].outputs['Color'];targets=[l.to_socket for l in source.links]
    tree.links.new(source,layer.inputs['Colour'])
    tree.links.new(next(n for n in tree.nodes if n.type=='GROUP_INPUT').outputs[STRENGTH],layer.inputs['Strength'])
    for dest in targets:tree.links.new(layer.outputs[0],dest)
    node.inputs[STRENGTH].default_value=strength
    return layer

def prefill(operator,context):
    config=settings(context.scene)
    operator.colour_adjustment_enabled=bool(config)
    if config:operator.colour_adjustment_strength=config['strength']

def apply_to_base(tree,base,config,uv):
    """Use only on disposable albedo bake copies, never source export PBR."""
    layer=tree.nodes.new('ShaderNodeGroup');layer.node_tree=group(config['image'],uv)
    layer.inputs['Strength'].default_value=config['strength']
    if base.is_linked:tree.links.new(base.links[0].from_socket,layer.inputs['Colour'])
    else:layer.inputs['Colour'].default_value=base.default_value
    return layer.outputs[0]

def bypass(tree):
    for n in list(tree.nodes):
        if n.get('bjs_colour_adjustment'):
            for link in list(n.inputs['Strength'].links):tree.links.remove(link)
            n.inputs['Strength'].default_value=0
        elif n.type=='GROUP':bypass(n.node_tree)

def resolve(scene, options):
    config=settings(scene)
    enabled=options.get('colour_adjustment_enabled',bool(config))
    if not enabled:return None
    if not config:raise ValueError('Enable colour adjustment only with a saved preview map')
    config['strength']=float(options.get('colour_adjustment_strength',config['strength']))
    if not math.isfinite(config['strength']) or not 0<=config['strength']<=1:raise ValueError('Invalid colour adjustment strength')
    return config

def uv_mask(objects,size,selected_faces=False,uv_index=1):
    h=w=size;mask=np.zeros((h,w),bool);tiny=0
    for obj in objects:
        if obj.type!='MESH':continue
        mesh=obj.data
        if len(mesh.uv_layers)<=uv_index:raise ValueError('Missing UV2: '+obj.name)
        mesh.calc_loop_triangles();uvs=mesh.uv_layers[uv_index].data
        for tri in mesh.loop_triangles:
            if selected_faces and not mesh.polygons[tri.polygon_index].select:continue
            coords=np.array([uvs[i].uv[:] for i in tri.loops]);coords[:,1]=1-coords[:,1]
            if (coords<-.00001).any() or (coords>1.00001).any():raise ValueError('UV2 must lie inside the atlas')
            coords*=np.array([w,h]);lo=np.maximum(np.floor(coords.min(0)).astype(int),0);hi=np.minimum(np.ceil(coords.max(0)).astype(int),[w,h])
            if (hi<=lo).any():tiny+=1;continue
            x,y=np.meshgrid(np.arange(lo[0],hi[0])+.5,np.arange(lo[1],hi[1])+.5)
            def edge(p,q):return (x-p[0])*(q[1]-p[1])-(y-p[1])*(q[0]-p[0])
            es=[edge(coords[i],coords[(i+1)%3]) for i in range(3)]
            inside=(np.logical_and.reduce([e>=-1e-6 for e in es])|np.logical_and.reduce([e<=1e-6 for e in es]))
            if not inside.any():tiny+=1
            mask[lo[1]:hi[1],lo[0]:hi[0]]|=inside
    return mask,tiny

def pad_gaps(image,receivers):
    from .agx_lightmap import fill_gaps
    a=read_codes(image);mask,tiny=uv_mask(receivers,a.shape[0])
    a=fill_gaps(a,mask);save_codes(bpy.path.abspath(image.filepath),a);image.reload()
    return dict(owned_texels=int(mask.sum()),subpixel_triangles=tiny,padding='nearest occupied colour and influence; never overwrites a receiver')

def tint_objects(image,objects,multiplier=(1,1,1),alpha=1,selected_faces=False,uv_index=1):
    """Rasterise existing UV2 faces; preserve all unpainted texels. Save explicitly.

    Use broad whole-surface tints; inspect compressed output on tiny islands.
    Returns painted texels and the number of faces too small to hit a texel centre.
    """
    if len(multiplier)!=3 or any(not math.isfinite(x) or not 0<=x<=2 for x in multiplier) or not 0<=alpha<=1:
        raise ValueError('Multipliers must be 0..2; influence must be 0..1')
    a=read_codes(image)
    mask,tiny=uv_mask(objects,a.shape[0],selected_faces,uv_index)
    a[mask,:3]=srgb_encode(np.array(multiplier)/2);a[mask,3]=alpha
    save_codes(bpy.path.abspath(image.filepath),a);image.reload()
    return dict(painted_texels=int(mask.sum()),subpixel_triangles=tiny)

class BJS_OT_colour_adjustment(bpy.types.Operator):
    bl_idname='babylon.colour_adjustment';bl_label='KaDshow: Create colour adjustment map';bl_options={'REGISTER','UNDO'}
    filepath:bpy.props.StringProperty(name='PNG source',subtype='FILE_PATH',default='//textures/colour_adjustments/colour_adjustment.png')
    size:bpy.props.EnumProperty(name='Size',items=(('512','512',''),('1024','1024','')),default='512')
    def invoke(self,context,event):return context.window_manager.invoke_props_dialog(self)
    def execute(self,context):
        try:
            if settings(context.scene):raise ValueError('A colour adjustment map already exists')
            if not bpy.data.filepath:raise ValueError('Save the Blender file first')
            image=create_image(bpy.path.abspath(self.filepath),int(self.size));attach(context.scene,image)
        except Exception as exc:self.report({'ERROR'},str(exc));return {'CANCELLED'}
        return {'FINISHED'}

class BJS_OT_tint_selected(bpy.types.Operator):
    bl_idname='babylon.tint_selected';bl_label='KaDshow: Tint selected UV2 surfaces';bl_options={'REGISTER'}
    multiplier:bpy.props.FloatVectorProperty(name='Linear RGB multipliers',size=3,default=(1,1,1),min=0,max=2)
    influence:bpy.props.FloatProperty(name='Influence',default=1,min=0,max=1)
    def invoke(self,context,event):return context.window_manager.invoke_props_dialog(self)
    def execute(self,context):
        try:
            config=settings(context.scene)
            if not config:raise ValueError('Create the colour adjustment map first')
            result=tint_objects(config['image'],context.selected_objects,self.multiplier,self.influence)
            markers=[o for o in context.scene.objects if o.name.startswith('lightmap_')]
            receivers=[o for m in markers for o in m.children_recursive if o.type=='MESH']
            if receivers:result['padding']=pad_gaps(config['image'],receivers)
            self.report({'INFO'},str(result))
        except Exception as exc:self.report({'ERROR'},str(exc));return {'CANCELLED'}
        return {'FINISHED'}
