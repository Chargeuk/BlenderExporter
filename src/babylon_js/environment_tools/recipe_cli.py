"""Config-driven preparation in disposable Blender processes and stage checks.

Run this installed file as --python recipe_cli.py -- COMMAND --config CONFIG OUT.
Project scripts can call main(COMMAND, CONFIG, argv) to keep their old CLI.
"""
import argparse
import copy
import hashlib
import io
import json
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


def check_candidate_geometry(manifest_path,source_hash,manifest=None):
    """Use the physical preparation receipt and this build's own geometry at its bake size."""
    manifest_path=Path(manifest_path);manifest=manifest if manifest is not None else read(manifest_path)
    sources=[Path(key) for key in manifest['inputs'] if key.endswith('.blend')]
    require(len(sources)==1,'Ambiguous candidate physical source')
    receipt=read(sources[0].parent/'REQUIRES_FULL_BAKE.json')
    geometry_path=manifest_path.parent/'geometry/result.json'
    require(sha(geometry_path)==manifest['stages']['geometry']['outputs']['geometry/result.json'],
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


def export(config_path,args):
    import bpy
    from babylon_js.colour_adjustment import settings as colour_settings
    from babylon_js.lighting_profile import scene_lighting_profile
    from babylon_js.environment_tools.image_binding import replace_from_disk
    root,options,source,source_hash=background_source(config_path,'source');out=fresh_output(root,args.out)
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
    glass_facts=prepare_export_glass(bpy.data.materials, policy.get('glass', []))
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
        runtime_multipliers=options['material_multipliers'],glass_alpha=glass_facts,audit_only=args.audit_only,source_saved=False)
    conversion_kwargs,conversion_checks=export_conversion_settings(root,options,colour)
    selection['conversion']=conversion_checks
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
        for rel,digest in manifest['stages'][name]['outputs'].items():
            require(sha(build/rel)==digest,'Candidate build output changed: '+rel)
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
        parser.add_argument('command',choices=('physical','albedo','export','verify-regions','verify-colour','bind-candidate'))
        parser.add_argument('--config',type=Path,required=True)
    chosen=command or (argv[0] if argv else '')
    if chosen in ('physical','albedo','export','bind-candidate'):parser.add_argument('out',type=Path)
    if chosen=='physical':
        parser.add_argument('--size',type=int,default=512);parser.add_argument('--reuse-current',action='store_true')
    if chosen=='export':
        parser.add_argument('--audit-only',action='store_true');parser.add_argument('--postprocess-python',type=Path,help='Deprecated compatibility option; conversion is integrated')
    if chosen=='bind-candidate':parser.add_argument('--build-candidate',type=Path,required=True)
    if chosen=='verify-regions':parser.add_argument('--stage',type=Path,required=True);parser.add_argument('--options',type=Path)
    if chosen=='verify-colour':parser.add_argument('--source-stage',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args(argv);command=command or args.command;config_path=Path(config_path or args.config)
    if command in ('physical','albedo','export','bind-candidate'):globals()[command.replace('-','_')](config_path,args)
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
