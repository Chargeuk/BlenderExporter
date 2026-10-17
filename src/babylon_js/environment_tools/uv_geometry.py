"""Dependency-free 2D UV triangle audit; import by file path outside Blender.

No scene changes, pixel sampling or packing are performed. Coordinates are never
snapped/clamped. Intersection uses binary64 convex clipping; declared area
thresholds apply to measured area, not to half-plane inclusion.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

SCHEMA = 'babylon_js.uv_geometry_audit.v1'
BOUNDS_TOLERANCE = 1e-6
AREA_EPSILON = 1e-12


def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _polygon_area(points):
    # A translated fan avoids cancellation from the absolute UV origin.
    if len(points) < 3:
        return 0.0
    return abs(math.fsum(_cross(points[0], points[i], points[i + 1])
                         for i in range(1, len(points) - 1))) * 0.5


def triangle_intersection_area(first, second):
    """Measured normalized area of two finite, nondegenerate UV triangles.

    Either winding/vertex order is accepted. Input validation is the caller's
    responsibility; use audit_catalogue for untrusted catalogue data.
    """
    polygon = list(first)
    clip = list(second)
    if _cross(*clip) < 0:
        clip.reverse()
    for i, a in enumerate(clip):
        b = clip[(i + 1) % 3]
        if not polygon:
            break
        result = []
        previous = polygon[-1]
        previous_side = _cross(a, b, previous)
        for current in polygon:
            current_side = _cross(a, b, current)
            if (current_side >= 0) != (previous_side >= 0):
                t = previous_side / (previous_side - current_side)
                result.append((previous[0] + t * (current[0] - previous[0]),
                               previous[1] + t * (current[1] - previous[1])))
            if current_side >= 0:
                result.append(current)
            previous, previous_side = current, current_side
        polygon = result
    return _polygon_area(polygon)


def _box(points):
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def _boxes_overlap(a, b):
    # Positive-area intersections require positive overlap in both axes.
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _tree(indices, triangles):
    bounds = [triangles[i]['bounds'] for i in indices]
    box = (min(b[0] for b in bounds), min(b[1] for b in bounds),
           max(b[2] for b in bounds), max(b[3] for b in bounds))
    if len(indices) <= 8:
        return (box, indices, None, None)
    axis = 0 if box[2] - box[0] >= box[3] - box[1] else 1
    ordered = sorted(indices, key=lambda i: (triangles[i]['bounds'][axis]
                                            + triangles[i]['bounds'][axis + 2], i))
    middle = len(ordered) // 2
    return (box, None, _tree(ordered[:middle], triangles),
            _tree(ordered[middle:], triangles))


def _candidates(node, box, index, triangles):
    if not _boxes_overlap(node[0], box):
        return
    if node[1] is not None:
        for other in node[1]:
            if other > index and _boxes_overlap(box, triangles[other]['bounds']):
                yield other
    else:
        yield from _candidates(node[2], box, index, triangles)
        yield from _candidates(node[3], box, index, triangles)


def _finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _identity(catalogue):
    try:
        raw = json.dumps(catalogue, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=True, allow_nan=True).encode('utf8')
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(raw).hexdigest()


def audit_catalogue(catalogue, *, bounds_tolerance=BOUNDS_TOLERANCE,
                    area_epsilon=AREA_EPSILON, max_details=100):
    """Return a JSON-serializable report; pass iff valid with no area > epsilon.

    catalogue is a nonempty list of islands with unique positive integer id,
    nonempty object string, optional nonnegative integer polygon_root and a
    nonempty triangles list. Each triangle has exactly three numeric [u, v]
    points. Malformed triangles fail validation and are excluded from overlap
    checks; the report explicitly counts the checked subset. Details are capped,
    totals are not. Invalid tolerance arguments raise ValueError.
    """
    for name, value in (('bounds_tolerance', bounds_tolerance),
                        ('area_epsilon', area_epsilon)):
        if not _finite_number(value) or value < 0:
            raise ValueError(name + ' must be finite and nonnegative')
    if bounds_tolerance > 1:
        raise ValueError('bounds_tolerance must be at most 1 normalized UV unit')
    if type(max_details) is not int or max_details < 0:
        raise ValueError('max_details must be a nonnegative integer')
    report = {
        'schema': SCHEMA, 'passed': False,
        'tolerances': {'bounds_tolerance': bounds_tolerance,
                       'area_epsilon': area_epsilon,
                       'half_plane_epsilon': 0.0},
        'catalogue_identity': {'canonical_json_sha256': _identity(catalogue)},
        'counts': {'islands': 0, 'triangles': 0, 'checked_triangles': 0,
                   'invalid_triangles': 0, 'validation_issues': 0,
                   'bounds_violations': 0, 'degenerate_triangles': 0,
                   'candidate_pairs': 0, 'overlap_pairs': 0,
                   'within_island_overlap_pairs': 0, 'across_island_overlap_pairs': 0},
        'bounds': None, 'validation_issues': [], 'overlaps': [],
        'max_details': max_details,
        'limitations': [
            'Binary64 2D clipping, not exact arithmetic; areas at or below area_epsilon are not flagged.',
            'Bounds within bounds_tolerance are accepted without clamping; only valid triangles are overlap-checked.',
            'Shared edges/vertices are legal; all candidate pairs are counted even when details are capped.',
            'No proof of sampled pixels, spacing, chart short-axis width, UVPackmaster success or on-model quality.',
            'AABB tree avoids spatially disjoint pairs; dense overlapping layouts can still require quadratic work.',
        ],
    }
    counts = report['counts']

    def issue(code, identity=None, **detail):
        counts['validation_issues'] += 1
        if len(report['validation_issues']) < max_details:
            report['validation_issues'].append(dict(code=code, identity=identity, **detail))

    triangles, all_points, seen = [], [], set()
    if not isinstance(catalogue, list) or not catalogue:
        issue('catalogue_must_be_nonempty_list')
        islands = []
    else:
        islands = catalogue
    counts['islands'] = len(islands)
    for island_index, island in enumerate(islands):
        base = {'island_index': island_index}
        if not isinstance(island, dict):
            issue('island_must_be_object', base)
            continue
        k, label = island.get('id'), island.get('object')
        metadata_valid = True
        if type(k) is not int or k <= 0:
            issue('id_must_be_positive_integer', base)
            metadata_valid = False
        elif k in seen:
            issue('duplicate_island_id', dict(base, island_id=k))
            metadata_valid = False
        else:
            seen.add(k)
        if not isinstance(label, str) or not label.strip():
            issue('object_must_be_nonempty_string', base)
            metadata_valid = False
        if 'polygon_root' in island and (type(island['polygon_root']) is not int
                                         or island['polygon_root'] < 0):
            issue('polygon_root_must_be_nonnegative_integer', base)
            metadata_valid = False
        if metadata_valid:
            base.update(island_id=k, object=label)
            if 'polygon_root' in island:
                base['polygon_root'] = island['polygon_root']
        raw_triangles = island.get('triangles')
        if not isinstance(raw_triangles, list) or not raw_triangles:
            issue('triangles_must_be_nonempty_list', base)
            continue
        counts['triangles'] += len(raw_triangles)
        for triangle_index, tri in enumerate(raw_triangles):
            identity = dict(base, triangle_index=triangle_index)
            if not isinstance(tri, (list, tuple)) or len(tri) != 3:
                issue('triangle_must_have_three_points', identity)
                continue
            if any(not isinstance(p, (list, tuple)) or len(p) != 2 for p in tri):
                issue('point_must_have_two_coordinates', identity)
                continue
            if any(not _finite_number(q) for p in tri for q in p):
                issue('coordinates_must_be_finite_numbers', identity)
                continue
            points = tuple((float(p[0]), float(p[1])) for p in tri)
            all_points.extend(points)
            box = _box(points)
            if any(q < -bounds_tolerance or q > 1 + bounds_tolerance for p in points for q in p):
                counts['bounds_violations'] += 1
                issue('coordinates_out_of_normalized_bounds', identity, bounds=list(box))
                continue
            area = _polygon_area(points)
            if area <= area_epsilon:
                counts['degenerate_triangles'] += 1
                issue('degenerate_triangle', identity, area=area)
                continue
            # Invalid metadata must not prevent checking otherwise valid geometry.
            triangles.append({'points': points, 'bounds': box, 'identity': identity,
                              'island_index': island_index})
    counts['checked_triangles'] = len(triangles)
    counts['invalid_triangles'] = counts['triangles'] - len(triangles)
    if all_points:
        report['bounds'] = list(_box(all_points))
    if triangles:
        tree = _tree(list(range(len(triangles))), triangles)
        for index, first in enumerate(triangles):
            for other in _candidates(tree, first['bounds'], index, triangles):
                counts['candidate_pairs'] += 1
                second = triangles[other]
                area = triangle_intersection_area(first['points'], second['points'])
                if area > area_epsilon:
                    counts['overlap_pairs'] += 1
                    same = first['island_index'] == second['island_index']
                    key = 'within_island_overlap_pairs' if same else 'across_island_overlap_pairs'
                    counts[key] += 1
                    if len(report['overlaps']) < max_details:
                        report['overlaps'].append({'first': first['identity'], 'second': second['identity'],
                                                   'area': area, 'within_island': same})
    report['omitted_details'] = {
        'validation_issues': counts['validation_issues'] - len(report['validation_issues']),
        'overlaps': counts['overlap_pairs'] - len(report['overlaps'])}
    report['passed'] = counts['validation_issues'] == 0 and counts['overlap_pairs'] == 0
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('catalogue', type=Path, help='Saved islands.json (top-level list)')
    parser.add_argument('--output', type=Path, required=True, help='Audit JSON output')
    parser.add_argument('--bounds-tolerance', type=float, default=BOUNDS_TOLERANCE)
    parser.add_argument('--area-epsilon', type=float, default=AREA_EPSILON)
    parser.add_argument('--max-details', type=int, default=100)
    args = parser.parse_args(argv)
    if args.output.resolve() == args.catalogue.resolve():
        parser.error('output must differ from the input catalogue')
    try:
        raw = args.catalogue.read_bytes()
        catalogue = json.loads(raw)
        report = audit_catalogue(catalogue, bounds_tolerance=args.bounds_tolerance,
                                 area_epsilon=args.area_epsilon, max_details=args.max_details)
        report['catalogue_identity'].update(source_path=str(args.catalogue.resolve()),
                                            source_file_sha256=hashlib.sha256(raw).hexdigest())
    except (OSError, ValueError, UnicodeError) as exc:
        report = {'schema': SCHEMA, 'passed': False, 'error': str(exc),
                  'catalogue_identity': {'source_path': str(args.catalogue.resolve())}}
    try:
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf8')
    except (OSError, ValueError) as exc:
        parser.exit(2, 'Cannot write audit: ' + str(exc) + '\n')
    print(json.dumps({'passed': report['passed'], 'output': str(args.output),
                      'counts': report.get('counts'), 'error': report.get('error')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
