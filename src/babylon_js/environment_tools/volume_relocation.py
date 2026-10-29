"""Path-only packaging of an accepted volume; never recapture or relabel a bake."""
import copy
import hashlib
import json
import os
from pathlib import Path


# Optional nonappearance producer policies. Omission preserves every old property.
VOLUME_EXPORT_POLICIES = {
    'simple_divisor_mode': ('bjs_environment_lighting_volume_simple_divisor_mode', ('per-pixel', 'block-4x4')),
    'advanced_alpha_policy': ('bjs_environment_lighting_volume_advanced_alpha_policy', ('source-ceiling',)),
}


def validate_volume_export_options(options):
    """Validate BEFORE any relocation writes, including directory creation."""
    if type(options) is not dict:
        raise ValueError('volume_export_options must be an object')
    if set(options) - set(VOLUME_EXPORT_POLICIES):
        raise ValueError('Unsupported volume export option key')
    for name, value in options.items():
        if type(value) is not str or value not in VOLUME_EXPORT_POLICIES[name][1]:
            raise ValueError('Unsupported volume export option value: ' + name)
    return dict(options)


def validate_saved_export_policies(worlds, proof):
    """Policy state is not appearance; bind it explicitly in relocation proof."""
    present = ('volumeExportOptions' in proof, 'volumeExportPolicyChanges' in proof)
    if not any(present): return  # Old portable receipts remain valid unchanged.
    if not all(present): raise ValueError('Incomplete saved volume export policy proof')
    options = validate_volume_export_options(proof['volumeExportOptions'])
    if options and not worlds: raise ValueError('Saved export policies require a World')
    changes = proof['volumeExportPolicyChanges']
    if type(changes) is not list: raise ValueError('Volume export changes must be a list')
    expected = {(world.name, VOLUME_EXPORT_POLICIES[name][0]): value for world in worlds for name, value in options.items()}
    actual = {}
    for change in changes:
        if type(change) is not dict or set(change) != {'world', 'key', 'previous', 'current'}:
            raise ValueError('Unsupported saved volume export policy change')
        if any(type(change[k]) is not str for k in ('world', 'key', 'current')) or (change['previous'] is not None and type(change['previous']) is not str):
            raise ValueError('Invalid saved volume export policy change types')
        pair = (change['world'], change['key'])
        if pair in actual or pair not in expected or change['current'] != expected[pair]:
            raise ValueError('Saved volume export policy change differs')
        actual[pair] = change['current']
    if actual != expected: raise ValueError('Saved volume export policy proof is incomplete')
    for world in worlds:
        for name, value in options.items():
            if world.get(VOLUME_EXPORT_POLICIES[name][0]) != value:
                raise ValueError('Saved volume export policy changed: ' + name)


def scene_identity(context, *, image_substitutions=None, omitted_images=()):
    """Preserve both editable physical state and the accepted baked preview."""
    import bpy
    import numpy as np
    from common import sha
    from appearance import preview_identity
    from babylon_js import environment_controls
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    meshes = {}
    for mesh in bpy.data.meshes:
        h = hashlib.sha256()
        for values, dtype in (([v.co[:] for v in mesh.vertices], '<f4'),
                              ([n.vector[:] for n in mesh.corner_normals], '<f4'),
                              ([l.vertex_index for l in mesh.loops], '<i4'),
                              ([(p.loop_start,p.loop_total,p.material_index,p.use_smooth) for p in mesh.polygons], '<i4')):
            h.update(np.asarray(values, dtype=dtype).tobytes())
        for uv in mesh.uv_layers:
            h.update(uv.name.encode());h.update(np.asarray([v.uv[:] for v in uv.data],dtype='<f4').tobytes())
        meshes[mesh.name] = h.hexdigest()
    materials = {}
    for material in bpy.data.materials:
        materials[material.name] = dict(graph=preview_identity(material,image_substitutions=image_substitutions) if material.use_nodes else None,
            diffuse=list(material.diffuse_color),metallic=material.metallic,roughness=material.roughness,
            backface=material.use_backface_culling,
            renderMethod=getattr(material,'surface_render_method',None))
    objects = [dict(name=o.name,type=o.type,data=o.data.name if o.data else None,
        parent=o.parent.name if o.parent else None,matrix=[list(row) for row in o.matrix_world],
        hideRender=o.hide_render,hideViewport=o.hide_viewport,
        materials=[slot.material.name if slot.material else None for slot in o.material_slots],
        collections=sorted(c.name for c in o.users_collection)) for o in sorted(bpy.data.objects,key=lambda o:o.name)]
    images = [dict(name=i.name,sha256=sha(Path(bpy.path.abspath(i.filepath)).resolve()),
        colourSpace=i.colorspace_settings.name,alpha=i.alpha_mode,source=i.source)
        for i in sorted(bpy.data.images,key=lambda i:i.name) if i.source=='FILE' and i.name not in omitted_images]
    worlds = {}
    for world in bpy.data.worlds:
        settings=environment_controls.settings(environment_controls.find_controls(world))
        settings=dict(settings,imageSha256=sha(Path(settings['image'])))
        settings.pop('image')
        worlds[world.name]=dict(settings=settings,graph=preview_identity(world))
    scenes = [dict(name=s.name,world=s.world.name if s.world else None,
        camera=s.camera.name if s.camera else None,objects=sorted(o.name for o in s.objects),
        controls=s.get('bjs_lighting_controls_material')) for s in sorted(bpy.data.scenes,key=lambda s:s.name)]
    lights = {light.name:{key:list(getattr(light,key)) if key=='color' else getattr(light,key,None)
        for key in ('type','color','energy','use_shadow','shadow_soft_size','shape','size','size_y','spot_size','spot_blend')}
        for light in bpy.data.lights}
    cameras = {camera.name:{key:getattr(camera,key) for key in ('type','lens','shift_x','shift_y','clip_start','clip_end','sensor_width','sensor_height')}
        for camera in bpy.data.cameras}
    return dict(version=1,sha256=digest(dict(meshes=meshes,materials=materials,objects=objects,images=images,worlds=worlds,scenes=scenes,lights=lights,cameras=cameras)))


def package_paths(record, receipt_path):
    """Portable receipts resolve within one package; legacy paths stay unchanged."""
    if record.get('pathBase') != 'receipt':
        return record['source']
    if receipt_path is None:
        raise ValueError('Portable volume receipt requires its saved receipt path')
    base=Path(receipt_path).resolve().parent
    package_root=Path(record['packageRoot'])
    if package_root.is_absolute():raise ValueError('Portable package root must be receipt-relative')
    root=(base/package_root).resolve()
    if not base.is_relative_to(root):raise ValueError('Portable receipt must be within its declared package')
    source=copy.deepcopy(record['source'])
    for key in ('scene','scopeRecipe','combined','combinedProvenance','reference'):
        path=Path(source[key])
        if path.is_absolute():raise ValueError('Portable volume input must be relative: '+key)
        path=(base/path).resolve()
        if not path.is_relative_to(root):raise ValueError('Portable volume input escapes package: '+key)
        source[key]=str(path)
    return source


def validate_relocation(context, record, receipt_path):
    """Validate the immutable historical receipt and the saved relocation state."""
    import bpy
    from common import sha
    if record.get('pathBase') != 'receipt':return
    source=package_paths(record,receipt_path)
    if Path(bpy.data.filepath).resolve()!=Path(source['scene']):
        raise ValueError('Open the saved source associated with the portable volume')
    base=Path(receipt_path).resolve().parent
    lineage=record.get('relocation',{})
    if lineage.get('version')!=1:raise ValueError('Missing supported volume relocation lineage')
    files={}
    for name in ('originalReceipt','proof'):
        filename=lineage.get(name,'')
        if not filename or Path(filename).name!=filename:raise ValueError('Relocation evidence must be beside receipt')
        path=base/filename
        if not path.is_file() or sha(path)!=lineage.get(name+'Sha256'):raise ValueError('Volume relocation evidence changed: '+name)
        files[name]=json.loads(path.read_text(encoding='utf-8-sig'))
    original=files['originalReceipt'];proof=files['proof']
    expected=copy.deepcopy(original)
    expected['source']=record['source']
    for key in ('pathBase','packageRoot','relocation'):expected[key]=record[key]
    if expected!=record:raise ValueError('Relocation changed accepted volume data')
    for key,value in original['source'].items():
        if key not in ('scene','sceneSha256','scopeRecipe','combined','combinedProvenance','reference','worldControls') and record['source'].get(key)!=value:
            raise ValueError('Relocation changed accepted source identity: '+key)
    for key in ('Rotation','Lighting Strength','Sky Lift','Lift Rolloff','Visible Sky Strength'):
        if record['source']['worldControls'].get(key)!=original['source']['worldControls'].get(key):raise ValueError('Relocation changed accepted World')
    if proof.get('version')!=1 or proof.get('originalSourceSha256')!=original['source']['sceneSha256']:
        raise ValueError('Volume relocation original source lineage changed')
    if proof.get('originalReceiptSha256')!=lineage['originalReceiptSha256'] or proof.get('sourceSha256')!=source['sceneSha256']:
        raise ValueError('Volume relocation source lineage changed')
    if scene_identity(context)!=proof.get('sceneIdentity'):
        raise ValueError('Relocated physical/preview scene state changed')
    # Old proofs lack this optional state. New proofs must match actual saved IDs.
    if 'volumeExportOptions' in proof or 'volumeExportPolicyChanges' in proof:
        validate_saved_export_policies(bpy.data.worlds, proof)
    if 'dependencyCleanup' in proof:
        validate_dependency_cleanup_lineage(record,proof,base)


def validate_dependency_cleanup_config(config):
    """Only explicit, hash-bound image substitutions belong to this operation."""
    keys={'schema_version','package_root','source_receipt','source_sha256',
          'destination_scene','protected_materials','image_remaps'}
    if type(config) is not dict or set(config)!=keys or type(config['schema_version']) is not int or config['schema_version']!=1:
        raise ValueError('Unsupported dependency cleanup config')
    for key in ('package_root','source_receipt','source_sha256','destination_scene'):
        if type(config[key]) is not str or not config[key]:raise ValueError('Invalid dependency cleanup field: '+key)
    names=config['protected_materials']
    if type(names) is not list or not names or any(type(n) is not str or not n for n in names) or len(set(names))!=len(names):
        raise ValueError('Cleanup requires explicit unique protected materials')
    rows=config['image_remaps']
    if type(rows) is not list or not rows:raise ValueError('Cleanup requires explicit image remaps')
    sources=set();targets=set()
    for row in rows:
        if type(row) is not dict or set(row)!={'source','target','source_sha256','target_sha256'}:
            raise ValueError('Invalid image remap record')
        if type(row['source']) is not str or not row['source']:raise ValueError('Invalid image source name')
        remove_only=row['target'] is None
        if remove_only:
            if row['target_sha256'] is not None:raise ValueError('Removal-only images must not declare a target hash')
        elif type(row['target']) is not str or not row['target']:raise ValueError('Invalid image target name')
        if row['source']==row['target'] or row['source'] in sources:raise ValueError('Duplicate/self image remap')
        for key in ('source_sha256','target_sha256'):
            if key=='target_sha256' and remove_only:continue
            if type(row[key]) is not str or len(row[key])!=64 or any(c not in '0123456789abcdef' for c in row[key]):raise ValueError('Invalid image remap hash')
        sources.add(row['source'])
        if not remove_only:targets.add(row['target'])
    if sources & targets:raise ValueError('Image remaps must target retained images, not another removed image')
    return config


def validate_dependency_cleanup_lineage(record,proof,base):
    """Bind the derivative to its unchanged portable parent, never a new capture."""
    from common import sha
    cleanup=proof['dependencyCleanup']
    if type(cleanup) is not dict or cleanup.get('version')!=1:raise ValueError('Unsupported dependency cleanup lineage')
    parents={}
    for key in ('parentReceipt','parentProof'):
        name=cleanup.get(key,'')
        if type(name) is not str or not name or Path(name).name!=name:raise ValueError('Cleanup evidence must be beside receipt')
        path=base/name
        if not path.is_file() or sha(path)!=cleanup.get(key+'Sha256'):raise ValueError('Dependency cleanup parent evidence changed')
        parents[key]=json.loads(path.read_text(encoding='utf-8-sig'))
    parent=parents['parentReceipt'];parent_proof=parents['parentProof']
    if parent.get('pathBase')!='receipt' or parent.get('relocation',{}).get('proofSha256')!=cleanup['parentProofSha256']:
        raise ValueError('Dependency cleanup parent receipt/proof differ')
    if (cleanup.get('sourceSha256')!=parent['source']['sceneSha256'] or
            cleanup.get('sceneIdentityBefore')!=parent_proof.get('sceneIdentity') or
            parent_proof.get('sourceSha256')!=cleanup['sourceSha256'] or
            cleanup.get('canonicalSceneIdentityBefore')!=proof.get('sceneIdentity')):
        raise ValueError('Dependency cleanup identity correspondence differs')
    expected=copy.deepcopy(parent)
    expected['source']['scene']=record['source']['scene']
    expected['source']['sceneSha256']=record['source']['sceneSha256']
    expected['relocation']['proofSha256']=record['relocation']['proofSha256']
    if expected!=record:raise ValueError('Dependency cleanup changed accepted capture inputs or payload')
    if (parent_proof.get('originalSourceSha256')!=proof.get('originalSourceSha256') or
            parent_proof.get('originalReceiptSha256')!=proof.get('originalReceiptSha256')):
        raise ValueError('Dependency cleanup changed original capture lineage')


def cleanup_dependencies(config_path):
    """Retarget declared legacy image nodes and save one audited portable derivative."""
    import bpy
    from common import sha,save_json
    from appearance import preview_identity
    from babylon_js.environment_lighting_volume import validate_record,validate_source
    from babylon_js.workflow_tools.inspect_blender import _render_candidates
    if not bpy.app.background:raise ValueError('Dependency cleanup requires disposable background Blender')
    config_path=Path(config_path).resolve()
    cfg=validate_dependency_cleanup_config(json.loads(config_path.read_text(encoding='utf-8-sig')))
    config_sha=sha(config_path)
    root=Path(cfg['package_root']).resolve();receipt=(root/cfg['source_receipt']).resolve()
    target=(root/cfg['destination_scene']).resolve()
    source=Path(bpy.data.filepath).resolve()
    if (not receipt.is_relative_to(root) or not target.is_relative_to(root) or
            target.parent!=source.parent or target.suffix.lower()!='.blend' or target.exists() or target==source):
        raise ValueError('Use one fresh sibling scene and an existing in-package portable receipt')
    if bpy.data.is_dirty or any(i.is_dirty for i in bpy.data.images) or bpy.context.mode!='OBJECT':
        raise ValueError('Dependency cleanup requires a clean saved Object-mode source')
    if sha(source)!=cfg['source_sha256']:raise ValueError('Dependency cleanup source hash differs')
    record=validate_record(json.loads(receipt.read_text(encoding='utf-8-sig')))
    if record.get('pathBase')!='receipt':raise ValueError('Dependency cleanup requires a validated portable parent')
    validate_source(bpy.context,record,receipt_path=receipt)
    before=scene_identity(bpy.context)
    base=receipt.parent;proof_path=base/record['relocation']['proof']
    parent_receipt_bytes=receipt.read_bytes();parent_proof_bytes=proof_path.read_bytes()
    parent_proof=json.loads(parent_proof_bytes)
    if 'dependencyCleanup' in parent_proof:raise ValueError('Start cleanup from the original portable parent')
    prefix='dependency_cleanup_'+cfg['source_sha256'][:12]
    parent_receipt=base/(prefix+'_receipt.json');parent_proof_path=base/(prefix+'_proof.json')
    if parent_receipt.exists() or parent_proof_path.exists():raise ValueError('Cleanup parent evidence already exists')
    materials=[]
    for name in cfg['protected_materials']:
        material=bpy.data.materials.get(name)
        if material is None or not material.use_nodes:raise ValueError('Missing protected material: '+name)
        materials.append(material)
    # A recipe may add dormant physical roots, but cannot omit visible materials.
    render_names=set()
    for scene in bpy.data.scenes:
        for layer in scene.view_layers:render_names.update(_render_candidates(layer))
    protected_names={material.name for material in materials}
    for name in sorted(render_names):
        for slot in bpy.data.objects[name].material_slots:
            material=slot.material
            if material is not None and material.use_nodes and material.name not in protected_names:
                materials.append(material);protected_names.add(material.name)
    controls={s.get('bjs_lighting_controls_material') for s in bpy.data.scenes if s.get('bjs_lighting_controls_material')}
    if not controls <= set(cfg['protected_materials']):raise ValueError('Protect every active baked-preview material')
    protected_materials={m.name:preview_identity(m) for m in materials}
    protected_worlds={w.name:preview_identity(w) for w in bpy.data.worlds}
    protected_trees=set()
    def trees(tree,seen):
        if tree is None or tree.as_pointer() in seen:return
        seen.add(tree.as_pointer());yield tree
        for node in tree.nodes:
            if node.type=='GROUP':yield from trees(node.node_tree,seen)
    for owner in [*materials,*bpy.data.worlds]:
        for tree in trees(owner.node_tree,set()):protected_trees.add(tree.as_pointer())
    replacements={};sources={};remaps=[]
    for row in cfg['image_remaps']:
        old=bpy.data.images.get(row['source']);new=bpy.data.images.get(row['target']) if row['target'] is not None else None
        bindings=[(old,'source_sha256')]+([(new,'target_sha256')] if row['target'] is not None else [])
        for image,key in bindings:
            if image is None or image.source!='FILE' or image.packed_file or image.is_dirty:
                raise ValueError('Cleanup images must be saved external FILE datablocks')
            path=Path(bpy.path.abspath(image.filepath)).resolve()
            if not path.is_relative_to(root) or sha(path)!=row[key]:raise ValueError('Cleanup image bytes/path differ: '+image.name)
        if new is not None:replacements[old.name]=new
        sources[old.name]=old
        remaps.append(dict(row,sourceFakeUser=bool(old.use_fake_user),
            sourcePath=os.path.relpath(Path(bpy.path.abspath(old.filepath)).resolve(),root).replace('\\','/'),
            targetPath=os.path.relpath(Path(bpy.path.abspath(new.filepath)).resolve(),root).replace('\\','/') if new is not None else None))
    edits=[];seen=set();counts={name:0 for name in sources}
    tree_names={m.node_tree.as_pointer():'material:'+m.name for m in bpy.data.materials if m.use_nodes}
    tree_names.update({tree.as_pointer():'node_group:'+tree.name for tree in bpy.data.node_groups})
    roots=[m.node_tree for m in bpy.data.materials if m.use_nodes]+list(bpy.data.node_groups)
    for root_tree in roots:
        for tree in trees(root_tree,seen):
            for node in tree.nodes:
                if node.type!='TEX_IMAGE' or node.image is None or node.image.name not in sources:continue
                if tree.as_pointer() in protected_trees:raise ValueError('Image remap touches a protected material/World graph')
                name=node.image.name;counts[name]+=1
                if name not in replacements:raise ValueError('Removal-only image still has node users: '+name)
                edits.append((tree,node,name,replacements[name]))
    for name,image in sources.items():
        if image.users!=counts[name]+int(image.use_fake_user):raise ValueError('Unsupported remaining image user: '+name)
    canonical=scene_identity(bpy.context,image_substitutions=replacements,omitted_images=set(sources))
    for tree,node,name,replacement in edits:node.image=replacement
    for name,image in sources.items():
        image.use_fake_user=False
        if image.users:raise ValueError('Image still has users after explicit node retargeting: '+name)
        bpy.data.images.remove(image,do_unlink=False)
    if ({m.name:preview_identity(m) for m in materials}!=protected_materials or
            {w.name:preview_identity(w) for w in bpy.data.worlds}!=protected_worlds):
        raise ValueError('Dependency cleanup changed protected appearance')
    if scene_identity(bpy.context)!=canonical:raise ValueError('Dependency cleanup changed undeclared scene state')
    if sha(source)!=cfg['source_sha256'] or sha(config_path)!=config_sha or receipt.read_bytes()!=parent_receipt_bytes or proof_path.read_bytes()!=parent_proof_bytes:
        raise ValueError('Dependency cleanup changed an original input')
    saved=bpy.ops.wm.save_as_mainfile(filepath=str(target),relative_remap=False,check_existing=True)
    if 'FINISHED' not in saved or not target.is_file() or scene_identity(bpy.context)!=canonical:
        raise ValueError('Dependency cleanup save changed protected state')
    parent_receipt.write_bytes(parent_receipt_bytes);parent_proof_path.write_bytes(parent_proof_bytes)
    proof=copy.deepcopy(parent_proof)
    proof.update(method='audited legacy TEX_IMAGE substitutions and unused image-datablock removal; accepted physical/preview/World and geometry unchanged; no recapture',
        sourceSha256=sha(target),sceneIdentity=canonical)
    proof['dependencyCleanup']=dict(version=1,sourceSha256=cfg['source_sha256'],sceneIdentityBefore=before,
        canonicalSceneIdentityBefore=canonical,parentReceipt=parent_receipt.name,parentReceiptSha256=sha(parent_receipt),
        parentProof=parent_proof_path.name,parentProofSha256=sha(parent_proof_path),
        protectedMaterials=protected_materials,protectedWorlds=protected_worlds,imageRemaps=remaps,
        nodeChanges=[dict(tree=tree_names[t.as_pointer()],node=n.name,source=s,target=r.name) for t,n,s,r in edits],
        configSha256=config_sha)
    record=copy.deepcopy(record)
    record['source']['scene']=os.path.relpath(target,base).replace('\\','/')
    record['source']['sceneSha256']=sha(target)
    save_json(proof_path,proof);record['relocation']['proofSha256']=sha(proof_path);save_json(receipt,record)
    validate_source(bpy.context,record,receipt_path=receipt)
    print('DEPENDENCY_CLEANUP_READY '+str(target),flush=True)


def relocate(config_path):
    """Validate the original, save one path-only candidate, and bind its receipt."""
    import bpy
    import shutil
    from common import sha,save_json
    from babylon_js.environment_lighting_volume import validate_record,validate_source
    if not bpy.app.background:raise ValueError('Relocation requires disposable background Blender')
    cfg=json.loads(Path(config_path).read_text(encoding='utf-8-sig'))
    if type(cfg) is not dict: raise ValueError('Relocation config must be an object')
    # Validate this optional block before all path rebasing/mkdir/copy/save.
    export_options=validate_volume_export_options(cfg.get('volume_export_options', {}))
    policy_changes=[]
    if export_options and not bpy.data.worlds: raise ValueError('Volume export policies require a World')
    for world in bpy.data.worlds:
        for name,value in export_options.items():
            key=VOLUME_EXPORT_POLICIES[name][0]
            previous=world.get(key)
            if previous is not None and not isinstance(previous,str):
                raise ValueError('Previous volume export policy must be text: '+name)
            policy_changes.append(dict(world=world.name,key=key,previous=previous,current=value))
    root=Path(cfg['package_root']).resolve();source_receipt=Path(cfg['source_receipt']).resolve()
    original=validate_record(json.loads(source_receipt.read_text(encoding='utf-8-sig')))
    if 'advanced_alpha_policy' in export_options and 'advanced' not in original:
        raise ValueError('Advanced export policy requires an Advanced receipt')
    if original.get('pathBase') or original.get('relocation'):raise ValueError('Relocate the original accepted capture receipt')
    if bpy.data.is_dirty or any(i.is_dirty for i in bpy.data.images):raise ValueError('Relocation requires a clean saved source')
    if Path(bpy.data.filepath).resolve()!=Path(original['source']['scene']).resolve():raise ValueError('Wrong original source loaded')
    validate_source(bpy.context,original)
    before=scene_identity(bpy.context)
    source_sha=sha(Path(bpy.data.filepath));mapping={str(Path(k).resolve()):(root/v).resolve() for k,v in cfg['path_map'].items()}
    if any(not p.is_relative_to(root) or not p.is_file() for p in mapping.values()):raise ValueError('Missing/escaping clean dependency')
    for old,new in mapping.items():
        if sha(Path(old))!=sha(new):raise ValueError('Relocation dependency bytes changed: '+old)
    target=(root/cfg['destination_scene']).resolve();receipt=(root/cfg['destination_receipt']).resolve()
    if not target.is_relative_to(root) or not receipt.is_relative_to(root) or target.exists() or receipt.exists():raise ValueError('Use fresh scene/receipt paths inside clean package')
    target.parent.mkdir(parents=True,exist_ok=True);receipt.parent.mkdir(parents=True,exist_ok=True)
    relative=lambda path:'//'+os.path.relpath(path,target.parent).replace('\\','/')
    for image in bpy.data.images:
        if image.source=='FILE':
            old=str(Path(bpy.path.abspath(image.filepath)).resolve())
            if old not in mapping:raise ValueError('Unmapped scene image: '+old)
            image.filepath=relative(mapping[old])
    # Only file-valued custom properties are rebased; descriptive history is inert.
    for collection in (bpy.data.scenes,bpy.data.worlds,bpy.data.objects,bpy.data.materials,bpy.data.node_groups):
        for item in collection:
            for key,value in list(item.items()):
                if isinstance(value,str) and value.startswith('//'):
                    old=str(Path(bpy.path.abspath(value)).resolve())
                    if old in mapping:item[key]=relative(mapping[old])
    # These two project-administration fields do not participate in appearance.
    administration=cfg.get('scene_administration',{})
    if set(administration)-{'gym_environment_lighting_volume_status','gym_current_runtime_package'}:
        raise ValueError('Unsupported administrative relocation field')
    changes=[]
    for scene in bpy.data.scenes:
        for key,value in administration.items():
            if not isinstance(value,str) or not value:raise ValueError('Administrative relocation values must be nonempty text')
            if key=='gym_current_runtime_package':
                if not value.startswith('//') or not (target.parent/value[2:]).resolve().is_relative_to(root):
                    raise ValueError('Runtime package association must remain package-relative')
            changes.append(dict(scene=scene.name,key=key,previous=scene.get(key),current=value))
            scene[key]=value
    for world in bpy.data.worlds:
        world['bjs_environment_lighting_volume_config']=relative(receipt)
        world['bjs_environment_lighting_volume_image']=relative(receipt.parent/original['master'])
        world['bjs_environment_lighting_volume_strength']=world.get('bjs_environment_lighting_volume_strength',1)
        for name,value in export_options.items():
            world[VOLUME_EXPORT_POLICIES[name][0]]=value
    # Saving without automatic rebasing preserves the explicit candidate-local paths.
    bpy.ops.wm.save_as_mainfile(filepath=str(target),relative_remap=False,check_existing=True)
    if scene_identity(bpy.context)!=before:raise ValueError('Path-only save changed physical/preview scene state')
    shutil.copyfile(source_receipt,receipt.parent/'original_volume_receipt.json')
    for name in (original['master'],original['advanced']['master'],original['advanced']['provenance']):
        source=source_receipt.parent/name;destination=receipt.parent/name
        if destination.exists():raise ValueError('Relocation output collision: '+name)
        shutil.copyfile(source,destination)
    record=copy.deepcopy(original)
    record['pathBase']='receipt';record['packageRoot']=os.path.relpath(root,receipt.parent).replace('\\','/')
    for key in ('scene','scopeRecipe','combined','combinedProvenance','reference'):
        path=target if key=='scene' else mapping.get(str(Path(original['source'][key]).resolve()))
        if path is None:raise ValueError('Missing source relocation: '+key)
        record['source'][key]=os.path.relpath(path,receipt.parent).replace('\\','/')
    record['source']['sceneSha256']=sha(target)
    world_image=str(Path(original['source']['worldControls']['image']).resolve())
    record['source']['worldControls']['image']=os.path.relpath(mapping[world_image],receipt.parent).replace('\\','/')
    proof=dict(version=1,method='validated dependency-path relocation and explicit administrative metadata updates; no appearance change, bake, capture or volume reprocessing',
        originalSourceSha256=source_sha,sourceSha256=sha(target),originalReceiptSha256=sha(source_receipt),sceneIdentity=before,
        administrativeChanges=changes)
    if export_options:
        proof['volumeExportOptions']=export_options
        proof['volumeExportPolicyChanges']=policy_changes
    save_json(receipt.parent/'relocation_provenance.json',proof)
    record['relocation']=dict(version=1,originalReceipt='original_volume_receipt.json',originalReceiptSha256=sha(source_receipt),
        proof='relocation_provenance.json',proofSha256=sha(receipt.parent/'relocation_provenance.json'))
    save_json(receipt,record)
    validate_source(bpy.context,record,receipt_path=receipt)
    print('VOLUME_RELOCATION_READY '+str(receipt),flush=True)
