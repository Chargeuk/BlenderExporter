"""Explicit, unaccepted raw/processed response and physical diffuse camera diagnostics.

This standalone tool verifies a completed immutable native candidate. It never
updates normal camera/bake APIs, rewrites combined provenance, saves the source,
rebakes or resynthesizes response/dilation. External Python validates provenance;
disposable factory-startup Blender renders actual linear channels and PNGs.
"""
from contextlib import contextmanager
from pathlib import Path
import argparse
import copy
import hashlib
import json
import subprocess
import sys

HERE=Path(__file__).resolve().parent
ENV=HERE.parent/'environment_tools'


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def save(path,value):Path(path).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf-8')


DEFAULT_VARIANTS=('raw','processed','physical-diffuse')


def selected_variants(recipe):
    if 'variants' not in recipe:return DEFAULT_VARIANTS
    if recipe['variants']==['processed']:return ('processed',)
    if recipe['variants']==['processed-ao-bypass']:return ('processed-ao-bypass',)
    raise ValueError('Only explicit single processed or processed-ao-bypass is supported as variant opt-in')


def validate_recipe(recipe,c):
    allowed={'schema_version','native_config','camera_ids','samples','physical_samples','crop','physical_objective_objects','variants','owned_evidence'}
    if not isinstance(recipe,dict) or set(recipe)-allowed or recipe.get('schema_version')!=1:
        raise ValueError('Expected explicit native-view-diagnostic recipe schema1')
    if not isinstance(recipe.get('native_config'),str) or not recipe['native_config'].strip():raise ValueError('Exact original native config required')
    if c.get('finishing_overrides') or not c.get('receiver_response',{}).get('enabled'):
        raise ValueError('Requires original native response recipe, no finishing overrides')
    ids=recipe.get('camera_ids')
    if not isinstance(ids,list) or not ids or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids):raise ValueError('Unique retained baked camera IDs required')
    from camera_views import validate_camera_set
    entries=validate_camera_set(c)
    selected=[]
    for identity in ids:
        matching=[entry for entry in entries if entry['id']==identity and entry.get('mode')=='baked']
        if len(matching)!=1:raise ValueError('Choose an existing original baked camera ID: '+str(identity))
        selected.append(copy.deepcopy(matching[0]))
    for key,default in [('samples',c['capture']['views']['samples']),('physical_samples',128)]:
        value=recipe.get(key,default)
        if isinstance(value,bool) or not isinstance(value,int) or not 1<=value<=65536:raise ValueError('Invalid diagnostic '+key)
    if recipe.get('crop') is not None:
        crop=recipe['crop'];width,height=c['capture']['views']['dimensions']
        if not isinstance(crop,list) or len(crop)!=4 or any(isinstance(v,bool) or not isinstance(v,int) for v in crop):raise ValueError('Crop requires integer[x0,y0,x1,y1]')
        x0,y0,x1,y1=crop
        if not 0<=x0<x1<=width or not 0<=y0<y1<=height:raise ValueError('Diagnostic crop outside retained image')
    objectives=recipe.get('physical_objective_objects',[])
    if not isinstance(objectives,list) or any(not isinstance(n,str) for n in objectives) or len(set(objectives))!=len(objectives):raise ValueError('Invalid physical objective objects')
    if not set(objectives)<=set(c['receivers']['names']) or set(objectives)&set(c.get('metal_receivers',{}).get('names',[])):
        raise ValueError('Physical diffuse objective must be native receivers outside metallic-cap selection')
    selected_variants(recipe)
    evidence=recipe.get('owned_evidence')
    if evidence is not None:
        if not isinstance(evidence,dict) or set(evidence)!={'objects','chart_ids'}:raise ValueError('Owned evidence requires explicit objects and chart_ids')
        objects=evidence['objects'];ids=evidence['chart_ids']
        if not isinstance(objects,list) or not objects or any(not isinstance(n,str) for n in objects) or len(set(objects))!=len(objects) or not set(objects)<=set(c['receivers']['names']):raise ValueError('Invalid owned evidence object selector')
        if not isinstance(ids,list) or not ids or any(isinstance(i,bool) or not isinstance(i,int) or i<=0 for i in ids) or len(set(ids))!=len(ids):raise ValueError('Explicit positive unique native chart IDs required')
    return selected


def verify_inputs(manifest):
    for path,digest in manifest.items():
        if sha(path)!=digest:raise ValueError('Verified diagnostic input changed: '+path)


def prepare(recipe_path,candidate,tool_root,output):
    """External NumPy/SciPy runtime performs full existing native provenance validation."""
    sys.path.insert(0,str(ENV))
    from common import load_config
    from native_recombine import validate_original
    recipe_path=Path(recipe_path).resolve();recipe=read(recipe_path)
    c=load_config((recipe_path.parent/recipe['native_config']).resolve())
    selected=validate_recipe(recipe,c)
    output=Path(output).resolve();candidate=Path(candidate).resolve()
    if output.exists() or output==candidate or output.is_relative_to(candidate) or candidate.is_relative_to(output):
        raise ValueError('Use a new diagnostic directory separate from native candidate')
    plan=validate_original(c,candidate,tool_root)
    inputs=dict(plan['inputs'])
    inputs[str(recipe_path)]=sha(recipe_path)
    inputs[str(Path(__file__).resolve())]=sha(__file__)
    for name in ('direct_response_raw.exr','indirect_response_raw.exr','direct_response_processed.exr','indirect_response_processed.exr','ao.exr','island_ids.exr','combined.exr','combined.provenance.json'):
        path=candidate/'masters'/name
        if not path.is_file():raise ValueError('Required completed diagnostic master missing: '+name)
        inputs[str(path)]=sha(path)
    output.mkdir(parents=True)
    report={'schema_version':1,'kind':'unaccepted-native-view-diagnostic','accepted':False,
        'recipe':str(recipe_path),'native_config':c['_config'],'candidate':str(candidate),
        'source':c['_source'],'source_sha256':plan['source_sha256'],'geometry_sha256':plan['geometry_sha256'],
        'native_proof':plan,'inputs':inputs,'selected_cameras':selected,
        'raw_master_semantics':'Existing receiver_response raw master: actual owned native RGB times measured response then ordinary ownership.dilate; no OIDN or diagnostic resynthesis.',
        'limits':'Diagnostic views are not new accepted combinations/captures or physical metallic-cap equivalence. Physical diffuse output is ungraded; saved source controls are unchanged.'}
    save(output/'verified-inputs.json',report)
    if recipe.get('owned_evidence'):
        evidence=native_owned_evidence(candidate,recipe['owned_evidence'],inputs)
        save(output/'owned-pass-evidence.json',evidence)
    return c,report


@contextmanager
def preview_variant(material,root,variant):
    """Copy the complete saved graph; replace only D/I samplers, restore all source state."""
    import bpy
    from appearance import preview_identity
    if variant not in ('raw','processed','processed-ao-bypass'):raise ValueError('Unknown response diagnostic variant')
    response_variant='processed' if variant=='processed-ao-bypass' else variant
    root=Path(root);before=preview_identity(material);groups={};images=[];clone=material.copy()
    try:
        def copy_groups(tree):
            for node in tree.nodes:
                if node.type=='GROUP' and node.node_tree:
                    original=node.node_tree;key=original.as_pointer()
                    if key not in groups:
                        groups[key]=original.copy();copy_groups(groups[key])
                    node.node_tree=groups[key]
        copy_groups(clone.node_tree)
        bound={}
        for tree in [clone.node_tree]+list(groups.values()):
            for node in tree.nodes:
                role=node.get('bjs_preview_image_role')
                if node.type=='TEX_IMAGE' and role in ('direct','indirect'):
                    original=node.image
                    if original is None:raise ValueError('Response sampler lacks original image')
                    if role not in bound:
                        path=root/'masters'/(role+'_response_'+response_variant+'.exr')
                        image=bpy.data.images.load(str(path),check_existing=False);images.append(image)
                        image.colorspace_settings.name=original.colorspace_settings.name;image.alpha_mode=original.alpha_mode
                        bound[role]={'path':str(path),'sha256':sha(path),'colorspace':image.colorspace_settings.name,'alpha_mode':image.alpha_mode,'image':image}
                    node.image=bound[role]['image']
        if set(bound)!={'direct','indirect'}:raise ValueError('Diagnostic requires explicit managed D/I sampler roles')
        from babylon_js import lightmap_grading
        original_control,_=lightmap_grading.inspect(material);clone_control,_=lightmap_grading.inspect(clone)
        original_values={i.name:float(i.default_value) for i in original_control.inputs if i.type=='VALUE'}
        clone_before={i.name:float(i.default_value) for i in clone_control.inputs if i.type=='VALUE'}
        if original_values!=clone_before:raise RuntimeError('Diagnostic clone did not retain saved scalar controls')
        if variant=='processed-ao-bypass':
            socket=clone_control.inputs['AO Strength']
            if socket.is_linked:raise ValueError('AO bypass requires unlinked managed AO Strength')
            socket.default_value=0
        clone_values={i.name:float(i.default_value) for i in clone_control.inputs if i.type=='VALUE'}
        if any(clone_values[k]!=value for k,value in original_values.items() if k!='AO Strength'):raise RuntimeError('AO bypass changed another saved control')
        record={'variant':variant,'accepted':False,'original_preview_sha256':before,'variant_preview_sha256':preview_identity(clone),
            'bindings':{role:{k:v for k,v in row.items() if k!='image'} for role,row in bound.items()},
            'response_variant':response_variant,'saved_grades_AO_colour_unchanged':variant!='processed-ao-bypass',
            'only_AO_strength_bypassed':variant=='processed-ao-bypass','original_actual_controls':original_values,'clone_actual_controls':clone_values,
            'combined_provenance_claim':'None for variant; original accepted provenance verified separately.'}
        yield clone,record
    finally:
        bpy.data.materials.remove(clone)
        # Parent clones must be removed before their descendants; remaining references are only cloned trees.
        while groups:
            removable=[key for key,tree in groups.items() if tree.users==0]
            if not removable:raise RuntimeError('Diagnostic cloned node groups retained unexpected users')
            for key in removable:bpy.data.node_groups.remove(groups.pop(key))
        for image in images:bpy.data.images.remove(image)
        if preview_identity(material)!=before:raise RuntimeError('Diagnostic changed original managed preview')


FORMAT_KEYS=('media_type','file_format','color_mode','color_depth','exr_codec','use_exr_interleave')


def image_format_state(fmt):
    return {key:getattr(fmt,key) for key in FORMAT_KEYS if hasattr(fmt,key)}


def restore_image_format(fmt,previous):
    # Blender5.2 media setters also change format/depth. Restore their context first.
    if 'media_type' in previous:fmt.media_type=previous['media_type']
    fmt.file_format=previous['file_format']
    for key in FORMAT_KEYS[2:]:
        if key in previous:setattr(fmt,key,previous[key])


def set_image_format(fmt,file_format,*,interleave=False):
    media='MULTI_LAYER_IMAGE' if file_format=='OPEN_EXR_MULTILAYER' else 'IMAGE'
    if hasattr(fmt,'media_type'):
        identifiers={item.identifier for item in fmt.bl_rna.properties['media_type'].enum_items}
        if media not in identifiers:raise ValueError('Required actual image media type unavailable: '+media)
        fmt.media_type=media
    identifiers={item.identifier for item in fmt.bl_rna.properties['file_format'].enum_items}
    if file_format not in identifiers:raise ValueError('Required actual image format unavailable: '+file_format)
    if fmt.file_format!=file_format:fmt.file_format=file_format
    if hasattr(fmt,'use_exr_interleave'):fmt.use_exr_interleave=bool(interleave)
    elif interleave:raise ValueError('Interleaved multilayer EXR requires explicit supported setting')


@contextmanager
def image_format_transaction(fmt):
    previous=image_format_state(fmt)
    try:yield previous
    finally:restore_image_format(fmt,previous)


@contextmanager
def diffuse_passes(scene,view_layer):
    """Use actual native passes, explicitly exclude scene compositor and render denoising."""
    flags=['use_pass_diffuse_direct','use_pass_diffuse_indirect','use_pass_diffuse_color']
    before={key:getattr(view_layer,key) for key in flags}
    compositing=scene.render.use_compositing;denoising=scene.cycles.use_denoising
    with image_format_transaction(scene.render.image_settings):
        try:
            set_image_format(scene.render.image_settings,'OPEN_EXR_MULTILAYER',interleave=True)
            scene.render.image_settings.color_mode='RGBA';scene.render.image_settings.color_depth='32';scene.render.image_settings.exr_codec='ZIP'
            for key in flags:setattr(view_layer,key,True)
            scene.render.use_compositing=False;scene.cycles.use_denoising=False
            yield {'formula':'(Diffuse Direct + Diffuse Indirect) * Diffuse Color','glossy_emission_excluded':True,
                   'compositor_used':False,'denoise':False,'multilayer_file_settings':image_format_state(scene.render.image_settings),
                   'channels':'Actual interleaved Cycles multilayer EXR passes, not Combined or a Principled approximation.'}
        finally:
            for key,value in before.items():setattr(view_layer,key,value)
            scene.render.use_compositing=compositing;scene.cycles.use_denoising=denoising


def compose_diffuse(direct,indirect,colour):
    import numpy as np
    arrays=[np.asarray(a,dtype=np.float32) for a in (direct,indirect,colour)]
    if len({a.shape for a in arrays})!=1 or arrays[0].ndim!=3 or arrays[0].shape[-1]!=3 or any(not np.isfinite(a).all() for a in arrays):
        raise ValueError('Expected matching finite RGB diffuse passes')
    result=(arrays[0]+arrays[1])*arrays[2]
    if not np.isfinite(result).all():raise ValueError('Nonfinite diffuse composition')
    return result


def owned_statistics(labels,catalogue,arrays,objects,chart_ids):
    """Statistics only on explicit native owner pixels; no UV/raster ownership guess."""
    import numpy as np
    labels=np.asarray(labels)
    if labels.ndim!=2 or not np.issubdtype(labels.dtype,np.integer) or (labels<0).any():raise ValueError('Actual nonnegative integer native labels required')
    rows={int(row['id']):row for row in catalogue}
    if len(rows)!=len(catalogue) or any(identity<=0 for identity in rows):raise ValueError('Unique positive native catalogue IDs required')
    if not set(np.unique(labels))-set(rows)<={0}:raise ValueError('Owner labels contain unknown catalogue ID')
    if any(identity not in rows or rows[identity]['object'] not in objects for identity in chart_ids):raise ValueError('Selected chart/object correspondence differs from catalogue')
    for name,array in arrays.items():
        array=np.asarray(array)
        if array.shape[:2]!=labels.shape or array.ndim not in (2,3) or (array.ndim==3 and array.shape[2]!=3) or not np.isfinite(array).all():
            raise ValueError('Expected matching finite scalar/RGB owned pass: '+name)
    def metrics(values):
        return {'min':float(values.min()),'median':float(np.median(values)),'max':float(values.max()),'variance':float(values.var())}
    def summary(mask):
        yy,xx=np.where(mask);count=len(xx)
        result={'owned_pixels':count,'pixel_bbox_top_left_exclusive':[int(xx.min()),int(yy.min()),int(xx.max())+1,int(yy.max())+1] if count else None,'passes':{}}
        for name,array in arrays.items():
            values=np.asarray(array)[mask].astype(np.float64,copy=False)
            if not count:result['passes'][name]=None
            elif values.ndim==1:result['passes'][name]={'scalar':metrics(values)}
            else:result['passes'][name]={'RGB':[metrics(values[:,i]) for i in range(3)],'luminance_Rec709':metrics(values@np.array([.2126,.7152,.0722]))}
        return result
    charts=[]
    for identity in chart_ids:
        row=rows[identity];uv=np.asarray(row['triangles'],dtype=np.float64).reshape(-1,2)
        charts.append({'native_owner_id':identity,'object':row['object'],'polygon_root':row['polygon_root'],
            'catalogue_UV_bounds':[uv.min(axis=0).tolist(),uv.max(axis=0).tolist()],'catalogue_UV_coordinates':'Original Blender UV values; no ownership inferred from these bounds',
            **summary(labels==identity)})
    totals=[]
    for name in objects:
        identities=[identity for identity,row in rows.items() if row['object']==name]
        if not identities:raise ValueError('Object absent from native catalogue: '+name)
        totals.append({'object':name,'native_owner_ids':sorted(identities),'scope':'All native-owned charts of this object, separate from selected face-chart records',**summary(np.isin(labels,identities))})
    return {'array_origin':'top-left, exact saved native labels/arrays','dimensions':[labels.shape[1],labels.shape[0]],
        'ownership_method':'Actual native-island-id labels; equality masks, no catalogue rasterization','charts':charts,'object_totals':totals}


def native_owned_evidence(root,selection,verified_inputs):
    """External runtime reuses installed native owner validator and untouched owned arrays."""
    import numpy as np
    from native_ownership import validate_native_ids
    root=Path(root)
    paths={'catalogue':root/'bake/islands.json','native_ids':root/'bake/native_island_ids.npy',
        'native_D':root/'bake/direct_raw.npy','native_I':root/'bake/indirect_raw.npy','labels':root/'ownership/labels.npy',
        'D_response_raw':root/'response/direct_response_raw_owned.npy','D_response_processed':root/'response/direct_response_processed_owned.npy',
        'I_response_raw':root/'response/indirect_response_raw_owned.npy','I_response_processed':root/'response/indirect_response_processed_owned.npy','scalar_AO':root/'ao/ao_raw.npy'}
    for path in paths.values():
        if verified_inputs.get(str(path.resolve()))!=sha(path):raise ValueError('Numeric evidence input lacks original completed-output proof: '+str(path))
    catalogue=read(paths['catalogue'])
    labels,ownership=validate_native_ids(np.load(paths['native_ids'],allow_pickle=False),catalogue,
        np.load(paths['native_D'],allow_pickle=False),np.load(paths['native_I'],allow_pickle=False))
    if not np.array_equal(labels,np.load(paths['labels'],allow_pickle=False)):raise ValueError('Numeric evidence labels differ from actual native owner IDs')
    arrays={name:np.load(paths[name],allow_pickle=False)[:,:,:3] for name in ('D_response_raw','D_response_processed','I_response_raw','I_response_processed')}
    ao=np.load(paths['scalar_AO'],allow_pickle=False)
    if ao.ndim!=3 or ao.shape[2]<3 or not np.allclose(ao[:,:,0],ao[:,:,1],rtol=0,atol=1e-7) or not np.allclose(ao[:,:,0],ao[:,:,2],rtol=0,atol=1e-7):raise ValueError('AO input is not scalar RGB')
    arrays['scalar_AO']=ao[:,:,0]
    result=owned_statistics(labels,catalogue,arrays,selection['objects'],selection['chart_ids'])
    result.update(schema_version=1,kind='bounded-native-owned-pass-evidence',accepted=False,native_owner_validation=ownership,
        source_paths_sha256={str(path):sha(path) for path in paths.values()},
        limits='Statistics describe native-owned source pixels only, not a spatial-render defect verdict. Scalar AO is ungraded raw AO; response RGB already includes measured receiver response but no saved finishing grade.')
    return result


DIFFUSE_PASS_ALIASES={'DiffDir':('Diffuse Direct','DiffDir'),
                      'DiffInd':('Diffuse Indirect','DiffInd'),
                      'DiffCol':('Diffuse Color','DiffCol')}


def resolve_diffuse_channels(channel_names,layer_name):
    """Exact layer and recognized role aliases only; never select another pass."""
    names=list(channel_names)
    if len(set(names))!=len(names):raise ValueError('Duplicate native pass channel names')
    names=set(names);resolved={}
    for role,aliases in DIFFUSE_PASS_ALIASES.items():
        candidates=[]
        for alias in aliases:
            prefix=layer_name+'.'+alias+'.'
            rgb=[prefix+channel for channel in ('R','G','B')]
            if any(name.startswith(prefix) for name in names):candidates.append(rgb)
        if len(candidates)>1:raise ValueError('Ambiguous recognized diffuse pass aliases for '+role)
        if len(candidates)!=1 or not all(name in names for name in candidates[0]):
            raise ValueError('Complete exact-layer RGB diffuse pass absent for '+role)
        resolved[role]=candidates[0]
    return resolved


def read_diffuse_passes(path,layer_name):
    import OpenImageIO as oiio
    import numpy as np
    handle=oiio.ImageInput.open(str(path))
    if handle is None:raise ValueError('Cannot read actual multilayer diffuse render')
    channels={};all_names=[]
    try:
        index=0
        while handle.seek_subimage(index,0):
            spec=handle.spec();names=list(spec.channelnames);pixels=np.asarray(handle.read_image(oiio.FLOAT),dtype=np.float32)
            for i,name in enumerate(names):
                all_names.append(name)
                if name in channels:raise ValueError('Ambiguous duplicate native pass channel: '+name)
                channels[name]=pixels[:,:,i]
            index+=1
    finally:handle.close()
    resolved=resolve_diffuse_channels(all_names,layer_name)
    def rgb(pass_name):
        names=resolved[pass_name]
        return np.stack([channels[name] for name in names],axis=-1)
    passes={name:rgb(name) for name in ('DiffDir','DiffInd','DiffCol')}
    return passes,{'layer':layer_name,'native_channels':all_names,'subimages_read':index,'chosen':['DiffDir','DiffInd','DiffCol'],
                   'resolved_channels':resolved,'excluded':'Combined, GlossDir/Ind/Col, Emit, Environment and display transform'}


def _save_png_from_linear(path,png,scene):
    import bpy
    image=bpy.data.images.load(str(path),check_existing=False)
    try:
        with image_format_transaction(scene.render.image_settings):
            image.colorspace_settings.name='Linear Rec.709'
            set_image_format(scene.render.image_settings,'PNG')
            scene.render.image_settings.color_mode='RGB';scene.render.image_settings.color_depth='8'
            image.save_render(str(png),scene=scene)
    finally:
        bpy.data.images.remove(image)


def blender_capture(output):
    import bpy
    import numpy as np
    from mathutils import Matrix
    sys.path.insert(0,str(ENV));sys.path.insert(0,str(HERE.parent.parent))
    from common import load_config
    from worker import setup,select,relink,write_exr,check_physical_world_routing
    from camera_views import _baked_materials,_camera_data_receipt
    from appearance import verify_combination,preview_identity
    from physical_colour_adjustment import physical_materials
    from ao import geometry_identity
    from babylon_js import environment_controls,lightmap_grading
    output=Path(output);proof=read(output/'verified-inputs.json');verify_inputs(proof['inputs'])
    recipe=read(proof['recipe']);c=load_config(proof['native_config']);entries=validate_recipe(recipe,c)
    if not bpy.app.background or Path(bpy.data.filepath).resolve()!=Path(c['_source']):raise ValueError('Diagnostics require exact immutable source in disposable background Blender')
    root=Path(proof['candidate']);scene=bpy.data.scenes[c['scene']];bpy.context.window.scene=scene;bpy.context.view_layer.update()
    original_hide={o:o.hide_render for o in scene.objects};original_camera=scene.camera
    original_compositing=scene.render.use_compositing
    original_format=image_format_state(scene.render.image_settings)
    relink(c,root);material=bpy.data.materials[c['preview_material']]
    combination=verify_combination(material,root/'masters/combined.exr',root/'masters/combined.provenance.json')
    baseline_identity=preview_identity(material)
    geometry=geometry_identity(c);assert geometry['geometry_sha256']==proof['geometry_sha256']
    scene=setup(c)
    receivers,contributors,glass=[select(c,k) for k in ('receivers','contributors','glass')]
    if select(c,'visible_emitters') or select(c,'bake_lights'):raise ValueError('This bounded diffuse diagnostic requires no analytic lights or visible emitters')
    visible=set(receivers+contributors+glass)
    for obj in scene.objects:obj.hide_render=obj not in visible
    eligible=set()
    def walk(layer,blocked=False):
        blocked=blocked or layer.exclude or layer.collection.hide_render
        if not blocked:
            eligible.update(obj for obj in layer.collection.objects if obj.type=='MESH' and not obj.hide_render)
            for child in layer.children:walk(child,blocked)
    walk(bpy.context.view_layer.layer_collection)
    if not visible<=eligible:raise ValueError('Diagnostic receiver is collection-excluded')
    controls=environment_controls.find_controls(scene.world)
    world_output=next(n for n in scene.world.node_tree.nodes if n.type=='OUTPUT_WORLD' and n.is_active_output)
    routing=check_physical_world_routing(c,controls,world_output)
    world=environment_controls.settings(controls)
    ctrl,_=lightmap_grading.inspect(material);saved_controls={i.name:float(i.default_value) for i in ctrl.inputs if i.type=='VALUE'}
    spec=c['capture']['views'];scene.render.resolution_x,scene.render.resolution_y=spec['dimensions'];scene.render.resolution_percentage=100
    scene.render.pixel_aspect_x=scene.render.pixel_aspect_y=1;scene.render.use_border=False;scene.render.film_transparent=False
    for key in ('view_transform','look','exposure','gamma'):setattr(scene.view_settings,key,spec['display'][key])
    scene.cycles.use_denoising=False;scene.cycles.time_limit=0;scene.cycles.use_adaptive_sampling=False
    scene.render.use_compositing=False
    if hasattr(scene.render,'use_motion_blur'):scene.render.use_motion_blur=False
    records=[]
    try:
        for entry in entries:
            original=scene.objects.get(entry.get('camera',''))
            if original is None or original.type!='CAMERA':raise ValueError('Retained diagnostic camera missing')
            data=original.data.copy();data.animation_data_clear();camera=bpy.data.objects.new('TEMP native diagnostic camera',data);scene.collection.objects.link(camera)
            try:
                camera.matrix_world=Matrix(entry['matrix_world']) if 'matrix_world' in entry else original.matrix_world.copy()
                data.lens=entry.get('lens',data.lens)
                for key,value in entry.get('camera_properties',{}).items():
                    if key=='dof_use':data.dof.use_dof=value
                    elif key=='dof_fstop':data.dof.aperture_fstop=value
                    else:setattr(data,key,value)
                scene.camera=camera
                for variant in selected_variants(recipe):
                    stem=entry['id']+'_'+variant;linear=output/(stem+'.exr');png=output/(stem+'.png')
                    if linear.exists() or png.exists():raise ValueError('Preserve previous diagnostic files')
                    scene.cycles.samples=recipe.get('physical_samples',128) if variant=='physical-diffuse' else recipe.get('samples',spec['samples'])
                    set_image_format(scene.render.image_settings,'OPEN_EXR')
                    scene.render.image_settings.color_mode='RGB';scene.render.image_settings.color_depth='32';scene.render.image_settings.exr_codec='ZIP'
                    if variant!='physical-diffuse':
                        with preview_variant(material,root,variant) as (clone,application):
                            with _baked_materials(receivers,clone):
                                scene.render.filepath=str(linear);bpy.ops.render.render(write_still=True)
                    else:
                        passfile=output/(stem+'_passes.exr')
                        with physical_materials(c) as physical:
                            with diffuse_passes(scene,bpy.context.view_layer) as application:
                                scene.render.filepath=str(passfile)
                                bpy.ops.render.render(write_still=True)
                                passes,channels=read_diffuse_passes(passfile,bpy.context.view_layer.name)
                                rgb=compose_diffuse(passes['DiffDir'],passes['DiffInd'],passes['DiffCol'])
                                rgba=np.concatenate([rgb,np.ones(rgb.shape[:2]+(1,),np.float32)],axis=-1)
                                write_exr(linear,rgba)
                                application.update(physical_overlay=physical,channels=channels,pass_file=str(passfile),pass_file_sha256=sha(passfile),
                                    ungraded_reference=True,metallic_cap_equivalence=False,objective_objects=recipe.get('physical_objective_objects',[]))
                    _save_png_from_linear(linear,png,scene)
                    record={'schema_version':1,'accepted':False,'variant':variant,'camera_id':entry['id'],'linear':str(linear),'linear_sha256':sha(linear),'PNG':str(png),'PNG_sha256':sha(png),
                        'source_sha256':proof['source_sha256'],'geometry_sha256':geometry['geometry_sha256'],'camera':entry['camera'],
                        'matrix_world':[list(row) for row in camera.matrix_world],'camera_properties':_camera_data_receipt(data),'samples':scene.cycles.samples,'denoise':False,'adaptive':False,
                        'dimensions':list(spec['dimensions']),'display':dict(spec['display']),'World':world,'routing':routing,'saved_controls':saved_controls,
                        'visible_meshes':len(visible),'analytic_lights':0,'physical_tint_strength':c['physical_colour_adjustment']['strength'],
                        'application':application,'baseline_combination_verification':combination,'baseline_combination_is_variant_provenance':False,
                        'limits':'Controlled diagnostic only; raw/processed emission shares saved grading/AO/tint. Physical DD+DI times DC is an ungraded diffuse reference; no glossy/emission channels, no cap equivalence on metallic receivers.'}
                    save(output/(stem+'.receipt.json'),record);records.append(record)
            finally:
                scene.camera=original_camera;bpy.data.objects.remove(camera,do_unlink=True);bpy.data.cameras.remove(data)
    finally:
        for obj,hidden in original_hide.items():obj.hide_render=hidden
        scene.camera=original_camera
        scene.render.use_compositing=original_compositing
        restore_image_format(scene.render.image_settings,original_format)
    if preview_identity(material)!=baseline_identity or geometry_identity(c)!=geometry:raise RuntimeError('Diagnostic source material or geometry restoration failed')
    verify_inputs(proof['inputs'])
    result={'schema_version':1,'kind':'unaccepted-native-camera-diagnostics','accepted':False,'source_saved':False,'source_sha256':proof['source_sha256'],
        'baseline_preview_identity_unchanged':True,'source_geometry_restored':True,'original_input_hashes_unchanged':True,'views':records}
    save(output/'result.json',result)


def main():
    if '--' in sys.argv:
        args=sys.argv[sys.argv.index('--')+1:]
        if len(args)!=1:raise ValueError('Background diagnostic entrypoint expects output with verified-inputs.json')
        blender_capture(Path(args[0]));return
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recipe',type=Path,required=True);parser.add_argument('--candidate',type=Path,required=True)
    parser.add_argument('--original-tool-source-root',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--blender',type=Path,required=True)
    args=parser.parse_args();c,proof=prepare(args.recipe,args.candidate,args.original_tool_source_root,args.output)
    argv=[str(args.blender),'--background','--factory-startup','--disable-autoexec',c['_source'],'--python-exit-code','1','--python',str(Path(__file__).resolve()),'--',str(args.output.resolve())]
    options={'creationflags':subprocess.CREATE_NO_WINDOW} if sys.platform=='win32' else {}
    try:
        with (args.output/'blender.log').open('w',encoding='utf-8') as log:subprocess.run(argv,stdout=log,stderr=subprocess.STDOUT,check=True,**options)
    finally:verify_inputs(proof['inputs'])
    result=read(args.output/'result.json')
    recipe=read(args.recipe)
    if recipe.get('crop'):
        from PIL import Image
        for view in result['views']:
            target=Path(view['PNG']).with_name(Path(view['PNG']).stem+'_crop.png')
            with Image.open(view['PNG']) as image:image.crop(recipe['crop']).save(target)
            view['crop']={'box':recipe['crop'],'PNG':str(target),'sha256':sha(target)}
        save(args.output/'result.json',result)


if __name__=='__main__':main()
