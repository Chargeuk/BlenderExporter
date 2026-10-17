"""Read-only UV triangle audit. Float64 NumPy geometry; no Blender/SciPy dependency."""
import argparse
import hashlib
import importlib.util
import heapq
import json
import math
from pathlib import Path
import numpy as np

# Load the dependency-free calculation by path: the add-on initializer needs bpy.
_geometry_spec = importlib.util.spec_from_file_location(
    '_workflow_uv_geometry', Path(__file__).resolve().parents[1]/'environment_tools/uv_geometry.py')
_geometry = importlib.util.module_from_spec(_geometry_spec)
_geometry_spec.loader.exec_module(_geometry)


def cross(a, b):
    return a[0]*b[1]-a[1]*b[0]


def polygon_area(points):
    points = np.asarray(points, dtype=np.float64)
    if len(points) < 3:
        return 0.0
    # Translation avoids losing small UV areas far from the origin.
    points = points-points[0]
    return abs(float(np.sum(points[:, 0]*np.roll(points[:, 1], -1)
                            -points[:, 1]*np.roll(points[:, 0], -1))))*.5


def triangle_intersection_area(first, second):
    """Sutherland-Hodgman clipping, including reversed winding; shared edges have zero area."""
    first = np.asarray(first, dtype=np.float64)
    second = np.asarray(second, dtype=np.float64)
    if first.shape != (3, 2) or second.shape != (3, 2) or not np.isfinite([first, second]).all():
        raise ValueError('Intersection requires finite 3x2 triangles')
    winding = cross(second[1]-second[0], second[2]-second[0])
    if not winding or polygon_area(first) == 0:
        return 0.0
    return _geometry.triangle_intersection_area(first, second)


def convex_hull(points):
    ordered = sorted(set(map(tuple, np.asarray(points, dtype=np.float64))))
    if len(ordered) < 3:
        return np.asarray(ordered, dtype=np.float64).reshape(-1, 2)
    def half(sequence):
        result = []
        for point in sequence:
            while len(result) > 1 and cross(np.subtract(result[-1], result[-2]), np.subtract(point, result[-1])) <= 0:
                result.pop()
            result.append(point)
        return result
    return np.asarray(half(ordered)[:-1]+half(reversed(ordered))[:-1], dtype=np.float64)


def hull_minimum_width(hull):
    """Minimum distance between parallel supporting lines; does not measure internal necks."""
    if len(hull) < 3:
        return 0.0
    minimum = math.inf
    for a, b in zip(hull, np.roll(hull, -1, axis=0)):
        edge = b-a
        length = float(np.linalg.norm(edge))
        if length:
            normal = np.array([-edge[1], edge[0]])/length
            projections = (hull-a)@normal
            minimum = min(minimum, float(np.ptp(projections)))
    return minimum


def chart_ids(rows):
    """Connect triangle edges only when object, geometry vertex IDs and UV endpoints agree exactly."""
    parent = list(range(len(rows)))
    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index
    edges = {}
    for index, row in enumerate(rows):
        if len(row.get('vertices', [])) != 3:
            raise ValueError('Automatic chart identity requires three evaluated vertex IDs')
        for first, second in ((0, 1), (1, 2), (2, 0)):
            points = tuple(sorted((int(row['vertices'][i]), *map(float, row['uv'][i])) for i in (first, second)))
            key = (row['object'], points)
            if key in edges:
                parent[find(index)] = find(edges[key])
            else:
                edges[key] = index
    identities = {}
    result = []
    for index in range(len(rows)):
        root = find(index)
        if root not in identities:
            identities[root] = len(identities)+1
        result.append(identities[root])
    return result


def audit(snapshot, *, pixels, overlap_area_tolerance, grid_size=64, max_issues=100,
          degenerate_area_tolerance=0.0, bounds_tolerance=0.0, max_candidate_pairs=None):
    """Audit all intended UV2 participants; selectors/exclusions belong in the snapshot's provenance."""
    if snapshot.get('schema_version') != 1 or not isinstance(snapshot.get('triangles'), list):
        raise ValueError('Expected UV snapshot schema_version 1 and triangle rows')
    if isinstance(pixels, bool) or not isinstance(pixels, int) or pixels < 1:
        raise ValueError('Explicit positive square pixel reference required')
    if not math.isfinite(overlap_area_tolerance) or overlap_area_tolerance <= 0:
        raise ValueError('Explicit positive overlap area tolerance required')
    if any(not math.isfinite(value) or value < 0 for value in (degenerate_area_tolerance, bounds_tolerance)):
        raise ValueError('Invalid degeneracy/bounds tolerance')
    if not isinstance(grid_size, int) or not 1 <= grid_size <= 1024 or not isinstance(max_issues, int) or max_issues < 1:
        raise ValueError('Invalid grid size or issue limit')
    if max_candidate_pairs is not None and (not isinstance(max_candidate_pairs, int) or max_candidate_pairs < 1):
        raise ValueError('Invalid explicit candidate-pair limit')
    rows = snapshot['triangles']
    if not rows:
        raise ValueError('No intended UV triangles')
    uv = np.asarray([row['uv'] for row in rows], dtype=np.float64)
    if uv.shape != (len(rows), 3, 2) or not np.isfinite(uv).all():
        raise ValueError('UV triangles must be finite Nx3x2')
    if any(not isinstance(row.get('object'), str) or 'triangle' not in row for row in rows):
        raise ValueError('Triangle rows require object and evaluated triangle identity')
    if len({(row['object'], row['triangle']) for row in rows}) != len(rows):
        raise ValueError('Duplicate evaluated triangle identity')
    explicit_charts = ['chart' in row for row in rows]
    if any(explicit_charts) and not all(explicit_charts):
        raise ValueError('Supply chart identity for every triangle or derive all charts')
    charts = [row['chart'] for row in rows] if all(explicit_charts) else chart_ids(rows)
    low = uv.min(axis=1)
    high = uv.max(axis=1)
    edges = np.roll(uv, -1, axis=1)-uv
    lengths = np.linalg.norm(edges, axis=2)
    doubled = np.abs(edges[:, 0, 0]*(uv[:, 2, 1]-uv[:, 0, 1])
                     -edges[:, 0, 1]*(uv[:, 2, 0]-uv[:, 0, 0]))
    areas = doubled*.5
    altitudes = np.divide(doubled, lengths.max(axis=1), out=np.zeros(len(rows)), where=lengths.max(axis=1) > 0)*pixels
    degenerate = areas <= degenerate_area_tolerance
    out_of_bounds = np.any((uv < -bounds_tolerance) | (uv > 1+bounds_tolerance), axis=(1, 2))
    domain_low = np.minimum(low.min(axis=0), [0., 0.])
    domain_high = np.maximum(high.max(axis=0), [1., 1.])
    def identity(index):
        row = rows[index]
        return {'object': row['object'], 'triangle': row['triangle'], 'chart': charts[index],
                **{name: row[name] for name in ('polygon', 'loops', 'vertices') if name in row}}
    bins = {}
    candidates = bbox_candidates = tested = overlaps = within_chart = cross_chart = cross_object = 0
    total_area = 0.0
    overlap_heap = []
    complete = True
    for index in range(len(rows)):
        if degenerate[index]:
            continue
        first_cell = np.clip(np.floor((low[index]-domain_low)/(domain_high-domain_low)*grid_size).astype(int), 0, grid_size-1)
        last_cell = np.clip(np.floor((high[index]-domain_low)/(domain_high-domain_low)*grid_size).astype(int), 0, grid_size-1)
        cells = [(x, y) for x in range(first_cell[0], last_cell[0]+1) for y in range(first_cell[1], last_cell[1]+1)]
        nearby = set()
        for cell in cells:
            nearby.update(bins.get(cell, ()))
        for other in sorted(nearby):
            if max_candidate_pairs is not None and candidates >= max_candidate_pairs:
                complete = False
                break
            candidates += 1
            if np.any(np.minimum(high[index], high[other])-np.maximum(low[index], low[other]) <= 0):
                continue
            bbox_candidates += 1
            area = triangle_intersection_area(uv[index], uv[other])
            tested += 1
            if area <= overlap_area_tolerance:
                continue
            overlaps += 1
            total_area += area
            if rows[index]['object'] != rows[other]['object']:
                cross_object += 1
            elif charts[index] == charts[other]:
                within_chart += 1
            else:
                cross_chart += 1
            item = (area, other, index)
            if len(overlap_heap) < max_issues:
                heapq.heappush(overlap_heap, item)
            elif item > overlap_heap[0]:
                heapq.heapreplace(overlap_heap, item)
        if not complete:
            break
        for cell in cells:
            bins.setdefault(cell, []).append(index)
    grouped = {}
    for index, row in enumerate(rows):
        grouped.setdefault((row['object'], charts[index]), []).append(index)
    chart_records = []
    for (obj, chart), indices in sorted(grouped.items()):
        coordinates = uv[indices].reshape(-1, 2)
        hull = convex_hull(coordinates)
        width = coordinates.max(axis=0)-coordinates.min(axis=0)
        chart_records.append({'object': obj, 'chart': chart, 'triangle_count': len(indices),
            'bounds_uv': [coordinates.min(axis=0).tolist(), coordinates.max(axis=0).tolist()],
            'bounding_width_pixels': (width*pixels).tolist(),
            'summed_triangle_area_uv2': float(areas[indices].sum()),
            'convex_hull_area_uv2': polygon_area(hull),
            'convex_hull_minimum_caliper_width_pixels': hull_minimum_width(hull)*pixels,
            'minimum_triangle_altitude_pixels': float(altitudes[indices].min())})
    return {'schema_version': 1, 'kind': 'uv2-positive-area-audit', 'complete': complete,
        'status': ('incomplete' if not complete else 'issues' if overlaps or degenerate.any() or out_of_bounds.any() else 'passed'),
        'snapshot_provenance': snapshot.get('provenance', {}),
        'settings': {'float_dtype': 'float64', 'pixel_reference': [pixels, pixels],
            'overlap_area_tolerance_uv2': overlap_area_tolerance, 'degenerate_area_tolerance_uv2': degenerate_area_tolerance,
            'bounds_tolerance_uv': bounds_tolerance, 'target_bounds_uv': [[0, 0], [1, 1]],
            'actual_bounds_uv': [low.min(axis=0).tolist(), high.max(axis=0).tolist()],
            'grid_size': [grid_size, grid_size], 'grid_domain_uv': [domain_low.tolist(), domain_high.tolist()],
            'max_candidate_pairs': max_candidate_pairs, 'max_issue_samples': max_issues,
            'chart_definition': 'Explicit snapshot chart IDs, or exact shared geometry vertex/UV edge connectivity within each object'},
        'totals': {'objects': len({row['object'] for row in rows}), 'triangles': len(rows), 'charts': len(grouped),
            'degenerate_triangles': int(degenerate.sum()), 'out_of_bounds_triangles': int(out_of_bounds.sum()),
            'grid_candidate_pairs': candidates, 'positive_bbox_candidate_pairs': bbox_candidates, 'tested_pairs': tested,
            'positive_overlap_pairs': overlaps, 'same_chart_overlap_pairs': within_chart,
            'same_object_cross_chart_overlap_pairs': cross_chart, 'cross_object_overlap_pairs': cross_object,
            'summed_pair_overlap_area_uv2': total_area, 'occupied_grid_cells': len(bins)},
        'overlap_samples': [{'area_uv2': area, 'area_pixels2': area*pixels*pixels,
                             'first': identity(first), 'second': identity(second)}
                            for area, first, second in sorted(overlap_heap, reverse=True)],
        'degenerate_samples': [identity(int(index)) for index in np.flatnonzero(degenerate)[:max_issues]],
        'out_of_bounds_samples': [dict(identity(int(index)), bounds_uv=[low[index].tolist(), high[index].tolist()])
                                  for index in np.flatnonzero(out_of_bounds)[:max_issues]],
        'thin_triangle_samples': [dict(identity(int(index)), minimum_altitude_pixels=float(altitudes[index]), area_uv2=float(areas[index]))
                                  for index in np.argsort(altitudes, kind='stable')[:max_issues]],
        'charts': chart_records,
        'limits': ['2D UV triangle intersection only; no coplanar 3D BVH or surface correspondence claim.',
            'Pair overlap areas are summed, not a union area. Adjacent/shared-edge pairs are excluded only when geometric intersection area is at or below the explicit threshold.',
            'Convex-hull caliper width describes the hull, not an internal neck or every local chart width. Triangle altitude is a diagnostic, not a deletion rule.',
            'Thin charts require visual review. Actual COLOR-excluded bake alpha and ownership rasterization remain the authority for sampled/unsampled chart coverage.',
            'No UV edits, packing, geometry deletion or automatic receiver exclusion. Grid candidate limit, if supplied, produces an incomplete result rather than a pass.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--pixels', type=int, required=True)
    parser.add_argument('--overlap-area-tolerance', type=float, required=True)
    parser.add_argument('--grid-size', type=int, default=64)
    parser.add_argument('--max-issues', type=int, default=100)
    parser.add_argument('--max-candidate-pairs', type=int)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text(encoding='utf8'))
    result = audit(snapshot, pixels=args.pixels, overlap_area_tolerance=args.overlap_area_tolerance,
        grid_size=args.grid_size, max_issues=args.max_issues, max_candidate_pairs=args.max_candidate_pairs)
    result['input_snapshot'] = {'path': str(args.snapshot.resolve()),
                              'sha256': hashlib.sha256(args.snapshot.read_bytes()).hexdigest()}
    result['audit_helper_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    with args.output.open('x', encoding='utf8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({'status': result['status'], 'complete': result['complete'], 'totals': result['totals']}, indent=2))


if __name__ == '__main__': main()
