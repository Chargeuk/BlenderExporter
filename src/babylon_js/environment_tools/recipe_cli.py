"""Config-driven preparation in disposable Blender processes and stage checks.

Run this installed file as --python recipe_cli.py -- COMMAND --config CONFIG OUT.
Project scripts can call main(COMMAND, CONFIG, argv) to keep their old CLI.
"""
import argparse
import copy
import hashlib
import io
import json
import math
from pathlib import Path
import shutil
import sys

# Existing environment worker helpers use local imports for standalone execution.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import sha


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + '\n', encoding='utf8')


def require(condition, message):
    if not condition: raise ValueError(message)


def validate_volume_export_routes(options):
    """Alternate receipt/completion contracts cannot both own one export."""
    separate = options.get('volume_export_route') == 'separate-source-v1'
    deferred = options.get('deferred_volume')
    selected_deferred = isinstance(deferred, dict) and bool(deferred)
    require(not (separate and selected_deferred),
            'Choose one volume export route: separate-source-v1 or deferred_volume, not both')


def package_path(root, relative):
    path = (root / relative).resolve()
    require(path.is_relative_to(root) and path.is_file(), 'Missing packaged dependency: ' + str(path))
    return path


def fresh_output(root, out):
    out = out.resolve()
    require(not out.exists() and not out.is_relative_to(root) and not root.is_relative_to(out),
            'Use a new scratch output outside the retained package')
    return out


def background_source(config_path, source_key):
    import bpy
    from babylon_js import bl_info
    require(tuple(bl_info['version']) >= (3,3,29), 'Install Babylon exporter 3.3.29 or newer')
    require(bpy.app.background, 'Use a disposable background Blender process')
    config = read(config_path)
    root = Path(config.get('package_root', Path(config_path).resolve().parent)).resolve()
    source = package_path(root, config[source_key])
    require(Path(bpy.data.filepath).resolve() == source, 'Open the source declared in the configuration')
    expected = config.get('source_sha256')
    if expected: require(sha(source) == expected, 'Source identity mismatch')
    for key, value in config.get('preparation', {}).get('scene_properties', {}).items():
        require(bpy.context.scene.get(key) == value, 'Scene property mismatch: ' + key)
    return root, config, source, sha(source)


def role_objects(scene, spec):
    if not spec: return []
    if 'names' in spec:
        require(set(spec['names']) <= set(scene.objects.keys()), 'Missing configured objects')
        return [scene.objects[n] for n in spec['names']]
    conditions = spec.get('properties', {spec.get('property'): spec.get('values', [])})
    prefixes = tuple(spec.get('name_prefixes', []))
    return sorted([o for o in scene.objects if o.type == spec.get('type', 'MESH') and
        (not prefixes or o.name.startswith(prefixes)) and
        all(o.get(key) in values for key, values in conditions.items() if key)], key=lambda o:o.name)



def _ordered_physical_slot_plan(manifest, objects, material_graphs, source_hash, geometry_hash):
    """Pure validation; return ordered assignments only after the entire scope passes."""
    def digest(value):
        return isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value)
    require(isinstance(manifest,dict) and set(manifest)=={
        'schema_version','kind','source_sha256','geometry_sha256','material_graph_identities','objects'},
        'Expected complete ordered physical-slot manifest')
    require(type(manifest['schema_version']) is int and manifest['schema_version']==1 and manifest['kind']=='ordered-physical-slot-restoration',
            'Unsupported ordered physical-slot manifest')
    require(digest(source_hash) and manifest['source_sha256']==source_hash,'Physical-slot source identity mismatch')
    require(digest(geometry_hash) and manifest['geometry_sha256']==geometry_hash,'Physical-slot geometry identity mismatch')
    declared=manifest['objects'];graphs=manifest['material_graph_identities']
    require(isinstance(declared,dict) and bool(declared) and set(declared)==set(objects),
            'Ordered physical-slot receiver coverage differs')
    require(isinstance(graphs,dict) and bool(graphs),'Missing physical material graph identities')
    for name,expected in graphs.items():
        require(isinstance(name,str) and bool(name) and digest(expected) and material_graphs.get(name)==expected,
                'Physical material missing or graph identity differs: '+str(name))
    plan={};shared={}
    for name in sorted(declared):
        row=declared[name];actual=objects[name]
        require(isinstance(row,dict) and set(row)=={'slots','polygon_material_indices'},'Invalid ordered slot row: '+name)
        slots=row['slots'];indices=row['polygon_material_indices'];current=actual['slots']
        require(isinstance(slots,list) and bool(slots) and len(slots)==len(current),'Ordered slot count differs: '+name)
        require(isinstance(indices,list) and all(type(v) is int and 0<=v<len(slots) for v in indices)
                and indices==actual['polygon_material_indices'],'Polygon material indices differ: '+name)
        normalized=[]
        for slot,previous in zip(slots,current):
            require(isinstance(slot,dict) and set(slot)=={'name','link'} and slot['link'] in ('DATA','OBJECT'),
                    'Invalid ordered material slot: '+name)
            require(slot['link']==previous['link'],'Material slot link differs: '+name)
            require(isinstance(slot['name'],str) and bool(slot['name']) and slot['name'] in graphs,
                    'Missing declared physical graph for slot: '+name)
            normalized.append(dict(name=slot['name'],link=slot['link']))
        if any(slot['link']=='DATA' for slot in normalized):
            require(not actual.get('outside_data_users'),'Shared material data used outside receiver scope: '+name)
            data=actual['mesh_data'];data_slots=tuple((i,s['name']) for i,s in enumerate(normalized) if s['link']=='DATA')
            require(data not in shared or shared[data]==data_slots,'Conflicting shared mesh physical assignments: '+name)
            shared[data]=data_slots
        plan[name]=normalized
    return plan


def restore_physical_slots(config_path,args):
    """Restore resident physical graphs in a fresh sibling copy; never flatten slots."""
    import bpy
    from babylon_js import bl_info
    from babylon_js.environment_tools.ao import geometry_identity
    from babylon_js.environment_tools.appearance import preview_identity
    from babylon_js.environment_tools.volume_relocation import scene_identity
    from babylon_js.workflow_tools.inspect_blender import inspect_jobs
    require(tuple(bl_info['version'])>=(3,3,45),'Install exporter 3.3.45 ordered-slot restoration')
    root,cfg,source,source_hash=background_source(config_path,'blend')
    require(cfg.get('schema_version')==1 and cfg.get('source_sha256')==source_hash,
            'Ordered-slot restoration requires an exact source hash')
    require(not bpy.data.is_dirty and bpy.context.mode=='OBJECT' and not any(i.is_dirty for i in bpy.data.images),
            'Ordered-slot restoration requires a clean Object-mode source')
    require(not any(inspect_jobs()['native'].values()),'Active Blender job blocks physical-slot restoration')
    reference=cfg.get('ordered_physical_slots')
    require(isinstance(reference,dict) and set(reference)=={'path','sha256'},'Missing hash-bound ordered-slot manifest')
    manifest_path=package_path(root,reference['path'])
    require(sha(manifest_path)==reference['sha256'],'Ordered-slot manifest hash mismatch')
    manifest=read(manifest_path)
    require(isinstance(manifest,dict) and isinstance(manifest.get('material_graph_identities'),dict),
            'Missing physical material graph identities')
    receiver_spec=cfg.get('receivers',{})
    require('names' in receiver_spec and len(receiver_spec['names'])==len(set(receiver_spec['names'])),
            'Ordered-slot restore needs explicit unique receiver names')
    receivers=role_objects(bpy.context.scene,receiver_spec)
    require(receivers and all(o.type=='MESH' for o in receivers),'Physical-slot receivers must be meshes')
    geometry=geometry_identity(cfg)
    materials={};graphs={}
    for name in manifest['material_graph_identities']:
        material=bpy.data.materials.get(name)
        require(material is not None and material.use_nodes and material.node_tree
                and any(n.type=='BSDF_PRINCIPLED' for n in material.node_tree.nodes),
                'Missing resident physical Principled material: '+name)
        materials[name]=material;graphs[name]=preview_identity(material)
    facts={o.name:dict(slots=[dict(name=s.material.name if s.material else None,link=s.link) for s in o.material_slots],
        polygon_material_indices=[p.material_index for p in o.data.polygons],mesh_data=o.data.name,
        outside_data_users=[other.name for other in bpy.data.objects if other.type=='MESH'
            and other.data==o.data and other not in receivers]) for o in receivers}
    plan=_ordered_physical_slot_plan(manifest,facts,graphs,source_hash,geometry['geometry_sha256'])
    target=args.out.resolve();receipt=target.with_suffix('.restoration.json')
    # Sibling output deliberately keeps all image/custom-property relative paths intact.
    require(target.parent==source.parent and target.suffix.lower()=='.blend' and target!=source
            and not target.exists() and not receipt.exists(),'Use a fresh sibling .blend copy and receipt path')
    config_hash=sha(config_path);manifest_hash=sha(manifest_path)
    before=scene_identity(bpy.context)
    def custom_state():
        return json.dumps({kind:{item.name:dict(item.items()) for item in collection}
            for kind,collection in (('scenes',bpy.data.scenes),('worlds',bpy.data.worlds),('objects',bpy.data.objects),
                ('materials',bpy.data.materials),('node_groups',bpy.data.node_groups),('images',bpy.data.images))},
            sort_keys=True,default=str)
    metadata=custom_state()
    quality={scene.name:{key:getattr(scene.cycles,key) for key in
        ('samples','use_adaptive_sampling','use_denoising','diffuse_bounces','max_bounces',
         'sample_clamp_direct','sample_clamp_indirect')} for scene in bpy.data.scenes}
    def quality_state():
        return {scene.name:{key:getattr(scene.cycles,key) for key in quality[scene.name]} for scene in bpy.data.scenes}
    originals={o:tuple(s.material for s in o.material_slots) for o in receivers}
    def original_slots():
        for obj,slots in originals.items():
            for slot,material in zip(obj.material_slots,slots):slot.material=material
    def physical_slots():
        for obj in receivers:
            for slot,wanted in zip(obj.material_slots,plan[obj.name]):slot.material=materials[wanted['name']]
    def check_assignments():
        for obj in receivers:
            require([dict(name=s.material.name if s.material else None,link=s.link) for s in obj.material_slots]==plan[obj.name]
                    and [p.material_index for p in obj.data.polygons]==facts[obj.name]['polygon_material_indices'],
                    'Ordered assignment verification failed: '+obj.name)
        require(geometry_identity(cfg)==geometry and custom_state()==metadata and quality_state()==quality,'Physical restore changed protected scene data')
    try:
        physical_slots();check_assignments()
        # Existing full scene identity covers all meshes, both UVs, CORNER normals,
        # graphs, slots, images, transforms, parents, visibility, World and cameras.
        original_slots()
        require(scene_identity(bpy.context)==before and custom_state()==metadata,'Physical restore changed more than slot materials')
        physical_slots();check_assignments()
        result=bpy.ops.wm.save_as_mainfile(filepath=str(target),relative_remap=False,check_existing=True)
        require('FINISHED' in result and target.is_file(),'Physical restored copy was not saved')
    finally:
        original_slots()
    require(scene_identity(bpy.context)==before and custom_state()==metadata and quality_state()==quality,'Original slot/protected state restoration failed')
    require(sha(source)==source_hash and sha(config_path)==config_hash and sha(manifest_path)==manifest_hash,
            'Physical restoration input bytes changed')
    result=dict(schema_version=1,kind='ordered-physical-slot-restoration',accepted=False,
        source=str(source),source_sha256=source_hash,source_unchanged_on_disk=True,
        config=dict(path=str(config_path),sha256=config_hash),manifest=dict(path=str(manifest_path),sha256=manifest_hash),
        output=dict(path=str(target),sha256=sha(target)),geometry=geometry,receivers=len(receivers),
        ordered_physical_assignments=plan,polygon_material_indices_preserved=True,
        protected_scene_identity=before,all_non_assignment_state_preserved=True,
        relative_paths_preserved_by_sibling_output=True,resident_physical_material_graphs=graphs,
        original_slots_restored_in_disposable_process=True,
        background_full_UI_restoration_claim=False,
        note='Editable physical copy only; no new native transport, capture or export authority. Source associations are retained history, not acceptance for this differently assigned copy.')
    write(receipt,result)
    print('ORDERED_PHYSICAL_SLOTS_RESTORED '+str(receipt),flush=True)


def physical(config_path, args):
    import bpy
    from babylon_js.environment_tools.image_binding import replace_from_disk
    from babylon_js.lighting_controls import update_preview_resolution, validate_preview_resolution
    root, cfg, source, source_hash = background_source(config_path, 'blend')
    policy = cfg['preparation']; out = fresh_output(root, args.out)
    require(str(args.size) in policy['profiles'], 'Size needs an explicit preparation profile')
    profile = policy['profiles'][str(args.size)]
    require(not args.reuse_current or args.size == cfg['size'], 'Reuse requires the original atlas resolution')
    generated = {cfg['images'][key] for key in policy['generated_images']}
    sources = set(cfg['source_images']) | {row['path'] for row in cfg.get('downloads', [])}
    require(not sources & generated, 'Physical source images overlap generated masters')
    for rel in sources: package_path(root, rel)
    for row in cfg.get('downloads', []):
        require(sha(root / row['path']) == row['sha256'], 'Downloaded source identity mismatch: ' + row['path'])
    if args.reuse_current:
        # Reuse guards are project data; fresh preparation never inherits old ownership.
        reuse = policy['reuse']
        for row in reuse['dependencies']:
            require(sha(package_path(root,row['path'])) == row['sha256'], 'Reuse identity mismatch: '+row['path'])
        identity = read(package_path(root,reuse['source_geometry']))
        geometry = read(package_path(root,reuse['geometry']))
        require(identity['associated_source_sha256'] == source_hash and identity['uv_comparison']['unchanged'] and
                identity['geometry_normals_transforms_parents_groups_exact'], 'Reuse source/geometry association mismatch')
        require(sha(root/reuse['geometry']) == identity['geometry_reference']['sha256'], 'Reuse geometry reference changed')
        require(geometry['geometry_sha256'] == reuse['geometry_sha256'], 'Reuse geometry differs')
        from babylon_js.ktx_conversion import load_region_policy
        bound = load_region_policy(package_path(root,reuse['region_policy']))
        import numpy as np
        with np.load(bound['ownership_labels'],allow_pickle=False) as archive:
            require(archive.files == ['labels'], 'Reuse ownership archive must contain labels only')
            stream=io.BytesIO(); np.save(stream,archive['labels'])
        require(hashlib.sha256(stream.getvalue()).hexdigest() == bound['ownership_array_npy_sha256'], 'Reuse ownership changed')
        for rel in generated | {cfg['combined_provenance']}: package_path(root,rel)
    out.mkdir(parents=True)
    for rel in sorted(sources | (generated | {cfg['combined_provenance']} if args.reuse_current else set())):
        target=out/rel; require(target.resolve().is_relative_to(out), 'Image path escapes scratch')
        target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(root/rel,target)
        require(sha(target)==sha(root/rel), 'Image copy changed: '+rel)
    if not args.reuse_current:
        for rel in generated:
            target=out/rel; require(target.resolve().is_relative_to(out), 'Generated path escapes scratch')
            target.parent.mkdir(parents=True,exist_ok=True)
            image=bpy.data.images.new('TEMP preflight placeholder',args.size,args.size,alpha=True,float_buffer=True)
            image.colorspace_settings.name='Linear Rec.709';image.filepath_raw=str(target);image.file_format='OPEN_EXR'
            image.save();bpy.data.images.remove(image)
    for image in list(bpy.data.images):
        if image.source!='FILE' or not image.filepath or image.packed_file: continue
        old=Path(bpy.path.abspath(image.filepath,library=image.library)).resolve()
        require(old.is_relative_to(root), 'Image reference escapes retained package: '+str(old))
        rel=old.relative_to(root).as_posix()
        require(rel in sources | generated, 'Declare referenced image in build configuration: '+rel)
        replace_from_disk(image,out/rel)
    cfg=copy.deepcopy(cfg);cfg['blend']=policy['output_blend'];cfg['size']=args.size
    cfg.update(copy.deepcopy(profile))
    # Profiles contain only overrides, retain AO distance/enabled from the source recipe.
    cfg['ao']={**read(config_path).get('ao',{}),**profile.get('ao',{})}
    for key in ('delivery_status','preparation_recipe'):cfg.pop(key,None)
    preview=bpy.data.materials[cfg['preview_material']]
    update_preview_resolution(preview);validate_preview_resolution(preview,(args.size,args.size))
    scene=bpy.context.scene
    from babylon_js.environment_tools.ao import geometry_identity
    receivers=role_objects(scene,cfg['receivers']);contributors=role_objects(scene,cfg['contributors']);lights=role_objects(scene,cfg['bake_lights'])
    excluded=[obj.name for obj in receivers+contributors+lights if obj.name not in bpy.context.view_layer.objects]
    require(not excluded, 'Physical participants excluded from active view layer: '+', '.join(sorted(excluded)))
    original_geometry=geometry_identity(cfg)
    default=bpy.data.materials[scene[policy['default_material_property']]];default.use_fake_user=True
    specials=[];metal_receivers=set(role_objects(scene,cfg.get('metal_receivers')))
    for obj in scene.objects:
        if obj.type=='MESH':
            special=obj.get(policy['override_material_property'])
            if obj in receivers or special:
                require(not special or isinstance(special,str), 'Material override must name a material')
                material=bpy.data.materials[special] if special else default
                require(material.use_nodes and material.node_tree, 'Physical material needs nodes: '+material.name)
                if obj in metal_receivers:
                    require(any(n.type=='BSDF_PRINCIPLED' for n in material.node_tree.nodes), 'Metal receiver requires Principled material')
                material.use_fake_user=True
                if obj.data.users>1:obj.data=obj.data.copy()
                if not obj.material_slots:obj.data.materials.append(material)
                for i,slot in enumerate(obj.material_slots):slot.link='DATA';obj.data.materials[i]=material
                if special and obj in receivers:
                    if 'override_value' in policy:
                        require(any(n.type=='VALUE' and n.outputs and abs(float(n.outputs[0].default_value)-policy['override_value'])<1e-6
                                    for n in material.node_tree.nodes), 'Physical override value differs: '+obj.name)
                    specials.append(obj.name)
            obj.hide_render=obj not in receivers+contributors
            if obj.name in bpy.context.view_layer.objects:obj.hide_set(obj.hide_render)
        elif obj.type=='LIGHT':obj.hide_render=obj not in lights
    counts={'receivers':len(receivers),'overrides':len(specials),'contributors':len(contributors),'lights':len(lights)}
    for key,count in policy.get('expected_counts',{}).items(): require(counts[key]==count,'Physical selection count mismatch: '+key)
    for rule in policy.get('contributor_checks',[]):
        require(sum(rule['name_contains'].lower() in o.name.lower() for o in contributors)==rule['count'], 'Contributor policy differs')
    require(geometry_identity(cfg)['geometry_sha256']==original_geometry['geometry_sha256'], 'Physical preparation changed geometry')
    for rule in policy.get('light_checks',[]):
        matched=[o for o in lights if o.data.type==rule['type'] and abs(o.data.energy-rule['energy'])<1e-4]
        require(len(matched)==rule['count'],'Physical light settings differ')
    from babylon_js import environment_controls
    controls=environment_controls.find_controls(scene.world)
    require(controls is not None,'Missing supported World controls')
    output=next(n for n in scene.world.node_tree.nodes if n.type=='OUTPUT_WORLD' and n.is_active_output)
    scene.world.node_tree.links.new(controls.outputs[policy.get('world_output','Lighting')],output.inputs['Surface'])
    for owner in (scene,scene.world,*scene.objects):
        for key in policy.get('path_properties',[]):
            old=owner.get(key)
            if not old:continue
            old=Path(bpy.path.abspath(old)).resolve()
            rel=old.relative_to(root).as_posix() if old.is_relative_to(root) else None
            owner[key]='//'+rel if rel in sources|generated else ''
    bpy.context.preferences.filepaths.save_version=0
    bpy.ops.wm.save_as_mainfile(filepath=str(out/cfg['blend']),relative_remap=True)
    write(out/'environment-build.json',cfg)
    receipt=dict(source=source.name,source_sha256=source_hash,snapshot_sha256=sha(out/cfg['blend']),
        requires_full_bake=not args.reuse_current,placeholders=[] if args.reuse_current else sorted(generated),
        reason='Matched reuse inputs copied' if args.reuse_current else 'Sized placeholders are preflight only; run a full bake for new matched ownership and masters',
        geometry_sha256=original_geometry['geometry_sha256'],counts=counts,overrides=specials,bake_only_contributors=[o.name for o in contributors],bake_lights=[o.name for o in lights])
    write(out/('REUSE_CURRENT_MAPS.json' if args.reuse_current else 'REQUIRES_FULL_BAKE.json'),receipt)
    require(sha(source)==source_hash,'Retained source changed')
    print('PHYSICAL_PREPARED',out,flush=True)


def albedo(config_path,args):
    import bpy
    import numpy as np
    from babylon_js import colour_adjustment
    from babylon_js.agx_lightmap import bake_albedo
    root,cfg,source,source_hash=background_source(config_path,'blend');out=fresh_output(root,args.out)
    for rel in cfg.get('source_images',[]):package_path(root,rel)
    settings=colour_adjustment.settings(bpy.context.scene)
    require(settings is not None,'Enable and bind the authored colour adjustment')
    policy=cfg['preparation'].get('albedo',{})
    if 'strength' in policy:require(settings['strength']==policy['strength'],'Colour adjustment strength differs')
    image=settings['image'];map_path=Path(bpy.path.abspath(image.filepath,library=image.library)).resolve()
    if policy.get('image'):require(map_path==package_path(root,policy['image']),'Colour adjustment binding differs')
    require(image.colorspace_settings.name=='sRGB' and image.alpha_mode=='CHANNEL_PACKED','Colour adjustment must be sRGB with channel packed alpha')
    receivers=role_objects(bpy.context.scene,cfg['receivers'])
    require(bool(receivers),'No albedo receivers')
    expected=cfg['preparation'].get('expected_counts',{}).get('receivers')
    if expected is not None:require(len(receivers)==expected,'Albedo receiver count differs')
    out.mkdir(parents=True);target=out/policy.get('output','albedo_uv_adjusted.exr')
    pixels=bake_albedo(bpy.context,receivers,target,cfg['size'],uv=cfg['uv'],colour_adjustment=settings)
    require(pixels.shape==(cfg['size'],cfg['size'],4) and np.isfinite(pixels).all() and (pixels[...,3]>.5).any(),'Invalid adjusted albedo')
    require(sha(source)==source_hash,'Retained source changed')
    write(out/'ALBEDO_FACTS.json',dict(source=source.name,source_sha256=source_hash,colour_adjustment=str(map_path),
        colour_adjustment_sha256=sha(map_path),strength=settings['strength'],receivers=len(receivers),uv=cfg['uv'],size=cfg['size'],
        albedo_sha256=sha(target),plugin_receipt_sha256=sha(str(target)+'.json'),covered_pixels=int((pixels[...,3]>.5).sum()),all_finite=True,source_saved=False))
    print('ADJUSTED_ALBEDO_PREPARED',out,flush=True)


def resolve_input(root, config, relative):
    binding=next((row for row in config.get('input_bindings',[]) if row['path']==relative),None)
    if binding:
        path=Path(binding['source']).resolve()
        require(path.is_file() and sha(path)==binding['sha256'],'Candidate input identity mismatch: '+relative)
        return path
    return package_path(root,relative)


def candidate_output_records(root, outputs):
    """Read portable/legacy manifest separators without rewriting recorded proof."""
    root = Path(root).resolve()
    require(isinstance(outputs, dict), 'Candidate stage outputs must be an object')
    records, spellings = {}, {}
    for relative, expected in outputs.items():
        require(isinstance(relative, str) and relative and '\0' not in relative,
                'Invalid candidate output path')
        parts = relative.replace('\\', '/').split('/')
        require(all(part and part not in ('.', '..') and ':' not in part for part in parts),
                'Invalid candidate output path: ' + relative)
        canonical = '/'.join(parts)
        folded = canonical.casefold()
        require(folded not in spellings or spellings[folded] == canonical,
                'Case-ambiguous candidate output paths: ' + relative)
        spellings[folded] = canonical
        require(isinstance(expected, str) and bool(expected), 'Invalid candidate output digest')
        require(canonical not in records or records[canonical][1] == expected,
                'Conflicting candidate output aliases: ' + relative)
        path = root.joinpath(*parts).resolve()
        require(path.is_relative_to(root) and path.is_file(),
                'Missing/escaping candidate output: ' + relative)
        records[canonical] = (path, expected)
    return records


def check_candidate_geometry(manifest_path,source_hash,manifest=None):
    """Use the physical preparation receipt and this build's own geometry at its bake size."""
    manifest_path=Path(manifest_path);manifest=manifest if manifest is not None else read(manifest_path)
    sources=[Path(key) for key in manifest['inputs'] if key.endswith('.blend')]
    require(len(sources)==1,'Ambiguous candidate physical source')
    receipt=read(sources[0].parent/'REQUIRES_FULL_BAKE.json')
    outputs=candidate_output_records(manifest_path.parent,manifest['stages']['geometry']['outputs'])
    require('geometry/result.json' in outputs, 'Missing candidate geometry output')
    geometry_path,expected=outputs['geometry/result.json']
    require(sha(geometry_path)==expected,
            'Fresh candidate geometry output changed')
    geometry=read(geometry_path)
    require(receipt['source_sha256']==source_hash and receipt['snapshot_sha256']==sha(sources[0]),
            'Candidate was not prepared from retained source')
    require(receipt['geometry_sha256']==geometry['geometry_sha256'],
            'Fresh bake geometry differs from verified physical preparation')
    return sources[0],geometry


def check_source_geometry_association(root,config,source_hash):
    """Accepted masters need their retained source association; fresh builds use their own proof."""
    policy=config.get('source_geometry_association')
    if not policy:return None
    if config.get('candidate_build'):
        candidate=config['candidate_build'];manifest_path=Path(candidate['path'])
        require(sha(manifest_path)==candidate['sha256'],'Fresh candidate build manifest changed')
        _,geometry=check_candidate_geometry(manifest_path,source_hash)
        proof=dict(mode='fresh_candidate',geometry_sha256=geometry['geometry_sha256'])
    else:
        association=read(resolve_input(root,config,policy['source_geometry']))
        require(association['associated_source_sha256']==source_hash and association['uv_comparison']['unchanged'] is True and
                association['geometry_normals_transforms_parents_groups_exact'] is True,
                'Accepted source/geometry association mismatch')
        geometry_path=resolve_input(root,config,association['geometry_reference']['path'])
        require(sha(geometry_path)==association['geometry_reference']['sha256'],'Accepted geometry reference changed')
        geometry=read(geometry_path)
        proof=dict(mode='accepted_maps',geometry_reference_sha256=sha(geometry_path),geometry_sha256=geometry['geometry_sha256'])
    reference=region_policy_reference(config)
    if reference:
        region=read(resolve_input(root,config,reference))
        require(region.get('geometry_sha256')==geometry['geometry_sha256'],
                'Region geometry differs from the associated source geometry')
    return proof


def region_policy_reference(options):
    """The generic option can explicitly disable the retained recipe alias."""
    return options.get('rgbd_region_policy') if 'rgbd_region_policy' in options else options.get('fixture_rgbd_options')


def export_conversion_settings(root, options, colour):
    """Resolve opt-in conversion overrides without changing operator defaults."""
    operator=options.get('preparation',{}).get('operator_settings',{})
    kwargs={}
    reference=region_policy_reference(options)
    if reference:
        kwargs['ktx_lightmap_region_policy']=str(resolve_input(root,options,reference))
    elif 'rgbd_region_policy' in options:
        kwargs['ktx_lightmap_region_policy']=''
    configured=kwargs.get('ktx_lightmap_region_policy',operator.get('ktx_lightmap_region_policy',''))
    enabled=bool(colour) and operator.get('colour_adjustment_enabled',True)
    kwargs['colour_adjustment_enabled']=enabled
    kwargs['colour_adjustment_strength']=colour['strength'] if enabled else 0
    if 'colour_adjustment_size' in options:
        kwargs['colour_adjustment_size']=str(options['colour_adjustment_size'])
    codec=operator.get('colour_adjustment_codec','INHERIT')
    if codec=='INHERIT':codec=operator.get('ktx_codec','basis-lz')
    require(codec in ('basis-lz','uastc'),'Unsupported requested colour adjustment codec')
    return kwargs,dict(regions=bool(configured),colour=enabled,colour_codec=codec if enabled else None)


def candidate_lightmap_size(requested, bake_size):
    """Keep ownership and source radiance at the same or a finer resolution."""
    requested=int(requested)
    require(requested in (0,512,1024,2048) and type(bake_size) is int and bake_size>0,'Invalid candidate lightmap size')
    if not requested or requested<=bake_size:return requested
    # Other authoring sizes use original-size export rather than invent an upscale.
    return bake_size if bake_size in (512,1024,2048) else 0


def refresh_export_preview(scene,options):
    """Rebound source lighting can have a different size from the retained preview."""
    if not options.get('input_bindings'):return None
    import bpy
    from babylon_js.lighting_controls import update_preview_resolution,validate_preview_resolution
    name=options.get('preparation',{}).get('preview_material') or scene.get('bjs_lighting_controls_material')
    if not name:return None
    material=bpy.data.materials.get(name)
    require(material is not None,'Configured managed preview material is missing: '+name)
    # Refresh only after all D/I/IDs/AO inputs have been rebound; mixed sizes fail.
    update_preview_resolution(material)
    size=options.get('candidate_delivery',{}).get('bake_size')
    expected=(size,size) if size else None
    return dict(validate_preview_resolution(material,expected),expected_input_size=list(expected) if expected else None)


def validate_export_glass_rules(rules):
    """Optional native RGB preservation is strict and validated before scene mutation."""
    for rule in rules:
        require(type(rule.get('preserve_rgb', False)) is bool,
                'Glass preserve_rgb must be a boolean')


def prepare_export_glass(materials, rules):
    """Apply existing scalar-alpha compatibility in a disposable export process."""
    validate_export_glass_rules(rules)
    facts = {}
    for rule in rules:
        glass = materials[rule['material']]
        principled = next(n for n in glass.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
        alpha = float(principled.inputs['Alpha'].default_value)
        rgb = tuple(principled.inputs['Base Color'].default_value[:3]) if rule.get('preserve_rgb', False) else (1, 1, 1)
        principled.inputs['Base Color'].default_value = (*rgb, alpha)
        glass.overloadChannels = True
        if hasattr(glass, 'transparencyMode'):
            glass.transparencyMode = str(rule['transparency_mode'])
        facts[glass.name] = alpha
    return facts


def prepare_glass_material(glass, rule):
    """Stage a glass rule in the disposable export copy; omission stays legacy.

    Only an explicit scalar override changes the Principled Alpha input. Linked
    Alpha cannot be safely overridden; reject it before changing any material.
    The caller's saved source is never written by export preparation.
    """
    principled=next(n for n in glass.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
    socket=principled.inputs['Alpha']
    original=float(socket.default_value)
    override='alpha' in rule
    requested=None
    if override:
        value=rule['alpha']
        require(isinstance(value,(int,float)) and not isinstance(value,bool),
                'Glass alpha override must be a finite numeric scalar in [0,1]')
        try:
            requested=float(value)
        except (OverflowError,ValueError):
            raise ValueError('Glass alpha override must be a finite numeric scalar in [0,1]') from None
        require(math.isfinite(requested) and 0<=requested<=1,
                'Glass alpha override must be a finite numeric scalar in [0,1]')
        require(not socket.is_linked, 'Glass alpha override does not support linked Alpha')
        socket.default_value=requested
    staged=float(socket.default_value)
    # Preserve the remote RGB opt-in together with the local scalar-alpha override.
    rgb=tuple(principled.inputs['Base Color'].default_value[:3]) if rule.get('preserve_rgb',False) else (1,1,1)
    principled.inputs['Base Color'].default_value=(*rgb,staged)
    glass.overloadChannels=True
    if hasattr(glass,'transparencyMode'):glass.transparencyMode=str(rule['transparency_mode'])
    return dict(original_alpha=original,staged_alpha=staged,alpha_override=requested,
                override_applied=override,original_alpha_linked=bool(socket.is_linked))


def native_diagnostic_settings(options):
    """Explicit pre-HDR mode; absent opt-in retains the complete legacy contract."""
    require(type(options.get('schema_version')) is int and options['schema_version']==1 and options.get('accepted') is False,
            'Native diagnostic export requires schema1 and accepted:false')
    require(isinstance(options.get('model'),str) and Path(options['model']).name==options['model']
            and Path(options['model']).suffix.lower()=='.babylon','Diagnostic model must be a local .babylon filename')
    native=options.get('native_diagnostic')
    required={'schema_version','native_config','candidate','original_tool_source_root','geometry_sha256','combined_sha256'}
    require(isinstance(native,dict) and set(native) in (required,required|{'appearance_config'})
            and type(native['schema_version']) is int and native['schema_version']==1,
            'Explicit native_diagnostic schema1/source proof required')
    derived='appearance_config' in native
    if derived:
        appearance=native['appearance_config']
        require(isinstance(appearance,dict) and set(appearance)=={'path','sha256'},'Hashed appearance_config required')
    config=native['native_config']
    require(isinstance(config,dict) and set(config)=={'path','sha256'},'Hashed original native_config required')
    require(not {'capture_association','candidate_build','source_geometry_association','input_bindings'}&set(options),
            'Native diagnostic must not fabricate capture or legacy preparation associations')
    require(not {'environment','skybox','spatial_lighting','environment_lighting_volume'}&set(options),
            'Environment output requests belong outside native diagnostic mode')
    ktx=options.get('ktx_options',{})
    require(isinstance(ktx,dict) and not {'environment','skybox','spatial_lighting'}&set(ktx),
            'Native diagnostic forbids ENV, skybox and spatial output')
    require('environment_lighting_volume' in ktx and ktx['environment_lighting_volume'] is None,
            'Explicit environment_lighting_volume:null required; saved volume fallback is forbidden')
    require(ktx.get('convert_materials') is True and ktx.get('lightmap_encoding')=='rgbd-v1'
            and ktx.get('lightmap_agx') is True and ktx.get('auto_lightmap') is False,
            'Native diagnostic requires explicit managed RGBD/AgX conversion')
    if derived:
        strength=ktx.get('colour_adjustment_strength')
        require(ktx.get('colour_adjustment_enabled') is True and type(ktx.get('colour_adjustment_size')) is int
                and ktx['colour_adjustment_size']==512 and ktx.get('colour_adjustment_codec')=='basis-lz'
                and not isinstance(strength,bool) and isinstance(strength,(int,float)) and math.isfinite(strength) and 0<=strength<=1,
                'Derived native appearance requires explicit 512 BasisLZ colour settings/finite strength')
    else:
        require(ktx.get('colour_adjustment_enabled') is False,'Native diagnostic does not introduce colour overlays')
    policy=options.get('preparation',{})
    require(isinstance(policy,dict) and not policy.get('runtime_material_receivers'),
            'Native diagnostic preserves ordered physical material slots')
    require(not policy.get('lights') or policy['lights']=={'names':[]},'Native diagnostic exports no analytic lights')
    require(set(policy.get('world_settings',{}))<={'positionsPrecision','normalsPrecision','UVsPrecision',
            'exportTangents','inlineTextures','usePBRMaterials','writeManifestFile','writeCsvFile'},
            'Only export serialization World settings may change')
    require(isinstance(options.get('expected_sampler_counts'),dict)
            and set(options['expected_sampler_counts'])<= ({'colour','direct','indirect','island_ids','ao','colour_adjustment'} if derived else {'colour','direct','indirect','island_ids','ao'})
            and {'colour','direct','indirect','island_ids'}<=set(options.get('expected_sampler_counts',{}))
            and all(type(v) is int and v>0 for v in options['expected_sampler_counts'].values()),
            'Explicit expected managed sampler-role counts required')
    if derived:require(options['expected_sampler_counts'].get('colour_adjustment')==1,'Derived appearance requires exactly one colour-adjustment sampler')
    return native


def native_diagnostic_plan(config_path):
    """Use original native authority, never a synthetic physical-preparation receipt."""
    from common import load_config
    from native_recombine import validate_original,verify_derived,appearance_settings
    config_path=Path(config_path).resolve();options=read(config_path);native=native_diagnostic_settings(options)
    root=Path(options.get('package_root',config_path.parent)).resolve()
    resolve=lambda value:(root/value).resolve()
    source=package_path(root,options['source']);require(sha(source)==options['source_sha256'],'Source identity mismatch')
    original_config=resolve(native['native_config']['path'])
    require(sha(original_config)==native['native_config']['sha256'],'Original native config changed')
    original_c=load_config(original_config)
    require(Path(original_c['_source']).resolve()==source and not original_c.get('finishing_overrides')
            and not appearance_settings(original_c),'Use the exact immutable original native source/config without finishing overrides')
    candidate=resolve(native['candidate'])
    if 'appearance_config' in native:
        declaration=native['appearance_config'];path=resolve(declaration['path'])
        require(sha(path)==declaration['sha256'],'Appearance recipe changed')
        c=load_config(path)
        require(Path(c['_source']).resolve()==source,'Appearance recipe must retain original physical source')
        proof=verify_derived(c,candidate,resolve(native['original_tool_source_root']))
        require(Path(proof['original_config']).resolve()==original_config and proof['original_config_sha256']==native['native_config']['sha256'],
                'Derived appearance names another original native recipe')
        settings=appearance_settings(c)
        require(settings is not None and options['ktx_options']['colour_adjustment_strength']==settings['strength'],
                'Export colour strength differs from verified appearance')
    else:
        c=original_c;proof=validate_original(c,candidate,resolve(native['original_tool_source_root']))
    require(proof['source_sha256']==options['source_sha256'] and proof['geometry_sha256']==native['geometry_sha256'],
            'Native source/geometry identity differs')
    combined=candidate/'masters/combined.exr'
    require(proof['inputs'].get(str(combined))==sha(combined)==native['combined_sha256'],
            'Combined master lacks matching completed native proof')
    ktx=options['ktx_options']
    require(resolve(ktx['lightmap'])==combined and ktx['lightmap_marker']==options['preparation']['lightmap_marker'],
            'Explicit lightmap/marker differs from completed native master')
    size=int(ktx['lightmap_size'])
    require(size>0 and size<=c['size'],'Native diagnostic cannot upscale the source lightmap')
    proof['inputs'][str(config_path)]=sha(config_path)
    for row in options.get('bound_dependencies',[]):
        path=resolve(row['path']);require(sha(path)==row['sha256'],'Bound export input changed: '+str(path))
        proof['inputs'][str(path)]=row['sha256']
    return root,options,source,c,candidate,proof


def native_export_registration(bpy,module,addon_utils):
    """CLI enabling is distinct from saved preferences; require actual registered RNA."""
    default,loaded=addon_utils.check('babylon_js')
    try:operator=bpy.ops.export.bjs.get_rna_type()
    except Exception as exc:raise ValueError('export.bjs is not actually registered; use --addons before source') from exc
    operator_keys=('filepath','export_selected','convert_to_ktx2','ktx_lightmap','material_metallic_multiplier','environment_image')
    world_keys=('positionsPrecision','normalsPrecision','UVsPrecision','exportTangents','inlineTextures','usePBRMaterials')
    material_keys=('transparencyMode','overloadChannels')
    path=Path(module.__file__).resolve()
    record=dict(default_enabled=bool(default),actually_loaded=bool(loaded),module_path=str(path),module_sha256=sha(path),
        module_version=list(module.bl_info['version']),operator_RNA=operator.identifier,
        operator_properties={k:k in operator.properties for k in operator_keys},
        World_properties={k:k in bpy.types.World.bl_rna.properties for k in world_keys},
        Material_properties={k:k in bpy.types.Material.bl_rna.properties for k in material_keys},
        forced_registration=False,preference_write=False)
    require(loaded is True and path==Path(__file__).resolve().parents[1]/'__init__.py'
            and tuple(module.bl_info['version'])>=(3,3,43) and bool(operator.identifier)
            and all(record['operator_properties'].values()) and all(record['World_properties'].values())
            and all(record['Material_properties'].values()),'Native export requires loaded matching add-on/operator/World/Material RNA')
    return record


def native_diagnostic_output(root,candidate,out):
    out=fresh_output(root,Path(out))
    require(not out.is_relative_to(candidate) and not candidate.is_relative_to(out),
            'Diagnostic output must be separate from the immutable native candidate')
    return out


def native_combined_image(candidate, size):
    """Load an absent, already-verified master; never repair bad resident state."""
    import bpy
    path=(Path(candidate)/'masters/combined.exr').resolve()
    require(path.is_file(),'Verified combined image is missing')
    images=[image for image in bpy.data.images if image.source=='FILE' and image.filepath
            and Path(bpy.path.abspath(image.filepath)).resolve()==path]
    materialized=not images
    if materialized:
        image=bpy.data.images.load(str(path),check_existing=False)
        image.colorspace_settings.name='Linear Rec.709'
        images=[image]
    require(all(image.colorspace_settings.name in ('Non-Color','Linear Rec.709')
                and tuple(image.size)==(size,size) for image in images),
            'Combined interpretation/dimensions differ')
    return dict(path=str(path),materialized=materialized,source_saved=False,
        images=[dict(name=image.name,colour_space=image.colorspace_settings.name,
                     dimensions=list(image.size)) for image in images])


def export_native_diagnostic(config_path,args):
    import bpy
    import addon_utils
    import babylon_js
    from worker import relink,select
    from ao import geometry_identity
    from appearance import preview_identity,verify_combination
    from babylon_js import environment_controls,lightmap_grading
    from babylon_js.lighting_profile import scene_lighting_profile
    from babylon_js.json_exporter import JsonExporter
    from babylon_js.workflow_tools.inspect_blender import snapshot_uv2
    from babylon_js.workflow_tools.native_view_diagnostics import preview_variant,verify_inputs
    root,options,source,c,candidate,proof=native_diagnostic_plan(config_path)
    current_tools={str(p.resolve()):sha(p) for p in Path(__file__).resolve().parents[1].rglob('*.py')}
    proof['inputs'].update(current_tools)  # Current diagnostic tools never replace the frozen original inventory.
    require(bpy.app.background and Path(bpy.data.filepath).resolve()==source and not bpy.data.is_dirty
            and bpy.context.mode=='OBJECT','Native diagnostic requires fresh clean exact source in background Object Mode')
    require(not any(im.is_dirty for im in bpy.data.images) and not any(bpy.app.is_job_running(k) for k in ('RENDER','OBJECT_BAKE','COMPOSITE')),
            'Native diagnostic requires no dirty images or active jobs')
    registration=native_export_registration(bpy,babylon_js,addon_utils)
    out=native_diagnostic_output(root,candidate,args.out);scene=bpy.data.scenes[c['scene']];bpy.context.window.scene=scene
    policy=options['preparation'];receivers=select(c,'receivers');glass=select(c,'glass')
    visible=role_objects(scene,policy['visible']);helpers=role_objects(scene,policy['helpers'])
    require(set(visible)==set(receivers+glass) and not set(visible)&set(helpers),'Visible scope must equal native receivers plus separate glass')
    allowed=set(visible+helpers)
    for obj in tuple(allowed):
        parent=obj.parent
        while parent:allowed.add(parent);parent=parent.parent
    marker=scene.objects[policy['lightmap_marker']];require(marker in allowed,'Missing selected lightmap marker ancestry')
    require(not any(o.type not in ('MESH','EMPTY') or o.get('role')=='EDITABLE_SOURCE_ONLY' for o in allowed),
            'Native export selection contains source geometry, cameras or lights')
    require({o for o in allowed if o.type=='MESH' and o in marker.children_recursive}==set(receivers)
            and not any(o in marker.children_recursive for o in glass),'Lightmap ancestry differs from exact opaque receiver domain')
    snapshot=lambda:snapshot_uv2([o.name for o in receivers],uv=c.get('uv','SimpleBake'),
        expected_uv_order=policy.get('expected_uv_order'),expected_scene=c['scene'])
    before=snapshot();slots={o:tuple(s.material for s in o.material_slots) for o in visible}
    materials={m for row in slots.values() for m in row if m}
    graphs={m:preview_identity(m) for m in materials};world_graph=preview_identity(scene.world)
    counts={'receivers':len(receivers),'visible_meshes':len(visible),'helpers':len(helpers),
            'receiver_triangles':sum(r['evaluated_triangles'] for r in before['objects'])}
    def triangles(obj):
        ev=obj.evaluated_get(bpy.context.evaluated_depsgraph_get());mesh=ev.to_mesh()
        try:mesh.calc_loop_triangles();return len(mesh.loop_triangles)
        finally:ev.to_mesh_clear()
    counts['visible_triangles']=counts['receiver_triangles']+sum(triangles(o) for o in glass)
    require(counts==policy['expected_counts'],'Native diagnostic selection/counts differ')
    original_images={str(Path(bpy.path.abspath(i.filepath)).resolve()):sha(bpy.path.abspath(i.filepath))
        for i in bpy.data.images if i.source=='FILE' and i.filepath and not i.packed_file}
    relink(c,candidate)
    if 'appearance_config' in options['native_diagnostic']:
        from native_recombine import apply_finishing
        c['_candidate']=str(candidate);apply_finishing(c)
    material=bpy.data.materials[c['preview_material']]
    combination=verify_combination(material,candidate/'masters/combined.exr',candidate/'masters/combined.provenance.json')
    preview=preview_identity(material)
    paths={role:str(candidate/'masters'/(role+('_response_processed.exr' if c.get('receiver_response',{}).get('enabled') else '_final.exr'))) for role in ('direct','indirect')}
    with preview_variant(material,candidate,'processed',native_maps=paths) as (_,samplers):
        actual={}
        for row in samplers['bindings'].values():actual[row['role']]=actual.get(row['role'],0)+len(row['samplers'])
        require(actual==options['expected_sampler_counts'],'Managed sampler-role counts differ')
    combined_image=native_combined_image(candidate,c['size'])
    require(environment_controls.find_controls(scene.world) is not None,'Managed World routing required; raw HDR fallback is not a room capture')
    require({k:getattr(scene.view_settings,k) for k in ('view_transform','look','exposure','gamma')}==options['display']
            and not scene.view_settings.use_curve_mapping and scene.display_settings.display_device=='sRGB','Saved display differs')
    require(scene_lighting_profile(bpy.context)==options['lighting_options'],'Saved runtime lighting controls differ')
    controls,_=lightmap_grading.inspect(material);saved_controls={i.name:float(i.default_value) for i in controls.inputs if i.type=='VALUE'}
    old_world={k:getattr(scene.world,k) for k in policy['world_settings']};old_marker=marker.get('bjs_lightmap_image')
    hidden={o:(o.hide_get(),o.hide_viewport) for o in allowed};layers=[];collections={};clones=[]
    needed={col for o in allowed for col in o.users_collection}
    def expose(layer):
        used=layer.collection in needed
        for child in layer.children:used=expose(child) or used
        if used:
            layers.append((layer,layer.exclude,layer.hide_viewport));layer.exclude=False;layer.hide_viewport=False
            col=layer.collection;collections.setdefault(col,col.hide_viewport);col.hide_viewport=False
        return used
    out.mkdir(parents=True);write(out/'native-proof.json',proof);write(out/'managed-samplers.json',samplers)
    serialized_counts=None
    try:
        expose(bpy.context.view_layer.layer_collection)
        for obj in allowed:obj.hide_viewport=False;obj.hide_set(obj not in visible)
        bpy.context.view_layer.update();require(all(o.visible_get() for o in visible),'A delivery mesh remains collection-excluded')
        for key,value in policy['world_settings'].items():setattr(scene.world,key,value)
        marker['bjs_lightmap_image']=str(candidate/'masters/combined.exr')
        glass_facts=[]
        for rule in policy.get('glass',[]):
            original=bpy.data.materials[rule['material']];clone=original.copy();clones.append(clone)
            require(all(o in glass for o,row in slots.items() if original in row),'Glass rule touches an opaque receiver')
            for obj,row in slots.items():
                for index,mat in enumerate(row):
                    if mat==original:obj.material_slots[index].material=clone
            glass_facts.append(dict(material=original.name,**prepare_glass_material(clone,rule)))
        selection=dict(source=str(source),source_sha256=sha(source),selected=[dict(name=o.name,type=o.type,
            role=o.get('role'),parent=o.parent.name if o.parent else None) for o in sorted(allowed,key=lambda o:o.name)],
            counts=counts,runtime_lights=[],registration=registration,source_saved=False,accepted=False)
        write(out/'selection-settings.json',selection)
        if not args.audit_only:
            exporter=JsonExporter();model=out/options['model']
            exporter.execute(bpy.context,str(model),list(allowed),ktx_options=options['ktx_options'],
                material_options=options['material_multipliers'],lighting_options=options['lighting_options'])
            require(not exporter.fatalError and exporter.nErrors==0,exporter.fatalError or 'Native diagnostic export failed')
            report=read(out/(model.stem+'.ktx-report.json'));delivered=read(model)
            require(report.get('status')=='passed' and not delivered.get('lights') and 'environmentTexture' not in delivered
                    and 'kadshowEnvironmentLightingVolume' not in json.dumps(delivered)
                    and not {'environment','skybox','spatial_lighting','environment_lighting_volume'}&set(report),
                    'Native diagnostic unexpectedly exported environment/volume/light output')
            delivered_meshes={row['name']:row for row in delivered.get('meshes',[])}
            require(all(o.name in delivered_meshes and delivered_meshes[o.name].get('isVisible',True) for o in visible),
                    'Delivered visible geometry differs from diagnostic selection')
            serialized_counts={'visible_meshes':len(visible),'visible_triangles':sum(len(delivered_meshes[o.name].get('indices',[]))//3 for o in visible)}
            require(serialized_counts['visible_triangles']==counts['visible_triangles']
                    and all(not delivered_meshes[o.name].get('isVisible',True) for o in helpers if o.type=='MESH'),
                    'Delivered triangle totals or hidden-helper visibility differ')
    finally:
        for obj,row in slots.items():
            for index,mat in enumerate(row):obj.material_slots[index].material=mat
        for clone in clones:bpy.data.materials.remove(clone)
        for key,value in old_world.items():setattr(scene.world,key,value)
        if old_marker is None:marker.pop('bjs_lightmap_image',None)
        else:marker['bjs_lightmap_image']=old_marker
        for obj,(hide,viewport) in hidden.items():obj.hide_viewport=viewport;obj.hide_set(hide)
        for layer,exclude,hide in reversed(layers):layer.exclude=exclude;layer.hide_viewport=hide
        for col,hide in collections.items():col.hide_viewport=hide
        require(snapshot()['objects']==before['objects'] and geometry_identity(c)['geometry_sha256']==proof['geometry_sha256'],
                'Export changed receiver geometry/UVs/normals')
        require(all(tuple(s.material for s in obj.material_slots)==row for obj,row in slots.items()),'Ordered physical slots changed')
        require(all(preview_identity(m)==digest for m,digest in graphs.items()) and preview_identity(scene.world)==world_graph
                and preview_identity(material)==preview,'Export changed source physical/World/preview graphs')
        require(all(sha(p)==digest for p,digest in original_images.items()),'Export changed source image bytes')
        verify_inputs(proof['inputs'])
        if 'appearance_config' in options['native_diagnostic']:
            from native_recombine import restore_appearance
            restore_appearance(c)
    write(out/'STAGE_RESULT.json',dict(schema_version=1,kind='unaccepted-native-diagnostic-export',accepted=False,
        source_saved=False,source_sha256=sha(source),geometry_sha256=proof['geometry_sha256'],audit_only=args.audit_only,
        actual_sampler_counts=actual,saved_controls=saved_controls,combined_verification=combination,combined_image=combined_image,glass_preparation=glass_facts,
        actual_serialized_counts=serialized_counts,
        appearance_colour_adjustment=proof.get('appearance_colour_adjustment'),
        original_native_candidate=proof.get('original_candidate'),
        original_native_inputs_unchanged=True,geometry_UVs_normals_ordered_slots_graphs_unchanged=True,
        background_full_state_restoration_claim=False,
        tools=current_tools,
        limits='Pre-HDR local codec evidence only; no accepted capture/final package/runtime proof. Native ownership is not reconstructed at delivery resolution.'))
    print('NATIVE_DIAGNOSTIC_EXPORT',out,flush=True)


def _digest(value):
    return isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value)


def deferred_volume_plan(root,config,source,source_hash,config_path):
    """Validate the saved volume against the clean source, before export staging."""
    import bpy
    from babylon_js import bl_info
    from babylon_js.environment_lighting_volume import saved_options,validate_record,validate_source
    from babylon_js.environment_tools.volume_relocation import scene_identity
    require(tuple(bl_info['version'])>=(3,3,46),'Install exporter 3.3.46 deferred-volume export')
    require(not bpy.data.is_dirty and bpy.context.mode=='OBJECT' and
            not any(image.is_dirty for image in bpy.data.images),'Deferred volume requires a clean saved Object-mode source')
    require(config.get('source_sha256')==source_hash,'Deferred volume requires the exact source hash')
    declared=config.get('deferred_volume')
    require(isinstance(declared,dict) and set(declared)=={'receipt','master','strength'},'Invalid deferred-volume declaration')
    refs={}
    for key in ('receipt','master'):
        row=declared[key]
        require(isinstance(row,dict) and set(row)=={'path','sha256'} and _digest(row['sha256']),
                'Invalid deferred-volume file binding: '+key)
        path=resolve_input(root,config,row['path'])
        require(sha(path)==row['sha256'],'Deferred-volume input changed: '+key)
        refs[key]=dict(path=str(path),sha256=row['sha256'])
    strength=declared['strength']
    require(type(strength) in (int,float) and math.isfinite(strength) and 0<=strength<=100,
            'Invalid deferred-volume strength')
    saved=saved_options(bpy.context.scene.world)
    require(saved is not None,'Missing saved volume association')
    receipt=Path(bpy.path.abspath(saved['config'])).resolve()
    record=validate_record(read(receipt))
    master=Path(bpy.path.abspath(saved['image'])).resolve() if saved.get('image') else receipt.parent/record['master']
    require(str(receipt)==refs['receipt']['path'] and str(master.resolve())==refs['master']['path'] and
            saved['strength']==strength and type(saved['strength']) in (int,float),
            'Deferred volume differs from saved World association')
    require(record['masterSha256']==refs['master']['sha256'],'Deferred volume master differs from receipt')
    policy=config['preparation'];operator=policy.get('operator_settings',{})
    require(operator.get('export_environment') is True,'Deferred volume requires normal ENV export')
    require(Path(config['model']).name==config['model'],'Deferred export requires a model filename')
    environment=dict(image=str(Path(bpy.path.abspath(bpy.context.scene.world[policy['capture_property']])).resolve()),
        exposure=operator.get('environment_exposure',0),highlight_gain=operator.get('environment_highlight_gain',1),
        highlight_threshold=operator.get('environment_highlight_threshold',1))
    validate_source(bpy.context,record,environment,receipt)
    identity=scene_identity(bpy.context)['sha256']
    return dict(schema_version=1,source=str(source),source_sha256=source_hash,
        config=str(Path(config_path).resolve()),config_sha256=sha(config_path),
        receipt=refs['receipt'],master=refs['master'],strength=strength,
        scene_identity_sha256=identity,environment=environment)


def deferred_operator_call(world,callback):
    """Suppress only saved-volume fallback during disposable runtime staging."""
    key='bjs_environment_lighting_volume_config'
    require(key in world and bool(world[key]),'Missing saved volume association before staging')
    original=world[key]
    try:
        del world[key]
        return callback()
    finally:
        world[key]=original


def _stage_inventory(stage):
    stage=Path(stage).resolve();rows=[]
    for path in sorted(p for p in stage.rglob('*') if p.is_file() and p!=stage/'STAGE_RESULT.json'):
        require(path.resolve().is_relative_to(stage),'Stage asset escapes output folder')
        rows.append(dict(path=path.relative_to(stage).as_posix(),sha256=sha(path),bytes=path.stat().st_size))
    return rows


def validate_deferred_stage(config_path,stage,stage_result_sha256,plan):
    """The caller binds the actual completed inventory hash; it is not a bypass token."""
    stage=Path(stage).resolve();manifest_path=stage/'STAGE_RESULT.json'
    require(_digest(stage_result_sha256) and manifest_path.is_file() and sha(manifest_path)==stage_result_sha256,
            'Deferred stage-result binding differs')
    manifest=read(manifest_path)
    require(manifest.get('kind')=='normal-export-deferred-volume' and manifest.get('audit_only') is False and
            manifest.get('source_saved') is False and manifest.get('volume_complete') is False,
            'Expected a completed deferred normal export')
    require(manifest.get('deferred_volume')==plan,'Deferred source/config/receipt identity differs')
    require(manifest.get('artifacts')==_stage_inventory(stage),'Deferred stage inventory differs')
    config=read(config_path);report_name=Path(config['model']).stem+'.ktx-report.json'
    report=read(stage/report_name);selection=read(stage/'selection-settings.json')
    require(selection.get('deferred_volume')==plan and selection.get('audit_only') is False,
            'Deferred selection binding differs')
    require(report.get('model')==str(stage/config['model']),'Deferred report model path differs')
    require(not report.get('environment_lighting_volume'),'Deferred export already contains a volume report')
    colour=dict(strength=selection.get('colour_adjustment_strength',0)) if selection.get('colour_adjustment_enabled') else None
    root=Path(config.get('package_root',Path(config_path).resolve().parent)).resolve()
    _,checks=export_conversion_settings(root,config,colour)
    verify_integrated_stage(config_path,stage,regions=checks['regions'],colour=checks['colour'],
        expected_colour_codec=checks['colour_codec'])
    files=report.get('delivery_files')
    require(isinstance(files,list) and bool(files),'Missing deferred final-package inventory')
    paths=set()
    for row in files:
        path=Path(row['path']).resolve()
        require(path.is_relative_to(stage) and path.is_file() and path not in paths and
                path.name!=report_name and sha(path)==row.get('sha256') and path.stat().st_size==row.get('bytes'),
                'Deferred delivery asset differs: '+str(path))
        paths.add(path)
    require(stage/config['model'] in paths,'Deferred inventory lacks the model')
    environment=report.get('environment',{})
    require(environment.get('status')=='passed','Deferred export lacks a passed ENV conversion')
    environment_path=(stage/environment.get('output','')).resolve()
    require(environment_path in paths and sha(environment_path)==environment.get('sha256'),
            'Deferred ENV bytes differ from conversion report')
    from babylon_js.environment_lighting_volume import marker_metadata
    marker_metadata(read(stage/config['model']))
    return manifest,report,selection


def attach_volume(config_path,args):
    """Finish an exact normal export on its original clean volume-associated source."""
    import bpy
    from babylon_js.environment_lighting_volume import export_volume
    from babylon_js.environment_tools.volume_relocation import scene_identity
    from babylon_js.ktx_export import final_delivery_files,publish_package
    from babylon_js.ktx_conversion import model_json
    root,config,source,source_hash=background_source(config_path,'source')
    require('native_diagnostic' not in config and 'deferred_volume' in config,'Use an explicitly deferred normal export')
    out=fresh_output(root,args.out);stage=args.stage.resolve()
    require(stage.is_dir() and not stage.is_relative_to(root) and not root.is_relative_to(stage) and
            not out.is_relative_to(stage) and not stage.is_relative_to(out),
            'Use disjoint intermediate and final export folders')
    plan=deferred_volume_plan(root,config,source,source_hash,config_path)
    manifest,report,selection=validate_deferred_stage(config_path,stage,args.stage_result_sha256,plan)
    work=out.with_name(out.name+'.volume-stage')
    require(not work.exists() and not root.is_relative_to(work) and not work.is_relative_to(root) and
            not stage.is_relative_to(work) and not work.is_relative_to(stage),'Use a fresh private volume stage')
    package=work/'package';lineage=work/'lineage';package.mkdir(parents=True);lineage.mkdir()
    report_name=Path(config['model']).stem+'.ktx-report.json'
    for name in ('STAGE_RESULT.json','selection-settings.json',report_name):shutil.copy2(stage/name,lineage/name)
    # Only runtime delivery assets enter the private package. The old report remains lineage.
    for row in report['delivery_files']:
        original=Path(row['path']).resolve();target=package/original.relative_to(stage)
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(original,target)
        require(sha(target)==row['sha256'],'Intermediate delivery copy differs')
    model=read(package/config['model'])
    options=dict(config=plan['receipt']['path'],image=plan['master']['path'],strength=plan['strength'])
    volume=export_volume(bpy.context,model,work,package,options,config['tools']['ktx'],
        config['preparation'].get('operator_settings',{}).get('ktx_threads',8),plan['environment'])
    # The final model stays compact; provenance reports keep the indented writer.
    (package/config['model']).write_text(model_json(model,ensure_ascii=True)+'\n',encoding='utf8')
    identity=scene_identity(bpy.context)['sha256']
    require(identity==plan['scene_identity_sha256'],'Volume attachment changed editable source state')
    require(sha(source)==source_hash and sha(config_path)==plan['config_sha256'] and
            sha(plan['receipt']['path'])==plan['receipt']['sha256'] and sha(plan['master']['path'])==plan['master']['sha256'],
            'Volume attachment changed an original input')
    # Recheck the entire externally bound intermediate after conversion, before publication.
    require(sha(stage/'STAGE_RESULT.json')==args.stage_result_sha256 and manifest['artifacts']==_stage_inventory(stage),
            'Intermediate export changed during volume attachment')
    for row in report['delivery_files']:
        relative=Path(row['path']).resolve().relative_to(stage)
        if relative!=Path(config['model']):require(sha(package/relative)==row['sha256'],'Ordinary/ENV asset changed')
    attachment=dict(schema_version=1,source=str(source),source_sha256=source_hash,config_sha256=sha(config_path),
        stage=str(stage),stage_result_sha256=args.stage_result_sha256,lineage=str(lineage),
        intermediate_report_sha256=sha(lineage/report_name),intermediate_selection_sha256=sha(lineage/'selection-settings.json'),
        receipt=plan['receipt'],master=plan['master'],strength=plan['strength'],scene_identity_sha256=identity,
        source_saved=False)
    selection=copy.deepcopy(selection);selection.pop('deferred_volume');selection['volume_attachment']=attachment
    write(package/'selection-settings.json',selection)
    summary=copy.deepcopy(report);summary['model']=str(out/config['model'])
    summary['environment_lighting_volume']=volume;summary['volume_attachment']=attachment
    summary.setdefault('options',{})['environment_lighting_volume']=options
    # Compute BEFORE the new report exists; never inventory an old report or recurse on its own hash.
    summary['delivery_files']=final_delivery_files(package,out,config['model'])
    summary['delivery_files_proof_stage']='final-package-after-strict-clean-source-volume-attachment'
    write(package/report_name,summary)
    write(package/'STAGE_RESULT.json',dict(schema_version=1,kind='normal-export-with-associated-volume',accepted=False,
        volume_complete=True,source_saved=False,source_sha256=source_hash,volume_attachment=attachment,
        artifacts=_stage_inventory(package)))
    publish_package(package,out,work/'previous_delivery')
    print('VOLUME_ATTACHED',out,flush=True)


def export(config_path,args):
    validate_volume_export_routes(read(config_path))
    if 'native_diagnostic' in read(config_path):
        require('deferred_volume' not in read(config_path),'Native diagnostics cannot defer a normal volume')
        return export_native_diagnostic(config_path,args)
    import bpy
    from babylon_js.colour_adjustment import settings as colour_settings
    from babylon_js.lighting_profile import scene_lighting_profile
    from babylon_js.environment_tools.image_binding import replace_from_disk
    root,options,source,source_hash=background_source(config_path,'source');out=fresh_output(root,args.out)
    deferred=deferred_volume_plan(root,options,source,source_hash,config_path) if 'deferred_volume' in options else None
    policy=options['preparation'];scene=bpy.context.scene
    validate_export_glass_rules(policy.get('glass', []))
    for guard in options.get('bound_dependencies',[]):
        require(sha(resolve_input(root,options,guard['path']))==guard['sha256'],'Export dependency changed: '+guard['path'])
    source_geometry=check_source_geometry_association(root,options,source_hash)
    # Fresh candidate bindings carry their own hashes; accepted paths stay guarded.
    bindings={str((root/row['path']).resolve()):Path(row['source']).resolve() for row in options.get('input_bindings',[])}
    image_hashes={}
    for image in list(bpy.data.images):
        if image.source!='FILE' or not image.filepath or image.packed_file:continue
        path=Path(bpy.path.abspath(image.filepath,library=image.library)).resolve()
        if str(path) in bindings:
            path=bindings[str(path)];replace_from_disk(image,path)
        require(path.is_file(),'Missing export image: '+str(path));image_hashes[str(path)]=sha(path)
    preview_resolution=refresh_export_preview(scene,options)
    for owner in (scene,scene.world,*scene.objects):
        for key in policy.get('path_properties',[]):
            old=owner.get(key)
            if old:
                path=str(Path(bpy.path.abspath(old)).resolve())
                if path in bindings:owner[key]=str(bindings[path])
    association_path=resolve_input(root,options,options['capture_association']);association=read(association_path)
    capture=Path(bpy.path.abspath(scene.world[policy['capture_property']])).resolve()
    require(capture==resolve_input(root,options,association['capture']['path']) and sha(capture)==association['capture']['sha256'],
            'Capture association mismatch')
    require(association['associated_source']['sha256']==source_hash and association['mode']==policy['capture_mode'] and
            association['world_output']==policy['capture_world_output'],'Capture/source association mismatch')
    if options.get('candidate_build'):
        candidate=options['candidate_build']
        require(sha(candidate['path'])==candidate['sha256'],'Fresh candidate build manifest changed')
    profile=scene_lighting_profile(bpy.context);colour=colour_settings(scene)
    runtime=bpy.data.materials[scene[policy['runtime_material_property']]];runtime.use_fake_user=True
    visible=role_objects(scene,policy['visible']);helpers=role_objects(scene,policy['helpers']);lights=role_objects(scene,policy['lights'])
    require(bool(visible),'No runtime meshes selected')
    if policy.get('expected_lights'):
        actual=[dict(name=o.name,type=o.data.type) for o in lights]
        require(actual==policy['expected_lights'],'Runtime light selection differs')
    allowed=set(visible+helpers+lights)
    for obj in tuple(allowed):
        parent=obj.parent
        while parent:allowed.add(parent);parent=parent.parent
    for name in policy.get('required_markers',[]):allowed.add(scene.objects[name])
    bpy.ops.object.select_all(action='DESELECT')
    receivers=role_objects(scene,policy['runtime_material_receivers'])
    for obj in scene.objects:
        if obj in receivers:
            if obj.data.users>1:obj.data=obj.data.copy()
            obj.data.materials.clear();obj.data.materials.append(runtime)
        obj.hide_set(obj not in allowed)
        if obj in allowed:obj.hide_viewport=False;obj.hide_select=False;obj.select_set(True)
        if obj.type=='LIGHT':obj.hide_render=obj not in lights
        if obj in lights and 'serialized_sun_shadow_enabled' in options:
            require(type(options['serialized_sun_shadow_enabled']) is bool,'Runtime shadow policy must be boolean')
            obj['bjs_runtime_shadow_enabled']=options['serialized_sun_shadow_enabled']
    bpy.context.view_layer.objects.active=visible[0]
    world=scene.world
    for key,value in policy.get('world_settings',{}).items():setattr(world,key,value)
    runtime.diffuse_color=tuple(policy.get('runtime_diffuse_color',[1,1,1,1]))
    if hasattr(runtime,'transparencyMode'):runtime.transparencyMode='0'
    glass_facts={};glass_preparation={}
    for rule in policy.get('glass',[]):
        glass=bpy.data.materials[rule['material']]
        staged=prepare_glass_material(glass,rule)
        glass_facts[glass.name]=staged['staged_alpha']
        glass_preparation[glass.name]=staged
    marker=scene.objects[policy['lightmap_marker']]
    require(marker in allowed,'Configured lightmap marker is not selected')
    combined=Path(bpy.path.abspath(marker[policy['lightmap_property']])).resolve()
    require(sha(combined)==options['expected_combined_sha256'],'Combined lightmap identity mismatch')
    require(not set(role_objects(scene,policy['excluded_bake_lights'])) & allowed,'Bake light included in export')
    out.mkdir(parents=True)
    selection=dict(source=source.name,source_sha256=source_hash,selected=[dict(name=o.name,type=o.type,role=o.get('role'),
        parent=o.parent.name if o.parent else None) for o in sorted(allowed,key=lambda o:o.name)],visible_meshes=len(visible),helpers=len(helpers),
        runtime_lights=[o.name for o in lights],lightmap=str(combined),lightmap_marker=marker.name,capture=str(capture),capture_sha256=sha(capture),
        capture_association=str(association_path),capture_association_sha256=sha(association_path),lighting_profile=profile,
        colour_adjustment_enabled=bool(colour),colour_adjustment_strength=colour['strength'] if colour else 0,
        preview_resolution=preview_resolution,source_geometry=source_geometry,
        volume_export_route=options.get('volume_export_route'),
        runtime_multipliers=options['material_multipliers'],glass_alpha=glass_facts,glass_preparation=glass_preparation,
        audit_only=args.audit_only,source_saved=False)
    conversion_kwargs,conversion_checks=export_conversion_settings(root,options,colour)
    selection['conversion']=conversion_checks
    if deferred:selection['deferred_volume']=deferred
    write(out/'selection-settings.json',selection)
    if not args.audit_only:
        tools=options['tools']
        for key in ('ktx','basisu','env_converter'):require(Path(tools[key]).is_file(),'Missing export tool: '+tools[key])
        kwargs=dict(policy['operator_settings'])
        kwargs.update(filepath=str(out/options['model']),export_selected=True,
            material_metallic_multiplier=options['material_multipliers']['metallic'],
            material_roughness_multiplier=options['material_multipliers']['roughness'],
            lighting_mode=profile['mode'],lighting_baked_intensity=profile['bakedIntensity'],lighting_reflection_intensity=profile['reflectionIntensity'],
            lighting_shadow_suppression=profile['shadowSuppression'],lighting_fully_lit_threshold=profile['fullyLitThreshold'],
            lighting_highlight_preservation=profile['highlightPreservation'],lighting_baked_diffuse_preservation=profile['bakedDiffusePreservation'],
            ktx_executable=tools['ktx'],ktx_lightmap=str(combined),ktx_lightmap_size=str(options['lightmap_size']),ktx_lightmap_marker=marker.name,
            basis_executable=tools['basisu'],
            environment_converter=tools['env_converter'],environment_image=str(capture))
        kwargs.update(conversion_kwargs)
        if deferred:
            result=deferred_operator_call(world,lambda:bpy.ops.export.bjs(**kwargs))
        else:
            from volume_assembly import without_saved_volume
            with without_saved_volume(world, options):
                result=bpy.ops.export.bjs(**kwargs)
        require('FINISHED' in result,'Exporter did not finish: '+str(result))
        verify_integrated_stage(config_path,out,regions=conversion_checks['regions'],colour=conversion_checks['colour'],
            expected_colour_codec=conversion_checks['colour_codec'])
        model=read(out/options['model'])
        for row in policy.get('expected_lights',[]) if 'serialized_sun_shadow_enabled' in options else []:
            match=[o for o in model.get('lights',[]) if o['name']==row['name']]
            require(len(match)==1 and match[0].get('shadowEnabled')==options['serialized_sun_shadow_enabled'],'Serialized runtime shadow policy differs')
    require(sha(source)==source_hash and all(sha(path)==digest for path,digest in image_hashes.items()),'Export changed a source input')
    if deferred:
        require(sha(config_path)==deferred['config_sha256'] and sha(deferred['receipt']['path'])==deferred['receipt']['sha256'] and
                sha(deferred['master']['path'])==deferred['master']['sha256'],'Deferred original input changed')
        write(out/'STAGE_RESULT.json',dict(schema_version=1,kind='normal-export-deferred-volume',accepted=False,
            audit_only=args.audit_only,source_sha256=source_hash,capture_sha256=sha(capture),
            volume_complete=False,source_saved=False,deferred_volume=deferred,artifacts=_stage_inventory(out)))
    else:
        write(out/'STAGE_RESULT.json',dict(audit_only=args.audit_only,source_sha256=source_hash,capture_sha256=sha(capture),
            rgbd_regions_integrated=not args.audit_only and conversion_checks['regions'],
            colour_codec_integrated=not args.audit_only and conversion_checks['colour'],source_saved=False,
            artifacts=[dict(path=p.name,sha256=sha(p)) for p in sorted(out.iterdir()) if p.is_file()]))
    print('EXPORT_AUDITED' if args.audit_only else 'EXPORT_STAGE',out,flush=True)


def check_stage_input_identity(root,config,selection,report):
    """Join saved stage inputs to current policy using the exporter's durable source hashes."""
    expected=config.get('expected_combined_sha256')
    if expected:
        lightmap=Path(selection.get('lightmap','')).resolve()
        require(lightmap.is_file() and sha(lightmap)==expected,'Export stage lightmap input changed')
        # AgX reports retain the original linear master; other lightmaps retain it in their texture row.
        agx=report.get('options',{}).get('agx_report',{})
        marker=config.get('preparation',{}).get('lightmap_marker')
        recorded=[agx['source_sha256']] if agx.get('source_sha256') else [row['source_sha256']
            for row in report.get('textures',[]) if row.get('source_sha256') and row.get('lightmap_markers')
            and (not marker or marker in row['lightmap_markers'])]
        require(bool(recorded),'Legacy stage has no durable combined input identity; regenerate the export')
        require(all(digest==expected for digest in recorded),'Export stage combined input identity differs')
    if config.get('capture_association'):
        association=read(resolve_input(root,config,config['capture_association']))
        capture=resolve_input(root,config,association['capture']['path']);expected=association['capture']['sha256']
        require(sha(capture)==expected,'Configured capture input changed')
        require(selection.get('capture_sha256')==expected,'Export stage capture identity differs')


def verify_integrated_stage(config_path,stage,regions=False,colour=False,expected_colour_codec=None):
    config=read(config_path);stage=Path(stage).resolve()
    root=Path(config.get('package_root',Path(config_path).resolve().parent)).resolve()
    for guard in config.get('bound_dependencies',[]):
        require(sha(resolve_input(root,config,guard['path']))==guard['sha256'],'Integrated stage dependency changed: '+guard['path'])
    check_source_geometry_association(root,config,config['source_sha256'])
    report=read(stage/(Path(config['model']).stem+'.ktx-report.json'))
    require(report.get('status')=='passed','Integrated export report did not pass')
    selection=read(stage/'selection-settings.json')
    require(selection['source_sha256']==config['source_sha256'],'Export stage source identity differs')
    check_stage_input_identity(root,config,selection,report)
    for row in report['textures']:
        require(sha(stage/row['output'])==row['output_sha256'],'Export stage texture changed: '+row['output'])
    if regions:
        rows=[row for row in report['textures'] if row.get('encoding')=='rgbd-v1']
        require(len(rows)==1 and rows[0].get('rgbd_region',{}).get('outside_decoded_exact') is True,
                'Export lacks verified integrated region RGBD; regenerate with the shared exporter')
        reference=region_policy_reference(config)
        if reference:
            require(rows[0]['rgbd_region'].get('policy_sha256')==sha(resolve_input(root,config,reference)),
                    'Export region policy identity differs')
    if colour:
        name=Path(config['model']).stem+'_colour_adjustment.ktx2'
        rows=[row for row in report['textures'] if row['output']==name]
        expected_colour_codec=expected_colour_codec or 'uastc'
        require(len(rows)==1 and rows[0]['codec']==expected_colour_codec and rows[0]['mipmaps'] is False,
                'Export colour adjustment does not match the requested codec: '+expected_colour_codec)
    return report


def verify_colour_stage(config_path,stage):
    """Compatibility checks use the same enabled features and codec as normal export."""
    config=read(config_path)
    root=Path(config.get('package_root',Path(config_path).resolve().parent)).resolve()
    selection=read(Path(stage)/'selection-settings.json')
    colour=dict(strength=selection.get('colour_adjustment_strength',0)) if selection.get('colour_adjustment_enabled') else None
    _,checks=export_conversion_settings(root,config,colour)
    return verify_integrated_stage(config_path,stage,regions=checks['regions'],colour=checks['colour'],
        expected_colour_codec=checks['colour_codec'])


def bind_candidate(config_path,args):
    """Bind only completed fresh build outputs, preserving all accepted guards."""
    config=read(config_path);root=Path(config_path).resolve().parent
    build=args.build_candidate.resolve();manifest=read(build/'build.json')
    required={'geometry','bake','ownership','combine','capture'}
    require(required <= set(manifest.get('stages',{})),'Candidate requires completed geometry/bake/ownership/combine/capture')
    for name in required:
        for rel,(path,digest) in candidate_output_records(build,manifest['stages'][name]['outputs']).items():
            require(sha(path)==digest,'Candidate build output changed: '+rel)
    provenance=read(build/'masters/combined.provenance.json')
    require(provenance['combined_sha256']==sha(build/'masters/combined.exr'),'Combined candidate provenance differs')
    require(sha(package_path(root,config['source']))==config['source_sha256'],'Retained source changed')
    prepared_source,geometry=check_candidate_geometry(build/'build.json',config['source_sha256'],manifest)
    out=args.out.resolve();require(not out.exists(),'Use a new candidate configuration filename')
    out.parent.mkdir(parents=True,exist_ok=True)
    association=read(package_path(root,config['capture_association']))
    association['capture']['sha256']=sha(build/'capture/capture.exr')
    association['associated_source']['sha256']=config['source_sha256']
    association['fresh_build_manifest']=dict(path=str(build/'build.json'),sha256=sha(build/'build.json'))
    association_path=out.with_name(out.stem+'.capture-association.json');require(not association_path.exists(),'Candidate association already exists')
    write(association_path,association)
    bake_size=read(prepared_source.parent/'environment-build.json')['size']
    delivery_size=candidate_lightmap_size(config['lightmap_size'],bake_size)
    mapping=config['preparation']['candidate_outputs']
    replacements={rel:build/path for rel,path in mapping.items()}
    replacements[config['capture_association']]=association_path
    reference=region_policy_reference(config)
    if reference:
        region=read(package_path(root,reference))
        region['size']=delivery_size or bake_size
        region.update(ownership_labels=str(build/'ownership/labels.npy'),ownership_labels_sha256=sha(build/'ownership/labels.npy'),
            ownership_array_npy_sha256=sha(build/'ownership/labels.npy'),islands=str(build/'bake/islands.json'),
            islands_sha256=sha(build/'bake/islands.json'),geometry_reference=str(build/'geometry/result.json'),geometry_sha256=geometry['geometry_sha256'])
        region_path=out.with_name(out.stem+'.rgbd-regions.json');require(not region_path.exists(),'Candidate region policy already exists');write(region_path,region)
        replacements[reference]=region_path
    bindings=[dict(path=rel,source=str(path),sha256=sha(path)) for rel,path in replacements.items()]
    result=copy.deepcopy(config);result['package_root']=str(root);result['input_bindings']=bindings
    result['expected_combined_sha256']=sha(build/'masters/combined.exr')
    result['lightmap_size']=str(delivery_size)
    result['candidate_delivery']=dict(bake_size=bake_size,lightmap_size=delivery_size,
        output_size=delivery_size or bake_size,ownership_resampling='same size or center-sample downsample')
    for row in result['bound_dependencies']:
        if row['path'] in replacements:row['sha256']=sha(replacements[row['path']])
    result['candidate_build']=dict(path=str(build/'build.json'),sha256=sha(build/'build.json'),accepted=False)
    write(out,result);print('CANDIDATE_EXPORT_CONFIG',out,flush=True)


def main(command=None,config_path=None,argv=None):
    argv=argv if argv is not None else sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else sys.argv[1:]
    parser=argparse.ArgumentParser(description=__doc__)
    if command is None:
        parser.add_argument('command',choices=('physical','restore-physical-slots','albedo','export','attach-volume','verify-regions','verify-colour','bind-candidate'))
        parser.add_argument('--config',type=Path,required=True)
    chosen=command or (argv[0] if argv else '')
    if chosen in ('physical','restore-physical-slots','albedo','export','attach-volume','bind-candidate'):parser.add_argument('out',type=Path)
    if chosen=='attach-volume':
        parser.add_argument('--stage',type=Path,required=True)
        parser.add_argument('--stage-result-sha256',required=True)
    if chosen=='physical':
        parser.add_argument('--size',type=int,default=512);parser.add_argument('--reuse-current',action='store_true')
    if chosen=='export':
        parser.add_argument('--audit-only',action='store_true');parser.add_argument('--postprocess-python',type=Path,help='Deprecated compatibility option; conversion is integrated')
    if chosen=='bind-candidate':parser.add_argument('--build-candidate',type=Path,required=True)
    if chosen=='verify-regions':parser.add_argument('--stage',type=Path,required=True);parser.add_argument('--options',type=Path)
    if chosen=='verify-colour':parser.add_argument('--source-stage',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args(argv);command=command or args.command;config_path=Path(config_path or args.config)
    if command in ('physical','restore-physical-slots','albedo','export','attach-volume','bind-candidate'):globals()[command.replace('-','_')](config_path,args)
    elif command=='verify-regions':
        if args.options:
            config=read(config_path);root=Path(config.get('package_root',config_path.resolve().parent)).resolve()
            reference=region_policy_reference(config)
            require(bool(reference),'--options requires a configured region policy')
            require(args.options.is_file() and sha(args.options)==sha(resolve_input(root,config,reference)),
                    '--options must match the configured region policy')
        verify_integrated_stage(config_path,args.stage,regions=True);print('REGIONS_ALREADY_INTEGRATED',args.stage)
    elif command=='verify-colour':
        verify_colour_stage(config_path,args.source_stage)
        root=Path(config_path).resolve().parent;out=fresh_output(root,args.out)
        require(not out.is_relative_to(args.source_stage.resolve()),'Output cannot be inside source stage')
        shutil.copytree(args.source_stage,out);print('INTEGRATED_COLOUR_STAGE_COPIED',out)


if __name__=='__main__':main()
