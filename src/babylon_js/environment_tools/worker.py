"""Run only in a disposable background Blender process opened on a saved scene."""
import sys, json, math, time, hashlib
from pathlib import Path
import bpy
import numpy as np
from mathutils import Vector

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))
from common import load_config, sha, save_json, normalize_metal_diffuse
from babylon_js import environment_controls as world_tools
from babylon_js.lighting_controls import validate_preview_resolution
from image_binding import replace_from_disk
from appearance import preview_identity, verify_combination
from receiver_bake import capped_materials, joined_receivers


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


def check_preview_resolution(c):
    result=validate_preview_resolution(bpy.data.materials[c['preview_material']],(c['size'],c['size']))
    if result['status']=='unverified_custom_graph':
        print('Custom preview: source-pixel smoothing offsets need manual validation before changing source resolution')
    return result


def _managed_relink_plan(c, root):
    """Plan current-master bindings only for an explicitly managed preview."""
    from babylon_js.lighting_controls import _managed_graphs
    material=bpy.data.materials.get(c.get('preview_material',''))
    graphs=_managed_graphs(material) if material is not None else []
    if not graphs:return None
    response=bool(c.get('receiver_response',{}).get('enabled'))
    targets={name:Path(root)/'masters'/filename for name,filename in (
        ('direct','direct_response_processed.exr' if response else 'direct_final.exr'),
        ('indirect','indirect_response_processed.exr' if response else 'indirect_final.exr'),
        ('island_ids','island_ids.exr'))}
    # Geometry/bake/preflight can run before any lighting masters exist. An ID
    # master alone also precedes a response derivation; never bind a partial set.
    if not any(targets[name].is_file() for name in ('direct','indirect')):return None
    missing=[name for name,path in targets.items() if not path.is_file()]
    if missing:raise ValueError('Incomplete managed lighting masters: '+', '.join(missing))
    validate_preview_resolution(material,(c['size'],c['size']))
    rows=[(node,targets[node.get('bjs_preview_image_role')]) for tree in graphs for node in tree.nodes
          if node.type=='TEX_IMAGE' and node.get('bjs_preview_image_role') in targets]
    return material,rows


def _bind_managed_lighting(c, plan):
    """Retain interpretation/controls, without remapping unrelated image users."""
    if plan is None:return
    material,rows=plan;loaded={};bindings=[]
    for node,target in rows:
        old=node.image;key=(old.as_pointer(),str(target.resolve()))
        if key not in loaded:
            # The existing helper preserves image interpretation and properties.
            # Work on a private image copy so legacy/custom graphs sharing the
            # donor image are not redirected by this managed-role correction.
            fresh=replace_from_disk(old.copy(),target)
            if tuple(fresh.size)!=(c['size'],c['size']):
                raise ValueError('Managed current-master dimensions differ: '+str(target))
            if Path(bpy.path.abspath(fresh.filepath,library=fresh.library)).resolve()!=target.resolve():
                raise ValueError('Managed current-master path differs: '+str(target))
            loaded[key]=fresh
        bindings.append((node,loaded[key]))
    for node,image in bindings:node.image=image
    validate_preview_resolution(material,(c['size'],c['size']))


def relink(c, root):
    managed_plan=_managed_relink_plan(c,root)
    replacements = {}
    for path in c.get('source_images',[]):
        replacements[(Path(c['_root'])/path).resolve()]=root/'source-assets'/path
    for row in c.get('downloads', []):
        replacements[(Path(c['_root']) / row['path']).resolve()] = root/'sources'/Path(row['path']).name
    for name, filename in [('direct','direct_final.exr'), ('indirect','indirect_final.exr'),
                           ('island_ids','island_ids.exr'), ('combined','combined.exr')]:
        replacements[binding(c, name)] = root/'masters'/filename
    if c.get('receiver_response',{}).get('enabled'):
        for name in ('direct','indirect'):
            replacements[binding(c,name)] = root/'masters'/(name+'_response_processed.exr')
    for im in list(bpy.data.images):
        if im.source == 'FILE' and im.filepath:
            target = replacements.get(Path(bpy.path.abspath(im.filepath)).resolve())
            if target and target.is_file(): replace_from_disk(im,target)
    _bind_managed_lighting(c,managed_plan)
    if c.get('ao',{}).get('enabled') and (root/'masters/ao.exr').is_file():
        from babylon_js.lightmap_grading import upgrade
        image=bpy.data.images.load(str(root/'masters/ao.exr'),check_existing=True)
        image.colorspace_settings.name='Non-Color'
        upgrade(bpy.data.materials[c['preview_material']],image,c.get('uv','SimpleBake'))


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
    from physical_colour_adjustment import select_scene
    s=select_scene(c); s.render.engine='CYCLES'
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


def image_dependencies(c):
    """Conservative node-image closure; ignore historical data only by explicit recipe."""
    from common import image_dependency_scope
    scope = image_dependency_scope(c)
    scene = bpy.context.scene
    roots = {'scene': scene.name, 'objects': {}, 'materials': [],
             'world': scene.world.name if scene.world else None,
             'preview_material': c.get('preview_material')}
    if scope == 'all':
        images = list(bpy.data.images)
    else:
        # Reuse the installed audit's cycle-safe recursive collector. This scans
        # every node in participating trees, including disconnected image nodes.
        from babylon_js.workflow_tools.inspect_environment import _images_from_tree
        materials = set()
        for role in ('receivers', 'contributors', 'glass', 'visible_emitters'):
            objects = select(c, role)
            roots['objects'][role] = [o.name for o in objects]
            materials.update(slot.material for o in objects for slot in o.material_slots if slot.material)
        preview = bpy.data.materials.get(c.get('preview_material', ''))
        if preview:
            materials.add(preview)
        roots['materials'] = sorted(m.name for m in materials)
        images = set()
        for material in materials:
            images.update(_images_from_tree(material.node_tree))
        images.update(_images_from_tree(scene.world.node_tree if scene.world else None))
        images = sorted(images, key=lambda image: image.name)
    required = set(images)
    def row(image):
        return {'name': image.name, 'source': image.source,
                'path': str(Path(bpy.path.abspath(image.filepath)).resolve()) if image.filepath else '',
                'packed': bool(image.packed_file)}
    report = {'scope': scope, 'roots': roots,
        'required_images': [row(image) for image in sorted(images, key=lambda image: image.name)],
        'ignored_images': [dict(row(image), reason='Outside selected participant material trees, active scene World and configured preview material; retained saved data, ignored only for native preparation')
                           for image in sorted(bpy.data.images, key=lambda image: image.name) if image not in required],
        'limits': ('All saved image datablocks; existing FILE-path declaration checks are unchanged' if scope == 'all' else
                   'Conservative recursive scan includes disconnected nodes in selected participant material trees, active scene World and configured preview material; does not trace active sockets or read non-node HDR/capture metadata. Ignored saved datablocks are retained.'),
        'file_check': 'Existing existence/declaration checks apply to unpacked FILE images with filepaths; packed and generated handling is unchanged'}
    return images, report


def check_physical_world_routing(c, controls, output):
    """Validate only direct Lighting or the explicitly opted exact camera-ray split."""
    from common import physical_world_routing
    requested = physical_world_routing(c)
    surface = output.inputs['Surface']
    direct = bool(surface.links and surface.links[0].from_socket == controls.outputs['Lighting'])
    if requested == 'direct':
        # Preserve the existing default's direct-only rule and error text.
        if not direct:
            raise ValueError('Physical baking requires the World Lighting output')
        return {'requested': requested, 'route': 'direct', 'controls': controls.name, 'active_output': output.name}
    relevant = [controls, output]
    if direct:
        route = 'direct'
        mix = ray = None
    else:
        message = 'camera-separated routing requires Lighting -> Mix input 1, Visible Sky -> input 2, direct Is Camera Ray -> Fac, Mix -> active World Surface'
        if len(surface.links) != 1:
            raise ValueError(message)
        mix = surface.links[0].from_node
        if mix.type != 'MIX_SHADER' or surface.links[0].from_socket != mix.outputs[0]:
            raise ValueError(message)
        if any(len(mix.inputs[i].links) != 1 for i in (0, 1, 2)):
            raise ValueError(message)
        if (mix.inputs[1].links[0].from_socket != controls.outputs['Lighting']
                or mix.inputs[2].links[0].from_socket != controls.outputs['Visible Sky']):
            raise ValueError(message)
        ray = mix.inputs[0].links[0].from_node
        if ray.type != 'LIGHT_PATH' or mix.inputs[0].links[0].from_socket != ray.outputs['Is Camera Ray']:
            raise ValueError(message)
        relevant.extend((mix, ray))
        route = 'camera-separated'
    if any(node.mute for node in relevant):
        raise ValueError('Physical World routing contains a muted relevant node')
    return {'requested': requested, 'route': route, 'controls': controls.name, 'active_output': output.name,
            'mix_shader': mix.name if mix else None, 'light_path': ray.name if ray else None,
            'noncamera_source': 'Lighting', 'camera_source': 'Visible Sky' if mix else 'Lighting',
            'factor': 'Is Camera Ray' if ray else None,
            'validation': 'Exact direct links and same controls node; no relevant muted route nodes; no rewiring performed'}


def preflight(c, *, allow_missing_generated=False):
    s=bpy.context.scene; receivers=select(c,'receivers'); contributors=select(c,'contributors')
    if not receivers:raise ValueError('No bake receivers selected')
    if set(receivers)&set(contributors):raise ValueError('Receiver and contributor sets overlap')
    if not set(select(c,'metal_receivers')).issubset(receivers):raise ValueError('Metal receivers must be bake receivers')
    emitters=select(c,'visible_emitters')
    if set(emitters)&set(receivers+contributors+select(c,'glass')):
        raise ValueError('Visible-only emitters must be separate from receivers, transport and glass')
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
    if c.get('ao',{}).get('enabled'):generated.update((binding(c,'ao'),Path(c['_candidate'])/'masters/ao.exr'))
    declared={(Path(c['_root'])/p).resolve() for p in c.get('source_images',[])}
    declared.update((Path(c['_root'])/p['path']).resolve() for p in c.get('downloads',[]))
    root=Path(c['_candidate'])
    declared.update((root/'source-assets'/p).resolve() for p in c.get('source_images',[]))
    declared.update((root/'sources'/Path(p['path']).name).resolve() for p in c.get('downloads',[]))
    generated.update(root/'masters'/p for p in ('direct_final.exr','indirect_final.exr','island_ids.exr','combined.exr'))
    required_images,image_scope=image_dependencies(c)
    for im in required_images:
        if im.source=='FILE' and im.filepath and not im.packed_file:
            path=Path(bpy.path.abspath(im.filepath)).resolve()
            if not path.is_file() and path not in generated:missing.append(str(path))
            if path not in generated and path not in declared:raise ValueError('Declare source image in source_images/downloads: '+str(path))
    if missing:raise ValueError('Missing required source images: '+str(missing))
    node=world_tools.find_controls(s.world)
    if not node:raise ValueError('Connect the supported KaDshow Environment Controls World group')
    output=next(n for n in s.world.node_tree.nodes if n.type=='OUTPUT_WORLD' and n.is_active_output)
    world_routing=check_physical_world_routing(c,node,output)
    if c.get('preview_material') not in bpy.data.materials:raise ValueError('Missing configured preview material')
    from image_binding import missing_generated_preflight
    with missing_generated_preflight(c, allow_missing_generated) as placeholders:
        if c.get('ao',{}).get('enabled'):
            from babylon_js.lightmap_grading import upgrade
            # Fresh missing maps need dimensions only here; no placeholder reaches
            # a later worker, bake, saved source, or accepted lighting result.
            upgrade(bpy.data.materials[c['preview_material']],uv=c.get('uv','SimpleBake'))
        resolution=check_preview_resolution(c)
    return {'scene':s.name,'receiver_count':len(receivers),'triangles':triangles,'receivers':[o.name for o in receivers],
        'contributors':[o.name for o in contributors],'visible_only_emitters':[o.name for o in emitters],'excluded_glass':[o.name for o in select(c,'glass')],
        'world_controls':world_tools.settings(node),'physical_world_routing':world_routing,
        'missing_source_images':missing,'blender_version':bpy.app.version_string,
        'samples':c['samples'],'quality':c.get('quality','final'),'preview_resolution':resolution,
        'image_dependencies':image_scope,'preflight_placeholders':placeholders,
        'requires_full_bake':bool(placeholders)}


def evaluate_receivers(c):
    s=bpy.context.scene;deps=bpy.context.evaluated_depsgraph_get(); copies=[];catalogue=[]
    from native_ownership import ownership_method, attach_face_ids
    native_ids=ownership_method(c)=='native-island-id'
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
        group_ids={}
        for key,tris in groups.items():
            identity=len(catalogue)+1
            group_ids[key]=identity
            catalogue.append({'id':identity,'object':o.name,'polygon_root':key,'triangles':tris})
        if native_ids:attach_face_ids(mesh,[group_ids[find(p.index)] for p in mesh.polygons])
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
    from physical_colour_adjustment import physical_materials
    from receiver_response_capture import verify_physical_capture
    setup(c);preflight(c)
    with physical_materials(c) as tint:
        verify_physical_capture(c,root,tint)
        result=_bake(c,root,stage,tint)
        result.update(scene=bpy.context.scene.name,physical_colour_adjustment=tint)
        return result


def _bake(c,root,stage,tint=None):
    s=bpy.context.scene
    if tint is not None:save_json(stage/'physical_colour_adjustment.json',tint)
    receivers=select(c,'receivers');contributors=select(c,'contributors'); lights=select(c,'bake_lights')
    from babylon_js.agx_lightmap import bake_albedo
    bake_albedo(bpy.context,receivers,root/'masters/albedo_uv.exr',c['size'],c.get('uv','SimpleBake'))
    for o in s.objects:
        if o.type=='MESH':o.hide_render=o not in receivers+contributors
        if o.type=='LIGHT':o.hide_render=o not in lights
    effective_records=[]
    def record_effective(phase,mode,names,passes=None,material_proof=None):
        enabled_lights=sorted(o.name for o in s.objects if o.type=='LIGHT' and not o.hide_render)
        assert enabled_lights==sorted(o.name for o in lights), 'Unexpected bake light visibility'
        assert not s.cycles.use_adaptive_sampling
        assert s.cycles.samples==c['samples'][mode]
        if c.get('quality')=='final' and mode=='INDIRECT':
            assert s.cycles.samples==2048, 'Final indirect must be exactly 2048 fixed samples'
        effective_records.append(dict(phase=phase,pass_name=mode,receivers=names,
            scene=s.name,physical_colour_adjustment=tint,
            pass_filter=sorted(passes or {mode}),
            preserve_metal_diffuse_weight=c.get('preserve_metal_diffuse_weight',False),
            material_proof=material_proof,
            transport_materials=[dict(object=o.name,materials=[m.name if m else None for m in o.data.materials])
                for o in sorted(s.objects,key=lambda obj:obj.name) if o.type=='MESH' and not o.hide_render],
            light_proof=[dict(name=o.name,type=o.data.type,energy=o.data.energy,color=list(o.data.color),
                matrix_world=[list(row) for row in o.matrix_world],
                properties={key:getattr(o.data,key) for key in ('size','size_y','shape','spot_size','spot_blend','shadow_soft_size') if hasattr(o.data,key)}) for o in lights],
            samples=s.cycles.samples,adaptive=s.cycles.use_adaptive_sampling,
            denoising=s.cycles.use_denoising,device=s.cycles.device,
            compute_device=bpy.context.preferences.addons['cycles'].preferences.compute_device_type,
            enabled_devices=[dict(name=d.name,type=d.type) for d in bpy.context.preferences.addons['cycles'].preferences.devices if d.use],
            enabled_lights=enabled_lights,
            excluded_lights=sorted(o.name for o in s.objects if o.type=='LIGHT' and o.hide_render),
            diffuse_bounces=s.cycles.diffuse_bounces,total_bounces=s.cycles.max_bounces))
        save_json(stage/'effective_bake_settings.json',effective_records)
    copies,catalogue=evaluate_receivers(c);save_json(stage/'islands.json',catalogue)
    with joined_receivers(copies,receivers) as (proxy,err):
        im=target_image(c,list(proxy.data.materials));proxy.data.uv_layers.active_index=proxy.data.uv_layers.find(c.get('uv','SimpleBake'))
        try:
            for mode in ('DIRECT','INDIRECT'):
                s.cycles.samples=c['samples'][mode]
                record_effective('joined',mode,[o.name for o in receivers])
                save_json(stage/'progress.json',{'phase':'baking','pass':mode,'samples':s.cycles.samples,'time':time.time()})
                bpy.ops.object.bake(type='DIFFUSE',pass_filter={mode},margin=0,use_clear=True,use_selected_to_active=False,uv_layer=c.get('uv','SimpleBake'))
                np.save(stage/(mode.lower()+'_raw.npy'),raw_pixels(im))
            native_ownership=None
            from native_ownership import ownership_method
            if ownership_method(c)=='native-island-id':
                from native_ownership import save_native_bake
                save_json(stage/'progress.json',{'phase':'native_ownership','pass':'EMIT','samples':1,'time':time.time()})
                native_ownership=save_native_bake(c,root,stage,proxy,catalogue)
        finally:
            bpy.data.images.remove(im)
    caps=select(c,'metal_receivers')
    if not set(caps).issubset(receivers):raise ValueError('Metal receivers must be bake receivers')
    metadata=[]
    preserve=c.get('preserve_metal_diffuse_weight',False)
    if type(preserve) is not bool:raise ValueError('preserve_metal_diffuse_weight must be boolean')
    source=Path(c.get('_source') or bpy.data.filepath)
    source_sha=sha(source) if preserve else None
    transport_meshes={obj:obj.data for obj in receivers+contributors}
    transport_materials={obj:tuple(obj.data.materials) for obj in receivers+contributors}
    for index,o in enumerate(caps):
        im=None
        try:
            # An EMIT bake on evaluated copies leaves all transport untouched and
            # provides the local base plus native coverage, including black texels.
            base_path=stage/f'metal_{index}_base.exr'
            base=bake_albedo(bpy.context,[o],base_path,c['size'],c.get('uv','SimpleBake')) if preserve else None
            proof={'source_blend_sha256':source_sha,'metallic_cap':c.get('metallic_cap',.8),
                   'cap_operation':'MINIMUM','materials':[]}
            if preserve:
                proof.update(base_file=base_path.name,base_sha256=sha(base_path),
                    base_provenance_file=base_path.name+'.json',
                    base_semantics='linear Principled Base Color via local native EMIT',
                    coverage_sha256=hashlib.sha256(np.ascontiguousarray(base[...,3]).tobytes()).hexdigest())
            with capped_materials(o,c.get('metallic_cap',.8),strict_principled=False,material_proof=proof['materials']) as clones:
                im=target_image(c,clones)
                bpy.ops.object.select_all(action='DESELECT');o.hide_set(False);o.select_set(True);bpy.context.view_layer.objects.active=o
                for mode in ('DIRECT','INDIRECT'):
                    s.cycles.samples=c['samples'][mode]
                    passes={mode,'COLOR'} if preserve else {mode}
                    record_effective('metal_cap',mode,[o.name],passes,proof)
                    save_json(stage/'progress.json',{'phase':'metal_cap','object':o.name,'completed':index,'total':len(caps),'pass':mode})
                    bpy.ops.object.bake(type='DIFFUSE',pass_filter=passes,margin=0,use_clear=True,use_selected_to_active=False,uv_layer=c.get('uv','SimpleBake'))
                    a=raw_pixels(im);path=stage/f'metal_{index}_{mode.lower()}.npz'
                    row={'object':o.name,'pass':mode.lower(),'file':path.name,
                         'pass_filter':sorted(passes),'preserve_metal_diffuse_weight':preserve}
                    if preserve:
                        # Retain the original weighted output independently of the
                        # normalized patch; COLOR alpha is deliberately not coverage.
                        weighted_path=stage/f'metal_{index}_{mode.lower()}_weighted.npz'
                        np.savez_compressed(weighted_path,pixels=a)
                        a,division=normalize_metal_diffuse(a,base)
                        row.update(proof,division=division,weighted_file=weighted_path.name,
                            weighted_sha256=sha(weighted_path),semantics='native diffuse including COLOR divided by EMIT base; base applied once later')
                    np.savez_compressed(path,pixels=a)
                    if preserve:row['sha256']=sha(path)
                    metadata.append(row)
        finally:
            if im is not None:bpy.data.images.remove(im)
            for obj,mesh in transport_meshes.items():
                assert obj.data==mesh and tuple(obj.data.materials)==transport_materials[obj], 'Transport materials changed: '+obj.name
    save_json(stage/'metal_patches.json',metadata)
    result={'joined_normal_max_error':err,'island_count':len(catalogue),'metal_receiver_count':len(caps),'samples':c['samples']}
    if native_ownership is not None:result['native_ownership']=native_ownership
    return result


def clone_tree(tree):
    result=tree.copy()
    for n in result.nodes:
        if n.type=='GROUP' and n.node_tree:n.node_tree=clone_tree(n.node_tree)
    return result


def flatten(c,root,stage):
    check_preview_resolution(c)
    original=bpy.data.materials[c['preview_material']]
    save_json(stage/'appearance.json',{'schema_version':1,'preview_sha256':preview_identity(original)})
    def tagged(tree):
        return any(n.get('bjs_preview_image_role')=='colour' or
                   (n.type=='GROUP' and tagged(n.node_tree)) for n in tree.nodes)
    if tagged(original.node_tree):
        # Share the deterministic CPU evaluator with AgX export. GPU texture
        # interpolation can differ at sharp atlas transitions even on identical nodes.
        from babylon_js.agx_lightmap import combine_saved_controls
        result=combine_saved_controls(bpy.context,c['size'],stage/'evaluated.exr',material=original)
        np.save(stage/'evaluated.npy',result)
        return {'method':'shared export/combination CPU EMIT evaluator','samples':1}
    s=setup(c);mat=original.copy()
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


def capture_denoise(spec,thumbnail=False):
    """Validate the optional thumbnail render filter; HDR capture stays raw."""
    denoise=spec.get('denoise',False)
    if not isinstance(denoise,bool):
        raise ValueError('Capture/thumbnail denoise must be a boolean')
    if denoise and not thumbnail:
        raise ValueError('HDR capture denoise must remain false; use thumbnail.denoise for JPEG thumbnails')
    return denoise if thumbnail else False


def capture(c,root,stage,thumbnail=False,*,prepare_only=False):
    spec=c['thumbnail' if thumbnail else 'capture']
    denoise=capture_denoise(spec,thumbnail)
    s=setup(c);receivers=select(c,'receivers');glass=select(c,'glass')
    mode=spec.get('mode','baked')
    if mode not in ('baked','physical'):raise ValueError('Capture mode must be baked or physical')
    if mode=='baked':
        check_preview_resolution(c)
        verify_combination(bpy.data.materials[c['preview_material']],root/'masters/combined.exr',root/'masters/combined.provenance.json')
    emitters=select(c,'visible_emitters')
    if set(emitters)&set(receivers+select(c,'contributors')+glass):
        raise ValueError('Visible-only emitters must be separate from receivers, transport and glass')
    visible=receivers+emitters+(glass if thumbnail else [])
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
    s.cycles.use_denoising=denoise
    s.render.use_border=False
    s.render.image_settings.file_format='JPEG' if thumbnail else 'OPEN_EXR'
    s.render.image_settings.color_mode='RGB';s.render.image_settings.color_depth='8' if thumbnail else '32'
    if thumbnail:s.render.image_settings.quality=90
    else:s.render.image_settings.exr_codec='ZIP'
    path=stage/('thumbnail.jpg' if thumbnail else 'capture.exr');s.render.filepath=str(path)
    if prepare_only:
        # Probe volumes reuse this exact prepared appearance and move one camera.
        return {'mode':mode,'world_output':branch,'position':list(camera.location)}
    rays=[]
    deps=bpy.context.evaluated_depsgraph_get()
    for direction in ((1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)):
        hit,loc,normal,index,obj,matrix=s.ray_cast(deps,camera.location,Vector(direction),distance=100)
        rays.append({'direction':direction,'object':obj.name if hit else None,'distance':float((loc-camera.location).length) if hit else None})
    bpy.ops.render.render(write_still=True)
    import OpenImageIO as oiio
    pixels=oiio.ImageBuf(str(path)).get_pixels(oiio.FLOAT)
    if not np.isfinite(pixels).all():raise ValueError('Capture contains non-finite pixels')
    return {'mode':mode,'visible_emitters':[o.name for o in emitters],'world_output':branch,'position':list(camera.location),'rotation':list(camera.rotation_euler),
        'samples':s.cycles.samples,'denoise':bool(s.cycles.use_denoising),'adaptive':bool(s.cycles.use_adaptive_sampling),
        'dimensions':[s.render.resolution_x,s.render.resolution_y],'rays':rays,
        'rgb_min':float(pixels[:,:,:3].min()),'rgb_max':float(pixels[:,:,:3].max()),'rgb_mean':float(pixels[:,:,:3].mean())}


def main():
    if not bpy.app.background:raise RuntimeError('Use the saved-scene CLI; do not run worker in a live session')
    args=sys.argv[sys.argv.index('--')+1:];config,command,rootpath,stagepath=args[:4]
    c=load_config(config);root=Path(rootpath);stage=Path(stagepath);c['_candidate']=str(root)
    if Path(bpy.data.filepath).resolve()!=Path(c['_source']):raise ValueError('Wrong source scene opened')
    from physical_colour_adjustment import select_scene
    select_scene(c)
    source_hash=sha(c['_source']);relink(c,root)
    finishing=None
    if c.get('finishing_overrides') or c.get('appearance_colour_adjustment',{}).get('enabled'):
        from native_recombine import apply_finishing
        check_preview_resolution(c)
        finishing=apply_finishing(c)
    if (root/'native_recombine.provenance.json').is_file():
        from native_recombine import verify_copied
        verify_copied(c,root)
    if command=='preflight':
        fresh=args[4:]==['--fresh-generated-preflight']
        if fresh:
            manifest=json.loads((root/'build.json').read_text())
            if manifest['inputs'].get('reuse_lightmaps') is not False or (root/'native_recombine.provenance.json').exists():
                raise ValueError('Missing-map dimension preparation is fresh/no-reuse only')
        result=preflight(c,allow_missing_generated=fresh)
    elif command=='native-recombine-check':
        from native_recombine import verify_copied
        result=verify_copied(c,root)
        check_preview_resolution(c)
        from ao import geometry_identity
        if geometry_identity(c)['geometry_sha256']!=result['geometry_sha256']:
            raise ValueError('Current native recombine geometry/UV/sampler identity differs')
    elif command=='geometry':
        from ao import geometry_identity
        result=geometry_identity(c)
    elif command=='ao':
        from ao import bake as bake_ao
        result=bake_ao(c,root,stage)
    elif command=='reuse-check':result=reuse_check(c,root)
    elif command=='response-capture':
        from receiver_response_capture import capture as capture_response
        result=capture_response(c,root,stage)
    elif command=='bake':result=bake(c,root,stage)
    elif command=='combine':result=flatten(c,root,stage)
    elif command in ('views','delivery-views'):
        if bool(c['capture']['views'].get('delivery_contract'))!=(command=='delivery-views'):
            raise ValueError('Delivery view contract requires dedicated delivery-views command')
        from camera_views import capture_views
        result=capture_views(c,root,stage)
    elif command in ('capture','thumbnail'):result=capture(c,root,stage,command=='thumbnail')
    elif command=='env':
        from babylon_js.env_export import export_environment
        result=export_environment(bpy.context,{},stage,stage,
            dict(c.get('env',{}),converter=args[4],image=str(root/'capture/capture.exr')))
    elif command=='snapshot':
        for o in bpy.data.objects:
            if o.get('bjs_lightmap_image'):o['bjs_lightmap_image']='//../masters/combined.exr'
        bpy.context.scene.world['bjs_environment_image']='//../capture/capture.exr'
        if (root/'masters/albedo_uv.exr').is_file():
            bpy.context.scene['bjs_lightmap_albedo']='//../masters/albedo_uv.exr'
        for im in bpy.data.images:
            if im.source=='FILE' and im.filepath:im.filepath=bpy.path.relpath(bpy.path.abspath(im.filepath),start=str(stage))
        bpy.ops.wm.save_as_mainfile(filepath=str(stage/'restored.blend'),relative_remap=False)
        result={'file':'restored.blend','accepted':False,'source_materials_preserved':True}
    elif command=='validate':
        import OpenImageIO as oiio
        rows=[]
        files=('direct_final.exr','indirect_final.exr','island_ids.exr','combined.exr')
        if c.get('receiver_response',{}).get('enabled'):
            files+=tuple(mode+'_response_'+variant+'.exr' for mode in ('direct','indirect') for variant in ('raw','processed'))
        if c.get('ao',{}).get('enabled'):files+=('ao.exr',)
        for filename in files:
            pixels=oiio.ImageBuf(str(root/'masters'/filename)).get_pixels(oiio.FLOAT)
            if pixels is None or pixels.shape!=(c['size'],c['size'],4) or not np.isfinite(pixels).all():
                raise ValueError('Invalid regenerated image: '+filename)
            if filename=='ao.exr' and (pixels[:,:,:3].min()<0 or pixels[:,:,:3].max()>1
                    or not np.array_equal(pixels[:,:,0],pixels[:,:,1]) or not np.array_equal(pixels[:,:,0],pixels[:,:,2])):
                raise ValueError('AO must be identical RGB scalar channels in [0,1]')
            rows.append({'file':filename,'shape':list(pixels.shape),'minimum':float(pixels.min()),'maximum':float(pixels.max())})
        result={'images':rows,'quality':c.get('quality','final'),'accepted':False,
                'note':'Regenerated candidates; visual review is required before promotion.'}
    else:raise ValueError('Unknown worker command')
    if sha(c['_source'])!=source_hash:raise RuntimeError('Authoring file changed during processing')
    if finishing is not None:result['finishing_overrides']=finishing
    result.update(status='passed',source_sha256=source_hash,scene=bpy.context.scene.name,scene_unchanged_on_disk=True)
    save_json(stage/'result.json',result)
    print('ENVIRONMENT_STAGE_PASSED '+command,flush=True)


if __name__=='__main__':main()
