"""Albedo-aware display compensation, isolated from physical lighting masters.

Blender-only preparation uses temporary scenes and copies. All arrays are top-down.
The resulting map targets Babylon's default gamma-2.2 display, not a second AgX pass.
"""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import numpy as np


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_exr(path, pixels):
    import OpenImageIO as oiio
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    pixels = np.ascontiguousarray(pixels, dtype=np.float32)
    if not np.isfinite(pixels).all():
        raise ValueError('Non-finite AgX preparation image')
    buf = oiio.ImageBuf(oiio.ImageSpec(pixels.shape[1], pixels.shape[0], pixels.shape[2], oiio.FLOAT))
    buf.set_pixels(oiio.ROI.All, pixels)
    if not buf.write(str(path)):
        raise RuntimeError(buf.geterror())


def read_exr(path):
    import OpenImageIO as oiio
    result = oiio.ImageBuf(str(path)).get_pixels(oiio.FLOAT)
    if result is None or not np.isfinite(result).all():
        raise ValueError('Invalid source image: ' + str(path))
    return result


def compensate(colour, lighting, processor, mask):
    """Keep preservation independent: never counteract the user's runtime slider."""
    if colour.shape != lighting.shape or colour.shape[:2] != mask.shape or not mask.any():
        raise ValueError('Colour, lighting and coverage must match')
    if not np.isfinite(colour).all() or not np.isfinite(lighting).all() or (colour < 0).any():
        raise ValueError('Invalid compensation inputs')
    display = np.ascontiguousarray(np.maximum(colour * lighting, 0), dtype=np.float32)
    processor.applyRGB(display.reshape(-1, 3))
    target = np.clip(display, 0, 1) ** 2.2
    result = target / np.maximum(colour, 1e-5)
    if not np.isfinite(result).all() or result[mask].max() > 255:
        raise ValueError('AgX correction exceeds RGBD range; inspect near-black albedo')
    return result, {'division_floor_channels': int((colour[mask] < 1e-5).sum()),
                    'owned_pixels': int(mask.sum()), 'corrected_max': float(result[mask].max())}


def fill_gaps(array, mask):
    """Propagate existing texels through empty gaps without mixing island colours."""
    if not mask.any():
        raise ValueError('Empty albedo coverage')
    result = array.copy(); valid = mask.copy()
    while not valid.all():
        before = valid.copy()
        for dy, dx in ((-1,0),(1,0),(0,-1),(0,1),(-1,-1),(-1,1),(1,-1),(1,1)):
            neighbour = np.roll(before, (dy,dx), axis=(0,1))
            if dy == -1: neighbour[-1,:] = False
            if dy == 1: neighbour[0,:] = False
            if dx == -1: neighbour[:,-1] = False
            if dx == 1: neighbour[:,0] = False
            take = neighbour & ~valid
            result[take] = np.roll(result, (dy,dx), axis=(0,1))[take]
            valid[take] = True
    return result


@contextmanager
def temporary_scene():
    import bpy
    original = bpy.context.window.scene
    kinds = ('objects','meshes','materials','node_groups','images')
    existing = {kind: set(getattr(bpy.data,kind)) for kind in kinds}
    scene = bpy.data.scenes.new('TEMP AgX colour preparation')
    try:
        bpy.context.window.scene = scene
        scene.render.engine = 'CYCLES'; scene.cycles.samples = 1
        scene.cycles.use_denoising = False; scene.cycles.use_adaptive_sampling = False
        scene.cycles.device = 'CPU'
        yield scene
    finally:
        bpy.context.window.scene = original
        bpy.data.scenes.remove(scene)
        for kind in kinds:
            store = getattr(bpy.data,kind)
            for item in list(store):
                if item not in existing[kind]: store.remove(item, do_unlink=True)


def pixels(image):
    w,h = image.size; result = np.empty(w*h*4,np.float32)
    image.pixels.foreach_get(result)
    return result.reshape(h,w,4)[::-1].copy()


def target_image(materials, size):
    import bpy
    im = bpy.data.images.new('TEMP albedo target',size,size,alpha=True,float_buffer=True)
    im.colorspace_settings.name = 'Non-Color'
    im.pixels.foreach_set(np.zeros(size*size*4,np.float32))
    for mat in materials:
        n = mat.node_tree.nodes.new('ShaderNodeTexImage'); n.image = im
        mat.node_tree.nodes.active = n
    return im


def bake_albedo(context, objects, path, size, uv=None):
    """Refresh colour in UV2 on evaluated copies; original scene/materials stay intact."""
    import bpy
    objects = list(objects)
    if not objects or any(o.type != 'MESH' for o in objects):
        raise ValueError('Albedo preparation requires explicit mesh receivers')
    source_scene = context.scene
    deps = context.evaluated_depsgraph_get()
    # Capture evaluated meshes in their original scene before switching contexts.
    meshes = []
    try:
        for obj in objects:
            mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(deps), preserve_all_data_layers=True, depsgraph=deps)
            meshes.append((obj,mesh,obj.matrix_world.copy()))
        with temporary_scene() as scene:
            clones = {}; copies = []
            for obj, mesh, matrix in meshes:
                layer = uv or (mesh.uv_layers[1].name if len(mesh.uv_layers)>1 else '')
                if not layer or layer not in mesh.uv_layers:
                    raise ValueError('Missing lightmap UV on '+obj.name)
                # Establish the bake UV without changing the default UV used by colour textures.
                mesh.uv_layers.active = mesh.uv_layers[layer]
                slots = [s.material for s in obj.material_slots]
                mesh.materials.clear()
                for source in slots:
                    if source and source.name == source_scene.get('bjs_lighting_controls_material'):
                        source = bpy.data.materials.get(source_scene.get('bjs_runtime_material',''))
                    if not source or not source.use_nodes:
                        raise ValueError('Albedo bake needs a physical Principled material on '+obj.name)
                    if source not in clones:
                        mat = source.copy(); nt = mat.node_tree
                        principal = [n for n in nt.nodes if n.type=='BSDF_PRINCIPLED']
                        if len(principal)!=1:
                            raise ValueError('Albedo preparation requires one Principled node: '+source.name)
                        base = principal[0].inputs['Base Color']
                        emit = nt.nodes.new('ShaderNodeEmission')
                        if base.is_linked: nt.links.new(base.links[0].from_socket,emit.inputs['Color'])
                        else: emit.inputs['Color'].default_value = base.default_value
                        for output in [n for n in nt.nodes if n.type=='OUTPUT_MATERIAL']:
                            nt.links.new(emit.outputs[0],output.inputs['Surface'])
                        clones[source] = mat
                    mesh.materials.append(clones[source])
                ob = bpy.data.objects.new('TEMP '+obj.name,mesh); scene.collection.objects.link(ob)
                ob.matrix_world = matrix; ob.select_set(True); copies.append(ob)
            bpy.context.view_layer.objects.active = copies[0]
            image = target_image(clones.values(),size)
            layer_name = uv or meshes[0][1].uv_layers[1].name
            # Joining removes per-object Cycles startup cost for atlas/UV-only colour.
            # Generated/Object coordinates and other per-object shader inputs must stay separate.
            def uv_only(mat):
                allowed = {'OUTPUT_MATERIAL','EMISSION','TEX_IMAGE','UVMAP','RGB','VALUE',
                           'VECT_MATH','MATH','MIX_RGB','MIX','SEPRGB','COMBRGB'}
                seen = set()
                def visit(node):
                    if node in seen: return True
                    seen.add(node)
                    return node.type in allowed and all(visit(link.from_node) for socket in node.inputs for link in socket.links)
                return all(visit(n) for n in mat.node_tree.nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output)
            layouts = {(tuple(u.name for u in mesh.uv_layers), next((u.name for u in mesh.uv_layers if u.active_render), '')) for _,mesh,_ in meshes}
            if len(copies)>1 and len(layouts)==1 and all(uv_only(m) for m in clones.values()):
                bpy.ops.object.join()
            print('Preparing albedo in lightmap UVs:',len(objects),'receivers',flush=True)
            bpy.ops.object.bake(type='EMIT',margin=0,use_clear=False,use_selected_to_active=False,uv_layer=layer_name)
            array = pixels(image)
            if not (array[:,:,3]>.5).any(): raise ValueError('Albedo bake has no coverage')
            write_exr(path,array)
    finally:
        for _,mesh,_ in meshes:
            try:
                if mesh.name in bpy.data.meshes: bpy.data.meshes.remove(mesh)
            except ReferenceError:
                pass  # Native join may already have removed a temporary mesh.
    record = {'schema_version':1,'kind':'linear-albedo-in-lightmap-uv','size':size,
              'objects':sorted(o.name for o in objects),'sha256':sha(path),
              'refresh_policy':'regenerated from current export receivers, never reuse unchecked colour'}
    Path(str(path)+'.json').write_text(json.dumps(record,indent=2))
    return array


def combine_saved_controls(context, size, path):
    """Evaluate managed preview with white albedo to obtain current combined lighting."""
    import bpy
    name = context.scene.get('bjs_lighting_controls_material')
    if not name: return None
    source = bpy.data.materials.get(name)
    if not source or not source.use_nodes: raise ValueError('Missing saved lighting controls')
    from .lighting_controls import validate_preview_resolution
    validate_preview_resolution(source,(size,size))
    with temporary_scene() as scene:
        def clone(tree):
            tree = tree.copy()
            for n in tree.nodes:
                if n.type=='GROUP': n.node_tree = clone(n.node_tree)
            return tree
        mat = source.copy()
        for n in mat.node_tree.nodes:
            if n.type=='GROUP': n.node_tree = clone(n.node_tree)
        count = 0; uvnames = set()
        def whiten(tree):
            nonlocal count
            for n in list(tree.nodes):
                if n.type=='GROUP': whiten(n.node_tree)
                if n.type=='UVMAP': uvnames.add(n.uv_map)
                if n.type=='TEX_IMAGE' and n.get('bjs_preview_image_role')=='colour':
                    white = tree.nodes.new('ShaderNodeRGB'); white.outputs[0].default_value=(1,1,1,1)
                    for link in list(n.outputs['Color'].links): tree.links.new(white.outputs[0],link.to_socket)
                    count += 1
        whiten(mat.node_tree)
        if not count: raise ValueError('AgX export requires tagged colour sampler(s) in the managed preview')
        mesh = bpy.data.meshes.new('TEMP lighting atlas'); mesh.from_pydata([(0,0,0),(1,0,0),(1,1,0),(0,1,0)],[],[(0,1,2,3)])
        ob = bpy.data.objects.new('TEMP lighting atlas',mesh); scene.collection.objects.link(ob); mesh.materials.append(mat)
        for name in sorted(uvnames or {'UVMap'}):
            layer = mesh.uv_layers.new(name=name)
            for d,co in zip(layer.data,[(0,0),(1,0),(1,1),(0,1)]): d.uv=co
        ob.select_set(True); bpy.context.view_layer.objects.active=ob
        image=target_image([mat],size)
        bpy.ops.object.bake(type='EMIT',margin=0,use_clear=False,use_selected_to_active=False)
        array=pixels(image);write_exr(path,array)
    return array


def export_compensated(context, objects, marker, source, directory):
    import bpy, PyOpenColorIO as ocio
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    if Path(str(source)+'.agx.json').is_file():
        raise ValueError('Already AgX-compensated input; select original linear lighting master')
    view=context.scene.view_settings
    if view.view_transform!='AgX': raise ValueError('AgX compensation requires Blender view transform AgX')
    settings={'exposure':view.exposure,'gamma':view.gamma,'look':view.look}
    if view.use_curve_mapping: raise ValueError('AgX export does not support custom view curves')
    if context.scene.display_settings.display_device!='sRGB': raise ValueError('AgX lightmap export targets sRGB display')
    original=read_exr(source)
    h,w=original.shape[:2]
    if h!=w: raise ValueError('AgX lightmap requires square source')
    combined=combine_saved_controls(context,w,directory/'combined_linear.exr')
    lighting=(combined if combined is not None else original)[...,:3]
    root=bpy.data.objects.get(marker)
    if root is None: raise ValueError('Lightmap marker missing in Blender')
    receivers=[o for o in objects if o.type=='MESH' and o in root.children_recursive]
    colour=bake_albedo(context,receivers,directory/'albedo_uv.exr',w)
    mask=colour[...,3]>.5
    config_path=Path(bpy.utils.resource_path('LOCAL'))/'datafiles/colormanagement/config.ocio'
    cfg=ocio.Config.CreateFromFile(str(config_path))
    transforms=[]
    if settings['look']!='None':
        transforms.append(ocio.LookTransform(src='Linear Rec.709',dst='Linear Rec.709',looks=settings['look']))
    transforms.append(ocio.DisplayViewTransform(src='Linear Rec.709',display='sRGB',view='AgX'))
    cpu=cfg.getProcessor(ocio.GroupTransform(transforms)).getDefaultCPUProcessor()
    class Display:
        def applyRGB(self, data):
            data *= 2**settings['exposure'];cpu.applyRGB(data)
            np.maximum(data,0,out=data);np.power(data,1/settings['gamma'],out=data)
    corrected,metrics=compensate(colour[...,:3],lighting,Display(),mask)
    corrected=fill_gaps(corrected,mask)
    output=directory/(Path(source).stem+'_agx.exr');write_exr(output,corrected)
    record={'schema_version':1,'source':str(source),'source_sha256':sha(source),
            'source_controls_recombined':combined is not None,'albedo':str(directory/'albedo_uv.exr'),
            'albedo_sha256':sha(directory/'albedo_uv.exr'),'config_sha256':sha(config_path),
            'settings':settings,'inverse_runtime_display':'gamma 2.2','preservation':'independent runtime control',
            'output_sha256':sha(output),**metrics}
    Path(str(output)+'.agx.json').write_text(json.dumps(record,indent=2))
    return output,record
