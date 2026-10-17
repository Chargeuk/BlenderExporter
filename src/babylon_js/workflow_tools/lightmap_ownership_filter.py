"""Opt-in native-owner box reduction and proven chart-specific bilinear padding.

No raster ownership is inferred from geometry. Geometry determines filtering
support only; actual native FACE IDs determine every source sample owner.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
METHOD = 'native-owner-box-bilinear-v1'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class OwnerFilterConflict(ValueError):
    def __init__(self, report):
        self.report = report
        super().__init__('Owner-aware delivery filtering conflict: ' + json.dumps(report['conflicts'][:12]))


def load_authority(setting):
    """Hash-check declared source/native evidence, then validate actual FACE IDs."""
    if not isinstance(setting, dict) or set(setting) != {'manifest', 'sha256'}:
        raise ValueError('Owner-aware filtering requires manifest and sha256')
    path = Path(setting['manifest']).resolve()
    if sha(path) != setting['sha256']:
        raise ValueError('Owner-aware manifest changed')
    authority = read(path)
    if authority.get('schema_version') != 1 or authority.get('method') != METHOD:
        raise ValueError('Unsupported owner-aware delivery authority')
    required = {'export_source', 'native_source', 'native_config', 'snapshot', 'capture_receipt',
                'catalogue', 'native_ids', 'labels', 'direct_raw', 'indirect_raw', 'native_provenance', 'combined'}
    if set(authority.get('inputs', {})) != required:
        raise ValueError('Complete explicit owner-aware input authority required')
    for role, record in authority['inputs'].items():
        if set(record) != {'path', 'sha256'} or sha(record['path']) != record['sha256']:
            raise ValueError('Owner-aware input changed: ' + role)
    inputs = authority['inputs']
    config = read(inputs['native_config']['path'])
    snapshot = read(inputs['snapshot']['path'])
    receipt = read(inputs['capture_receipt']['path'])
    provenance = read(inputs['native_provenance']['path'])
    if (receipt.get('kind') != 'full-current-native-island-id-capture' or receipt.get('source_saved') is not False
            or receipt.get('native_radiance_baked') is not False
            or receipt.get('source_sha256') != inputs['native_source']['sha256']
            or receipt.get('config_sha256') != inputs['native_config']['sha256']
            or snapshot.get('source_sha256') != inputs['native_source']['sha256']
            or receipt.get('geometry', {}).get('geometry_sha256') != authority.get('geometry_sha256')):
        raise ValueError('Native source/config/geometry capture authority differs')
    for name, role in (('snapshot.json', 'snapshot'), ('islands.json', 'catalogue'), ('native_island_ids.npy', 'native_ids')):
        if receipt.get('files', {}).get(name) != inputs[role]['sha256']:
            raise ValueError('Native capture file proof differs: ' + name)
    if (provenance.get('source_sha256') != inputs['native_source']['sha256']
            or provenance.get('config_sha256') != inputs['native_config']['sha256']):
        raise ValueError('Native transport provenance differs from current source/config')
    catalogue = read(inputs['catalogue']['path'])
    ownership = module('_delivery_native_ownership', HERE.parent/'environment_tools/native_ownership.py')
    labels, verified = ownership.validate_native_ids(
        np.load(inputs['native_ids']['path'], allow_pickle=False), catalogue,
        np.load(inputs['direct_raw']['path'], allow_pickle=False),
        np.load(inputs['indirect_raw']['path'], allow_pickle=False))
    names = [row['object'] for row in snapshot['uv']['objects']]
    if (config.get('scene') != authority.get('scene') or config.get('uv') != authority.get('uv')
            or config.get('size') != labels.shape[0] or config.get('contributors') != {'names': []}
            or set(config.get('receivers', {})) != {'names'}
            or sorted(config['receivers']['names']) != sorted(names)
            or (Path(inputs['native_config']['path']).parent/config['blend']).resolve() != Path(inputs['native_source']['path']).resolve()):
        raise ValueError('Exact full-current native scope/source/UV/size required')
    if (provenance.get('method') != 'native-island-id'
            or provenance.get('geometry_sha256') != authority['geometry_sha256']
            or provenance.get('uv') != authority['uv'] or provenance.get('size') != labels.shape[0]
            or provenance.get('catalogue_file_sha256') != inputs['catalogue']['sha256']
            or provenance.get('catalogue_identity_sha256') != verified['catalogue_identity_sha256']
            or provenance.get('native_ids_sha256') != inputs['native_ids']['sha256']
            or provenance.get('raw_sha256') != {key: inputs[key+'_raw']['sha256'] for key in ('direct', 'indirect')}
            or provenance.get('actual_owner_capture_sha256') != inputs['capture_receipt']['sha256']):
        raise ValueError('Native transport/catalogue/owner provenance differs')
    declared_labels = np.load(inputs['labels']['path'], allow_pickle=False)
    if not np.array_equal(labels, declared_labels) or verified['unsampled_island_ids']:
        raise ValueError('Actual native labels/complete island coverage differ')
    # The exact current catalogue must cover every saved evaluated UV triangle once.
    rows = {}
    for row in snapshot['uv']['triangles']:
        key = (row['object'], np.asarray(row['uv'], dtype='<f4').tobytes())
        rows.setdefault(key, []).append((row['object'], row['triangle']))
    used = set()
    for chart in catalogue:
        for triangle in chart['triangles']:
            matches = rows.get((chart['object'], np.asarray(triangle, dtype='<f4').tobytes()), [])
            if len(matches) != 1 or matches[0] in used:
                raise ValueError('Actual catalogue triangle membership missing/ambiguous')
            used.add(matches[0])
    if len(used) != len(snapshot['uv']['triangles']):
        raise ValueError('Actual catalogue omits current receiver triangles')
    selected = authority.get('owners')
    by_id = {row['id']: row for row in catalogue}
    if not isinstance(selected, list) or not selected or len(selected) != len({row.get('id') for row in selected}):
        raise ValueError('Unique explicit owner selection required')
    for row in selected:
        chart = by_id.get(row.get('id'))
        if (set(row) != {'id', 'object', 'polygon_root'} or chart is None
                or any(chart.get(key) != row[key] for key in ('object', 'polygon_root'))):
            raise ValueError('Selected actual owner identity differs')
    return authority, snapshot, catalogue, labels, verified


def validate_context(setting, context, combined):
    """Read-only exact saved export source and evaluated current UV/geometry proof."""
    import bpy
    if not bpy.app.background:
        raise ValueError('Owner-aware native delivery requires isolated background export')
    authority, snapshot, _, _, verified = load_authority(setting)
    source = authority['inputs']['export_source']
    if Path(bpy.data.filepath).resolve() != Path(source['path']).resolve() or sha(bpy.data.filepath) != source['sha256']:
        raise ValueError('Owner-aware export requires exact declared saved source')
    if sha(combined) != authority['inputs']['combined']['sha256']:
        raise ValueError('Owner-aware accepted combined master differs')
    from .inspect_blender import snapshot_uv2
    expected = snapshot['uv']['provenance']
    names = [row['object'] for row in snapshot['uv']['objects']]
    current = snapshot_uv2(names, uv=authority['uv'], expected_scene=context.scene.name)
    for key in ('geometry_uv1_identity_sha256', 'uv2_identity_sha256'):
        if current['provenance'][key] != expected[key]:
            raise ValueError('Owner-aware current geometry/UV identity differs: ' + key)
    if context.scene.name != authority['scene']:
        raise ValueError('Owner-aware physical scene differs')
    return {'manifest_sha256': setting['sha256'], 'export_source_sha256': source['sha256'],
            'native_source_sha256': authority['inputs']['native_source']['sha256'],
            'geometry_sha256': authority['geometry_sha256'], 'uv2_identity_sha256': expected['uv2_identity_sha256'],
            'native_ownership': verified, 'scene': context.scene.name, 'read_only': True}


def triangle_support(triangle, centres):
    """Positive-area triangle/square intersection via separating axes.

    Pixel centres are top-first integers. A bilinear texel contributes within
    distance one of its centre. Boundary-only contact has zero filter weight.
    """
    triangle = np.asarray(triangle, np.float64)
    if triangle.shape != (3, 2) or not np.isfinite(triangle).all():
        raise ValueError('Finite UV triangles required')
    edges = np.roll(triangle, -1, axis=0)-triangle
    if abs(float(edges[0,0]*(-edges[2,1])-edges[0,1]*(-edges[2,0]))) == 0:
        raise ValueError('Degenerate triangle cannot prove bilinear support')
    result = np.ones(len(centres), bool)
    axes = [np.array([1., 0.]), np.array([0., 1.])]
    axes += [np.array([-edge[1], edge[0]]) for edge in edges]
    for axis in axes:
        if not np.any(axis):
            continue
        projected = triangle @ axis
        middle = centres @ axis
        half = np.abs(axis).sum()
        result &= (middle+half > projected.min()) & (middle-half < projected.max())
    return result


def pixel_triangles(chart, size):
    triangles = np.asarray(chart['triangles'], np.float64).copy()
    if (triangles < 0).any() or (triangles > 1).any():
        raise ValueError('Owner-aware filtering requires in-bounds UVs')
    triangles[..., 0] = triangles[..., 0]*size-.5
    triangles[..., 1] = (1-triangles[..., 1])*size-.5
    return triangles


def chart_support(chart, size):
    triangles = pixel_triangles(chart, size)
    points = triangles.reshape(-1, 2)
    lo = np.maximum(np.floor(points.min(0)-1).astype(int), 0)
    hi = np.minimum(np.ceil(points.max(0)+1).astype(int), size-1)
    xx, yy = np.meshgrid(np.arange(lo[0], hi[0]+1), np.arange(lo[1], hi[1]+1))
    centres = np.column_stack((xx.ravel(), yy.ravel()))
    selected = np.zeros(len(centres), bool)
    for triangle in triangles:
        selected |= triangle_support(triangle, centres)
    return centres[selected]


def reduce_owned(array, standard, labels, catalogue, owners):
    """Repair only declared owner's integer box blocks and uncontested support.

    Unowned native samples never enter a repaired average. Padding copies the
    closest same-owner reduced sample, not an inferred owner or a blur.
    """
    if (array.dtype != np.float32 or standard.dtype != np.float32 or array.ndim != 3
            or array.shape[2] not in (3, 4) or array.shape[0] != array.shape[1]
            or standard.shape[0] != standard.shape[1] or labels.shape != array.shape[:2]
            or standard.shape[2] != array.shape[2] or not np.isfinite(array).all() or not np.isfinite(standard).all()):
        raise ValueError('Matching finite float32 square source/delivery arrays required')
    size = standard.shape[0]
    if size >= array.shape[0] or array.shape[0] % size:
        raise ValueError('Owner-aware repair requires an exact integer box downsample')
    factor = array.shape[0]//size
    by_id = {row['id']: row for row in catalogue}
    selected_ids = [row['id'] for row in owners]
    if len(selected_ids) != len(set(selected_ids)) or not selected_ids or not set(selected_ids) <= set(by_id):
        raise ValueError('Actual selected owner IDs required')
    declared = np.array(sorted(by_id), np.int32)
    if labels.dtype != np.int32 or not np.isin(labels, np.append(declared, 0)).all():
        raise ValueError('Exact validated int32 native labels required')
    blocks = labels.reshape(size, factor, size, factor).transpose(0, 2, 1, 3).reshape(size, size, -1)
    values = array.reshape(size, factor, size, factor, -1).transpose(0, 2, 1, 3, 4).reshape(size, size, factor*factor, -1)
    supports = {owner: chart_support(by_id[owner], size) for owner in selected_ids}
    conflicts = []
    required = {}
    for owner, points in supports.items():
        for x, y in points:
            required.setdefault((int(x), int(y)), set()).add(owner)
    coords = np.asarray(list(required), np.int32)
    # Validate complete support against every retained chart, including unselected
    # subpixel triangles whose actual native sample may lie outside this block.
    for chart in catalogue:
        triangles = pixel_triangles(chart, size)
        points = triangles.reshape(-1, 2)
        near = np.all((coords > points.min(0)-1) & (coords < points.max(0)+1), axis=1)
        indices = np.flatnonzero(near)
        if not len(indices):
            continue
        hits = np.zeros(len(indices), bool)
        for triangle in triangles:
            hits |= triangle_support(triangle, coords[indices])
        for x, y in coords[indices[hits]]:
            wanted = required[(int(x), int(y))]
            if wanted != {chart['id']}:
                conflicts.append({'kind': 'shared-bilinear-support', 'texel': [int(x), int(y)],
                                  'requested_owners': sorted(wanted), 'other_owner': chart['id'], 'object': chart['object']})
    for owner, points in supports.items():
        for x, y in points:
            foreign = np.unique(blocks[y, x]);foreign = foreign[(foreign != 0) & (foreign != owner)]
            if len(foreign):
                conflicts.append({'kind': 'foreign-native-owned-block', 'texel': [int(x), int(y)],
                                  'requested_owners': [owner], 'other_owners': foreign.tolist()})
        touched = np.any(blocks == owner, axis=2)
        mixed = touched & np.any((blocks != 0) & (blocks != owner), axis=2)
        for y, x in np.argwhere(mixed):
            conflicts.append({'kind': 'multiowner-box', 'texel': [int(x), int(y)],
                              'requested_owners': [owner], 'actual_ids': np.unique(blocks[y, x]).tolist()})
    report = {'method': METHOD, 'source_size': array.shape[0], 'target_size': size, 'factor': factor,
              'owners': selected_ids, 'complete_bilinear_support_texels': len(required),
              'conflict_count': len(conflicts), 'conflicts': conflicts, 'owner_inference': False}
    if conflicts:
        raise OwnerFilterConflict(report)
    result = standard.copy()
    modified = np.zeros((size, size), bool)
    reports = []
    for owner, points in supports.items():
        mask = blocks == owner
        count = mask.sum(2)
        touched = count > 0
        seeds_yx = np.argwhere(touched)
        if not len(seeds_yx):
            raise ValueError('Selected owner has no sampled delivery blocks')
        sums = (values*mask[..., None]).sum(2, dtype=np.float64)
        partial = touched & (count < factor*factor)
        # Full same-owner blocks retain the established box reducer's exact
        # floating-point arithmetic. Only contaminated partial blocks change.
        result[partial] = (sums[partial]/count[partial, None]).astype(np.float32)
        corrected = result[touched].copy()
        modified |= partial
        padded = 0
        for x, y in points:
            if touched[y, x]:
                continue
            distances = ((seeds_yx-np.array([y, x]))**2).sum(1)
            nearest = int(np.argmin(distances))  # stable y/x order, same-owner only
            result[y, x] = corrected[nearest]
            modified[y, x] = True;padded += 1
        reports.append({'id': owner, 'owned_target_blocks': int(touched.sum()),
                        'partial_blocks': int(((count > 0) & (count < factor*factor)).sum()),
                        'same_owner_padding_texels': padded})
    if not np.array_equal(result[~modified].view(np.uint32), standard[~modified].view(np.uint32)):
        raise ValueError('Unrelated delivery texels changed')
    report.update({'owner_blocks': reports, 'modified_texels': int(modified.sum()),
                   'unrelated_texels_bit_exact': True, 'padding': 'nearest actual same-owner reduced sample',
                   'unowned_samples_excluded': True, 'accepted_appearance': False})
    return result, report


def prepare(array, standard, setting, report_path=None, *, context_proof=None):
    authority, _, catalogue, labels, verified = load_authority(setting)
    if array.shape[0] != authority.get('source_size') or standard.shape[0] != authority.get('target_size'):
        raise ValueError('Declared owner-aware source/target size differs')
    if (not isinstance(context_proof, dict) or context_proof.get('read_only') is not True
            or context_proof.get('manifest_sha256') != setting['sha256']
            or context_proof.get('export_source_sha256') != authority['inputs']['export_source']['sha256']
            or context_proof.get('native_source_sha256') != authority['inputs']['native_source']['sha256']
            or context_proof.get('geometry_sha256') != authority['geometry_sha256']
            or context_proof.get('scene') != authority['scene']):
        raise ValueError('Verified current export geometry/UV context proof required')
    try:
        result, report = reduce_owned(array, standard, labels, catalogue, authority['owners'])
    except OwnerFilterConflict as error:
        if report_path:
            Path(report_path).write_text(json.dumps(error.report, indent=2)+'\n', encoding='utf-8')
        raise
    report['authority_sha256'] = setting['sha256']
    report['authority'] = setting['manifest']
    report['inputs'] = authority['inputs']
    report['filter_helper_sha256'] = sha(__file__)
    report['native_ownership'] = verified
    for role, record in authority['inputs'].items():
        if sha(record['path']) != record['sha256']:
            raise ValueError('Owner-aware input changed during preparation: ' + role)
    if report_path:
        Path(report_path).write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    return result


def main():
    """Read-only feasibility inspection; writes evidence JSON, never a texture."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--source', required=True, help='Actual compensated scene-linear EXR')
    parser.add_argument('--source-sha256', required=True)
    parser.add_argument('--target', required=True, type=int)
    parser.add_argument('--output', required=True, help='New evidence JSON; no image is written')
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        raise ValueError('Inspection output must be new')
    if sha(args.source) != args.source_sha256:
        raise ValueError('Compensated diagnostic source changed')
    authority, _, catalogue, labels, verified = load_authority({'manifest': args.manifest, 'sha256': args.sha256})
    if args.target != authority.get('target_size'):
        raise ValueError('Inspection target differs from explicit authority')
    core = module('_owner_filter_ktx_core', HERE.parent/'ktx_conversion.py')
    array = core.read_float_hdr(Path(args.source)).astype(np.float32)
    standard = core.resize_lightmap(array, {'size': args.target, 'color_space': 'linear'}, True)
    try:
        _, report = reduce_owned(array, standard, labels, catalogue, authority['owners'])
        report['status'] = 'filter-support-feasible-not-appearance-accepted'
    except OwnerFilterConflict as error:
        report = error.report
        report['status'] = 'blocked-filter-support-conflicts-no-texture-written'
    report.update({'authority_sha256': args.sha256, 'source_sha256': args.source_sha256,
                   'native_ownership': verified, 'texture_written': False})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
