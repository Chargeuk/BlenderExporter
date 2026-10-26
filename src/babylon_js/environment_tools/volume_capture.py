"""Baked-appearance panorama probes; run only in disposable background Blender.

blender -b SOURCE.blend --python volume_capture.py -- --config RECIPE --phase trial
Review trial panoramas/clearance before repeating with --phase capture.
An explicit --skip-trial on capture records the user's waiver without a pilot.
"""
import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE))


def preview(path, pixels, scale=1):
    """Fixed Reinhard + sRGB display only; never a local min/max transform."""
    import numpy as np
    from babylon_js.ktx_export import _write_png
    rgb = np.maximum(np.asarray(pixels)[..., :3], 0)
    rgb = rgb / (1 + rgb)
    rgb = np.where(rgb <= .0031308, rgb * 12.92, 1.055 * rgb ** (1/2.4) - .055)
    codes = np.rint(np.clip(rgb, 0, 1) * 255).astype(np.uint8)
    _write_png(Path(path), np.repeat(np.repeat(codes, scale, axis=0), scale, axis=1))


def capture_geometry(scene):
    """One BVH of the actual captured surfaces, excluding hidden helpers/glass."""
    import bpy
    from mathutils.bvhtree import BVHTree
    vertices = []; triangles = []
    deps = bpy.context.evaluated_depsgraph_get()
    for obj in scene.objects:
        if obj.type != 'MESH' or obj.hide_render: continue
        evaluated = obj.evaluated_get(deps); mesh = evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles(); offset = len(vertices)
            vertices.extend(obj.matrix_world @ v.co for v in mesh.vertices)
            triangles.extend(tuple(offset + i for i in face.vertices) for face in mesh.loop_triangles)
        finally: evaluated.to_mesh_clear()
    if not triangles: raise ValueError('No visible baked-appearance capture surfaces')
    return BVHTree.FromPolygons(vertices, triangles, all_triangles=True)


def clearance(tree, babylon_position, minimum):
    """Conservative six-direction exit/backface and nearest-surface rejection."""
    from mathutils import Vector
    x, y, z = babylon_position
    position = Vector((x, z, y))  # Blender XYZ -> Babylon XZY, inverse is identical.
    near = tree.find_nearest(position)
    if near[0] is not None and near[3] < minimum:
        return dict(valid=False, reason='near-surface', distance=near[3])
    rays = []
    for direction in ((1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)):
        vector = Vector(direction)
        hit, normal, _, distance = tree.ray_cast(position, vector, 100)
        rays.append(dict(direction=list(direction), distance=distance if hit is not None else None))
        # A first back-facing exit suggests a point inside a solid. Reject it
        # rather than inventing radiance there; the final grid uses valid neighbours.
        if hit is not None and normal.dot(vector) > 1e-5:
            return dict(valid=False, reason='backface-exit', rays=rays)
    return dict(valid=True, nearestDistance=near[3] if near[0] is not None else None, rays=rays)


def fill_grid(grid, positions, valid):
    """Use the same physical-distance nearest donor for Simple and every axis."""
    import numpy as np
    donor=positions[valid];values=grid[valid]
    for z,y,x in np.ndindex(valid.shape):
        if not valid[z,y,x]:
            grid[z,y,x]=values[np.argmin(np.sum((donor-positions[z,y,x])**2,axis=1))]
    return grid


def write_advanced(grids, positions, valid, reference, bounds, output, provenance):
    import numpy as np
    from common import sha, save_json
    from worker import write_exr
    from babylon_js.environment_lighting_volume import resample_grid,pack_atlas,pack_advanced_atlas,DIMENSIONS,DIRECTION_ORDER,ADVANCED_LAYOUT,PANORAMA_CONVENTION
    for grid in grids: fill_grid(grid,positions,valid)
    np.save(output/'advanced_coarse_rgba.npy',grids,allow_pickle=False)
    volumes=np.stack([resample_grid(grid) for grid in grids])
    np.save(output/'advanced_volume_rgba.npy',volumes,allow_pickle=False)
    atlas=pack_advanced_atlas(volumes)
    master=output/'environment_lighting_volume_advanced.exr';write_exr(master,atlas)
    tiles=[pack_atlas(volume) for volume in volumes]
    contact=np.concatenate([np.concatenate(tiles[row:row+2],axis=1) for row in (0,2,4)],axis=0)
    preview(output/'advanced_slices.png',contact,2)
    for name,tile in zip(('px','nx','py','ny','pz','nz'),tiles):preview(output/('advanced_'+name+'_slices.png'),tile,4)
    provenance=dict(provenance,panoramaConvention=PANORAMA_CONVENTION,cameraEulerBlender=[math.pi/2,0,0],
        directionOrder=DIRECTION_ORDER,invalidProbeFill='same physical-distance nearest valid donor as Simple',
        previewOrder='2 columns x 3 rows: +X,-X; +Y,-Y; +Z,-Z',
        rawStorage='[direction,z,y,x,rgba]; runtime Z bank=direction*32+z')
    proof=output/'advanced_provenance.json';save_json(proof,provenance)
    statistics=[]
    for direction,volume in zip(DIRECTION_ORDER,volumes):
        rgb=volume[...,:3]
        statistics.append(dict(direction=direction,rgbMin=rgb.min(axis=(0,1,2)).tolist(),
            rgbMax=rgb.max(axis=(0,1,2)).tolist(),rgbMean=rgb.mean(axis=(0,1,2)).tolist()))
    return dict(version=1,master=master.name,masterSha256=sha(master),bounds=bounds,dimensions=list(DIMENSIONS),
        directionOrder=list(DIRECTION_ORDER),atlasColumns=4,atlasRows=24,atlasLayout=ADVANCED_LAYOUT,flip_y=False,
        panoramaConvention=PANORAMA_CONVENTION,provenance=proof.name,provenanceSha256=sha(proof),
        normalization=dict(method='cosine-hemisphere-rgb-mean-over-env-reference',referenceRgb=reference.tolist(),nearZeroGuard=1e-6),
        statistics=statistics,previews=dict(contactSheet='advanced_slices.png',display='Fixed Reinhard then sRGB; no local minmax'))


def calibrate(output):
    """One native six-emission-wall panorama proves the actual axis convention."""
    import bpy
    import numpy as np
    from common import sha,save_json
    from worker import setup
    from spatial_capture import set_exr_format
    from babylon_js.agx_lightmap import temporary_scene,read_exr
    from babylon_js.environment_lighting_volume import panorama_directions,DIRECTION_ORDER,PANORAMA_CONVENTION
    if not bpy.app.background:raise ValueError('Calibration requires disposable background Blender')
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    colours=np.array([[1,.02,.02],[.02,1,1],[.02,1,.02],[1,.02,1],[.02,.02,1],[1,1,.02]])
    axes=np.array([[1,0,0],[-1,0,0],[0,1,0],[0,-1,0],[0,0,1],[0,0,-1]])
    with temporary_scene() as scene:
        setup(dict(device='OPTIX',diffuse_bounces=0,total_bounces=0));scene.cycles.samples=1
        world=bpy.data.worlds.new('TEMP black orientation world');world.use_nodes=True;scene.world=world
        next(n for n in world.node_tree.nodes if n.type=='BACKGROUND').inputs['Strength'].default_value=0
        for name,axis,colour in zip(DIRECTION_ORDER,axes,colours):
            tangent=np.array([1,0,0]) if axis[1] else np.cross(axis,[0,1,0])
            bitangent=np.cross(axis,tangent)
            vertices=[(axis*2+tangent*x*2+bitangent*y*2)[[0,2,1]].tolist() for x,y in ((-1,-1),(1,-1),(1,1),(-1,1))]
            mesh=bpy.data.meshes.new('TEMP '+name);mesh.from_pydata(vertices,[],[(0,1,2,3)])
            obj=bpy.data.objects.new(mesh.name,mesh);scene.collection.objects.link(obj)
            material=bpy.data.materials.new(mesh.name);material.use_nodes=True;material.node_tree.nodes.clear()
            emit=material.node_tree.nodes.new('ShaderNodeEmission');emit.inputs['Color'].default_value=(*colour,1);emit.inputs['Strength'].default_value=4
            out=material.node_tree.nodes.new('ShaderNodeOutputMaterial');material.node_tree.links.new(emit.outputs[0],out.inputs['Surface']);mesh.materials.append(material)
        data=bpy.data.cameras.new('TEMP orientation camera');camera=bpy.data.objects.new(data.name,data);scene.collection.objects.link(camera);scene.camera=camera
        data.type='PANO';data.panorama_type='EQUIRECTANGULAR';camera.rotation_euler=(math.pi/2,0,0)
        data.longitude_min=-math.pi;data.longitude_max=math.pi;data.latitude_min=-math.pi/2;data.latitude_max=math.pi/2
        scene.render.resolution_x=128;scene.render.resolution_y=64;scene.render.resolution_percentage=100
        scene.render.film_transparent=False;scene.render.use_border=False;set_exr_format(scene);scene.render.image_settings.color_mode='RGB'
        scene.render.filepath=str(output/'panorama.exr');bpy.ops.render.render(write_still=True)
        pixels=read_exr(output/'panorama.exr')
    directions=panorama_directions(64,128);samples=[]
    for name,axis,colour in zip(DIRECTION_ORDER,axes,colours):
        row,column=np.unravel_index(np.argmax(directions@axis),(64,128))
        actual=pixels[row,column,:3];expected=colour*4
        samples.append(dict(direction=name,pixel=[int(column),int(row)],expectedRgb=expected.tolist(),
            actualRgb=actual.tolist(),passed=bool(np.max(np.abs(actual-expected))<.005)))
    passed=all(item['passed'] for item in samples)
    result=dict(status='passed' if passed else 'failed',panoramaConvention=PANORAMA_CONVENTION,directionOrder=DIRECTION_ORDER,
        cameraEulerBlender=[math.pi/2,0,0],panoramaSha256=sha(output/'panorama.exr'),samples=samples)
    save_json(output/'orientation.json',result);preview(output/'panorama.png',pixels,4)
    if not passed:raise ValueError('Native panorama orientation mismatch; inspect orientation.json before any directional derivation')
    print('VOLUME_ORIENTATION_READY '+str(output/'orientation.json'),flush=True)


def derive(config_path, receipt_path, output, orientation_path):
    """Reprocess immutable panorama checkpoints; no render or physical bake."""
    import bpy
    import numpy as np
    import shutil
    import copy
    from common import sha,save_json
    from babylon_js.agx_lightmap import read_exr
    from babylon_js.environment_lighting_volume import validate_record,validate_source,relative_rgb,hemisphere_means,relative_hemispheres,resample_grid,pack_atlas,PANORAMA_CONVENTION,DIRECTION_ORDER
    if not bpy.app.background:raise ValueError('Directional derivation requires disposable background Blender')
    receipt_path=Path(receipt_path).resolve();original=receipt_path.parent;output=Path(output).resolve()
    if output.exists():raise ValueError('Preserve previous outputs; choose a fresh derivation folder')
    record=validate_record(json.loads(receipt_path.read_text()));receipt_sha=sha(receipt_path)
    if 'advanced' in record:raise ValueError('Input must be the accepted Simple-only capture receipt')
    validate_source(bpy.context,record)
    calibration=json.loads(Path(orientation_path).read_text())
    if calibration.get('status')!='passed' or calibration.get('panoramaConvention')!=PANORAMA_CONVENTION or calibration.get('directionOrder')!=DIRECTION_ORDER:
        raise ValueError('Missing matching native panorama orientation calibration')
    if [s.get('direction') for s in calibration.get('samples',[])]!=DIRECTION_ORDER or not all(s.get('passed') is True for s in calibration['samples']):
        raise ValueError('All six actual axis directions must pass calibration')
    config=json.loads(Path(config_path).read_text(encoding='utf-8-sig'))
    signature=json.loads((original/'capture_inputs.json').read_text())
    if signature['configSha256']!=sha(config_path) or signature['source']!=record['source']:
        raise ValueError('Accepted capture inputs/config changed')
    if config['bounds']!=record['bounds'] or config['coarse_dimensions']!=record['coarseDimensions'] or config['dimensions']!=record['dimensions']:
        raise ValueError('Accepted grid dimensions/bounds changed')
    master=original/record['master']
    if sha(master)!=record['masterSha256']:raise ValueError('Accepted Simple master changed')
    trial=json.loads((original/'trial.json').read_text());audit=json.loads((original/'probe_clearance.json').read_text())
    if trial.get('status')!='passed' or trial['source']!=record['source'] or audit['bounds']!=record['bounds'] or audit['coarseDimensions']!=record['coarseDimensions']:
        raise ValueError('Accepted trial/clearance provenance changed')
    nx,ny,nz=record['coarseDimensions'];valid=np.load(original/'coarse_valid_mask.npy',allow_pickle=False)
    if valid.shape!=(nz,ny,nx) or valid.dtype!=np.bool_ or int(valid.sum())!=record['statistics']['validCoarseProbes']:
        raise ValueError('Accepted valid-probe mask changed')
    low=np.array(record['bounds']['min']);extent=np.array(record['bounds']['max'])-low
    positions=np.empty((nz,ny,nx,3));simple=np.ones((nz,ny,nx,4),np.float32);advanced=np.ones((6,nz,ny,nx,4),np.float32)
    reference_pixels=read_exr(record['source']['reference']);reference=hemisphere_means(reference_pixels)
    pilot={tuple(p['index']):original/p['directory'] for p in trial['probes']}
    audits={tuple(p['index']):p for p in audit['probes']};checkpoints=[]
    for z,y,x in np.ndindex(nz,ny,nx):
        position=low+(np.array([x,y,z])+.5)/np.array([nx,ny,nz])*extent;positions[z,y,x]=position
        check=audits.get((x,y,z))
        if check is None or check['valid']!=bool(valid[z,y,x]) or not np.allclose(check['position'],position,rtol=0,atol=1e-12):
            raise ValueError('Accepted clearance/grid position changed')
        if not valid[z,y,x]:continue
        directory=pilot.get((x,y,z),original/'probes'/('%02d_%02d_%02d'%(x,y,z))).resolve()
        if not directory.is_relative_to(original):raise ValueError('Probe checkpoint escapes capture folder')
        point=directory/'probe.json';pano=directory/'panorama.exr';saved=json.loads(point.read_text())
        if sha(pano)!=saved['panoramaSha256'] or not np.allclose(saved['position'],position,rtol=0,atol=1e-12):
            raise ValueError('Accepted panorama checkpoint changed: '+str(directory))
        pixels=read_exr(pano)
        if list(pixels.shape[:2][::-1])!=record['capture']['panoramaSize']:raise ValueError('Probe panorama size changed')
        ratio=relative_rgb(pixels,record['normalization']['referenceRgb'])
        if not np.allclose(ratio,saved['ratioRgb'],rtol=0,atol=1e-6):raise ValueError('Checkpoint Simple ratio differs from panorama')
        simple[z,y,x,:3]=ratio;advanced[:,z,y,x,:3]=relative_hemispheres(pixels,reference)
        checkpoints.append(dict(directory=str(directory.relative_to(original)),probeSha256=sha(point),panoramaSha256=sha(pano)))
    fill_grid(simple,positions,valid)
    if not np.array_equal(pack_atlas(resample_grid(simple)),read_exr(master)):
        raise ValueError('Accepted panoramas/mask do not reproduce the exact Simple master')
    for checkpoint in checkpoints:
        directory=original/checkpoint['directory']
        if sha(directory/'probe.json')!=checkpoint['probeSha256'] or sha(directory/'panorama.exr')!=checkpoint['panoramaSha256']:
            raise ValueError('Checkpoint changed during derivation: '+str(directory))
    output.mkdir(parents=True)
    shutil.copyfile(master,output/master.name);shutil.copyfile(receipt_path,output/'simple_volume_receipt.json')
    shutil.copyfile(orientation_path,output/'panorama_orientation.json')
    for filename in ('volume_slices.png','trial_00_panorama.png'):
        if (original/filename).is_file():shutil.copyfile(original/filename,output/filename)
    proof=dict(method='derive accepted panorama checkpoints; no render/no physical bake',source=record['source'],
        acceptedReceipt=str(receipt_path),acceptedReceiptSha256=receipt_sha,acceptedSimpleMasterSha256=record['masterSha256'],
        acceptedCaptureInputsSha256=sha(original/'capture_inputs.json'),validMaskSha256=sha(original/'coarse_valid_mask.npy'),
        calibration='panorama_orientation.json',calibrationSha256=sha(orientation_path),checkpoints=checkpoints)
    result=copy.deepcopy(record)
    result['advanced']=write_advanced(advanced,positions,valid,reference,record['bounds'],output,proof)
    if sha(master)!=record['masterSha256'] or sha(receipt_path)!=receipt_sha or sha(output/master.name)!=record['masterSha256']:
        raise ValueError('Simple inputs changed during derivation')
    save_json(output/'volume_receipt.json',result)
    print('VOLUME_ADVANCED_READY '+str(output/'volume_receipt.json'),flush=True)


def prepare_output(output, signature, phase, skip_trial=False):
    """Bind fresh/resumed output to the same inputs and explicit trial policy."""
    from common import save_json
    if skip_trial and phase != 'capture':
        raise ValueError('--skip-trial is only supported with --phase capture')
    if phase == 'trial':
        output.mkdir(parents=True, exist_ok=False)
        save_json(output/'capture_inputs.json', signature)
        return None
    if skip_trial and not output.exists():
        output.mkdir(parents=True)
        save_json(output/'capture_inputs.json', signature)
        # A waiver must never masquerade as a passing trial or invent probes.
        save_json(output/'trial.json', dict(status='skipped', source=signature['source'],
            explicitUserOverride=True, reason='User explicitly requested --skip-trial for full capture', probes=[]))
    if json.loads((output/'capture_inputs.json').read_text()) != signature:
        raise ValueError('Capture inputs changed; use a fresh output and repeat the selected trial policy')
    trial = json.loads((output/'trial.json').read_text())
    if skip_trial:
        if trial.get('status') != 'skipped' or trial.get('explicitUserOverride') is not True or trial.get('source') != signature['source'] or trial.get('probes') != []:
            raise ValueError('Capture resume requires the matching explicit skipped-trial receipt')
    elif trial.get('status') != 'passed':
        raise ValueError('Reviewable trial captures must pass before full capture')
    if (output/'volume_receipt.json').exists():
        raise ValueError('Preserve completed volume; choose a new output')
    return trial


def run(config_path, phase, skip_trial=False):
    import bpy
    import numpy as np
    from common import sha, save_json
    from worker import capture, write_exr
    from appearance import verify_combination, preview_identity
    from spatial_capture import set_exr_format, verify_gpu
    from babylon_js import environment_controls
    from babylon_js.agx_lightmap import read_exr
    from babylon_js.environment_lighting_volume import panorama_mean, relative_rgb, hemisphere_means, relative_hemispheres, resample_grid, pack_atlas, DIMENSIONS, LAYOUT
    from ao import geometry_identity
    if not bpy.app.background: raise ValueError('Volume capture requires disposable background Blender')
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8-sig')); base = config_path.parent
    if config.get('schema_version') != 1: raise ValueError('Expected volume capture schema_version 1')
    include_advanced = config.get('include_advanced', True)
    if type(include_advanced) is not bool:
        raise ValueError('include_advanced must be a boolean')
    resolve = lambda key: (base / config[key]).resolve()
    source = resolve('source_scene'); scope = resolve('scope_recipe')
    if Path(bpy.data.filepath).resolve() != source or sha(source) != config['source_sha256']:
        raise ValueError('Wrong or changed accepted source scene')
    scene = bpy.context.scene
    if bpy.data.is_dirty or any(i.is_dirty for i in bpy.data.images):
        raise ValueError('Saved scene contains unsaved source/image changes')
    recipe = json.loads(scope.read_text(encoding='utf-8-sig'))
    recipe['_root'] = str(scope.parent)
    material = bpy.data.materials[scene.get('bjs_lighting_controls_material', '')]
    if material.name != recipe['preview_material']: raise ValueError('Scope preview material differs from saved controls')
    combined = resolve('combined'); provenance = resolve('combined_provenance')
    verify_combination(material, combined, provenance)
    reference = resolve('reference_environment')
    associated = scene.world.get('bjs_environment_image', '')
    if not associated or Path(bpy.path.abspath(associated)).resolve() != reference:
        raise ValueError('Reference panorama differs from accepted World ENV association')
    if config.get('dimensions') != DIMENSIONS: raise ValueError('Delivery dimensions must be 32x16x32')
    dims = config.get('coarse_dimensions')
    if not isinstance(dims, list) or len(dims) != 3 or any(type(n) is not int or not 2 <= n <= 16 for n in dims):
        raise ValueError('Coarse grid needs three dimensions in 2..16')
    bounds = config['bounds']; low = np.array(bounds['min'], dtype=float); high = np.array(bounds['max'], dtype=float)
    if low.shape != (3,) or high.shape != (3,) or not np.isfinite([low, high]).all() or (high <= low).any():
        raise ValueError('Bounds must be finite Babylon environment-local XYZ min/max')
    minimum = float(config.get('clearance', .08))
    if not math.isfinite(minimum) or minimum <= 0: raise ValueError('Positive probe clearance required')
    reference_pixels = read_exr(reference)
    reference_rgb = panorama_mean(reference_pixels)
    reference_hemispheres = hemisphere_means(reference_pixels) if include_advanced else None
    relative_rgb(np.ones((2,4,3), np.float32), reference_rgb)
    world = environment_controls.settings(environment_controls.find_controls(scene.world))
    identity = geometry_identity(recipe)
    inputs = dict(scene=str(source), sceneSha256=sha(source), scopeRecipe=str(scope), scopeRecipeSha256=sha(scope),
        combined=str(combined), combinedSha256=sha(combined), combinedProvenance=str(provenance), combinedProvenanceSha256=sha(provenance),
        reference=str(reference), referenceSha256=sha(reference), previewSha256=preview_identity(material),
        geometrySha256=identity['geometry_sha256'], worldControls=world, worldImageSha256=sha(world['image']))
    output = resolve('output_dir')
    signature = dict(configSha256=sha(config_path), source=inputs)
    trial = prepare_output(output, signature, phase, skip_trial)
    masters = output/'masters'; masters.mkdir(exist_ok=True)
    import shutil
    for original, filename in ((combined, 'combined.exr'), (provenance, 'combined.provenance.json')):
        destination = masters/filename
        if destination.exists():
            if sha(destination) != sha(original): raise ValueError('Staged appearance input changed')
        else: shutil.copyfile(original, destination)
    spec = config.get('panorama', {})
    size = spec.get('size', 128); samples = spec.get('samples', 4)
    if type(size) is not int or not 32 <= size <= 1024 or size % 2 or type(samples) is not int or not 1 <= samples <= 64:
        raise ValueError('Invalid probe panorama resolution/samples')
    recipe['device'] = spec.get('device', 'OPTIX')
    recipe['capture'] = dict(mode='baked', world_output='Lighting', position=[0,0,0], size=size, samples=samples)
    set_exr_format(scene)
    capture(recipe, output, output, prepare_only=True)
    devices = verify_gpu(scene, spec)
    tree = capture_geometry(scene)
    # worker.capture has now prepared accepted colour x adjusted-lighting EMIT,
    # no analytic lights/reflective glass/helpers, and managed World Lighting.
    if any(o.type == 'LIGHT' and not o.hide_render for o in scene.objects):
        raise ValueError('Analytic light unexpectedly visible in baked capture')
    nx, ny, nz = dims
    positions = np.empty((nz,ny,nx,3), np.float64)
    checks = []; valid = np.zeros((nz,ny,nx), dtype=bool)
    for z, y, x in np.ndindex(nz, ny, nx):
        position = low + (np.array([x,y,z]) + .5) / np.array(dims) * (high-low)
        positions[z,y,x] = position
        check = clearance(tree, position.tolist(), minimum)
        valid[z,y,x] = check['valid']
        checks.append(dict(index=[x,y,z], position=position.tolist(), **check))
    save_json(output/'probe_clearance.json', dict(bounds=bounds, coarseDimensions=dims, valid=int(valid.sum()), total=valid.size, probes=checks))
    if valid.sum() < config.get('minimum_valid_probes', 16): raise ValueError('Too few clear probe positions; inspect clearance evidence')

    def render(position, directory):
        directory.mkdir(exist_ok=True)
        if (directory/'probe.json').exists():
            saved=json.loads((directory/'probe.json').read_text())
            if saved['position']!=list(position) or sha(directory/'panorama.exr')!=saved['panoramaSha256']:
                raise ValueError('Probe checkpoint changed: '+str(directory))
            pixels=read_exr(directory/'panorama.exr')
            return relative_rgb(pixels,reference_rgb),pixels,saved
        scene.camera.location=(position[0], position[2], position[1])
        scene.render.filepath=str(directory/'panorama.exr')
        bpy.ops.render.render(write_still=True)
        pixels=read_exr(directory/'panorama.exr'); ratio=relative_rgb(pixels,reference_rgb)
        result=dict(position=list(position), measuredRgb=panorama_mean(pixels).tolist(), ratioRgb=ratio.tolist(),
            panoramaSha256=sha(directory/'panorama.exr'), rgbMin=float(pixels[...,:3].min()), rgbMax=float(pixels[...,:3].max()))
        save_json(directory/'probe.json',result)
        return ratio,pixels,result

    if phase == 'trial':
        records=[]
        for index,voxel in enumerate(config['trial_indices']):
            if len(voxel)!=3 or any(type(v) is not int or not 0<=v<d for v,d in zip(voxel,dims)):
                raise ValueError('Trial index is outside coarse grid')
            x,y,z=voxel;position=positions[z,y,x]
            check=clearance(tree,position.tolist(),minimum)
            if not check['valid']: raise ValueError('Trial point is not clear: '+repr(position.tolist())+' '+repr(check))
            _,pixels,result=render(position,output/('trial_%02d'%index))
            preview(output/('trial_%02d_panorama.png'%index),pixels, max(1,512//size))
            records.append(dict(**result,index=voxel,directory='trial_%02d'%index,clearance=check))
        save_json(output/'trial.json',dict(status='passed',source=inputs,referenceRgb=reference_rgb.tolist(),
            probes=records,validCoarseProbes=int(valid.sum()),enabledDevices=devices,
            display='Fixed Reinhard then sRGB; display previews are not linear lighting maps'))
        print('VOLUME_TRIAL_READY '+str(output/'trial.json'),flush=True)
        return
    grid=np.ones((nz,ny,nx,4),np.float32); grid[...,:3]=np.nan
    advanced_grid=None
    if include_advanced:
        advanced_grid=np.ones((6,nz,ny,nx,4),np.float32); advanced_grid[...,:3]=np.nan
    pilot={tuple(p['index']):output/p['directory'] for p in trial['probes']}
    completed=0; probes=output/'probes';probes.mkdir(exist_ok=True)
    total=int(valid.sum()); checkpoints=[]
    for z,y,x in np.ndindex(nz,ny,nx):
        if not valid[z,y,x]: continue
        directory=pilot.get((x,y,z),probes/('%02d_%02d_%02d'%(x,y,z)))
        ratio,pixels,result=render(positions[z,y,x],directory)
        grid[z,y,x,:3]=ratio
        if include_advanced:
            advanced_grid[:,z,y,x,:3]=relative_hemispheres(pixels,reference_hemispheres)
        checkpoints.append(dict(directory=str(directory.relative_to(output)),probeSha256=sha(directory/'probe.json'),panoramaSha256=result['panoramaSha256']))
        completed+=1
        if completed == 1 and skip_trial:
            preview(output/'capture_00_panorama.png', pixels, max(1,512//size))
        if completed==1 or completed%8==0 or completed==total:
            # Magenta denotes unmeasured cells; it is preview-only and never
            # enters nearest filling, interpolation, or the linear EXR master.
            sheet=np.full((nz,ny*nx,3),[1.,0.,1.],np.float32)
            for sy in range(ny):
                tile=grid[:,sy,:,:3]; mask=np.isfinite(tile).all(axis=-1)
                sheet[:,sy*nx:(sy+1)*nx][mask]=tile[mask]
            preview(output/'coarse_progress.png',sheet,max(1,128//nz))
            save_json(output/'progress.json',dict(completed=completed,total=total,stage='capturing',
                preview='coarse_progress.png',unmeasured='magenta',sliceOrder='Y left-to-right; X right; Z down'))
    fill_grid(grid,positions,valid)
    np.save(output/'coarse_valid_mask.npy',valid,allow_pickle=False)
    np.save(output/'coarse_volume.npy',grid,allow_pickle=False)
    volume=resample_grid(grid);np.save(output/'volume_rgba.npy',volume,allow_pickle=False)
    atlas=pack_atlas(volume);master=output/'environment_lighting_volume.exr';write_exr(master,atlas)
    preview(output/'volume_slices.png',atlas,4)
    rgb=volume[...,:3]
    stats=dict(rgbMin=rgb.min(axis=(0,1,2)).tolist(),rgbMax=rgb.max(axis=(0,1,2)).tolist(),rgbMean=rgb.mean(axis=(0,1,2)).tolist(),
        voxelsAboveOnePercent=float(np.any(rgb>1,axis=-1).mean()*100),nearNeutralPercent=float(np.all(np.abs(rgb-1)<.05,axis=-1).mean()*100),
        validCoarseProbes=int(valid.sum()),filledCoarseProbes=int((~valid).sum()))
    record=dict(version=1,master=master.name,masterSha256=sha(master),bounds=bounds,dimensions=DIMENSIONS,
        atlasColumns=4,atlasRows=4,atlasLayout=LAYOUT,flip_y=False,
        normalization=dict(method='solid-angle-rgb-mean-over-env-reference',referenceRgb=reference_rgb.tolist(),nearZeroGuard=1e-6),
        source=inputs,coarseDimensions=dims,statistics=stats,
        capture=dict(mode='baked',world_output='Lighting',samples=samples,panoramaSize=[size,size//2],enabledDevices=devices,
            invalidProbePolicy='six-direction-backface-or-clearance-rejection;nearest-valid-euclidean-fill',interpolation='texel-centered-trilinear'),
        storage=dict(rawVoxelIndex='((z*dimY+y)*dimX+x)*4',blenderToBabylon='XZY',atlasTile='X right; Z down; Y slices row-major',
            texelCenters='voxel position=min+(index+0.5)/dimensions*(max-min); normalized3D=(position-min)/(max-min), sampled directly with clamp-to-edge; no endpoint remapping',
            atlasSampling='grid=normalized3D*dimensions-0.5; clamp grid to [0,dimensions-1]; tile UV=(tileOrigin+clampedGridXZ+0.5)/atlasDimensions; interpolate adjacent Y slices; never filter between tiles'),
        previews=dict(slices='volume_slices.png',panorama='capture_00_panorama.png' if skip_trial else 'trial_00_panorama.png',display='Fixed Reinhard then sRGB; neutral ratio 1 maps to approximately 0.735 sRGB; no local minmax'))
    if skip_trial:
        record['capture']['trial'] = dict(status='skipped', explicitUserOverride=True, reason=trial['reason'], receipt='trial.json', receiptSha256=sha(output/'trial.json'))
    # Keep the untouched Simple receipt usable by derive(); default capture still
    # writes both products from these same panoramas without extra renders.
    if include_advanced:
        record['advanced']=write_advanced(advanced_grid,positions,valid,reference_hemispheres,bounds,output,
            dict(method='same captured panoramas as Simple; no additional probe renders',source=inputs,
                acceptedCaptureInputsSha256=sha(output/'capture_inputs.json'),validMaskSha256=sha(output/'coarse_valid_mask.npy'),checkpoints=checkpoints))
    for key in ('scene','scopeRecipe','combined','combinedProvenance','reference'):
        if sha(inputs[key])!=inputs[key+'Sha256']: raise ValueError('Input changed during capture: '+key)
    save_json(output/'volume_receipt.json',record)
    save_json(output/'progress.json',dict(completed=total,total=total,stage='complete',preview='volume_slices.png',statistics=stats))
    print('VOLUME_CAPTURE_READY '+str(output/'volume_receipt.json'),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',type=Path)
    parser.add_argument('--phase',choices=('trial','capture','calibrate','derive','relocate'),required=True)
    parser.add_argument('--receipt',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--orientation',type=Path)
    parser.add_argument('--skip-trial',action='store_true',help='Explicit user override: capture the full grid without a trial; all source/clearance checks remain enabled')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    if args.skip_trial and args.phase != 'capture':parser.error('--skip-trial requires --phase capture')
    if args.phase=='relocate':
        if not args.config:parser.error('relocate requires --config')
        from volume_relocation import relocate
        relocate(args.config)
    elif args.phase=='calibrate':
        if not args.output:parser.error('calibrate requires --output')
        calibrate(args.output)
    elif args.phase=='derive':
        if not all((args.config,args.receipt,args.output,args.orientation)):parser.error('derive requires --config, --receipt, --output and --orientation')
        derive(args.config,args.receipt,args.output,args.orientation)
    else:
        if not args.config:parser.error('trial/capture requires --config')
        run(args.config,args.phase,args.skip_trial)
