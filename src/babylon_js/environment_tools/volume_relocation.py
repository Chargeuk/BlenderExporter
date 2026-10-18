"""Path-only packaging of an accepted volume; never recapture or relabel a bake."""
import copy
import hashlib
import json
import os
from pathlib import Path


def scene_identity(context):
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
        materials[material.name] = dict(graph=preview_identity(material) if material.use_nodes else None,
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
        for i in sorted(bpy.data.images,key=lambda i:i.name) if i.source=='FILE']
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


def relocate(config_path):
    """Validate the original, save one path-only candidate, and bind its receipt."""
    import bpy
    import shutil
    from common import sha,save_json
    from babylon_js.environment_lighting_volume import validate_record,validate_source
    if not bpy.app.background:raise ValueError('Relocation requires disposable background Blender')
    cfg=json.loads(Path(config_path).read_text(encoding='utf-8-sig'))
    root=Path(cfg['package_root']).resolve();source_receipt=Path(cfg['source_receipt']).resolve()
    original=validate_record(json.loads(source_receipt.read_text(encoding='utf-8-sig')))
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
    save_json(receipt.parent/'relocation_provenance.json',proof)
    record['relocation']=dict(version=1,originalReceipt='original_volume_receipt.json',originalReceiptSha256=sha(source_receipt),
        proof='relocation_provenance.json',proofSha256=sha(receipt.parent/'relocation_provenance.json'))
    save_json(receipt,record)
    validate_source(bpy.context,record,receipt_path=receipt)
    print('VOLUME_RELOCATION_READY '+str(receipt),flush=True)
