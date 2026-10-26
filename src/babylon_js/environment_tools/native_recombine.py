"""Exact-source native finishing reuse; explicit preview-only colour strength diagnostics."""
import json
import math
from pathlib import Path
import shutil
import numpy as np
from common import sha, save_json
from native_ownership import catalogue_identity, validate_native_ids

COLOUR_STRENGTH='Colour Adjustment Strength'
RANGES={'Direct Strength':(0,100),'Indirect Strength':(0,100),'Shadow Lift':(0,1),
        'AO Strength':(0,1),'AO Contrast':(.05,4),'AO Direct Influence':(0,1),'AO Indirect Influence':(0,1),COLOUR_STRENGTH:(0,1)}
FLAGS={'reuse_lightmaps','recombine','rebake_ao','env_converter'}

APPEARANCE='appearance_colour_adjustment'
APPEARANCE_COPY='appearance/colour_adjustment.png'


def appearance_settings(c):
    """An appearance-only map is not a physical/source_images transport input."""
    if APPEARANCE not in c:return None
    settings=c[APPEARANCE]
    if not isinstance(settings,dict) or type(settings.get('schema_version')) is not int or settings['schema_version']!=1 or type(settings.get('enabled')) is not bool:
        raise ValueError('appearance_colour_adjustment requires schema1 and boolean enabled')
    if not settings['enabled']:
        if set(settings)!={'schema_version','enabled'}:raise ValueError('Disabled appearance map must not carry active settings')
        return None
    required={'schema_version','enabled','image','sha256','size','colour_space','alpha_mode','strength','uv','uv_index','geometry_sha256'}
    if set(settings)!=required:raise ValueError('Appearance map requires exact image/hash/512/interpretation/strength/UV2/geometry declaration')
    for key in ('sha256','geometry_sha256'):
        value=settings[key]
        if not isinstance(value,str) or len(value)!=64 or any(x not in '0123456789abcdef' for x in value):
            raise ValueError('Invalid appearance '+key)
    if type(settings['size']) is not int or settings['size']!=512 or settings['colour_space']!='sRGB' or settings['alpha_mode']!='CHANNEL_PACKED':
        raise ValueError('Appearance map must be 512 sRGB CHANNEL_PACKED')
    strength=settings['strength']
    if isinstance(strength,bool) or not isinstance(strength,(int,float)) or not math.isfinite(strength) or not 0<=strength<=1:
        raise ValueError('Invalid appearance strength')
    if not isinstance(settings['uv'],str) or not settings['uv'] or settings['uv']!=c.get('uv','SimpleBake') or type(settings['uv_index']) is not int or settings['uv_index']!=1:
        raise ValueError('Appearance map requires exact native UV2 name/index1')
    if c.get('physical_colour_adjustment',{}).get('enabled') or COLOUR_STRENGTH in c.get('finishing_overrides',{}):
        raise ValueError('A new appearance map cannot stack with physical tint or a colour-strength override')
    if not isinstance(settings['image'],str) or not settings['image']:raise ValueError('Appearance map requires a saved PNG path')
    path=(Path(c['_root'])/settings['image']).resolve()
    if path in {(Path(c['_root'])/p).resolve() for p in c.get('source_images',[])}:
        raise ValueError('Appearance map must remain outside physical source_images')
    if not path.is_file() or sha(path)!=settings['sha256']:raise ValueError('Appearance map hash is stale')
    # Header validation works in the ordinary CLI without importing Blender/Pillow.
    # A fresh Blender load/read_codes below additionally validates the actual pixels.
    import struct,zlib
    data=path.read_bytes()
    if (len(data)<33 or data[:8]!=b'\x89PNG\r\n\x1a\n' or data[8:16]!=b'\x00\x00\x00\rIHDR'
            or struct.unpack('>I',data[29:33])[0]!=(zlib.crc32(data[12:29])&0xffffffff)):
        raise ValueError('Appearance image is not a valid RGBA8 PNG header')
    if struct.unpack('>IIBBBBB',data[16:29])!=(512,512,8,6,0,0,0):
        raise ValueError('Appearance image must be 512x512 RGBA8 PNG')
    return dict(settings,image=str(path),strength=float(strength))


def restore_appearance(c):
    """Discard only helper-owned disposable graph/image copies, never saved inputs."""
    state=c.pop('_appearance_runtime',None)
    if state is None:return
    import bpy
    from appearance import preview_identity
    c['preview_material']=state['original_material'].name
    if state['association'] is None:bpy.context.scene.pop('bjs_lighting_controls_material',None)
    else:bpy.context.scene['bjs_lighting_controls_material']=state['association']
    bpy.data.materials.remove(state['material'])
    pending=list(state['groups'])
    while pending:
        removable=[tree for tree in pending if tree.users==0]
        if not removable:raise RuntimeError('Disposable appearance groups retained unexpected users')
        for tree in removable:bpy.data.node_groups.remove(tree);pending.remove(tree)
    for image in state['images']:bpy.data.images.remove(image)
    if preview_identity(state['original_material'])!=state['original_preview_sha256']:
        raise RuntimeError('Appearance application changed original managed preview')


def _apply_appearance(c,settings):
    """Attach exactly once on a deep clone in a disposable background process."""
    import bpy
    from babylon_js import colour_adjustment as ca
    from appearance import preview_identity
    from ao import geometry_identity
    from worker import clone_tree,select
    existing=c.get('_appearance_runtime')
    if existing is not None:
        active=ca.settings(bpy.context.scene)
        if (existing['settings']!=settings or c['preview_material']!=existing['material'].name or not active
                or active['strength']!=settings['strength'] or sha(bpy.path.abspath(active['image'].filepath))!=settings['sha256']):
            raise ValueError('Repeated appearance application differs; stacking is forbidden')
        return existing['record']
    if geometry_identity(c)['geometry_sha256']!=settings['geometry_sha256']:
        raise ValueError('Appearance geometry/UV2 identity is stale')
    receivers=select(c,'receivers')
    if not receivers or any(len(o.data.uv_layers)<=1 or o.data.uv_layers[1].name!=settings['uv'] for o in receivers):
        raise ValueError('Appearance receivers must retain configured native UV at index1')
    original=bpy.data.materials.get(c.get('preview_material',''))
    if original is None or bpy.context.scene.get('bjs_lighting_controls_material')!=original.name or ca.settings(bpy.context.scene) is not None:
        raise ValueError('New appearance map requires the original managed preview with no existing overlay')
    groups_before=set(bpy.data.node_groups);images_before=set(bpy.data.images)
    clone=original.copy();clone.use_fake_user=False;state={'original_material':original,'material':clone,'association':bpy.context.scene.get('bjs_lighting_controls_material'),
        'original_preview_sha256':preview_identity(original),'groups':[],'images':[],'settings':settings}
    c['_appearance_runtime']=state
    try:
        for node in clone.node_tree.nodes:
            if node.type=='GROUP' and node.node_tree:node.node_tree=clone_tree(node.node_tree)
        for tree in bpy.data.node_groups:
            if tree not in groups_before:tree.use_fake_user=False
        c['preview_material']=clone.name;bpy.context.scene['bjs_lighting_controls_material']=clone.name
        path=Path(settings['image']);copied=Path(c.get('_candidate',c['_root']))/APPEARANCE_COPY
        if copied.exists():
            if sha(copied)!=settings['sha256']:raise ValueError('Copied appearance map changed')
            path=copied
        image=ca.load_image(path);codes=ca.read_codes(image)
        if (tuple(image.size)!=(512,512) or image.colorspace_settings.name!='sRGB' or image.alpha_mode!='CHANNEL_PACKED'
                or codes.shape!=(512,512,4)):
            raise ValueError('Actual loaded appearance interpretation/dimensions differ')
        ca.attach(bpy.context.scene,image,uv=settings['uv'],strength=settings['strength'])
        state['record']={'schema_version':1,'kind':'disposable-native-appearance-only','image':str(path),'image_sha256':settings['sha256'],
            'geometry_sha256':settings['geometry_sha256'],'uv':settings['uv'],'uv_index':1,'size':512,'colour_space':'sRGB',
            'alpha_mode':'CHANNEL_PACKED','encoding':ca.ENCODING,'strength':settings['strength'],'applications':1,
            'physical_source_unchanged':True,'source_saved':False,'illumination':'Existing white-albedo evaluator bypasses this layer; it remains albedo only'}
        return state['record']
    except Exception:
        state['groups']=[tree for tree in bpy.data.node_groups if tree not in groups_before]
        state['images']=[image for image in bpy.data.images if image not in images_before]
        restore_appearance(c);raise
    finally:
        if '_appearance_runtime' in c:
            state['groups']=[tree for tree in bpy.data.node_groups if tree not in groups_before]
            state['images']=[image for image in bpy.data.images if image not in images_before]


def finishing_overrides(c):
    values=c.get('finishing_overrides',{})
    if not isinstance(values,dict) or set(values)-set(RANGES):
        raise ValueError('finishing_overrides supports only managed strength/lift/AO sockets and explicit appearance-only Colour Adjustment Strength')
    for name,value in values.items():
        low,high=RANGES[name]
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
            raise ValueError('Invalid finishing override: '+name)
    if values.get('AO Strength',0)>0 and not c.get('ao',{}).get('enabled'):
        raise ValueError('AO finishing requires an existing enabled native AO recipe')
    if COLOUR_STRENGTH in values and not c.get('physical_colour_adjustment',{}).get('enabled'):
        raise ValueError('Appearance-only colour diagnostic requires existing enabled immutable physical tint')
    return values


def apply_finishing(c):
    """Only disposable managed preview changes; physical/source files remain exact."""
    values=finishing_overrides(c);appearance=appearance_settings(c)
    if not values and appearance is None:return None
    import bpy
    if not bpy.app.background:raise RuntimeError('Finishing overrides require disposable background Blender')
    appearance_record=_apply_appearance(c,appearance) if appearance else None
    material=bpy.data.materials.get(c.get('preview_material',''))
    if not material or not material.use_nodes:raise ValueError('Missing managed finishing preview material')
    nodes=[node for node in material.node_tree.nodes if node.type=='GROUP' and node.get('bjs_lighting_controls')==1]
    if len(nodes)!=1:raise ValueError('Finishing overrides require exactly one managed lighting control node')
    node=nodes[0];colour=None
    if COLOUR_STRENGTH in values:
        from babylon_js import colour_adjustment as ca
        colour=ca.settings(bpy.context.scene)
        if ca.STRENGTH!=COLOUR_STRENGTH or ca.controls(bpy.context.scene)!=node or colour is None:
            raise ValueError('Colour diagnostic requires existing managed colour_adjustment.controls/settings')
        if colour['strength']!=c['physical_colour_adjustment']['strength']:
            raise ValueError('Original saved preview/physical colour strength differs')
    for name in values:
        socket=node.inputs.get(name)
        if socket is None or socket.is_linked or not isinstance(socket.default_value,(int,float)):
            raise ValueError('Finishing socket is missing, linked or nonscalar: '+name)
    records={}
    for name,value in values.items():
        socket=node.inputs[name];before=float(socket.default_value);socket.default_value=value;effective=float(socket.default_value)
        if not math.isfinite(effective) or not math.isclose(effective,value,rel_tol=1e-6,abs_tol=1e-7):
            raise ValueError('Finishing socket clamped or changed requested value: '+name)
        records[name]={'before':before,'requested':value,'effective':effective}
    return {'schema_version':1,'material':material.name,'node':node.name,'overrides':records,'source_saved':False,
            'appearance_only_colour_diagnostic':colour is not None or appearance is not None,
            'original_physical_colour_adjustment':c.get('physical_colour_adjustment') if colour is not None else None,
            'appearance_colour_adjustment':appearance_record,
            'scope':'Managed finishing only. Appearance map is applied once to a disposable preview, never physical transport. Intrinsic physical tint still requires matching fresh transport/response.'}


def _read(path):return json.loads(Path(path).read_text(encoding='utf8'))


def _tool_relative(path):
    normalized=str(path).replace('\\','/')
    marker='/babylon_js/'
    return 'src/babylon_js/'+normalized.split(marker,1)[1] if marker in normalized else None


def _tool_inventory(tool_root,required):
    tool_root=Path(tool_root).resolve()
    if not required or 'src/babylon_js/environment_tools/worker.py' not in required or 'src/babylon_js/environment_tools/native_ownership.py' not in required:
        raise ValueError('Original native candidate lacks required tool-input proof')
    for path in sorted(tool_root.glob('source-copy-provenance*.json'),reverse=True):
        record=_read(path)
        entries={entry['relative_path']:entry for entry in record.get('final_source_files',[])}
        if all(relative in entries and entries[relative]['sha256']==digest for relative,digest in required.items()):
            checked={}
            for relative,digest in required.items():
                source=(tool_root/relative).resolve()
                if not source.is_relative_to(tool_root) or sha(source)!=digest:
                    raise ValueError('Original retained tool bytes changed: '+relative)
                checked[str(source)]=digest
            checked[str(path)]=sha(path)
            return checked
    raise ValueError('No retained complete source provenance matches original native tool inputs')


def validate_original(c,original_root,tool_root,*,_patch_tool_provenance=None):
    """Read every original input/output before copying; retain the actual native owner labels."""
    finishing_overrides(c);appearance=appearance_settings(c)
    if appearance and any((Path(original_root)/name).exists() for name in ('native_local_patch.provenance.json','native_recombine.provenance.json')):
        raise ValueError('Appearance recombination requires a fresh native original; no patch/recombine chains')
    if (Path(original_root)/'native_local_patch.provenance.json').is_file():
        if _patch_tool_provenance:raise ValueError('Nested patch tool fallback is unsupported')
        from native_local_patch import validate_for_recombine
        return validate_for_recombine(c,original_root,tool_root)
    if (Path(original_root)/'native_recombine.provenance.json').is_file():
        if _patch_tool_provenance:raise ValueError('Nested patch tool fallback is unsupported')
        return _validate_patch_derived(c,original_root,tool_root)
    original=Path(original_root).resolve();build_path=original/'build.json';build=_read(build_path)
    if build.get('schema_version')!=1:raise ValueError('Original native build schema differs')
    native_path=original/'bake/native_ownership.provenance.json';native=_read(native_path)
    original_inputs=build['inputs']
    configs=[Path(path) for path,digest in original_inputs.items() if path not in FLAGS
             and Path(path).suffix.lower()=='.json' and digest==native.get('config_sha256')]
    if len(configs)!=1:raise ValueError('Original native recipe identity is not unique')
    original_config_path=configs[0];original_config=_read(original_config_path)
    trial_config=_read(c['_config'])
    trial_config.pop('finishing_overrides',None)
    trial_config.pop(APPEARANCE,None)
    if APPEARANCE in original_config:raise ValueError('Original native recipe must not be an appearance derivative')
    if trial_config!=original_config:
        raise ValueError('Native recombination permits only finishing_overrides/appearance_colour_adjustment; physical recipe fields changed')
    original_source=(original_config_path.parent/original_config['blend']).resolve()
    if sha(c['_source'])!=sha(original_source) or sha(original_source)!=native.get('source_sha256'):
        raise ValueError('Native recombination requires the exact immutable original saved source')
    for relative in original_config.get('source_images',[]):
        old=(original_config_path.parent/relative).resolve();current=(Path(c['_root'])/relative).resolve()
        if sha(current)!=sha(old):raise ValueError('Native recombination source image changed: '+relative)
    if original_config.get('ownership')!={'schema_version':1,'method':'native-island-id'}:
        raise ValueError('Original candidate must use native-island-id ownership')
    if original_inputs.get('reuse_lightmaps') or original_inputs.get('recombine'):
        raise ValueError('Original candidate must contain freshly measured native transport')
    required=['preflight','geometry','bake','ownership','process','combine']
    response=bool(original_config.get('receiver_response',{}).get('enabled'))
    if response:required+=['response-capture','response']
    if original_config.get('ao',{}).get('enabled'):required+=['ao']
    if any(name not in build.get('stages',{}) for name in required):
        raise ValueError('Original candidate has incomplete native/response/AO stages')
    preserved_tools={_tool_relative(path):digest for path,digest in original_inputs.items()
                     if path not in FLAGS and _tool_relative(path)}
    if _patch_tool_provenance:
        from native_local_patch import verify_tool_provenance
        checked=verify_tool_provenance(tool_root,preserved_tools,_patch_tool_provenance)
    else:checked=_tool_inventory(tool_root,preserved_tools)
    for path,digest in original_inputs.items():
        if path in FLAGS or _tool_relative(path):continue
        if sha(path)!=digest:raise ValueError('Original native input changed: '+path)
        checked[path]=digest
    outputs={}
    for stage in build['stages'].values():
        for relative,digest in stage['outputs'].items():
            path=(original/relative.replace('\\','/')).resolve()
            if not path.is_relative_to(original) or sha(path)!=digest:
                raise ValueError('Original native completed output changed: '+relative)
            outputs[str(path)]=digest
    checked.update(outputs);checked[str(build_path)]=sha(build_path)
    catalogue=_read(original/'bake/islands.json');geometry_path=original/'geometry/result.json';geometry=_read(geometry_path)
    if appearance:
        if appearance['geometry_sha256']!=geometry['geometry_sha256']:raise ValueError('Appearance geometry/UV2 identity differs from native proof')
        checked[appearance['image']]=appearance['sha256']
    expected={'schema_version':1,'method':'native-island-id','kind':'native-island-id-ownership',
        'source_sha256':sha(original_source),'config_sha256':sha(original_config_path),'scene':c.get('scene'),
        'size':c['size'],'uv':c.get('uv','SimpleBake'),'geometry_sha256':geometry['geometry_sha256'],
        'geometry_record_sha256':sha(geometry_path),'catalogue_file_sha256':sha(original/'bake/islands.json'),
        'catalogue_identity_sha256':catalogue_identity(catalogue),'native_ids_sha256':sha(original/'bake/native_island_ids.npy')}
    if any(native.get(key)!=value for key,value in expected.items()):
        raise ValueError('Original native source/config/geometry/UV/catalogue/ID proof differs')
    import hashlib
    if native.get('build_inputs_sha256')!=hashlib.sha256(json.dumps(original_inputs,sort_keys=True,separators=(',',':')).encode()).hexdigest():
        raise ValueError('Original native build-input identity differs')
    for name in ('native_ownership.py','worker.py'):
        relative='src/babylon_js/environment_tools/'+name
        if native.get('helper_sha256' if name=='native_ownership.py' else 'worker_sha256')!=preserved_tools.get(relative):
            raise ValueError('Original native helper/worker proof differs')
    ids=np.load(original/'bake/native_island_ids.npy',allow_pickle=False)
    if ids.shape!=(c['size'],c['size'],4):raise ValueError('Original native sampler dimensions differ')
    raw=[]
    for mode in ('direct','indirect'):
        path=original/'bake'/(mode+'_raw.npy')
        if native.get('raw_sha256',{}).get(mode)!=sha(path):raise ValueError('Original native transport bytes differ')
        raw.append(np.load(path,allow_pickle=False))
    labels,report=validate_native_ids(ids,catalogue,*raw)
    saved_labels=np.load(original/'ownership/labels.npy',allow_pickle=False)
    from reuse import validate_label_schema
    # The measured native labels above already prove the exact raster footprint.
    validate_label_schema(saved_labels,catalogue,catalogue,c['size'])
    if not np.array_equal(labels,saved_labels):
        raise ValueError('Original ownership labels differ from actual native IDs')
    if c.get('ao',{}).get('enabled'):
        validate_native_ids(ids,catalogue,np.load(original/'ao/ao_raw.npy',allow_pickle=False))
    if response:
        capture=_read(original/'response-capture/capture.provenance.json')
        captured_ids=np.load(original/'response-capture/native_island_ids.npy',allow_pickle=False)
        validate_native_ids(captured_ids,_read(original/'response-capture/islands.json'),ids)
        if not np.array_equal(captured_ids,ids):raise ValueError('Original response native owners differ')
        if capture.get('source_sha256')!=native['source_sha256'] or capture.get('config_sha256')!=native['config_sha256']:
            raise ValueError('Original response source/config differs')
    required_masters=['direct_final.exr','indirect_final.exr','island_ids.exr','albedo_uv.exr','albedo_uv.exr.json']
    if response:required_masters += [mode+'_response_'+variant+'.exr' for mode in ('direct','indirect') for variant in ('raw','processed')]
    if c.get('ao',{}).get('enabled'):required_masters+=['ao.exr']
    for name in required_masters:
        if str((original/'masters'/name).resolve()) not in outputs:
            raise ValueError('Original required master lacks completed-output proof: '+name)
    copies=['geometry/result.json','bake/islands.json','ownership/labels.npy','ownership/result.json']
    if response:copies+=['response/result.json']
    copies+=['masters/'+name for name in required_masters]
    result={'schema_version':1,'kind':'exact-source-native-recombine','original_candidate':str(original),
        'original_config':str(original_config_path),'original_config_sha256':sha(original_config_path),
        'source_sha256':native['source_sha256'],'scene':c.get('scene'),'size':c['size'],'uv':c.get('uv','SimpleBake'),
        'geometry_sha256':geometry['geometry_sha256'],'native_ownership_sha256':sha(native_path),
        'catalogue_identity_sha256':report['catalogue_identity_sha256'],'inputs':checked,'copies':copies,
        'finishing_overrides':finishing_overrides(c),'physical_source_unchanged':True,
        'appearance_only_colour_diagnostic':COLOUR_STRENGTH in finishing_overrides(c),
        'original_physical_colour_adjustment':original_config.get('physical_colour_adjustment'),
        'limits':'Measured transport/response/AO retained exactly. Seven finishing scalars plus explicitly appearance-only preview Colour Adjustment Strength may differ; changed intrinsic colour promotion requires fresh matching native transport/response. Native owner labels are never reconstructed from conservative footprints.'}
    if appearance:
        result[APPEARANCE]=appearance
        result['appearance_copy']=APPEARANCE_COPY
        result['appearance_only_colour_diagnostic']=True
        result['limits']+=' New512RGBA colour is appearance-only; original physical source/config/native proof remain unchanged.'
    return result


def copy_verified(plan,root):
    root=Path(root).resolve();original=Path(plan['original_candidate'])
    if root==original or root.is_relative_to(original) or original.is_relative_to(root):
        raise ValueError('Native recombine candidate must be separate from original')
    for relative in plan['copies']:
        source=(original/relative).resolve();destination=(root/relative).resolve()
        if not destination.is_relative_to(root) or sha(source)!=plan['inputs'].get(str(source)):
            raise ValueError('Native recombine copy input differs: '+relative)
        destination.parent.mkdir(parents=True,exist_ok=True)
        if destination.exists():
            if sha(destination)!=sha(source):raise ValueError('Retained recombine copy changed: '+relative)
        else:shutil.copyfile(source,destination)
    if plan.get(APPEARANCE):
        target=root/plan['appearance_copy'];target.parent.mkdir(parents=True,exist_ok=True)
        source=Path(plan[APPEARANCE]['image'])
        if sha(source)!=plan[APPEARANCE]['sha256']:raise ValueError('Appearance map changed before copying')
        if target.exists() and sha(target)!=plan[APPEARANCE]['sha256']:raise ValueError('Existing appearance copy changed')
        if not target.exists():shutil.copyfile(source,target)
        if sha(target)!=plan[APPEARANCE]['sha256']:raise ValueError('Appearance copy differs')
    receipt=root/'native_recombine.provenance.json'
    if receipt.exists():
        if _read(receipt)!=plan:raise ValueError('Native recombine receipt changed')
    else:save_json(receipt,plan)


def verify_copied(c,root):
    root=Path(root);record=_read(root/'native_recombine.provenance.json')
    if (record.get('source_sha256')!=sha(c['_source']) or record.get('finishing_overrides')!=finishing_overrides(c)
            or record.get('size')!=c['size'] or record.get('uv')!=c.get('uv','SimpleBake') or record.get('scene')!=c.get('scene')):
        raise ValueError('Native recombine candidate source/settings identity differs')
    if record.get(APPEARANCE)!=appearance_settings(c):raise ValueError('Native recombine appearance declaration differs')
    if record.get(APPEARANCE) and (record.get('appearance_copy')!=APPEARANCE_COPY or sha(root/APPEARANCE_COPY)!=record[APPEARANCE]['sha256']):
        raise ValueError('Retained appearance map differs')
    for path,digest in record['inputs'].items():
        if sha(path)!=digest:raise ValueError('Original native recombine input changed: '+path)
    for relative in record['copies']:
        original=Path(record['original_candidate'])/relative
        if sha(root/relative)!=sha(original):raise ValueError('Retained native recombine master/labels changed: '+relative)
    return record


def _validate_patch_derived(c,root,tool_root):
    """One ordinary combined derivative of an immutable validated island assembly."""
    from native_local_patch import METHOD
    root=Path(root).resolve();record=_read(root/'native_recombine.provenance.json')
    if record.get('assembly_kind')!=METHOD or finishing_overrides(c) or appearance_settings(c) or record.get('finishing_overrides'):
        raise ValueError('Only exact-source patch-derived candidates without finishing overrides are supported')
    original=Path(record['original_candidate']).resolve()
    if (original==root or original.is_relative_to(root) or root.is_relative_to(original)
            or (original/'native_recombine.provenance.json').exists()
            or not (original/'native_local_patch.provenance.json').is_file()):
        raise ValueError('Patch-derived original must be one separate immutable assembly; no recombine chains')
    verify_copied(c,root)
    expected=validate_original(c,original,tool_root)
    if record!=expected:raise ValueError('Patch-derived receipt differs from authorized immutable assembly plan')
    combined=root/'masters/combined.exr';sidecar=root/'masters/combined.provenance.json';appearance=_read(sidecar)
    required={'schema_version':1,'source_sha256':sha(c['_source']),'combined_sha256':sha(combined),
        'ownership_sha256':sha(root/'ownership/labels.npy'),
        'receiver_response_provenance_sha256':sha(root/'response/result.json'),
        'native_recombine_provenance_sha256':sha(root/'native_recombine.provenance.json'),
        'original_native_candidate':str(original),'finishing_overrides':{}}
    if any(appearance.get(key)!=value for key,value in required.items()):
        raise ValueError('Patch-derived combined master provenance differs')
    inputs=dict(expected['inputs'])
    for path in [root/name for name in expected['copies']]+[root/'native_recombine.provenance.json',combined,sidecar]:
        inputs[str(path.resolve())]=sha(path)
    return dict(expected,original_candidate=str(root),inputs=inputs,
        immutable_assembly_candidate=str(original),fresh_full_room_bake=False,
        limits=expected['limits']+' Combined finishing is an exact-source derivative of that immutable assembly.')


def verify_derived(c,root,tool_root):
    """Verify one complete appearance derivative against a fresh native original.

    This public export gate never changes the source hash in native ownership and
    never treats a copied ID mask as newly measured ownership.
    """
    root=Path(root).resolve();record=_read(root/'native_recombine.provenance.json')
    if not appearance_settings(c) or not record.get(APPEARANCE) or record.get('assembly_kind'):
        raise ValueError('Derived appearance verification requires one explicit appearance-map derivative')
    original=Path(record['original_candidate']).resolve()
    if (original==root or original.is_relative_to(root) or root.is_relative_to(original)
            or (original/'native_recombine.provenance.json').exists()
            or (original/'native_local_patch.provenance.json').exists()):
        raise ValueError('Derived appearance must name one separate fresh native original; no chains')
    verify_copied(c,root)
    expected=validate_original(c,original,tool_root)
    if record!=expected:raise ValueError('Derived appearance receipt differs from exact original native/map plan')
    build_path=root/'build.json';build=_read(build_path)
    if build.get('schema_version')!=1 or 'combine' not in build.get('stages',{}):
        raise ValueError('Derived appearance combine has not completed')
    if build.get('inputs',{}).get(str(Path(c['_config']).resolve()))!=sha(c['_config']) or build['inputs'].get(c['_source'])!=sha(c['_source']):
        raise ValueError('Derived appearance source/config inputs differ')
    inputs=dict(expected['inputs'])
    for path,digest in build['inputs'].items():
        if path in FLAGS:continue
        if sha(path)!=digest:raise ValueError('Derived appearance input/tool changed: '+path)
        inputs[path]=digest
    outputs={}
    for stage in build['stages'].values():
        for relative,digest in stage['outputs'].items():
            path=(root/relative.replace(chr(92),'/')).resolve()
            if not path.is_relative_to(root) or sha(path)!=digest:raise ValueError('Derived appearance completed output changed: '+relative)
            outputs[str(path)]=digest
    combined=root/'masters/combined.exr';sidecar=root/'masters/combined.provenance.json';appearance=_read(sidecar)
    if not {str(combined),str(sidecar)}<=set(outputs):raise ValueError('Derived combined master/provenance lack completed-output proof')
    required={'schema_version':1,'source_sha256':sha(c['_source']),'combined_sha256':sha(combined),
        'ownership_sha256':sha(root/'ownership/labels.npy'),'native_recombine_provenance_sha256':sha(root/'native_recombine.provenance.json'),
        'original_native_candidate':str(original),'finishing_overrides':finishing_overrides(c),APPEARANCE:appearance_settings(c),
        'appearance_only':True,'illumination_colour_separate':True}
    if any(appearance.get(k)!=v for k,v in required.items()):raise ValueError('Derived appearance combined provenance differs')
    preview=appearance.get('preview_sha256')
    if not isinstance(preview,str) or len(preview)!=64 or any(x not in '0123456789abcdef' for x in preview):
        raise ValueError('Derived appearance preview identity is missing')
    inputs.update(outputs)
    for path in [root/name for name in record['copies']]+[root/APPEARANCE_COPY,root/'native_recombine.provenance.json',build_path]:
        inputs[str(path)]=sha(path)
    return dict(expected,kind='verified-native-appearance-derivative',appearance_candidate=str(root),inputs=inputs,
        derived_build_sha256=sha(build_path),derived_preview_sha256=preview,
        limits=expected['limits']+' One completed appearance derivative verified; original physical ownership source hash remains unchanged.')
