"""Capture a horizontal, neutral relative irradiance field in background Blender.

Run: blender -b PHYSICAL.blend --python spatial_capture.py -- --config scene.json
This helper deliberately rejects interactive invocation and never saves the input.
"""
import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE))


def bilinear(array, position, bounds):
    """Top-left arrays have +BlenderY at their top edge."""
    x, y = position[:2]
    if not bounds[0] <= x <= bounds[1] or not bounds[2] <= y <= bounds[3]:
        raise ValueError('Reference point outside field')
    h, w = array.shape[:2]
    px = (x-bounds[0])/(bounds[1]-bounds[0])*w-.5
    py = (bounds[3]-y)/(bounds[3]-bounds[2])*h-.5
    px = max(0., min(w-1., px)); py = max(0., min(h-1., py))
    ix, iy = int(px), int(py); fx, fy = px-ix, py-iy
    jx, jy = min(w-1, ix+1), min(h-1, iy+1)
    return (1-fy)*((1-fx)*array[iy, ix]+fx*array[iy, jx])+fy*((1-fx)*array[jy, ix]+fx*array[jy, jx])


def normalize(array, position, bounds):
    import numpy as np
    if not np.isfinite(array).all() or np.any(array[..., :3] < 0):
        raise ValueError('Field must be finite nonnegative linear RGB')
    sampled = bilinear(array[..., :3], position, bounds)
    scalar = float(np.dot(sampled, (.2126, .7152, .0722)))
    if not math.isfinite(scalar) or scalar <= 1e-12:
        raise ValueError('Reference point has zero or nonfinite luminance')
    result = array.copy(); result[..., :3] /= scalar; result[..., 3] = 1
    if np.any(result[..., :3] > 255):
        raise ValueError('Normalized field exceeds RGBD representable range; choose a brighter reference')
    return result, scalar, sampled.tolist()


def pass_pixels(path, name, layer=None):
    import OpenImageIO as oiio
    import numpy as np
    aliases={'DiffDir':('Diffuse Direct','DiffDir'),'DiffInd':('Diffuse Indirect','DiffInd'),'Combined':('Combined',)}[name]
    reader=oiio.ImageInput.open(str(path))
    if reader is None:raise ValueError('Cannot open render passes: '+str(path))
    matches=[];layout=[]
    try:
        index=0
        while reader.seek_subimage(index,0):
            channels=list(reader.spec().channelnames);layout.append(dict(subimage=index,channels=channels))
            for alias in aliases:
                names=[next((i for i,ch in enumerate(channels) if (ch.endswith('.'+alias+'.'+component) or ch==alias+'.'+component) and (layer is None or ch.startswith(layer+'.'))),None) for component in 'RGB']
                if all(i is not None for i in names):matches.append((index,names))
            index+=1
    finally:reader.close()
    if len(matches)!=1:raise ValueError('Missing or ambiguous EXR render pass '+name+': '+repr(layout))
    index,names=matches[0]
    raw=oiio.ImageBuf(str(path),index,0).get_pixels(oiio.FLOAT)
    rgb=raw[...,names]
    if not np.isfinite(rgb).all():raise ValueError('Non-finite render pass '+name)
    # Fancy channel indexing may produce channel-major strides; OIIO writers
    # require the interleaved C-contiguous RGBA layout used by native masters.
    return np.ascontiguousarray(np.concatenate((rgb, np.ones((*rgb.shape[:2],1),np.float32)), axis=-1))


def set_exr_format(scene, multilayer=False):
    """Blender5.2 filters the dynamic file format enum by media_type first."""
    settings=scene.render.image_settings
    if hasattr(settings,'media_type'):
        kinds=[item.identifier for item in settings.bl_rna.properties['media_type'].enum_items]
        requested='MULTI_LAYER_IMAGE' if multilayer else 'IMAGE'
        if requested not in kinds:raise ValueError('Required EXR media type unavailable: '+requested)
        settings.media_type=requested
    format='OPEN_EXR_MULTILAYER' if multilayer else 'OPEN_EXR'
    kinds=[item.identifier for item in settings.bl_rna.properties['file_format'].enum_items]
    if format not in kinds:raise ValueError('Required EXR format unavailable: '+format)
    settings.file_format=format;settings.color_depth='32';settings.exr_codec='ZIP'


def verify_gpu(scene, config):
    """Confirm the actual requested device and disable every CPU/other GPU."""
    import bpy
    prefs=bpy.context.preferences.addons['cycles'].preferences
    requested=config.get('device','OPTIX');name=config.get('device_name','')
    if requested!='OPTIX' or prefs.compute_device_type!='OPTIX' or scene.cycles.device!='GPU':
        raise ValueError('Spatial lighting capture requires explicit OPTIX GPU execution')
    for device in prefs.devices:device.use=device.type==requested and name.casefold() in device.name.casefold()
    enabled=[dict(name=d.name,type=d.type) for d in prefs.devices if d.use]
    if not enabled:raise ValueError('Requested OPTIX device not found: '+name)
    if any(d['type']=='CPU' for d in enabled):raise ValueError('Spatial capture CPU device must remain disabled')
    return enabled


def denoise_hdr(source, output, config=None):
    """Reuse native HDR compositor Denoise without panorama wrapping or cropping."""
    import bpy
    import numpy as np
    from babylon_js.agx_lightmap import temporary_scene, read_exr
    from worker import write_exr
    original=read_exr(source)
    h,w=original.shape[:2]
    with temporary_scene() as scene:
        scene.cycles.device='GPU';verify_gpu(scene,config or {})
        image=bpy.data.images.load(str(source),check_existing=False);image.colorspace_settings.name='Linear Rec.709'
        group=bpy.data.node_groups.new('SOURCE_ONLY planar HDR denoise','CompositorNodeTree')
        group.interface.new_socket(name='Image',in_out='OUTPUT',socket_type='NodeSocketColor')
        node=group.nodes.new('CompositorNodeImage');node.image=image
        denoise=group.nodes.new('CompositorNodeDenoise');denoise.inputs['HDR'].default_value=True
        out=group.nodes.new('NodeGroupOutput');group.links.new(node.outputs['Image'],denoise.inputs['Image']);group.links.new(denoise.outputs['Image'],out.inputs['Image'])
        scene.compositing_node_group=group
        camera=bpy.data.cameras.new('SOURCE_ONLY denoise evaluation camera');cam=bpy.data.objects.new(camera.name,camera);scene.collection.objects.link(cam);scene.camera=cam
        scene.render.resolution_x=w;scene.render.resolution_y=h;scene.render.resolution_percentage=100
        set_exr_format(scene);scene.render.image_settings.color_mode='RGBA';scene.render.filepath=str(output)
        bpy.ops.render.render(write_still=True)
    result=read_exr(output)
    if result.shape[:2]!=(h,w) or result.shape[-1] not in (3,4):raise ValueError('Denoising changed field dimensions')
    if result.shape[-1]==3:result=np.concatenate((result,np.ones((h,w,1),np.float32)),axis=-1)
    result=np.ascontiguousarray(result,dtype=np.float32);result[...,3]=1
    write_exr(output,result)
    return result


def denoise_proof(source_dir, output):
    import bpy
    from worker import setup
    from common import sha,save_json
    if not bpy.app.background:raise ValueError('Denoise proof requires background Blender')
    output.mkdir(parents=True,exist_ok=False)
    scene=setup(dict(device='OPTIX'));devices=verify_gpu(scene,{})
    records={}
    for name in ('direct','indirect'):
        source=source_dir/(name+'.exr');target=output/(name+'_denoised.exr')
        before=sha(source);array=denoise_hdr(source,target)
        if sha(source)!=before:raise ValueError('Denoising modified raw pass')
        records[name]=dict(source=str(source),sourceSha256=before,output=str(target),outputSha256=sha(target),dimensions=[array.shape[1],array.shape[0]])
    result=dict(status='passed',method='native HDR compositor Denoise, image-only, no planar wrap',enabledDevices=devices,passes=records,aoProcessed=False)
    save_json(output/'denoise_proof.json',result);print(json.dumps(result),flush=True);return result


def clone_preview(path, name):
    import bpy
    with bpy.data.libraries.load(str(path), link=False) as (src, dst):
        if name not in src.materials:
            raise ValueError('Saved controls material not found')
        dst.materials = [name]
    mat = dst.materials[0]
    def clone(tree):
        tree = tree.copy()
        for node in tree.nodes:
            if node.type == 'GROUP' and node.node_tree: node.node_tree = clone(node.node_tree)
        return tree
    # Imported graphs may share names with physical scene; isolate before binding.
    mat = mat.copy()
    for node in mat.node_tree.nodes:
        if node.type == 'GROUP': node.node_tree = clone(node.node_tree)
    return mat


def bind_field_preview(mat, images, controls):
    import bpy
    from babylon_js.lighting_controls import update_preview_resolution
    count = {}
    instances = [n for n in mat.node_tree.nodes if n.type=='GROUP' and n.get('bjs_lighting_controls') == 1]
    if len(instances) != 1: raise ValueError('Expected one saved managed controls group')
    instance = instances[0]
    actual = {}
    for key, expected in controls.items():
        socket = instance.inputs.get(key)
        if socket is None or socket.is_linked or abs(socket.default_value-expected)>1e-5:
            raise ValueError('Saved source controls mismatch: '+key)
        actual[key] = float(socket.default_value)
    def visit(tree):
        for node in tree.nodes:
            if node.type=='GROUP': visit(node.node_tree)
            role = node.get('bjs_preview_image_role')
            if node.type=='TEX_IMAGE' and role in images:
                node.image = images[role]; count[role] = count.get(role,0)+1
            if node.get('bjs_ao_available'): node.outputs[0].default_value = float('ao' in images)
    visit(mat.node_tree)
    if any(not count.get(role) for role in ('direct','indirect','island_ids','colour')):
        raise ValueError('Incomplete saved managed preview roles')
    update_preview_resolution(mat)
    return actual


def covered_global_minmax(array):
    """One common per-pixel RGB factor, then one global RGB peak; opaque master."""
    import numpy as np
    raw=np.asarray(array,dtype=np.float64)
    if raw.ndim!=3 or raw.shape[2]!=4 or not np.isfinite(raw).all() or np.any(raw[...,:3]<0):
        raise ValueError('Minmax requires finite nonnegative linear RGBA')
    mask=raw[...,3]>.999
    if not np.any(mask):raise ValueError('No fully covered surface pixels')
    y=raw[...,:3]@np.array([.2126,.7152,.0722]);black=float(y[mask].min())
    factor=np.maximum(y-black,0)/np.maximum(y,1e-12)
    adjusted=raw[...,:3]*factor[...,None];adjusted[~mask]=0
    peak=float(adjusted[mask].max())
    if not math.isfinite(peak) or peak<=0:raise ValueError('Covered minmax has zero/nonfinite peak')
    result=np.ones(raw.shape,np.float32);result[...,:3]=adjusted/peak
    return np.ascontiguousarray(result),dict(method='covered-global-minmax-rec709',blackLuminance=black,globalRgbPeak=peak,coverageThreshold=.999,uncoveredInside='black'),mask


def capture_surface_direct(config_path):
    """Project the accepted aggregate DIRECT bake on authored surfaces, then bound it."""
    import bpy
    from mathutils import Vector
    import numpy as np
    from worker import setup,select,write_exr
    from common import sha,save_json
    from babylon_js import environment_controls
    from babylon_js.agx_lightmap import read_exr
    from babylon_js.spatial_lighting import capture_input_identity,direct_surface_control
    from babylon_js.ktx_export import _write_png
    if not bpy.app.background:raise ValueError('Surface direct capture requires disposable background Blender')
    c=json.loads(config_path.read_text(encoding='utf-8-sig'));base=config_path.parent
    source=Path(bpy.data.filepath).resolve()
    if sha(source)!=c['source_sha256']:raise ValueError('Saved surface source bytes changed')
    def path(key):return (base/c[key]).resolve()
    recipe_path=path('scope_recipe');direct_path=path('direct_master');proof_path=path('bake_proof');preflight_path=path('bake_preflight')
    for file,key in ((recipe_path,'scope_recipe_sha256'),(direct_path,'direct_master_sha256'),(proof_path,'bake_proof_sha256'),(preflight_path,'bake_preflight_sha256')):
        if sha(file)!=c[key]:raise ValueError('Accepted direct capture input changed: '+key)
    recipe=json.loads(recipe_path.read_text(encoding='utf-8-sig'));proof=json.loads(proof_path.read_text(encoding='utf-8-sig'));preflight=json.loads(preflight_path.read_text(encoding='utf-8-sig'))
    rows=[r for r in proof if r.get('pass_name')=='DIRECT'];lights=sorted(recipe['bake_lights']['names'])
    if not rows or any(sorted(r['enabled_lights'])!=lights or 'DIRECT' not in r['pass_filter'] for r in rows):raise ValueError('Aggregate DIRECT contribution proof mismatch')
    strength=direct_surface_control(bpy.context.scene,c['direct_master_sha256'])
    if abs(strength-c['direct_strength'])>1e-5:raise ValueError('Saved Direct Strength changed')
    identity=capture_input_identity(recipe)
    scene=bpy.context.scene
    world_values=environment_controls.settings(environment_controls.find_controls(scene.world));world_image=Path(world_values['image'])
    for key in ('Rotation','Lighting Strength','Sky Lift','Lift Rolloff'):
        if abs(world_values[key]-preflight['world_controls'][key])>1e-5:raise ValueError('World direct bake correspondence changed')
    output=path('output_dir');output.mkdir(parents=True,exist_ok=False)
    receivers=select(recipe,'receivers');hidden=set(c['hidden_overhead']);visible=[o for o in receivers if o.name not in hidden];uv_name=recipe['uv']
    if not hidden.issubset({o.name for o in receivers}) or not visible:raise ValueError('Invalid authored receiver/overhead scope')
    if any(len(o.data.uv_layers)<2 or o.data.uv_layers[1].name!=uv_name for o in receivers):raise ValueError('UV2 ordinal/name mismatch')
    direct=np.ascontiguousarray(read_exr(direct_path),dtype=np.float32)
    if direct.shape!=(recipe['size'],recipe['size'],4) or not np.isfinite(direct).all() or np.any(direct[...,:3]<0):raise ValueError('Invalid accepted DIRECT master')
    direct[...,:3]*=strength;direct[...,3]=1
    atlas=output/'surface_direct_atlas.exr';write_exr(atlas,direct)
    image=bpy.data.images.load(str(atlas),check_existing=False);image.colorspace_settings.name='Linear Rec.709'
    material=bpy.data.materials.new('SOURCE_ONLY Authored Surface DIRECT EMIT');material.use_nodes=True;material.node_tree.nodes.clear()
    n=material.node_tree.nodes;l=material.node_tree.links
    uv=n.new('ShaderNodeUVMap');uv.uv_map=uv_name
    texture=n.new('ShaderNodeTexImage');texture.image=image;texture.interpolation='Linear';texture.extension='EXTEND'
    emission=n.new('ShaderNodeEmission');emission.inputs['Strength'].default_value=1;out=n.new('ShaderNodeOutputMaterial')
    l.new(uv.outputs['UV'],texture.inputs['Vector']);l.new(texture.outputs['Color'],emission.inputs['Color']);l.new(emission.outputs[0],out.inputs['Surface'])
    for obj in scene.objects:
        if obj.type=='MESH':obj.hide_render=obj not in visible
        if obj.type=='LIGHT':obj.hide_render=True
    for obj in visible:
        obj.visible_camera=True
        for i in range(len(obj.data.materials)):obj.data.materials[i]=material
    world=bpy.data.worlds.new('SOURCE_ONLY Surface DIRECT Black World');world.use_nodes=True
    background=next(n for n in world.node_tree.nodes if n.type=='BACKGROUND');background.inputs['Color'].default_value=(0,0,0,1);background.inputs['Strength'].default_value=0;scene.world=world
    scene=setup(dict(recipe,device=c['device']));scene.cycles.samples=c['samples'];devices=verify_gpu(scene,c)
    size=c['size']
    if type(size)is not int or size!=512:raise ValueError('Authored surface delivery requires512 square')
    scene.render.resolution_x=size;scene.render.resolution_y=size;scene.render.resolution_percentage=100
    scene.render.use_border=False;scene.render.film_transparent=True;set_exr_format(scene);scene.render.image_settings.color_mode='RGBA'
    bounds=c['bounds_xy'];xmin,xmax,ymin,ymax=bounds
    if len(bounds)!=4 or not all(math.isfinite(v) for v in bounds) or xmax<=xmin or ymax<=ymin or abs(xmax-xmin-(ymax-ymin))>1e-6:raise ValueError('Surface capture needs finite square bounds')
    highest=max((o.matrix_world@Vector(v)).z for o in visible for v in o.bound_box)
    camera=bpy.data.cameras.new('SOURCE_ONLY Surface DIRECT Camera');cam=bpy.data.objects.new(camera.name,camera);scene.collection.objects.link(cam)
    cam.location=((xmin+xmax)/2,(ymin+ymax)/2,highest+1);cam.rotation_euler=(0,0,0);camera.type='ORTHO';camera.ortho_scale=xmax-xmin;camera.clip_start=.01;camera.clip_end=max(100,highest+10);scene.camera=cam
    raw_path=output/'surface_direct_raw.exr';scene.render.filepath=str(raw_path);bpy.ops.render.render(write_still=True)
    raw=np.ascontiguousarray(read_exr(raw_path),dtype=np.float32)
    if raw.shape!=(size,size,4):raise ValueError('Unexpected surface projection dimensions')
    result,norm,mask=covered_global_minmax(raw)
    master=output/'surface_direct_minmax.exr';write_exr(master,result)
    coverage=np.ones(raw.shape,np.float32);coverage[...,:3]=raw[...,3:4]
    coverage_path=output/'surface_coverage.exr';write_exr(coverage_path,coverage)
    rgb=result[...,:3];display=np.where(rgb<=.0031308,rgb*12.92,1.055*np.maximum(rgb,0)**(1/2.4)-.055)
    _write_png(output/'surface_direct_minmax_srgb.png',np.rint(np.clip(display,0,1)*255).astype(np.uint8))
    _write_png(output/'surface_coverage.png',np.repeat(np.rint(np.clip(raw[...,3:4],0,1)*255).astype(np.uint8),3,axis=-1))
    if sha(source)!=c['source_sha256'] or sha(direct_path)!=c['direct_master_sha256']:raise ValueError('Saved capture inputs changed during generation')
    record=dict(version=1,semantics='irradiance-multiplier',captureMethod='authored-surface-direct',master=master.name,masterSha256=sha(master),dimensions=[size,size],bounds=dict(xMin=xmin,xMax=xmax,zMin=ymin,zMax=ymax),orientation='blender-xy-top-left-positive-y;babylon-xz-v-positive-z',normalization=norm,source=dict(sourceScene=str(source),sourceSceneSha256=sha(source),scopeRecipe=str(recipe_path),scopeRecipeSha256=sha(recipe_path),directMaster=str(direct_path),directMasterSha256=sha(direct_path),directStrength=strength,bakeProof=str(proof_path),bakeProofSha256=sha(proof_path),bakePreflight=str(preflight_path),bakePreflightSha256=sha(preflight_path),geometrySha256=identity['geometrySha256'],lightsSha256=identity['lightsSha256'],worldControls=world_values,worldImageSha256=sha(world_image)),capture=dict(receiverCount=len(receivers),visibleReceiverCount=len(visible),hiddenOverhead=sorted(hidden),uv2=uv_name,samples=scene.cycles.samples,enabledDevices=devices,blackWorld=True,emissionOnly=True,receiverPlaneCreated=False,indirectContribution=0,shadowLift=0,aoApplied=False,colourFinishApplied=False,agxCompensationApplied=False,recordedBakeLights=lights,perLightSeparationAvailable=False,filterByExportedLightIdentity=False,cameraPosition=list(cam.location)),rawMaster=raw_path.name,rawSha256=sha(raw_path),coverage=dict(file=coverage_path.name,sha256=sha(coverage_path),threshold=.999,coveredPixels=int(mask.sum()),coveredPercent=float(mask.mean()*100),uncoveredInside='black',maskIsRgbdDivisor=False),masterRgbRange=[float(result[...,:3].min()),float(result[...,:3].max())])
    save_json(output/'spatial_receipt.json',record)
    print(json.dumps(dict(status='passed',receipt=str(output/'spatial_receipt.json'),master=str(master),coveragePercent=record['coverage']['coveredPercent'],normalization=norm)),flush=True)
    return record


def capture(config_path):
    import bpy
    import numpy as np
    from worker import setup, select, write_exr
    from common import sha, save_json
    from babylon_js import environment_controls
    from babylon_js.agx_lightmap import combine_saved_controls
    from babylon_js.spatial_lighting import artist_graph_sha256, capture_input_identity
    if not bpy.app.background: raise ValueError('Spatial capture requires a disposable background Blender process')
    c = json.loads(config_path.read_text(encoding='utf-8-sig')); base = config_path.parent
    if c.get('version') != 1: raise ValueError('Spatial capture config version must be 1')
    if c.get('capture_method')=='authored-surface-direct':return capture_surface_direct(config_path)
    source = Path(bpy.data.filepath).resolve()
    if sha(source) != c['source_sha256']: raise ValueError('Physical source snapshot SHA256 mismatch')
    recipe_path = (base/c['scope_recipe']).resolve()
    if sha(recipe_path) != c['scope_recipe_sha256']: raise ValueError('Physical scope recipe SHA256 mismatch')
    recipe = json.loads(recipe_path.read_text(encoding='utf-8-sig'))
    size = c.get('size',128)
    if type(size) is not int or size not in (128,256,512): raise ValueError('Field size must be 128,256 or512')
    bounds = c['bounds_xy']; xmin,xmax,ymin,ymax = bounds
    if any(not math.isfinite(x) for x in bounds) or xmax<=xmin or ymax<=ymin or abs((xmax-xmin)-(ymax-ymin))>1e-6:
        raise ValueError('Capture requires square finite bounds')
    height = float(c['height']); reference=c['reference_position']
    if abs(reference[2]-height)>1e-6: raise ValueError('Reference height must match receiver height')
    output = (base/c['output_dir']).resolve()
    output.mkdir(parents=True, exist_ok=False)
    settings=dict(recipe,device=c.get('device','OPTIX'))
    controls_path=(base/c['controls_scene']).resolve()
    if sha(controls_path)!=c['controls_scene_sha256']: raise ValueError('Saved appearance source SHA256 mismatch')
    s=setup(settings); s.cycles.samples=int(c.get('samples',128))
    enabled_devices=verify_gpu(s,c)
    s.render.resolution_x=size;s.render.resolution_y=size;s.render.resolution_percentage=100
    set_exr_format(s,multilayer=True)
    world=environment_controls.find_controls(s.world)
    if world is None: raise ValueError('Missing managed World Lighting controls')
    actual_world=environment_controls.settings(world)
    world_image=Path(actual_world['image'])
    if not world_image.is_file(): raise ValueError('Physical World image missing')
    world_image_sha256=sha(world_image)
    for key,value in c['world_controls'].items():
        if key not in actual_world or abs(actual_world[key]-value)>1e-5: raise ValueError('World control mismatch: '+key)
    world_output=next(n for n in s.world.node_tree.nodes if n.type=='OUTPUT_WORLD' and n.is_active_output)
    s.world.node_tree.links.new(world.outputs['Lighting'],world_output.inputs['Surface'])
    transport=select(recipe,'receivers')+select(recipe,'contributors')
    lights=select(recipe,'bake_lights')
    if not transport or not lights: raise ValueError('Empty physical capture scope')
    identity=capture_input_identity(recipe)
    if c.get('geometry_sha256') and identity['geometrySha256']!=c['geometry_sha256']:
        raise ValueError('Accepted physical geometry identity mismatch')
    source_images={}
    seen=set()
    def physical_images(tree):
        if not tree or tree.as_pointer() in seen:return
        seen.add(tree.as_pointer())
        for node in tree.nodes:
            if node.type=='GROUP':physical_images(node.node_tree)
            if node.type=='TEX_IMAGE' and node.image:
                path=Path(bpy.path.abspath(node.image.filepath))
                if not path.is_file():raise ValueError('Missing physical texture: '+str(path))
                source_images[str(path.resolve())]=sha(path)
    for obj in transport:
        for material in obj.data.materials:
            if material and material.use_nodes:physical_images(material.node_tree)
    before_geometry=[dict(name=o.name,matrix=[list(row) for row in o.matrix_world],materials=[m.name if m else None for m in o.data.materials]) for o in transport]
    for o in s.objects:
        if o.type=='MESH':
            o.hide_render=o not in transport
            o.visible_camera=False
        elif o.type=='LIGHT': o.hide_render=o not in lights
    helpers=bpy.data.collections.new('SOURCE_ONLY Spatial Lighting Capture');s.collection.children.link(helpers)
    mesh=bpy.data.meshes.new('SOURCE_ONLY neutral irradiance receiver')
    mesh.from_pydata([(xmin,ymin,height),(xmax,ymin,height),(xmax,ymax,height),(xmin,ymax,height)],[],[(0,1,2,3)])
    receiver=bpy.data.objects.new(mesh.name,mesh);helpers.objects.link(receiver)
    for attr in ('visible_shadow','visible_diffuse','visible_glossy','visible_transmission','visible_volume_scatter'):
        setattr(receiver,attr,False)
    receiver.visible_camera=True
    mat=bpy.data.materials.new('SOURCE_ONLY neutral WHITE diffuse');mat.use_nodes=True;mat.node_tree.nodes.clear()
    diffuse=mat.node_tree.nodes.new('ShaderNodeBsdfDiffuse');diffuse.inputs['Color'].default_value=(1,1,1,1);diffuse.inputs['Roughness'].default_value=0
    out=mat.node_tree.nodes.new('ShaderNodeOutputMaterial');mat.node_tree.links.new(diffuse.outputs[0],out.inputs['Surface']);mesh.materials.append(mat)
    camera=bpy.data.cameras.new('SOURCE_ONLY orthographic field');cam=bpy.data.objects.new(camera.name,camera);helpers.objects.link(cam)
    cam.location=((xmin+xmax)/2,(ymin+ymax)/2,height+2);cam.rotation_euler=(0,0,0);camera.type='ORTHO';camera.ortho_scale=xmax-xmin;camera.clip_start=.01;camera.clip_end=100;s.camera=cam
    layer=bpy.context.view_layer;layer.use_pass_diffuse_direct=True;layer.use_pass_diffuse_indirect=True
    raw=output/'field_passes.exr';s.render.filepath=str(raw);bpy.ops.render.render(write_still=True)
    direct=pass_pixels(raw,'DiffDir',layer.name);indirect=pass_pixels(raw,'DiffInd',layer.name)
    write_exr(output/'direct.exr',direct);write_exr(output/'indirect.exr',indirect)
    denoise_record=dict(enabled=bool(c.get('denoise',False)),method='native HDR compositor Denoise, image-only, no planar wrap',aoProcessed=False)
    if denoise_record['enabled']:
        direct=denoise_hdr(output/'direct.exr',output/'direct_denoised.exr',c)
        indirect=denoise_hdr(output/'indirect.exr',output/'indirect_denoised.exr',c)
        denoise_record['outputHashes']={name:sha(output/name) for name in ('direct_denoised.exr','indirect_denoised.exr')}
    denoise_record['rawHashes']={name:sha(output/name) for name in ('direct.exr','indirect.exr')}
    # AO is computed at this same virtual receiver, with physical geometry retained.
    ao=mat.node_tree.nodes.new('ShaderNodeAmbientOcclusion');ao.inputs['Color'].default_value=(1,1,1,1);ao.inputs['Distance'].default_value=float(c.get('ao_distance',.5));ao.samples=int(c.get('ao_samples',32))
    emission=mat.node_tree.nodes.new('ShaderNodeEmission');mat.node_tree.links.new(ao.outputs['AO'],emission.inputs['Color']);mat.node_tree.links.new(emission.outputs[0],out.inputs['Surface'])
    set_exr_format(s);s.render.image_settings.color_mode='RGBA';s.render.filepath=str(output/'ao.exr');s.cycles.samples=int(c.get('ao_render_samples',32));bpy.ops.render.render(write_still=True)
    from babylon_js.agx_lightmap import read_exr
    ao_pixels=read_exr(output/'ao.exr')
    if not np.isfinite(ao_pixels).all() or np.any(ao_pixels[...,:3]<0) or np.any(ao_pixels[...,:3]>1+1e-6): raise ValueError('Invalid receiver AO')
    controls_path=(base/c['controls_scene']).resolve()
    if sha(controls_path)!=c['controls_scene_sha256']: raise ValueError('Saved appearance source SHA256 mismatch')
    preview=clone_preview(controls_path,c['preview_material'])
    artist_graph_sha256_value=artist_graph_sha256(preview)
    def image(name,array):
        im=bpy.data.images.new('SOURCE_ONLY field '+name,size,size,alpha=True,float_buffer=True)
        im.colorspace_settings.name='Non-Color';im.pixels.foreach_set(array[::-1].astype(np.float32).ravel());return im
    white=np.ones((size,size,4),np.float32)
    roles={'direct':image('direct',direct),'indirect':image('indirect',indirect),'ao':image('ao',ao_pixels),'colour':image('colour',white),'island_ids':image('ids',white)}
    actual_controls=bind_field_preview(preview,roles,c['appearance_controls'])
    graded=combine_saved_controls(bpy.context,size,output/'graded.exr',material=preview)
    normalized,scalar,sampled=normalize(graded,reference,bounds)
    master=output/'spatial_irradiance.exr';write_exr(master,normalized)
    # Display previews are explicitly not processing masters.
    from babylon_js.ktx_export import _write_png
    rgb=normalized[...,:3]/(1+normalized[...,:3]);rgb=np.where(rgb<=.0031308,rgb*12.92,1.055*np.maximum(rgb,0)**(1/2.4)-.055)
    _write_png(output/'spatial_preview.png',np.rint(rgb*255).astype(np.uint8))
    luminance=normalized[...,:3]@np.array([.2126,.7152,.0722])
    heat=np.clip((np.log2(np.maximum(luminance,1e-6))+4)/8,0,1)
    heat_rgb=np.stack((np.clip(2*heat,0,1),1-np.abs(2*heat-1),np.clip(2*(1-heat),0,1)),axis=-1)
    _write_png(output/'spatial_luminance_heatmap.png',np.rint(heat_rgb*255).astype(np.uint8))
    record=dict(version=1,semantics='irradiance-multiplier',master=master.name,masterSha256=sha(master),dimensions=[size,size],bounds=dict(xMin=xmin,xMax=xmax,zMin=ymin,zMax=ymax),referenceHeight=height,orientation='blender-xy-top-left-positive-y;babylon-xz-v-positive-z',normalization=dict(method='reference-luminance-rec709',referencePosition=[reference[0],reference[2],reference[1]],measuredScalar=scalar,measuredRgb=sampled,neutralRgb=[1,1,1]),source=dict(physicalScene=str(source),physicalSceneSha256=sha(source),controlsScene=str(controls_path),controlsSceneSha256=sha(controls_path),scopeRecipeSha256=sha(recipe_path),worldControls=actual_world,appearanceControls=actual_controls,transport=before_geometry,lights=[o.name for o in lights]),capture=dict(method='neutral-white-Lambert-horizontal-proxy;relative-E-over-pi',samples=c.get('samples',128),device=settings['device'],receiverVisibility=dict(camera=True,shadow=False,diffuse=False,glossy=False,transmission=False,volumeScatter=False),geometryCameraVisible=False,aoDistance=c.get('ao_distance',.5),aoNodeSamples=c.get('ao_samples',32)))
    record['source']['artistGraphSha256']=artist_graph_sha256_value
    record['source']['worldImageSha256']=world_image_sha256
    record['source']['scopeRecipe']=str(recipe_path)
    record['source']['geometrySha256']=identity['geometrySha256']
    record['source']['lightsSha256']=identity['lightsSha256']
    record['source']['lightParameters']=identity['lights']
    record['source']['sourceImages']=source_images
    record['capture']['enabledDevices']=enabled_devices
    record['capture']['denoise']=denoise_record
    record['preview']=dict(color='per-channel Reinhard c/(1+c), IEC sRGB, exposure0',luminanceHeatmap='log2 luminance, fixed[-4,+4] stops relative to reference; blue(low), green(mid), red(high)',topEdge='+BlenderY / +BabylonZ')
    if sha(source)!=c['source_sha256'] or sha(controls_path)!=c['controls_scene_sha256'] or sha(world_image)!=world_image_sha256:
        raise ValueError('Saved capture source changed during generation')
    save_json(output/'spatial_receipt.json',record)
    print(json.dumps(dict(status='passed',receipt=str(output/'spatial_receipt.json'),master=str(master),referenceScalar=scalar,range=[float(normalized[...,:3].min()),float(normalized[...,:3].max())])),flush=True)
    return record


def native_fixture(output):
    """Small correctness scene: orientation, neutral receiver, invisible transport."""
    import bpy
    import numpy as np
    from worker import setup
    from common import save_json
    if not bpy.app.background:raise ValueError('Native fixture requires background Blender')
    output.mkdir(parents=True,exist_ok=False)
    fixture=bpy.data.scenes.new('SOURCE_ONLY spatial correctness fixture')
    bpy.context.window.scene=fixture
    settings=dict(device='OPTIX',diffuse_bounces=4,total_bounces=8)
    scene=setup(settings);scene.cycles.samples=64;scene.cycles.seed=19
    enabled_devices=verify_gpu(scene,settings)
    scene.render.resolution_x=64;scene.render.resolution_y=64;scene.render.resolution_percentage=100
    set_exr_format(scene,multilayer=True)
    world=bpy.data.worlds.new('SOURCE_ONLY black fixture World');world.use_nodes=True
    background=next(n for n in world.node_tree.nodes if n.type=='BACKGROUND');background.inputs['Color'].default_value=(0,0,0,1);background.inputs['Strength'].default_value=0;scene.world=world
    def quad(name,z):
        mesh=bpy.data.meshes.new(name);mesh.from_pydata([(-2,-2,z),(2,-2,z),(2,2,z),(-2,2,z)],[],[(0,1,2,3)])
        obj=bpy.data.objects.new(name,mesh);scene.collection.objects.link(obj);return obj
    def diffuse(name,color):
        mat=bpy.data.materials.new(name);mat.use_nodes=True;mat.node_tree.nodes.clear()
        node=mat.node_tree.nodes.new('ShaderNodeBsdfDiffuse');node.inputs['Color'].default_value=(*color,1)
        out=mat.node_tree.nodes.new('ShaderNodeOutputMaterial');mat.node_tree.links.new(node.outputs[0],out.inputs['Surface']);return mat,node
    floor=quad('SOURCE_ONLY fixture floor',0);floor_mat,floor_node=diffuse('SOURCE_ONLY fixture floor material',(.8,.8,.8));floor.data.materials.append(floor_mat)
    light=bpy.data.lights.new('SOURCE_ONLY positiveXY known light','POINT');light.energy=80;light.color=(1,1,1);light.shadow_soft_size=.05
    obj=bpy.data.objects.new(light.name,light);scene.collection.objects.link(obj);obj.location=(1,1,3)
    camera=bpy.data.cameras.new('SOURCE_ONLY fixture camera');camera.type='ORTHO';camera.ortho_scale=4
    cam=bpy.data.objects.new(camera.name,camera);scene.collection.objects.link(cam);cam.location=(0,0,4);cam.rotation_euler=(0,0,0);scene.camera=cam
    bpy.context.view_layer.use_pass_diffuse_direct=True;bpy.context.view_layer.use_pass_diffuse_indirect=True
    def render(name):
        path=output/(name+'.exr');scene.render.filepath=str(path);bpy.ops.render.render(write_still=True)
        return pass_pixels(path,'Combined'),pass_pixels(path,'DiffDir'),pass_pixels(path,'DiffInd')
    baseline,_,_=render('baseline_without_proxy')
    receiver=quad('SOURCE_ONLY fixture neutral receiver',1)
    for attr in ('visible_shadow','visible_diffuse','visible_glossy','visible_transmission','visible_volume_scatter','visible_camera'):setattr(receiver,attr,False)
    receiver_mat,receiver_node=diffuse('SOURCE_ONLY fixture receiver WHITE',(1,1,1));receiver.data.materials.append(receiver_mat)
    invisible,_,_=render('baseline_with_transport_invisible_proxy')
    unchanged=float(np.max(np.abs(baseline-invisible)))
    transport_scale=float(np.max(np.abs(baseline)));transport_tolerance=1e-6+1e-5*transport_scale
    if unchanged>transport_tolerance:raise ValueError('Virtual receiver changed room transport: '+str(unchanged))
    receiver.visible_camera=True;floor.visible_camera=False
    _,direct,_=render('neutral_receiver_field')
    floor_node.inputs['Color'].default_value=(.9,.02,.02,1)
    _,red_floor_direct,_=render('red_room_surface_neutral_receiver_field')
    no_albedo_leak=float(np.max(np.abs(direct-red_floor_direct)))
    direct_scale=float(np.max(np.abs(direct)));direct_tolerance=1e-6+1e-5*direct_scale
    if no_albedo_leak>direct_tolerance:raise ValueError('Direct field leaked room receiver albedo: '+str(no_albedo_leak))
    intensity=direct[...,:3]@np.array([.2126,.7152,.0722]);peak=np.unravel_index(int(np.argmax(intensity)),intensity.shape)
    if not peak[0]<32 or not peak[1]>32:raise ValueError('PositiveXY light orientation incorrect: '+str(peak))
    positive=bilinear(direct,[1,1,1],[-2,2,-2,2]);negative=bilinear(direct,[-1,-1,1],[-2,2,-2,2])
    if not float(positive[:3].mean())>float(negative[:3].mean()):raise ValueError('Fixture field orientation gradient failed')
    if tuple(receiver_node.inputs['Color'].default_value)!=(1.,1.,1.,1.):raise ValueError('Receiver is not neutral WHITE')
    result=dict(status='passed',transportProxyMaxError=unchanged,directAlbedoLeakMaxError=no_albedo_leak,peakTopLeftRowColumn=[int(x) for x in peak],positiveXY=list(map(float,positive[:3])),negativeXY=list(map(float,negative[:3])),receiverColor=list(receiver_node.inputs['Color'].default_value),receiverVisibility={attr:getattr(receiver,attr) for attr in ('visible_camera','visible_shadow','visible_diffuse','visible_glossy','visible_transmission','visible_volume_scatter')},inputSceneSaved=False)
    result['enabledDevices']=enabled_devices
    result['float32Tolerance']=dict(absolute=1e-6,relative=1e-5,transportScale=transport_scale,transportThreshold=transport_tolerance,directScale=direct_scale,directThreshold=direct_tolerance)
    save_json(output/'fixture_result.json',result);print(json.dumps(result),flush=True);return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser();group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--config',type=Path);group.add_argument('--fixture',type=Path)
    group.add_argument('--denoise-proof',type=Path);parser.add_argument('--denoise-output',type=Path)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    if args.config:capture(args.config.resolve())
    elif args.fixture:native_fixture(args.fixture.resolve())
    else:
        if args.denoise_output is None:parser.error('--denoise-proof requires --denoise-output')
        denoise_proof(args.denoise_proof.resolve(),args.denoise_output.resolve())
