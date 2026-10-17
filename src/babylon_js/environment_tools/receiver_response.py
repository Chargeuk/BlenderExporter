"""Measured receiver material response; native transport arrays stay unchanged."""
from pathlib import Path
import json
import numpy as np

def validate_settings(config):
    settings = config.get('receiver_response', {})
    if not isinstance(settings, dict) or not isinstance(settings.get('enabled', False), bool):
        raise ValueError('receiver_response must be an object with a boolean enabled flag')
    if settings.get('enabled') and (settings.get('schema_version') != 1
                                    or set(settings) - {'enabled', 'schema_version'}):
        raise ValueError('Enabled receiver_response requires schema_version 1 and no unknown settings')
    return settings.get('enabled', False)


def validate_image(array, labels, name, *, native=False):
    if array.shape != (*labels.shape, 4) or array.dtype != np.float32:
        raise ValueError(name + ' must be matching float32 RGBA')
    if not np.isfinite(array).all() or (array[:, :, :3] < 0).any():
        raise ValueError(name + ' contains non-finite or negative RGB')
    if native and not np.array_equal(array[:, :, 3], (labels > 0).astype(np.float32)):
        raise ValueError(name + ' coverage differs from native ownership')


def factor_from_captures(base, diffuse, labels, catalogue, captured_catalogue, patches, cap_objects, *, native_ids=None):
    """COLOR alpha is deliberately ignored; only native COLOR-excluded coverage owns pixels."""
    # Blender's bundled capture runtime has NumPy; ownership/application runs in
    # the separate image-processing runtime with SciPy. Keep capture imports light.
    from reuse import validate_ownership
    validate_ownership(labels, catalogue, captured_catalogue, labels.shape[0],native_ids=native_ids)
    # Exact IDs are needed for patch masks, unlike catalogue reuse which permits enumeration changes.
    if catalogue != captured_catalogue:
        raise ValueError('Response catalogue labels differ from the native bake')
    validate_image(base, labels, 'Source Base Colour')
    validate_image(diffuse, labels, 'Measured Diffuse Colour')
    names = [row['object'] for row in patches]
    if len(names) != len(set(names)) or set(names) != set(cap_objects):
        raise ValueError('Response patches differ from selected capped receivers')
    owned = labels > 0
    response = diffuse.copy()
    for row in patches:
        ids = [island['id'] for island in catalogue if island['object'] == row['object']]
        if not ids:
            raise ValueError('Unknown capped receiver: ' + row['object'])
        mask = np.isin(labels, ids)
        patch = row['pixels']
        validate_image(patch, labels, 'Capped Diffuse Colour')
        response[mask, :3] = patch[mask, :3]
    small = base[:, :, :3] < 1e-8
    undefined = small & owned[:, :, None]
    if (response[:, :, :3][undefined] != 0).any():
        raise ValueError('Nonzero Diffuse Colour at near-zero source Base Colour')
    factor = np.zeros_like(base)
    factor[owned, :3] = 1
    np.divide(response[:, :, :3], base[:, :, :3], out=factor[:, :, :3],
              where=(~small & owned[:, :, None]))
    factor[:, :, 3] = owned
    response[~owned] = 0
    response[:, :, 3] = owned
    if not np.isfinite(factor).all():
        raise ValueError('Non-finite receiver response factor')
    return factor, response, {'owned_pixels': int(owned.sum()),
        'zero_base_and_diffuse_channels': int(undefined.sum()), 'cap_objects': names,
        'factor_rgb_min': factor[owned, :3].min(0).tolist(),
        'factor_rgb_max': factor[owned, :3].max(0).tolist()}


def apply_response(native, factor, labels):
    from ownership import dilate
    validate_image(native, labels, 'Native transport', native=True)
    validate_image(factor, labels, 'Response factor', native=True)
    owned = labels > 0
    derived = native.copy()
    derived[owned, :3] *= factor[owned, :3]
    if not np.isfinite(derived).all():
        raise ValueError('Non-finite response application')
    if not np.array_equal(derived[:, :, 3], native[:, :, 3]):
        raise ValueError('Response application changed alpha')
    return derived, dilate(derived, labels)


def save_new_array(path, array):
    """Never replace a capture, native master or earlier derived output."""
    path = Path(path)
    with path.open('xb') as stream:
        np.save(stream, array, allow_pickle=False)


def derive(c, root, stage, write_exr):
    """Apply freshly measured response separately to raw and independently processed masters."""
    from common import sha, save_json
    if not validate_settings(c):
        raise ValueError('Enable receiver_response before applying material response')
    capture = root/'response-capture'
    record = json.loads((capture/'capture.provenance.json').read_text())
    if (record.get('schema_version') != 1 or record['source_sha256'] != sha(c['_source'])
            or record['config_sha256'] != sha(c['_config'])
            or record['size'] != c['size'] or record['uv'] != c.get('uv', 'SimpleBake')
            or record['metallic_cap'] != c.get('metallic_cap', .8)):
        raise ValueError('Response capture source/material/UV/cap identity differs')
    geometry = json.loads((root/'geometry/result.json').read_text())
    if geometry['geometry_sha256'] != record['geometry']['geometry_sha256']:
        raise ValueError('Response geometry differs from native transport input')
    bake = json.loads((root/'bake/result.json').read_text())
    from physical_colour_adjustment import verify_native_response
    verify_native_response(bake,record)
    if bake['source_sha256'] != record['source_sha256']:
        raise ValueError('Response capture differs from native transport source')
    for name, digest in record['files'].items():
        if Path(name).name != name or sha(capture/name) != digest:
            raise ValueError('Response capture file changed: ' + name)
    catalogue = json.loads((root/'bake/islands.json').read_text())
    captured = json.loads((capture/'islands.json').read_text())
    labels_path = root/'ownership/labels.npy'
    labels = np.load(labels_path, allow_pickle=False)
    native_patches = json.loads((root/'bake/metal_patches.json').read_text())
    if set(record['cap_objects']) != {row['object'] for row in native_patches}:
        raise ValueError('Native transport and response selected caps differ')
    patch_rows = json.loads((capture/'patches.json').read_text())
    if any(Path(row['file']).name != row['file'] for row in patch_rows):
        raise ValueError('Response patch path must remain inside its capture stage')
    patches = [dict(row, pixels=np.load(capture/row['file'], allow_pickle=False)) for row in patch_rows]
    native_ids=None
    from native_ownership import ownership_method
    if ownership_method(c)=='native-island-id':
        from native_ownership import load_native_ids, validate_native_ids
        native_ids,native_record=load_native_ids(c,root,catalogue)
        captured_ids=np.load(capture/'native_island_ids.npy',allow_pickle=False)
        validate_native_ids(captured_ids,captured,native_ids)
        if not np.array_equal(captured_ids,native_ids):
            raise ValueError('Response capture native pixel ownership differs from transport')
    factor, response, report = factor_from_captures(
        np.load(capture/'source_base_colour.npy', allow_pickle=False),
        np.load(capture/'diffuse_colour.npy', allow_pickle=False), labels, catalogue, captured,
        patches, record['cap_objects'],native_ids=native_ids)
    save_new_array(stage/'factor.npy', factor)
    save_new_array(stage/'diffuse_colour_owned.npy', response)
    inputs = {str(labels_path.relative_to(root)): sha(labels_path),
              'response-capture/capture.provenance.json': sha(capture/'capture.provenance.json'),
              'bake/islands.json': sha(root/'bake/islands.json')}
    if native_ids is not None:
        inputs['bake/native_ownership.provenance.json']=sha(root/'bake/native_ownership.provenance.json')
        inputs['bake/native_island_ids.npy']=sha(root/'bake/native_island_ids.npy')
        inputs['response-capture/native_island_ids.npy']=sha(capture/'native_island_ids.npy')
    # Preserve the independently filtered native EXRs as named, hashed physical masters too.
    for mode in ('direct', 'indirect'):
        native_master = root/'masters'/(mode+'_final.exr')
        inputs[str(native_master.relative_to(root))] = sha(native_master)
    outputs = []
    for mode in ('direct', 'indirect'):
        for variant, source in (('raw', root/'ownership'/(mode+'_raw.npy')),
                                ('processed', root/'process'/mode/'interiors.npy')):
            digest = sha(source)
            inputs[str(source.relative_to(root))] = digest
            native = np.load(source, allow_pickle=False)
            interior, filled = apply_response(native, factor, labels)
            name = mode+'_response_'+variant
            save_new_array(stage/(name+'_owned.npy'), interior)
            # Shared linear EXR writer keeps the derived delivery format identical to native delivery.
            master = root/'masters'/(name+'.exr')
            if master.exists() or (stage/(name+'.npy')).exists():
                raise ValueError('Refusing to overwrite response output: ' + str(master))
            write_exr(filled, name, stage)
            if sha(source) != digest:
                raise ValueError('Native transport changed during response application')
            outputs.append({'native': str(source.relative_to(root)), 'native_sha256': digest,
                'owned': name+'_owned.npy', 'dilated_master': str(master.relative_to(root)),
                'master_sha256': sha(master), 'alpha_preserved_in_owned_image': True})
    if any(sha(root/name) != digest for name, digest in inputs.items()):
        raise ValueError('A native master or response input changed during application')
    report.update(schema_version=1, kind='derived-receiver-response-application',
        source_sha256=record['source_sha256'], geometry_sha256=geometry['geometry_sha256'],
        scene=record.get('scene'),physical_colour_adjustment=record.get('physical_colour_adjustment'),
        inputs=inputs, outputs=outputs, native_transport_masters_preserved=True,
        operation='Owned RGB *= measured Diffuse Colour / actual source Base Colour; then gap-only ownership dilation',
        response_capture='response-capture/capture.provenance.json',
        coverage_basis='Native COLOR-excluded zero-margin alpha and exact island labels',
        combination_inputs=['direct_response_processed.exr', 'indirect_response_processed.exr'])
    save_json(stage/'result.json', report)
