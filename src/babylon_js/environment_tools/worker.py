"""Run only in a disposable background Blender process opened on a saved scene."""
import sys, json, math, time
from pathlib import Path
import bpy
import numpy as np
from mathutils import Vector

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))
from common import load_config, sha, save_json
from babylon_js import environment_controls as world_tools
from image_binding import replace_from_disk
from appearance import preview_identity, verify_combination


def select(c, name):
    spec = c.get(name, {})
    if 'names' in spec:
        missing = set(spec['names']) - set(bpy.context.scene.objects.keys())
        if missing: raise ValueError(f'{name}: missing objects {sorted(missing)}')
        return [bpy.context.scene.objects[n] for n in spec['names']]
    if not spec: return []
    conditions=spec.get('properties', {spec.get('property'):spec.get('values',[])})
    return sorted([o for o in bpy.context.scene.objects if o.type == spec.get('type', 'MESH')
        and all(o.get(key) in values for key,values in conditions.items())], key=lambda o: o.name)


def binding(c, name): return (Path(c['_root']) / c['images'][name]).resolve()


def relink(c, root):
    replacements = {}
    for path in c.get('source_images',[]):
        replacements[(Path(c['_root'])/path).resolve()]=root/'source-assets'/path
    for row in c.get('downloads', []):
        replacements[(Path(c['_root']) / row['path']).resolve()] = root/'sources'/Path(row['path']).name
    for name, filename in [('direct','direct_final.exr'), ('indirect','indirect_final.exr'),
                           ('island_ids','island_ids.exr'), ('combined','combined.exr')]:
        replacements[binding(c, name)] = root/'masters'/filename
    for im in list(bpy.data.images):
        if im.source == 'FILE' and im.filepath:
            target = replacements.get(Path(bpy.path.abspath(im.filepath)).resolve())
            if target and target.is_file(): replace_from_disk(im,target)


def write_exr(path, array):
    import OpenImageIO as oiio
    array = np.asarray(array, dtype=np.float32)
    if not np.isfinite(array).all(): raise ValueError('Non-finite output')
    spec = oiio.ImageSpec(array.shape[1], array.shape[0], array.shape[2], oiio.FLOAT)
    spec.channelnames = list('RGBA')[:array.shape[2]]
    if array.shape[2] == 4: spec.alpha_channel = 3
    spec.attribute('colorInteropID','lin_rec709_scene'); spec.attribute('oiio:ColorSpace','lin_rec709_scene')
    spec.attribute('compression','zip')
    buf=oiio.ImageBuf(spec); buf.set_pixels(oiio.ROI.All,array)
    if not buf.write(str(path)): raise RuntimeError(buf.geterror())
    if not np.array_equal(oiio.ImageBuf(str(path)).get_pixels(oiio.FLOAT),array): raise RuntimeError('EXR roundtrip changed pixels')


def raw_pixels(image):
    w,h=image.size; a=np.empty(w*h*4,np.float32);image.pixels.foreach_get(a)
    a=a.reshape(h,w,4)[::-1].copy()
    if not np.isfinite(a).all(): raise ValueError('Non-finite bake')
    return a


def setup(c):
    s=bpy.context.scene; s.render.engine='CYCLES'
    s.cycles.use_adaptive_sampling=False; s.cycles.use_denoising=False; s.cycles.use_animated_seed=False
    s.cycles.diffuse_bounces=c.get('diffuse_bounces',8);s.cycles.max_bounces=c.get('total_bounces',12)
    s.cycles.sample_clamp_direct=0;s.cycles.sample_clamp_indirect=0
    s.compositing_node_group=None
    requested=c.get('device','CPU')
    if requested=='CPU': s.cycles.device='CPU'
    else:
        prefs=bpy.context.preferences.addons['cycles'].preferences
        available=[item[0] for item in prefs.get_device_types(bpy.context)]
        if requested not in available: raise ValueError(f'Requested {requested} unavailable: {available}')
        prefs.compute_device_type=requested;prefs.refresh_devices()
        for device in prefs.devices:device.use=device.type==requested
        if not any(d.use for d in prefs.devices):raise ValueError('No requested compute device found')
        s.cycles.device='GPU'
    return s


def preflight(c):
    s=bpy.context.scene; receivers=select(c,'receivers'); contributors=select(c,'contributors')
    if not receivers:raise ValueError('No bake receivers selected')
    if set(receivers)&set(contributors):raise ValueError('Receiver and contributor sets overlap')
    if not set(select(c,'metal_receivers')).issubset(receivers):raise ValueError('Metal receivers must be bake receivers')
    uvname=c.get('uv','SimpleBake');missing=[]; triangles=0
    for o in receivers:
        if any(slot.link!='DATA' for slot in o.material_slots):raise ValueError('Use data-linked material slots for baking: '+o.name)
        mesh=o.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh()
        try:
            if uvname not in mesh.uv_layers:raise ValueError('Missing lightmap UV: '+o.name)
            coords=np.array([tuple(t.uv) for t in mesh.uv_layers[uvname].data])
            if not np.isfinite(coords).all() or (coords<-.000001).any() or (coords>1.000001).any():raise ValueError('Invalid/out-of-range lightmap UV: '+o.name)
            mesh.calc_loop_triangles();triangles+=len(mesh.loop_triangles)
            if not mesh.materials or any(m is None for m in mesh.materials):raise ValueError('Missing material: '+o.name)
            for mat in mesh.materials:
                if not mat.use_nodes or not any(n.type=='BSDF_PRINCIPLED' for n in mat.node_tree.nodes):
                    raise ValueError('Physical bake requires a Principled source material: '+mat.name)
        finally:o.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh_clear()
    generated={binding(c,k) for k in ('direct','indirect','island_ids','combined')}
    declared={(Path(c['_root'])/p).resolve() for p in c.get('source_images',[])}
    declared.update((Path(c['_root'])/p['path']).resolve() for p in c.get('downloads',[]))
    root=Path(c['_candidate'])
    declared.update((root/'source-assets'/p).resolve() for p in c.get('source_images',[]))
    declared.update((root/'sources'/Path(p['path']).name).resolve() for p in c.get('downloads',[]))
    generated.update(root/'masters'/p for p in ('direct_final.exr','indirect_final.exr','island_ids.exr','combined.exr'))
    for im in bpy.data.images:
        if im.source=='FILE' and im.filepath and not im.packed_file:
            path=Path(bpy.path.abspath(im.filepath)).resolve()
            if not path.is_file() and path not in generated:missing.append(str(path))
            if path not in generated and path not in declared:raise ValueError('Declare source image in source_images/downloads: '+str(path))
    if missing:raise ValueError('Missing required source images: '+str(missing))
    node=world_tools.find_controls(s.world)
    if not node:raise ValueError('Connect the supported KaDshow Environment Controls World group')
    output=next(n for n in s.world.node_tree.nodes if n.type=='OUTPUT_WORLD' and n.is_active_output)
    if not output.inputs['Surface'].links or output.inputs['Surface'].links[0].from_socket!=node.outputs['Lighting']:
        raise ValueError('Physical baking requires the World Lighting output')
    if c.get('preview_material') not in bpy.data.materials:raise ValueError('Missing configured preview material')
    return {'receiver_count':len(receivers),'triangles':triangles,'receivers':[o.name for o in receivers],
        'contributors':[o.name for o in contributors],'excluded_glass':[o.name for o in select(c,'glass')],
        'world_controls':world_tools.settings(node),'missing_source_images':missing,'blender_version':bpy.app.version_string,
        'samples':c['samples'],'quality':c.get('quality','final')}


def evaluate_receivers(c):
    s=bpy.context.scene;deps=bpy.context.evaluated_depsgraph_get(); copies=[];catalogue=[]
    for o in select(c,'receivers'):
        ev=o.evaluated_get(deps)
        mesh=bpy.data.meshes.new_from_object(ev,preserve_all_data_layers=True,depsgraph=deps)
        ob=bpy.data.objects.new('TEMP receiver '+o.name,mesh);s.collection.objects.link(ob);ob.matrix_world=o.matrix_world.copy()
        mesh.calc_loop_triangles();uv=mesh.uv_layers[c.get('uv','SimpleBake')]
        parent=list(range(len(mesh.polygons)));edges={}
        def find(x):
            while parent[x]!=x:parent[x]=parent[parent[x]];x=parent[x]
            return x
        for p in mesh.polygons:
            loops=list(p.loop_indices)
            for a,b in zip(loops,loops[1:]+loops[:1]):
                key=tuple(sorted((mesh.loops[q].vertex_index,round(uv.data[q].uv.x,7),round(uv.data[q].uv.y,7)) for q in (a,b)))
                if key in edges:parent[find(p.index)]=find(edges[key])
                else:edges[key]=p.index
        groups={}
        for tri in mesh.loop_triangles:groups.setdefault(find(tri.polygon_index),[]).append([list(uv.data[q].uv) for q in tri.loops])
        for key,tris in groups.items():catalogue.append({'id':len(catalogue)+1,'object':o.name,'polygon_root':key,'triangles':tris})
        normal_matrix=o.matrix_world.to_3x3().inverted().transposed()
        attr=mesh.attributes.new('_environment_world_normal','FLOAT_VECTOR','CORNER')
        for i,n in enumerate(mesh.corner_normals):attr.data[i].vector=(normal_matrix@n.vector).normalized()
        copies.append(ob)
    return copies,catalogue


def target_image(c,materials):
    n=c['size'];im=bpy.data.images.new('TEMP raw',n,n,alpha=True,float_buffer=True)
    im.colorspace_settings.name='Linear Rec.709';im.pixels.foreach_set(np.zeros(n*n*4,np.float32))
    for mat in materials:
        node=mat.node_tree.nodes.new('ShaderNodeTexImage');node.image=im;mat.node_tree.nodes.active=node
    return im


def bake(c,root,stage):
    s=setup(c);preflight(c)
    receivers=select(c,'receivers');contributors=select(c,'contributors'); lights=select(c,'bake_lights')
    for o in s.objects:
        if o.type=='MESH':o.hide_render=o not in receivers+contributors
        if o.type=='LIGHT':o.hide_render=o not in lights
    copies,catalogue=evaluate_receivers(c);save_json(stage/'islands.json',catalogue)
    for o in receivers:o.hide_render=True
    bpy.ops.object.select_all(action='DESELECT')
    for o in copies:o.select_set(True)
    bpy.context.view_layer.objects.active=copies[0];bpy.ops.object.join();proxy=bpy.context.object
    restore=proxy.matrix_world.to_3x3().transposed()
    wanted=[(restore@v.vector).normalized() for v in proxy.data.attributes['_environment_world_normal'].data]
    proxy.data.normals_split_custom_set(wanted)
    err=max(((a.vector-b).length for a,b in zip(proxy.data.corner_normals,wanted)),default=0)
    if err>.001:raise ValueError('Joined receiver normals changed: '+str(err))
    im=target_image(c,list(proxy.data.materials));proxy.data.uv_layers.active_index=proxy.data.uv_layers.find(c.get('uv','SimpleBake'))
    for mode in ('DIRECT','INDIRECT'):
        s.cycles.samples=c['samples'][mode]
        save_json(stage/'progress.json',{'phase':'baking','pass':mode,'samples':s.cycles.samples,'time':time.time()})
        bpy.ops.object.bake(type='DIFFUSE',pass_filter={mode},margin=0,use_clear=True,use_selected_to_active=False,uv_layer=c.get('uv','SimpleBake'))
        np.save(stage/(mode.lower()+'_raw.npy'),raw_pixels(im))
    # Metallic receiving cap: restore other objects' actual materials for every local bake.
    bpy.data.objects.remove(proxy,do_unlink=True)
    for o in receivers:o.hide_render=False
    caps=select(c,'metal_receivers')
    if not set(caps).issubset(receivers):raise ValueError('Metal receivers must be bake receivers')
    metadata=[]
    for index,o in enumerate(caps):
        original_mesh=o.data
        # Data-linked instances must keep their real materials during this receiver's bake.
        o.data=original_mesh.copy()
        originals=list(o.data.materials); clones=[]
        for mat in originals:
            clone=mat.copy();p=next(n for n in clone.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
            socket=p.inputs['Metallic']; minimum=clone.node_tree.nodes.new('ShaderNodeMath');minimum.operation='MINIMUM'
            minimum.inputs[1].default_value=c.get('metallic_cap',.8)
            if socket.is_linked:clone.node_tree.links.new(socket.links[0].from_socket,minimum.inputs[0])
            else:minimum.inputs[0].default_value=socket.default_value
            clone.node_tree.links.new(minimum.outputs[0],socket);clones.append(clone)
        o.data.materials.clear()
        for m in clones:o.data.materials.append(m)
        im=target_image(c,clones)
        bpy.ops.object.select_all(action='DESELECT');o.hide_set(False);o.select_set(True);bpy.context.view_layer.objects.active=o
        for mode in ('DIRECT','INDIRECT'):
            s.cycles.samples=c['samples'][mode]
            save_json(stage/'progress.json',{'phase':'metal_cap','object':o.name,'completed':index,'total':len(caps),'pass':mode})
            bpy.ops.object.bake(type='DIFFUSE',pass_filter={mode},margin=0,use_clear=True,use_selected_to_active=False,uv_layer=c.get('uv','SimpleBake'))
            a=raw_pixels(im);path=stage/f'metal_{index}_{mode.lower()}.npz';np.savez_compressed(path,pixels=a)
            metadata.append({'object':o.name,'pass':mode.lower(),'file':path.name})
        o.data.materials.clear()
        for m in originals:o.data.materials.append(m)
        for m in clones:bpy.data.materials.remove(m)
        bpy.data.images.remove(im)
        temporary_mesh=o.data;o.data=original_mesh;bpy.data.meshes.remove(temporary_mesh)
    save_json(stage/'metal_patches.json',metadata)
    return {'joined_normal_max_error':err,'island_count':len(catalogue),'metal_receiver_count':len(caps),'samples':c['samples']}


def clone_tree(tree):
    result=tree.copy()
    for n in result.nodes:
        if n.type=='GROUP' and n.node_tree:n.node_tree=clone_tree(n.node_tree)
    return result


def flatten(c,root,stage):
    s=setup(c);original=bpy.data.materials[c['preview_material']];mat=original.copy()
    save_json(stage/'appearance.json',{'schema_version':1,'preview_sha256':preview_identity(original)})
    for n in mat.node_tree.nodes:
        if n.type=='GROUP':n.node_tree=clone_tree(n.node_tree)
    count=0
    def whiten(tree):
        nonlocal count
        for n in list(tree.nodes):
            if n.type=='GROUP':whiten(n.node_tree)
            if n.type=='TEX_IMAGE' and n.image and Path(bpy.path.abspath(n.image.filepath)).resolve() in (binding(c,'colour'),(root/'source-assets'/c['images']['colour']).resolve()):
                white=tree.nodes.new('ShaderNodeRGB');white.outputs[0].default_value=(1,1,1,1)
                for link in list(n.outputs['Color'].links):tree.links.new(white.outputs[0],link.to_socket)
                count+=1
    whiten(mat.node_tree)
    if not count:raise ValueError('Configured colour image was not found in the preview graph')
    mesh=bpy.data.meshes.new('TEMP identity atlas');mesh.from_pydata([(0,0,0),(1,0,0),(1,1,0),(0,1,0)],[],[(0,1,2,3)])
    ob=bpy.data.objects.new('TEMP identity atlas',mesh);s.collection.objects.link(ob);mesh.materials.append(mat)
    for name in ('UVMap',c.get('uv','SimpleBake')):
        uv=mesh.uv_layers.new(name=name)
        for d,co in zip(uv.data,[(0,0),(1,0),(1,1),(0,1)]):d.uv=co
    bpy.ops.object.select_all(action='DESELECT');ob.select_set(True);bpy.context.view_layer.objects.active=ob
    im=target_image(c,[mat]);s.cycles.samples=1
    bpy.ops.object.bake(type='EMIT',margin=0,use_clear=True,uv_layer=c.get('uv','SimpleBake'))
    np.save(stage/'evaluated.npy',raw_pixels(im))
    return {'colour_samplers_replaced':count,'method':'native saved shader EMIT evaluation','samples':1}


def reuse_check(c,root):
    import OpenImageIO as oiio
    copies,current=evaluate_receivers(c)
    saved=json.loads((root/'reuse/islands.json').read_text())
    labels=np.load(root/'reuse/labels.npy',allow_pickle=False)
    from reuse import validate_catalogue
    validate_catalogue(saved,current)
    for name in ('direct_final.exr','indirect_final.exr','island_ids.exr'):
        pixels=oiio.ImageBuf(str(root/'masters'/name)).get_pixels(oiio.FLOAT)
        if pixels is None or pixels.shape!=(c['size'],c['size'],4) or not np.isfinite(pixels).all():
            raise ValueError('Invalid reused master: '+name)
        if name=='island_ids.exr':np.save(root/'reuse-check/island_ids.npy',pixels)
    return {'uv_catalogue_matches':True,
            'sampled_islands':int(len(np.unique(labels[labels>0]))),'physical_bake_performed':False,
            'note':'Caller must verify unchanged physical source/lighting against bake provenance.'}


def capture(c,root,stage,thumbnail=False):
    s=setup(c);receivers=select(c,'receivers');glass=select(c,'glass')
    spec=c['thumbnail' if thumbnail else 'capture']
    mode=spec.get('mode','baked')
    if mode not in ('baked','physical'):raise ValueError('Capture mode must be baked or physical')
    if mode=='baked':
        verify_combination(bpy.data.materials[c['preview_material']],root/'masters/combined.exr',root/'masters/combined.provenance.json')
    visible=receivers+(glass if thumbnail else [])
    if mode=='physical':visible+=select(c,'contributors')
    for o in s.objects:
        if o.type=='MESH':o.hide_render=o not in visible
        if o.type=='LIGHT':o.hide_render=(mode=='baked' or o not in select(c,'bake_lights'))
    if mode=='baked':
        for o in receivers:
            for i in range(len(o.data.materials)):o.data.materials[i]=bpy.data.materials[c['preview_material']]
    node=world_tools.find_controls(s.world)
    output=next(n for n in s.world.node_tree.nodes if n.type=='OUTPUT_WORLD' and n.is_active_output)
    branch=spec.get('world_output','Visible Sky' if thumbnail else 'Lighting')
    if branch not in ('Lighting','Visible Sky'):raise ValueError('Invalid World branch')
    s.world.node_tree.links.new(node.outputs[branch],output.inputs['Surface'])
    data=bpy.data.cameras.new('TEMP environment camera');camera=bpy.data.objects.new(data.name,data);s.collection.objects.link(camera);s.camera=camera
    camera.location=spec['position']
    if thumbnail:
        camera.rotation_euler=(Vector(spec['target'])-camera.location).to_track_quat('-Z','Y').to_euler();data.lens=spec.get('lens',24)
    else:
        camera.rotation_euler=(math.pi/2,0,0);data.type='PANO';data.panorama_type='EQUIRECTANGULAR'
    size=spec.get('size',512 if thumbnail else 4096);s.render.resolution_x=size;s.render.resolution_y=size if thumbnail else size//2
    s.render.resolution_percentage=100;s.render.film_transparent=False;s.cycles.samples=spec.get('samples',64 if thumbnail else 32)
    s.render.use_border=False
    s.render.image_settings.file_format='JPEG' if thumbnail else 'OPEN_EXR'
    s.render.image_settings.color_mode='RGB';s.render.image_settings.color_depth='8' if thumbnail else '32'
    if thumbnail:s.render.image_settings.quality=90
    else:s.render.image_settings.exr_codec='ZIP'
    path=stage/('thumbnail.jpg' if thumbnail else 'capture.exr');s.render.filepath=str(path)
    rays=[]
    deps=bpy.context.evaluated_depsgraph_get()
    for direction in ((1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)):
        hit,loc,normal,index,obj,matrix=s.ray_cast(deps,camera.location,Vector(direction),distance=100)
        rays.append({'direction':direction,'object':obj.name if hit else None,'distance':float((loc-camera.location).length) if hit else None})
    bpy.ops.render.render(write_still=True)
    import OpenImageIO as oiio
    pixels=oiio.ImageBuf(str(path)).get_pixels(oiio.FLOAT)
    if not np.isfinite(pixels).all():raise ValueError('Capture contains non-finite pixels')
    return {'mode':mode,'world_output':branch,'position':list(camera.location),'rotation':list(camera.rotation_euler),
        'samples':s.cycles.samples,'dimensions':[s.render.resolution_x,s.render.resolution_y],'rays':rays,
        'rgb_min':float(pixels[:,:,:3].min()),'rgb_max':float(pixels[:,:,:3].max()),'rgb_mean':float(pixels[:,:,:3].mean())}


def main():
    if not bpy.app.background:raise RuntimeError('Use the saved-scene CLI; do not run worker in a live session')
    args=sys.argv[sys.argv.index('--')+1:];config,command,rootpath,stagepath=args[:4]
    c=load_config(config);root=Path(rootpath);stage=Path(stagepath);c['_candidate']=str(root)
    if Path(bpy.data.filepath).resolve()!=Path(c['_source']):raise ValueError('Wrong source scene opened')
    source_hash=sha(c['_source']);relink(c,root)
    if command=='preflight':result=preflight(c)
    elif command=='reuse-check':result=reuse_check(c,root)
    elif command=='bake':result=bake(c,root,stage)
    elif command=='combine':result=flatten(c,root,stage)
    elif command in ('capture','thumbnail'):result=capture(c,root,stage,command=='thumbnail')
    elif command=='env':
        from babylon_js.env_export import export_environment
        result=export_environment(bpy.context,{},stage,stage,
            dict(c.get('env',{}),converter=args[4],image=str(root/'capture/capture.exr')))
    elif command=='snapshot':
        for o in bpy.data.objects:
            if o.get('bjs_lightmap_image'):o['bjs_lightmap_image']='//../masters/combined.exr'
        bpy.context.scene.world['bjs_environment_image']='//../capture/capture.exr'
        for im in bpy.data.images:
            if im.source=='FILE' and im.filepath:im.filepath=bpy.path.relpath(bpy.path.abspath(im.filepath),start=str(stage))
        bpy.ops.wm.save_as_mainfile(filepath=str(stage/'restored.blend'),relative_remap=False)
        result={'file':'restored.blend','accepted':False,'source_materials_preserved':True}
    elif command=='validate':
        import OpenImageIO as oiio
        rows=[]
        for filename in ('direct_final.exr','indirect_final.exr','island_ids.exr','combined.exr'):
            pixels=oiio.ImageBuf(str(root/'masters'/filename)).get_pixels(oiio.FLOAT)
            if pixels is None or pixels.shape!=(c['size'],c['size'],4) or not np.isfinite(pixels).all():
                raise ValueError('Invalid regenerated image: '+filename)
            rows.append({'file':filename,'shape':list(pixels.shape),'minimum':float(pixels.min()),'maximum':float(pixels.max())})
        result={'images':rows,'quality':c.get('quality','final'),'accepted':False,
                'note':'Regenerated candidates; visual review is required before promotion.'}
    else:raise ValueError('Unknown worker command')
    if sha(c['_source'])!=source_hash:raise RuntimeError('Authoring file changed during processing')
    result.update(status='passed',source_sha256=source_hash,scene_unchanged_on_disk=True)
    save_json(stage/'result.json',result)
    print('ENVIRONMENT_STAGE_PASSED '+command,flush=True)


if __name__=='__main__':main()
