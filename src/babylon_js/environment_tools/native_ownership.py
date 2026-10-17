"""Opt-in native FLOAT FACE island IDs; strict zero-margin coverage and provenance."""
import hashlib
import json
from pathlib import Path
import numpy as np

ATTRIBUTE = '_environment_island_id'


def ownership_method(c):
    settings = c.get('ownership')
    if settings is None:
        return 'conservative-uv'
    if (not isinstance(settings, dict) or set(settings) != {'schema_version', 'method'}
            or type(settings['schema_version']) is not int or settings['schema_version'] != 1
            or settings['method'] not in ('native-island-id', 'conservative-uv')):
        raise ValueError('ownership requires schema_version 1 and method native-island-id or conservative-uv')
    return settings['method']


def catalogue_identity(catalogue):
    if not isinstance(catalogue, list) or not catalogue:
        raise ValueError('Native ownership requires a nonempty island catalogue')
    seen = set()
    for row in catalogue:
        if not isinstance(row,dict):
            raise ValueError('Native catalogue entries must be objects')
        identity = row.get('id')
        if type(identity) is not int or not 1 <= identity <= 2**24 or identity in seen:
            raise ValueError('Native catalogue IDs must be unique positive float32-exact integers')
        if not isinstance(row.get('object'), str) or not row['object']:
            raise ValueError('Native catalogue requires object identity')
        triangles = np.asarray(row.get('triangles'), dtype=np.float64)
        if triangles.ndim != 3 or triangles.shape[1:] != (3, 2) or not len(triangles) or not np.isfinite(triangles).all():
            raise ValueError('Native catalogue requires finite UV triangles')
        seen.add(identity)
    return hashlib.sha256(json.dumps(catalogue, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate_native_ids(image, catalogue, *raw_bakes):
    """Exact integers/alpha only; no rounding, filling or geometric owner inference."""
    digest = catalogue_identity(catalogue)
    if (not isinstance(image, np.ndarray) or image.dtype != np.float32 or image.ndim != 3
            or image.shape[0] != image.shape[1] or image.shape[2] != 4 or not np.isfinite(image).all()):
        raise ValueError('Native ID bake must be finite square float32 RGBA')
    if not np.isin(image[:, :, 3], [0, 1]).all():
        raise ValueError('Native ID alpha must be binary')
    owned = image[:, :, 3] == 1
    if not owned.any():
        raise ValueError('Native ID bake has no owned samples')
    rgb = image[:, :, :3]
    if (not np.array_equal(rgb[:, :, 0], rgb[:, :, 1]) or not np.array_equal(rgb[:, :, 0], rgb[:, :, 2])
            or not (rgb[~owned] == 0).all()):
        raise ValueError('Native ID channels must agree with zero RGB outside coverage')
    values = rgb[:, :, 0][owned]
    if (values <= 0).any() or (values > 2**24).any() or not np.array_equal(values, np.floor(values)):
        raise ValueError('Native IDs must be exact positive integers; interpolation/rounding rejected')
    present = set(map(int, np.unique(values)))
    declared = {row['id'] for row in catalogue}
    if not present <= declared:
        raise ValueError('Native sampled ID absent from catalogue')
    for raw in raw_bakes:
        if (not isinstance(raw, np.ndarray) or raw.dtype != np.float32 or raw.shape != image.shape
                or not np.isfinite(raw).all() or not np.isin(raw[:, :, 3], [0, 1]).all()):
            raise ValueError('Native transport must be matching finite float32 RGBA with binary alpha')
        if not np.array_equal(raw[:, :, 3], image[:, :, 3]):
            raise ValueError('Native ID alpha differs from native transport coverage')
    labels = np.zeros(owned.shape, np.int32)
    labels[owned] = values.astype(np.int32)
    return labels, {'method': 'native-island-id', 'owned_pixels': int(owned.sum()), 'sampled_islands': len(present),
        'unsampled_island_ids': sorted(declared - present), 'catalogue_identity_sha256': digest,
        'coverage_verified_native_passes': len(raw_bakes), 'owner_inference': False,
        'limits': 'Native raster owner at this exact UV/resolution, not a UV overlap proof or exhaustive geometric chart coverage.'}


def attach_face_ids(mesh, polygon_ids):
    """Only on caller-owned disposable evaluated meshes; never an authored mesh."""
    if len(polygon_ids) != len(mesh.polygons) or any(type(value) is not int or not 1 <= value <= 2**24 for value in polygon_ids):
        raise ValueError('One exact catalogue ID per disposable polygon required')
    old = mesh.attributes.get(ATTRIBUTE)
    if old:
        mesh.attributes.remove(old)
    attribute = mesh.attributes.new(ATTRIBUTE, 'FLOAT', 'FACE')
    for datum, value in zip(attribute.data, polygon_ids):
        datum.value = value
    return attribute


def bake_native_ids(c, proxy, catalogue):
    """Fixed one-sample EMIT on the same joined proxy; restore its physical materials."""
    import bpy
    from worker import target_image, raw_pixels
    scene = bpy.context.scene
    if not bpy.app.background or proxy != bpy.context.view_layer.objects.active or not proxy.select_get():
        raise ValueError('Native ID bake requires selected joined proxy in background Blender')
    attribute = proxy.data.attributes.get(ATTRIBUTE)
    if not attribute or attribute.domain != 'FACE' or attribute.data_type != 'FLOAT':
        raise ValueError('Joined proxy lost its FLOAT FACE native island attribute')
    face_ids = np.asarray([datum.value for datum in attribute.data], dtype=np.float32)
    catalogue_identity(catalogue)
    if (not np.isfinite(face_ids).all() or not np.array_equal(face_ids, np.floor(face_ids))
            or set(map(int, face_ids)) != {row['id'] for row in catalogue}):
        raise ValueError('Joined FACE IDs do not match catalogue membership')
    materials = list(proxy.data.materials)
    indices = [polygon.material_index for polygon in proxy.data.polygons]
    original_settings = scene.cycles.samples, scene.cycles.use_adaptive_sampling, scene.cycles.use_denoising
    material = bpy.data.materials.new('TEMP native island IDs')
    material.use_nodes = True
    image = None
    try:
        nodes, links = material.node_tree.nodes, material.node_tree.links
        nodes.clear()
        attribute_node = nodes.new('ShaderNodeAttribute')
        attribute_node.attribute_type = 'GEOMETRY'
        attribute_node.attribute_name = ATTRIBUTE
        emission = nodes.new('ShaderNodeEmission')
        emission.inputs['Strength'].default_value = 1
        output = nodes.new('ShaderNodeOutputMaterial')
        links.new(attribute_node.outputs['Fac'], emission.inputs['Color'])
        links.new(emission.outputs[0], output.inputs['Surface'])
        proxy.data.materials.clear()
        proxy.data.materials.append(material)
        for polygon in proxy.data.polygons:
            polygon.material_index = 0
        image = target_image(c, [material])
        scene.cycles.samples = 1
        scene.cycles.use_adaptive_sampling = False
        scene.cycles.use_denoising = False
        bpy.ops.object.bake(type='EMIT', margin=0, use_clear=False, use_selected_to_active=False, uv_layer=c.get('uv', 'SimpleBake'))
        pixels = raw_pixels(image)
        return pixels, {'attribute': ATTRIBUTE, 'attribute_domain': 'FACE', 'attribute_type': 'FLOAT',
            'joined_faces': len(face_ids), 'samples': 1, 'adaptive': False, 'denoising': False,
            'engine':scene.render.engine,'device':scene.cycles.device,'requested_device':c.get('device','CPU'),
            'blender_version':bpy.app.version_string,
            'compute_device':bpy.context.preferences.addons['cycles'].preferences.compute_device_type,
            'pass': 'EMIT', 'margin': 0, 'use_clear': False, 'initial_rgba': [0, 0, 0, 0],
            'orientation': 'top-left', 'encoding': 'float32 integer island ID in identical RGB; binary native raster alpha',
            'physical_materials_restored': True}
    finally:
        proxy.data.materials.clear()
        for saved in materials:
            proxy.data.materials.append(saved)
        for polygon, index in zip(proxy.data.polygons, indices):
            polygon.material_index = index
        scene.cycles.samples, scene.cycles.use_adaptive_sampling, scene.cycles.use_denoising = original_settings
        if image is not None:
            bpy.data.images.remove(image)
        bpy.data.materials.remove(material)


def save_native_bake(c, root, stage, proxy, catalogue):
    from common import sha, save_json
    raw = [np.load(stage/(mode + '_raw.npy'), allow_pickle=False) for mode in ('direct', 'indirect')]
    pixels, settings = bake_native_ids(c, proxy, catalogue)
    _, report = validate_native_ids(pixels, catalogue, *raw)
    path = stage/'native_island_ids.npy'
    with path.open('xb') as stream:
        np.save(stream, pixels, allow_pickle=False)
    geometry_path = root/'geometry/result.json'
    geometry = json.loads(geometry_path.read_text(encoding='utf8'))
    inputs = json.loads((root/'build.json').read_text(encoding='utf8'))['inputs']
    report.update(schema_version=1, kind='native-island-id-ownership', source_sha256=sha(c['_source']),
        config_sha256=sha(c['_config']), scene=c.get('scene'), size=c['size'], uv=c.get('uv', 'SimpleBake'),
        geometry_sha256=geometry['geometry_sha256'], geometry_record_sha256=sha(geometry_path),
        catalogue_file_sha256=sha(stage/'islands.json'), native_ids_sha256=sha(path),
        raw_sha256={mode: sha(stage/(mode + '_raw.npy')) for mode in ('direct', 'indirect')},
        build_inputs_sha256=hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        helper_sha256=sha(__file__), worker_sha256=sha(Path(__file__).with_name('worker.py')), settings=settings)
    save_json(stage/'native_ownership.provenance.json', report)
    return report


def load_native_ids(c, root, catalogue):
    """Strict same-candidate proof for ownership, response and AO; no native reuse guessing."""
    from common import sha
    root = Path(root)
    stage = root/'bake'
    record = json.loads((stage/'native_ownership.provenance.json').read_text(encoding='utf8'))
    geometry_path = root/'geometry/result.json'
    geometry = json.loads(geometry_path.read_text(encoding='utf8'))
    expected = {'schema_version': 1, 'kind': 'native-island-id-ownership', 'method': 'native-island-id',
        'source_sha256': sha(c['_source']), 'config_sha256': sha(c['_config']), 'scene': c.get('scene'),
        'size': c['size'], 'uv': c.get('uv', 'SimpleBake'), 'geometry_sha256': geometry['geometry_sha256'],
        'geometry_record_sha256': sha(geometry_path), 'catalogue_file_sha256': sha(stage/'islands.json'),
        'catalogue_identity_sha256': catalogue_identity(catalogue), 'native_ids_sha256': sha(stage/'native_island_ids.npy'),
        'helper_sha256': sha(__file__), 'worker_sha256': sha(Path(__file__).with_name('worker.py'))}
    if any(record.get(key) != value for key, value in expected.items()):
        raise ValueError('Native ownership source/config/geometry/UV/catalogue/tool/image provenance differs')
    build = json.loads((root/'build.json').read_text(encoding='utf8'))
    inputs = build['inputs']
    if record.get('build_inputs_sha256') != hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(',', ':')).encode()).hexdigest():
        raise ValueError('Native ownership candidate build inputs changed')
    outputs={name.replace('\\','/'):digest for name,digest in build.get('stages',{}).get('bake',{}).get('outputs',{}).items()}
    for name in ('native_ownership.provenance.json','native_island_ids.npy','islands.json','direct_raw.npy','indirect_raw.npy'):
        if outputs.get('bake/'+name)!=sha(stage/name):
            raise ValueError('Native ownership completed bake output changed or is unverified: '+name)
    for name, digest in inputs.items():
        if name not in ('reuse_lightmaps', 'recombine', 'rebake_ao', 'env_converter') and sha(name) != digest:
            raise ValueError('Native ownership source/tool input changed: ' + name)
    raw = []
    for mode in ('direct', 'indirect'):
        path = stage/(mode + '_raw.npy')
        if record.get('raw_sha256', {}).get(mode) != sha(path):
            raise ValueError('Native transport changed after native ownership')
        raw.append(np.load(path, allow_pickle=False))
    image = np.load(stage/'native_island_ids.npy', allow_pickle=False)
    validate_native_ids(image, catalogue, *raw)
    return image, record
