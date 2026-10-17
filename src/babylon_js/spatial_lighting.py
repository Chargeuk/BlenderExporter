"""Optional environment-local relative irradiance field. No runtime shader changes."""
import hashlib
import json
import math
from pathlib import Path

KEY = 'kadshowSpatialLighting'
VERSION = 1


def finite(value, name, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(name + ' must be finite numeric data')
    if low is not None and value < low or high is not None and value > high:
        raise ValueError(name + ' outside supported range')
    return float(value)


def validate_record(record):
    """Validate the capture receipt; bounds use Blender XY = Babylon local XZ."""
    if not isinstance(record, dict) or type(record.get('version')) is not int or record['version'] != VERSION:
        raise ValueError('Spatial lighting receipt version must be 1')
    if record.get('semantics') != 'irradiance-multiplier':
        raise ValueError('Spatial lighting requires relative irradiance-multiplier semantics')
    bounds = record.get('bounds', {})
    values = {key: finite(bounds.get(key), key) for key in ('xMin', 'xMax', 'zMin', 'zMax')}
    if values['xMax'] <= values['xMin'] or values['zMax'] <= values['zMin']:
        raise ValueError('Spatial lighting bounds must have positive area')
    norm = record.get('normalization', {})
    if record.get('captureMethod')=='authored-surface-direct' and norm.get('method')!='covered-global-minmax-rec709':
        raise ValueError('Authored surface direct requires covered global minmax normalization')
    if norm.get('method') == 'covered-global-minmax-rec709':
        if record.get('captureMethod') != 'authored-surface-direct':
            raise ValueError('Covered minmax requires authored-surface-direct capture')
        finite(norm.get('blackLuminance'),'blackLuminance',0)
        finite(norm.get('globalRgbPeak'),'globalRgbPeak',1e-12)
        if norm.get('coverageThreshold') != .999 or norm.get('uncoveredInside') != 'black':
            raise ValueError('Unsupported covered minmax fill policy')
        if 'referenceHeight' in record or 'referencePosition' in norm:
            raise ValueError('Authored surfaces do not have a fixed reference height/position')
    elif norm.get('method') == 'reference-luminance-rec709':
        finite(record.get('referenceHeight'), 'referenceHeight')
        if len(norm.get('referencePosition', [])) != 3:
            raise ValueError('Missing scalar reference-luminance normalization')
        for value in norm['referencePosition']:
            finite(value, 'referencePosition')
        x, height, z = norm['referencePosition']
        if abs(height-record['referenceHeight'])>1e-6 or not values['xMin']<=x<=values['xMax'] or not values['zMin']<=z<=values['zMax']:
            raise ValueError('Reference position must be inside field at referenceHeight')
        finite(norm.get('measuredScalar'), 'measuredScalar', 1e-12)
    else:
        raise ValueError('Missing scalar reference-luminance normalization')
    if record.get('orientation') != 'blender-xy-top-left-positive-y;babylon-xz-v-positive-z':
        raise ValueError('Unknown spatial field orientation')
    source_hash = record.get('masterSha256', '')
    if not isinstance(source_hash, str) or len(source_hash) != 64 or any(c not in '0123456789abcdef' for c in source_hash):
        raise ValueError('Missing exact spatial master SHA256')
    return record


def payload(record, filename, texture_sha256, strength=1., min_divisor=1/255, *, unmapped_intensity=None):
    validate_record(record)
    from .ktx_conversion import output_name
    output_name(filename, '.ktx2')
    if len(texture_sha256) != 64 or any(c not in '0123456789abcdef' for c in texture_sha256):
        raise ValueError('Invalid delivered texture SHA256')
    result = dict(version=VERSION, texture=filename, textureSha256=texture_sha256,
        encoding='rgbd-v1', minDivisor=finite(min_divisor, 'minDivisor', 1/255, 1),
        strength=finite(strength, 'strength', 0, 100), semantics=record['semantics'],
        coordinateSpace='environment-local', bounds=dict(record['bounds']),
        orientation=record['orientation'], uvMapping='unrotated-xz-v1',
        worldToUV=dict(input='inverse(environmentRootWorldMatrix) * worldPosition',
            u='(localX-xMin)/(xMax-xMin)', v='(localZ-zMin)/(zMax-zMin)',
            outsideBounds='neutral', neutralRgb=[1,1,1], insideSampler='clamp-to-edge'),
        normalization=dict(record['normalization']))
    if record.get('captureMethod') == 'authored-surface-direct':
        result['captureMethod']='authored-surface-direct'
    else:
        result['referenceHeight']=record['referenceHeight']
    if unmapped_intensity is not None:
        result['unmappedIntensity']=finite(unmapped_intensity, 'unmappedIntensity', 0)
    return result


def direct_surface_control(scene, expected_master_sha256):
    """Only the current direct sampler and unlinked Direct Strength affect this mode."""
    import bpy
    from .ktx_conversion import sha
    from .lightmap_grading import inspect
    name=scene.get('bjs_lighting_controls_material')
    material=bpy.data.materials.get(name) if name else None
    if material is None:raise ValueError('Missing saved managed direct controls')
    instance,_=inspect(material);socket=instance.inputs.get('Direct Strength')
    if socket is None or socket.is_linked:raise ValueError('Direct Strength must be saved unlinked data')
    strength=finite(float(socket.default_value),'Direct Strength',0)
    images=[];seen=set()
    def visit(tree):
        if tree.as_pointer() in seen:return
        seen.add(tree.as_pointer())
        for node in tree.nodes:
            if node.type=='GROUP':visit(node.node_tree)
            if node.type=='TEX_IMAGE' and node.get('bjs_preview_image_role')=='direct':images.append(node.image)
    visit(material.node_tree)
    if len(images)!=1 or images[0] is None:raise ValueError('Expected one managed direct sampler')
    image=images[0];path=Path(bpy.path.abspath(image.filepath))
    if image.packed_file or image.is_dirty or not path.is_file() or sha(path)!=expected_master_sha256:
        raise ValueError('Current managed direct source differs from captured pass')
    if image.colorspace_settings.name not in ('Non-Color','Linear Rec.709'):
        raise ValueError('Direct master must be decoded linear data')
    return strength


def validate_surface_source(context, record):
    """Check direct-only correspondence without invalidating on unrelated I/AO edits."""
    from . import ktx_conversion as core, environment_controls
    source=record['source']
    for path_key,hash_key in (('sourceScene','sourceSceneSha256'),('directMaster','directMasterSha256'),('scopeRecipe','scopeRecipeSha256'),('bakeProof','bakeProofSha256'),('bakePreflight','bakePreflightSha256')):
        path=Path(source[path_key])
        if not path.is_file() or core.sha(path)!=source[hash_key]:raise ValueError('Surface direct input changed: '+path_key)
    identity=capture_input_identity(core.read_json(Path(source['scopeRecipe'])))
    if identity['geometrySha256']!=source['geometrySha256'] or identity['lightsSha256']!=source['lightsSha256']:
        raise ValueError('Direct field geometry/UV/transforms or bake lights changed')
    strength=direct_surface_control(context.scene,source['directMasterSha256'])
    if abs(strength-source['directStrength'])>1e-5:raise ValueError('Direct Strength changed')
    current=environment_controls.settings(environment_controls.find_controls(context.scene.world))
    for key in ('Rotation','Lighting Strength','Sky Lift','Lift Rolloff'):
        if abs(current[key]-source['worldControls'][key])>1e-5:raise ValueError('World direct lighting changed: '+key)
    if core.sha(Path(current['image']))!=source['worldImageSha256']:raise ValueError('World direct source image changed')


def artist_graph_sha256(material):
    """Saved artistic graph correspondence, independent of image bindings/layout.

    Physical maps are separately identified by snapshot/master hashes. Here we
    retain shader operations, curves, ramps, artist values and wiring so manual
    grading edits cannot silently reuse a stale field.
    """
    ignored={'rna_type','name','label','location','location_absolute','width','height','dimensions','select','hide','show_options','show_preview','show_texture','use_custom_color','color','parent','warning_propagation'}
    def value(v):
        if isinstance(v,(str,int,float,bool)) or v is None: return v
        return list(v)
    def tree_data(tree, ancestors=()):
        if tree.as_pointer() in ancestors: raise ValueError('Recursive artist graph')
        rows=[]
        for node in sorted(tree.nodes,key=lambda n:n.name):
            props={}
            for p in node.bl_rna.properties:
                if p.identifier not in ignored and not p.is_readonly and p.type in ('BOOLEAN','INT','FLOAT','STRING','ENUM'):
                    props[p.identifier]=value(getattr(node,p.identifier))
            row=dict(name=node.name,type=node.bl_idname,properties=props,
                inputs={str(i):value(s.default_value) for i,s in enumerate(node.inputs) if not s.is_linked and hasattr(s,'default_value')})
            if node.type in ('RGB','VALUE'):row['outputs']=[value(s.default_value) for s in node.outputs]
            if node.type=='GROUP':row['group']=tree_data(node.node_tree,ancestors+(tree.as_pointer(),))
            if hasattr(node,'mapping'):
                mapping=node.mapping
                row['mapping']=dict(settings={p.identifier:value(getattr(mapping,p.identifier)) for p in mapping.bl_rna.properties if p.identifier!='rna_type' and p.type in ('BOOLEAN','INT','FLOAT','STRING','ENUM')}, curves=[[dict(location=list(p.location),handle_type=p.handle_type) for p in curve.points] for curve in mapping.curves])
            if hasattr(node,'color_ramp'):
                ramp=node.color_ramp;row['ramp']=dict(interpolation=ramp.interpolation,colorMode=ramp.color_mode,hueInterpolation=ramp.hue_interpolation,elements=[dict(position=e.position,color=list(e.color)) for e in ramp.elements])
            rows.append(row)
        return dict(nodes=rows,links=sorted((l.from_node.name,list(l.from_node.outputs).index(l.from_socket),l.to_node.name,list(l.to_node.inputs).index(l.to_socket)) for l in tree.links))
    return hashlib.sha256(json.dumps(tree_data(material.node_tree),sort_keys=True,separators=(',',':')).encode()).hexdigest()


def capture_input_identity(recipe):
    """Reuse the installed evaluated AO geometry identity; add exact bake-light state."""
    import sys
    import bpy
    sys.path.insert(0,str(Path(__file__).parent/'environment_tools'))
    from .environment_tools.ao import geometry_identity
    from .environment_tools.worker import select
    geometry=geometry_identity(recipe)
    lights=[]
    for obj in sorted(select(recipe,'bake_lights'),key=lambda o:o.name):
        data=obj.data
        graph=None;images={};seen=set()
        def light_images(tree):
            if not tree or tree.as_pointer() in seen:return
            seen.add(tree.as_pointer())
            for node in tree.nodes:
                if node.type in ('SCRIPT','TEX_IES','TEX_POINTDENSITY'):
                    raise ValueError('Unsupported external light shader state: '+obj.name+' '+node.type)
                if node.type=='GROUP':light_images(node.node_tree)
                if hasattr(node,'image') and node.image:
                    path=Path(bpy.path.abspath(node.image.filepath))
                    if node.image.packed_file or not path.is_file():raise ValueError('Light textures must be saved external files: '+obj.name)
                    images[str(path.resolve())]=hashlib.sha256(path.read_bytes()).hexdigest()
        if data.use_nodes:
            if not data.node_tree:raise ValueError('Missing active light node graph: '+obj.name)
            light_images(data.node_tree);graph=artist_graph_sha256(data)
        lights.append(dict(name=obj.name,type=data.type,energy=data.energy,color=list(data.color),matrixWorld=[list(r) for r in obj.matrix_world],properties={key:getattr(data,key) for key in ('size','size_y','shape','spot_size','spot_blend','shadow_soft_size','diffuse_factor','specular_factor','volume_factor','normalize','spread','use_shadow','use_soft_falloff','use_nodes') if hasattr(data,key)},cycles={key:getattr(data.cycles,key) for key in ('max_bounces','use_multiple_importance_sampling','is_portal','is_caustics_light') if hasattr(data.cycles,key)},nodeGraphSha256=graph,nodeImages=images))
    return dict(geometrySha256=geometry['geometry_sha256'],lightsSha256=hashlib.sha256(json.dumps(lights,sort_keys=True,separators=(',',':')).encode()).hexdigest(),lights=lights)


def opted_lights(model, objects):
    by_name = {o.name: o for o in objects if o.type == 'LIGHT'}
    result = []
    for light in model.get('lights', []):
        obj = by_name.get(light.get('name'))
        if obj and getattr(obj.data, 'bjs_spatial_lighting', False):
            metadata = light.get('metadata', {})
            if not isinstance(metadata, dict) or KEY in metadata:
                raise ValueError('Invalid or already associated spatial lighting metadata')
            result.append((light, obj))
    return result


def export_spatial(context, model, objects, work, package, options, ktx, threads=8):
    """Export a shared map once, referenced only by explicit exported light opt-ins."""
    import bpy
    from . import ktx_conversion as core
    from .ktx_export import prepare_blender, process_options
    import subprocess
    selected = opted_lights(model, objects)
    if not selected:
        return dict(status='skipped', reason='No exported light opted in', lights=[])
    if context.scene.world.preserveZUpRight:
        raise ValueError('Spatial lighting requires exporter Babylon Y-up axis conversion')
    receipt_path = Path(bpy.path.abspath(options.get('config', ''))).resolve()
    record = validate_record(core.read_json(receipt_path))
    source_record = record.get('source', {})
    if record.get('captureMethod') == 'authored-surface-direct':
        validate_surface_source(context, record)
    else:
        validate_legacy_source(context, record)
    source = Path(bpy.path.abspath(options['image'])).resolve() if options.get('image') else (receipt_path.parent / record['master']).resolve()
    return _export_master(context,model,selected,work,package,options,ktx,threads,record,receipt_path,source)


def validate_legacy_source(context, record):
    import bpy
    from . import ktx_conversion as core
    source_record=record.get('source',{})
    for field in ('physicalScene', 'controlsScene'):
        path = Path(source_record.get(field, ''))
        if not path.is_file() or core.sha(path) != source_record.get(field+'Sha256'):
            raise ValueError('Spatial capture source changed: '+field)
    recipe_path=Path(source_record.get('scopeRecipe',''))
    if not recipe_path.is_file() or core.sha(recipe_path)!=source_record.get('scopeRecipeSha256'):
        raise ValueError('Spatial physical scope recipe changed')
    identity=capture_input_identity(core.read_json(recipe_path))
    if identity['geometrySha256']!=source_record.get('geometrySha256') or identity['lightsSha256']!=source_record.get('lightsSha256'):
        raise ValueError('Spatial field geometry/UV/transforms or physical bake lights changed')
    for path,digest in source_record.get('sourceImages',{}).items():
        file=Path(path)
        if not file.is_file() or core.sha(file)!=digest:
            raise ValueError('Spatial physical source texture changed: '+path)
    from .lightmap_grading import inspect
    name = context.scene.get('bjs_lighting_controls_material')
    material = bpy.data.materials.get(name) if name else None
    if material is None:
        raise ValueError('Spatial export requires the saved matching appearance controls')
    instance, _ = inspect(material)
    if artist_graph_sha256(material) != source_record.get('artistGraphSha256'):
        raise ValueError('Spatial field saved grading graph is stale')
    for key, expected in source_record.get('appearanceControls', {}).items():
        socket = instance.inputs.get(key)
        if socket is None or socket.is_linked or abs(float(socket.default_value)-expected) > 1e-5:
            raise ValueError('Spatial field appearance controls are stale: '+key)
    from . import environment_controls
    world_node=environment_controls.find_controls(context.scene.world)
    current_world=environment_controls.settings(world_node)
    expected_world=source_record.get('worldControls',{})
    for key in ('Rotation','Lighting Strength','Sky Lift','Lift Rolloff'):
        if abs(current_world[key]-expected_world[key]) > 1e-5:
            raise ValueError('Spatial field World lighting is stale: '+key)
    world_image=Path(current_world['image'])
    if not world_image.is_file() or core.sha(world_image) != source_record.get('worldImageSha256'):
        raise ValueError('Spatial field World image is stale')


def _export_master(context,model,selected,work,package,options,ktx,threads,record,receipt_path,source):
    from . import ktx_conversion as core
    from .ktx_export import prepare_blender,process_options
    import subprocess
    if source.suffix.lower() != '.exr' or not source.is_file() or core.sha(source) != record['masterSha256']:
        raise ValueError('Spatial master missing or hash does not match capture receipt')
    if record.get('dimensions') != [512, 512]:
        raise ValueError('Delivery spatial lighting master must be exactly 512 square')
    name = core.output_name(source.stem + '_rgbd.ktx2', '.ktx2')
    target = package / name
    if target.exists():
        raise ValueError('Spatial lighting output collides with another package asset')
    stage = work / 'spatial_lighting'; stage.mkdir()
    spec = dict(source_path=str(source), output=name, encoding='rgbd-v1', size=0,
        flip_y=True, color_space='linear', codec='uastc', mipmaps=False, alpha='preserve')
    prepared, metrics = prepare_blender(spec, stage)
    if record.get('captureMethod')=='authored-surface-direct' and (metrics['linear_min']<0 or metrics['linear_max']>1):
        raise ValueError('Surface minmax master must already be bounded RGB0..1')
    if metrics['dimensions'] != [512, 512]:
        raise ValueError('Spatial lighting master dimensions disagree with capture receipt')
    command = [ktx, 'create', '--format', 'R8G8B8A8_UNORM', '--assign-oetf', 'linear',
        '--encode', 'uastc', '--threads', str(threads), '--uastc-quality', '4', '--zstd', '3', str(prepared), str(target)]
    for argv in (command, [ktx, 'validate', str(target)]):
        run = subprocess.run(argv, capture_output=True, text=True, **process_options())
        if run.returncode:
            raise RuntimeError('Spatial KTX conversion failed: ' + run.stdout + run.stderr)
    if core.sha(source) != record['masterSha256']:
        raise ValueError('Spatial master changed during conversion')
    minimum = max(1/255, metrics['alpha_min']/255)
    digest = core.sha(target)
    for light, obj in selected:
        unmapped=finite(getattr(obj.data, 'bjs_spatial_lighting_unmapped_intensity', -1.), 'unmappedIntensity', -1)
        light.setdefault('metadata', {})[KEY] = payload(record, name, digest,
            getattr(obj.data, 'bjs_spatial_lighting_strength', 1.), minimum,
            unmapped_intensity=None if unmapped<0 else unmapped)
    return dict(status='passed', lights=[l['name'] for l, _ in selected],
        texture=name, textureSha256=digest, bytes=target.stat().st_size,
        source=str(source), receipt=str(receipt_path), metrics=metrics, command=command)
