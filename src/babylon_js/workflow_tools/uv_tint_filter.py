"""Read-only finite UV2 colour-overlay probes; standalone NumPy/Pillow, no Blender import."""
import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np


def srgb_decode(values):
    values = np.asarray(values, dtype=np.float64)
    return np.where(values <= .04045, values / 12.92, ((values + .055) / 1.055) ** 2.4)


def decode_rgba8(rgba, *, assume_srgb, alpha_mode, texture_role):
    """Explicit caller declarations: encoded tint RGB, straight linear influence alpha."""
    if assume_srgb is not True:
        raise ValueError('Explicit assume_srgb=True required; image tags are not a colour-space proof')
    if alpha_mode != 'straight':
        raise ValueError('Nonpremultiplied (straight) RGBA8 required')
    if texture_role != 'uv2-colour-overlay':
        raise ValueError('Only uv2-colour-overlay supported; RGBD alpha is a divisor, not influence or opacity')
    rgba = np.asarray(rgba)
    if rgba.dtype != np.uint8 or rgba.ndim != 3 or rgba.shape[2] != 4 or min(rgba.shape[:2]) < 1:
        raise ValueError('Expected nonempty HxWx4 uint8 RGBA8')
    result = rgba.astype(np.float64) / 255
    # GPU sRGB sampling decodes each RGB texel BEFORE interpolation. Alpha stays linear.
    result[..., :3] = srgb_decode(result[..., :3])
    return result


def bilinear(decoded, uv, *, image_origin):
    """Base-level clamp-to-edge pixel-centre sampling, UV origin always bottom-left."""
    if image_origin not in ('top-left', 'bottom-left'):
        raise ValueError('Explicit top-left or bottom-left image_origin required')
    uv = np.asarray(uv, dtype=np.float64)
    if uv.ndim != 2 or uv.shape[1] != 2 or not np.isfinite(uv).all() or np.any((uv < 0) | (uv > 1)):
        raise ValueError('Probe UVs must be finite Nx2 within [0,1]')
    height, width = decoded.shape[:2]
    u, v = uv.T
    x = np.clip(u * width - .5, 0, width - 1)
    y = np.clip((1 - v if image_origin == 'top-left' else v) * height - .5, 0, height - 1)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    x1, y1 = np.minimum(x0 + 1, width - 1), np.minimum(y0 + 1, height - 1)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    return ((decoded[y0, x0] * (1 - fx) + decoded[y0, x1] * fx) * (1 - fy)
            + (decoded[y1, x0] * (1 - fx) + decoded[y1, x1] * fx) * fy)


def effective_multiplier(filtered_rgba, strength):
    influence = strength * filtered_rgba[..., 3:4]
    return 1 - influence + 2 * filtered_rgba[..., :3] * influence


def _positive_int(value, name, minimum=1):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(name + ' must be an integer >= ' + str(minimum))


def _finite(value, name, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(name + ' must be finite numeric')
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(name + ' outside allowed range')


def _expected_targets(expected, objects):
    if not isinstance(expected, dict) or expected.get('schema_version') != 1:
        raise ValueError('Expected multiplier JSON requires schema_version 1')
    default = expected.get('default_multiplier', [1, 1, 1])
    if default != [1, 1, 1]:
        raise ValueError('default_multiplier must be neutral [1,1,1]')
    overrides = expected.get('objects', {})
    if not isinstance(overrides, dict) or any(name not in objects for name in overrides):
        raise ValueError('Expected object overrides must name snapshot participants')
    result = {}
    for name in objects:
        values = overrides.get(name, default)
        if not isinstance(values, list) or len(values) != 3:
            raise ValueError('Expected RGB multiplier needs three numbers for ' + name)
        for value in values:
            _finite(value, 'Expected multiplier', maximum=2)
        result[name] = np.asarray(values, dtype=np.float64)
    return result


def _probe_plan(uv, dimensions, edge_samples, interior_pixel_spacing):
    subdivisions = 0
    if interior_pixel_spacing is not None:
        pixel_edges = (np.roll(uv, -1, axis=0) - uv) * dimensions
        ratio = float(np.linalg.norm(pixel_edges, axis=1).max()) / interior_pixel_spacing
        if not math.isfinite(ratio):
            raise ValueError('Interior pixel spacing produces nonfinite subdivisions')
        subdivisions = max(3, math.ceil(ratio))
    interior_count = (subdivisions - 1) * (subdivisions - 2) // 2 if subdivisions else 0
    return 4 + 3 * edge_samples + interior_count, subdivisions


def _probes(uv, edge_samples, subdivisions):
    yield 'centroid', uv.mean(axis=0)
    for point in uv:
        yield 'vertex', point
    for first, second in ((0, 1), (1, 2), (2, 0)):
        for k in range(1, edge_samples + 1):
            t = k / (edge_samples + 1)
            yield 'edge', uv[first] * (1 - t) + uv[second] * t
    for i in range(1, subdivisions - 1):
        for j in range(1, subdivisions - i):
            yield 'interior', (uv[0] * i + uv[1] * j + uv[2] * (subdivisions - i - j)) / subdivisions


def _metrics(values, errors, neutral, strength, multiplier_tolerance, neutral_alpha_tolerance):
    if not len(values):
        return {'sample_count': 0, 'maximum_linear_multiplier_error': None,
                'mean_linear_multiplier_error': None, 'multiplier_tolerance_violations': 0,
                'maximum_alpha': None, 'maximum_alpha_influence': None,
                'neutral_alpha_influence_violations': 0}
    influence = strength * values[:, 3]
    return {'sample_count': len(values), 'maximum_linear_multiplier_error': float(errors.max()),
            'mean_linear_multiplier_error': float(errors.mean()),
            'multiplier_tolerance_violations': int(np.count_nonzero(errors > multiplier_tolerance)),
            'maximum_alpha': float(values[:, 3].max()), 'maximum_alpha_influence': float(influence.max()),
            'neutral_alpha_influence_violations': int(np.count_nonzero(influence > neutral_alpha_tolerance)) if neutral else 0}


def diagnose(snapshot, rgba, expected, *, strength, image_origin, assume_srgb,
             alpha_mode, texture_role='uv2-colour-overlay', edge_samples=4,
             interior_pixel_spacing=None, max_samples=1000000,
             multiplier_tolerance=.05, neutral_alpha_tolerance=0.0, max_examples=5):
    """Finite diagnostic only. complete means the requested probe plan finished, never acceptance."""
    _finite(strength, 'strength', maximum=1)
    _finite(multiplier_tolerance, 'multiplier_tolerance')
    _finite(neutral_alpha_tolerance, 'neutral_alpha_tolerance', maximum=1)
    _positive_int(edge_samples, 'edge_samples', 0)
    _positive_int(max_samples, 'max_samples')
    _positive_int(max_examples, 'max_examples', 0)
    if interior_pixel_spacing is not None:
        _finite(interior_pixel_spacing, 'interior_pixel_spacing')
        if interior_pixel_spacing == 0:
            raise ValueError('interior_pixel_spacing must be positive')
    if image_origin not in ('top-left', 'bottom-left'):
        raise ValueError('Explicit top-left or bottom-left image_origin required')
    decoded = decode_rgba8(rgba, assume_srgb=assume_srgb, alpha_mode=alpha_mode, texture_role=texture_role)
    if not isinstance(snapshot, dict) or snapshot.get('schema_version') != 1 or not isinstance(snapshot.get('triangles'), list):
        raise ValueError('Expected UV snapshot schema_version 1 with triangles')
    rows = snapshot['triangles']
    if not rows:
        raise ValueError('No intended UV triangles')
    identities = set()
    coordinates = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('object'), str) or not row['object']:
            raise ValueError('Triangle rows require object names')
        _positive_int(row.get('triangle'), 'triangle identity', 0)
        identity = (row['object'], row['triangle'])
        if identity in identities:
            raise ValueError('Duplicate evaluated triangle identity')
        identities.add(identity)
        uv = np.asarray(row.get('uv'), dtype=np.float64)
        if uv.shape != (3, 2) or not np.isfinite(uv).all() or np.any((uv < 0) | (uv > 1)):
            raise ValueError('Triangle UVs must be finite 3x2 within [0,1]')
        coordinates.append(uv)
    targets = _expected_targets(expected, {row['object'] for row in rows})
    height, width = decoded.shape[:2]
    triangle_records, object_records = [], {}
    remaining = max_samples
    for row, uv in zip(rows, coordinates):
        requested, subdivisions = _probe_plan(uv, [width, height], edge_samples, interior_pixel_spacing)
        count = min(requested, remaining)
        points, kinds = [], []
        if count:
            for kind, point in _probes(uv, edge_samples, subdivisions):
                kinds.append(kind)
                points.append(point)
                if len(points) == count:
                    break
        values = bilinear(decoded, np.asarray(points).reshape(-1, 2), image_origin=image_origin)
        multipliers = effective_multiplier(values, strength)
        target = targets[row['object']]
        errors = np.abs(multipliers - target).max(axis=1)
        neutral = bool(np.array_equal(target, [1, 1, 1]))
        metrics = _metrics(values, errors, neutral, strength, multiplier_tolerance, neutral_alpha_tolerance)
        record = {key: row[key] for key in ('object', 'triangle', 'chart', 'polygon', 'loops', 'vertices') if key in row}
        record.update(metrics, requested_sample_count=requested, complete=count == requested,
                      interior_subdivisions=subdivisions, expected_multiplier=target.tolist(), neutral=neutral,
                      sample_kind_counts={kind: kinds.count(kind) for kind in ('centroid', 'vertex', 'edge', 'interior')},
                      examples=[{'kind': kinds[index], 'uv': points[index].tolist(),
                                 'effective_multiplier': multipliers[index].tolist(),
                                 'alpha': float(values[index, 3]), 'alpha_influence': float(strength * values[index, 3]),
                                 'max_error': float(errors[index])}
                                for index in np.argsort(-errors, kind='stable')[:max_examples]])
        triangle_records.append(record)
        remaining -= count
        obj = object_records.setdefault(row['object'], {'object': row['object'], 'expected_multiplier': target.tolist(),
            'neutral': neutral, 'triangles': 0, 'requested_sample_count': 0, 'sample_count': 0, 'complete': True,
            'maximum_linear_multiplier_error': None, 'mean_linear_multiplier_error': None,
            'multiplier_tolerance_violations': 0, 'maximum_alpha': None, 'maximum_alpha_influence': None,
            'neutral_alpha_influence_violations': 0, 'error_sum': 0.0})
        obj['triangles'] += 1
        obj['requested_sample_count'] += requested
        obj['sample_count'] += count
        obj['complete'] &= record['complete']
        obj['error_sum'] += float(errors.sum())
        for key in ('multiplier_tolerance_violations', 'neutral_alpha_influence_violations'):
            obj[key] += metrics[key]
        for key in ('maximum_linear_multiplier_error', 'maximum_alpha', 'maximum_alpha_influence'):
            if metrics[key] is not None:
                obj[key] = max(obj[key] or 0.0, metrics[key])
    for obj in object_records.values():
        error_sum = obj.pop('error_sum')
        obj['mean_linear_multiplier_error'] = error_sum / obj['sample_count'] if obj['sample_count'] else None
    complete = all(record['complete'] for record in triangle_records)
    return {'schema_version': 1, 'kind': 'uv2-colour-overlay-filter-diagnostic', 'complete': complete,
        'status': 'diagnostic' if complete else 'incomplete',
        'snapshot_provenance': snapshot.get('provenance', {}),
        'inputs': {'rgba8_pixels_sha256': hashlib.sha256(np.asarray(rgba).tobytes()).hexdigest(),
                   'helper_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   'snapshot_json_sha256': hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest(),
                   'expected_json_sha256': hashlib.sha256(json.dumps(expected, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()},
        'method': 'Decode each sRGB RGB texel before bilinear RGB interpolation; interpolate linear alpha independently; effectiveMultiplier=1-strength*alpha+2*decodedRGB*strength*alpha.',
        'settings': {'dimensions': [width, height], 'image_origin': image_origin, 'uv_origin': 'bottom-left',
            'assume_srgb': assume_srgb, 'alpha_mode': alpha_mode, 'texture_role': texture_role,
            'strength': strength, 'address_mode': 'clamp-to-edge', 'filter': 'base-level bilinear',
            'pixel_coordinates': 'u*width-0.5; (1-v)*height-0.5 for top-left rows, v*height-0.5 for bottom-left',
            'edge_samples_per_edge': edge_samples, 'interior_pixel_spacing': interior_pixel_spacing,
            'interior_grid': 'strict interior barycentrics i/n,j/n,1-(i+j)/n; n=max(3,ceil(longest pixel edge/spacing))',
            'max_samples': max_samples, 'max_examples_per_triangle': max_examples,
            'multiplier_tolerance': multiplier_tolerance, 'neutral_alpha_influence_tolerance': neutral_alpha_tolerance},
        'totals': {'objects': len(object_records), 'triangles': len(rows),
            'requested_samples': sum(record['requested_sample_count'] for record in triangle_records),
            'sampled': max_samples - remaining, 'incomplete_triangles': sum(not record['complete'] for record in triangle_records),
            'multiplier_tolerance_violations': sum(record['multiplier_tolerance_violations'] for record in triangle_records),
            'neutral_alpha_influence_violations': sum(record['neutral_alpha_influence_violations'] for record in triangle_records)},
        'objects': [object_records[name] for name in sorted(object_records)], 'triangles': triangle_records,
        'limits': ['Finite probes never establish exhaustive triangle, texel ownership, filter-footprint or texture coverage acceptance. complete only means the requested finite plan was sampled.',
            'No mipmaps, anisotropy, derivatives, wrapping, lighting, alpha opacity or RGBD decoding; input is a straight RGBA8 colour-overlay image, including a separately decoded KTX image.',
            'Samples are ordered centroid, vertices, uniform edge points, interior grid; global cap can leave later triangles unsampled. Coincident probe positions may repeat. Means are probe-weighted, not area-weighted.',
            'Interior spacing bounds steps along triangle edges in image pixels, not a raster ownership mask or a guarantee to hit every pixel. Degenerate triangles remain finite probes.',
            'Tolerances report sampled deviations only. No automatic UV/image edits or acceptance verdict. Encoded-asset hash records provenance only; caller owns decoder, orientation, straight-alpha and sRGB declarations.']}


def _file_record(path):
    path = Path(path)
    return {'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def diagnose_files(snapshot_path, image_path, expected_path, *, encoded_asset_path=None, **settings):
    """Read authored PNG or an externally decoded KTX PNG without changing any input."""
    from PIL import Image
    image_path = Path(image_path)
    header = image_path.read_bytes()[:26]
    if len(header) < 26 or header[:8] != b'\x89PNG\r\n\x1a\n' or header[24:26] != bytes([8, 6]):
        raise ValueError('Input must be an 8-bit RGBA PNG; no implicit mode/bit-depth conversion')
    with Image.open(image_path) as image:
        if image.format != 'PNG' or image.mode != 'RGBA' or getattr(image, 'n_frames', 1) != 1:
            raise ValueError('Single-frame RGBA8 PNG required')
        rgba = np.asarray(image).copy()
    snapshot = json.loads(Path(snapshot_path).read_text(encoding='utf8'))
    expected = json.loads(Path(expected_path).read_text(encoding='utf8'))
    result = diagnose(snapshot, rgba, expected, **settings)
    result['inputs'].update(snapshot=_file_record(snapshot_path), image=_file_record(image_path),
                            expected=_file_record(expected_path), helper=_file_record(__file__))
    if encoded_asset_path is not None:
        result['inputs']['encoded_asset'] = _file_record(encoded_asset_path)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('image', type=Path)
    parser.add_argument('expected', type=Path)
    parser.add_argument('--strength', type=float, required=True)
    parser.add_argument('--image-origin', choices=('top-left', 'bottom-left'), required=True)
    parser.add_argument('--assume-srgb', action='store_true', required=True)
    parser.add_argument('--alpha-mode', choices=('straight',), required=True)
    parser.add_argument('--edge-samples', type=int, default=4)
    parser.add_argument('--interior-pixel-spacing', type=float)
    parser.add_argument('--max-samples', type=int, default=1000000)
    parser.add_argument('--multiplier-tolerance', type=float, default=.05)
    parser.add_argument('--neutral-alpha-tolerance', type=float, default=0)
    parser.add_argument('--max-examples', type=int, default=5)
    parser.add_argument('--encoded-asset', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = vars(parser.parse_args())
    output = args.pop('output')
    result = diagnose_files(args.pop('snapshot'), args.pop('image'), args.pop('expected'),
                            encoded_asset_path=args.pop('encoded_asset'), **args)
    with output.open('x', encoding='utf8') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
    print(json.dumps({'status': result['status'], 'complete': result['complete'], 'totals': result['totals']}))


if __name__ == '__main__':
    main()
