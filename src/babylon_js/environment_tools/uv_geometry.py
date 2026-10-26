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
SPACING_SCHEMA = 'babylon_js.uv_chart_spacing.v1'


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


def _point_segment_nearest(point, a, b):
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    if length == 0:
        return math.dist(point, a), a
    unit = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
    projection = math.fsum((point[i] - a[i]) * unit[i] for i in (0, 1))
    t = min(1.0, max(0.0, projection / length))
    nearest = tuple((1 - t) * a[i] + t * b[i] for i in (0, 1))
    return math.dist(point, nearest), nearest


def _distance_cross(a, b, c):
    value = _cross(a, b, c)
    if not math.isfinite(value):
        raise ValueError('Nonfinite triangle-distance orientation')
    return value


def _segments_meet(a, b, c, d):
    sides = (_distance_cross(a, b, c), _distance_cross(a, b, d),
             _distance_cross(c, d, a), _distance_cross(c, d, b))
    if ((sides[0] > 0 and sides[1] < 0 or sides[0] < 0 and sides[1] > 0)
            and (sides[2] > 0 and sides[3] < 0 or sides[2] < 0 and sides[3] > 0)):
        return True
    return any(side == 0 and min(start[0], end[0]) <= p[0] <= max(start[0], end[0])
               and min(start[1], end[1]) <= p[1] <= max(start[1], end[1])
               for side, p, start, end in ((sides[0], c, a, b), (sides[1], d, a, b),
                                           (sides[2], a, c, d), (sides[3], b, c, d)))


def _point_in_triangle(point, triangle):
    sides = [_distance_cross(triangle[i], triangle[(i + 1) % 3], point) for i in range(3)]
    return min(sides) >= 0 or max(sides) <= 0


def triangle_distance(first, second):
    """Minimum Euclidean distance between two closed filled triangles.

    Inputs must be finite nondegenerate binary64 triangles; untrusted catalogue
    callers should use measure_spacing. Containment, crossing and touching give
    zero. No tolerance or snapping is applied; either winding is accepted.
    """
    if _point_in_triangle(first[0], second) or _point_in_triangle(second[0], first):
        return 0.0
    best = math.inf
    for i, a in enumerate(first):
        b = first[(i + 1) % 3]
        for j, c in enumerate(second):
            d = second[(j + 1) % 3]
            if _segments_meet(a, b, c, d):
                return 0.0
            best = min(best, _point_segment_nearest(a, c, d)[0],
                       _point_segment_nearest(b, c, d)[0],
                       _point_segment_nearest(c, a, b)[0],
                       _point_segment_nearest(d, a, b)[0])
    return best


def _box_distance(first, second):
    return math.hypot(max(first[0] - second[2], second[0] - first[2], 0.0),
                      max(first[1] - second[3], second[1] - first[3], 0.0))


def _spacing_tree(node, triangles):
    """Annotate existing BVH with homogeneous chart and largest input index."""
    if node[1] is not None:
        charts = {triangles[i]['island_index'] for i in node[1]}
        return (*node, next(iter(charts)) if len(charts) == 1 else None, max(node[1]))
    left, right = _spacing_tree(node[2], triangles), _spacing_tree(node[3], triangles)
    chart = left[4] if left[4] == right[4] else None
    return (node[0], None, left, right, chart, max(left[5], right[5]))


def measure_spacing(catalogue, *, pixels=2048, max_details=100):
    """Measure global cross-chart triangle-union distance and signed tile border.

    Uses audit_catalogue's island/triangle schema, but accepts finite coordinates
    outside [0,1] so negative borders remain measurable. Invalid metadata,
    degenerate triangles or nonfinite derived geometry fail closed: no subset
    distance is returned. Zero means closed unions touch or intersect. Same-chart
    pairs are ignored, including disconnected components. No gap pass policy is
    applied. Returns one minimizing pair, not a per-chart/all-ties catalogue.
    """
    if not _finite_number(pixels) or pixels <= 0:
        raise ValueError('pixels must be finite and positive')
    if type(max_details) is not int or max_details < 0:
        raise ValueError('max_details must be a nonnegative integer')
    report = {'schema': SPACING_SCHEMA, 'valid': False, 'complete': False,
              'pixel_reference': pixels, 'catalogue_identity': {'canonical_json_sha256': _identity(catalogue)},
              'scope': 'Global minimum between distinct chart triangle unions; signed border of unit square.',
              'arithmetic': 'binary64; no distance tolerance, snapping, clamping or gap acceptance threshold',
              'counts': {'islands': 0, 'triangles': 0, 'checked_triangles': 0,
                         'validation_issues': 0, 'tree_nodes_visited': 0, 'triangle_pair_distances': 0},
              'validation_issues': [], 'bounds': None,
              'minimum_interchart': None, 'minimum_signed_border': None,
              'limitations': [
                  'Binary64 measurements, not exact predicates; extremely ill-conditioned input may lose precision.',
                  'One witnessed minimizing chart/triangle pair; equal-distance ties are not enumerated.',
                  'Each chart is its filled triangle union, including disconnected/nonconvex components. Same-chart pairs are ignored.',
                  'No proof of native ownership, minimum local chart/neck width, packing completion or appearance.',
                  'BVH prunes by bounding-box lower bounds, but returned distances use actual triangles; worst case remains quadratic.',
                  'Catalogue identity binds supplied data only, not a Blender scene, source file or bake.',
              ]}
    counts = report['counts']

    def issue(code, identity=None):
        counts['validation_issues'] += 1
        if len(report['validation_issues']) < max_details:
            report['validation_issues'].append({'code': code, 'identity': identity})

    if report['catalogue_identity']['canonical_json_sha256'] is None:
        issue('catalogue_must_have_canonical_json_identity')
    triangles, points, seen = [], [], set()
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
        if type(k) is not int or k <= 0:
            issue('id_must_be_positive_integer', base)
        elif k in seen:
            issue('duplicate_island_id', base)
        else:
            seen.add(k)
        if not isinstance(label, str) or not label.strip():
            issue('object_must_be_nonempty_string', base)
        root = island.get('polygon_root')
        if 'polygon_root' in island and (type(root) is not int or root < 0):
            issue('polygon_root_must_be_nonnegative_integer', base)
        # Invalid labels are kept out of the JSON report; no invalid subset is measured.
        if type(k) is int and isinstance(label, str):
            base.update(island_id=k, object=label)
        if type(root) is int:
            base['polygon_root'] = root
        raw = island.get('triangles')
        if not isinstance(raw, list) or not raw:
            issue('triangles_must_be_nonempty_list', base)
            continue
        counts['triangles'] += len(raw)
        for triangle_index, tri in enumerate(raw):
            identity = dict(base, triangle_index=triangle_index)
            if (not isinstance(tri, (list, tuple)) or len(tri) != 3
                    or any(not isinstance(p, (list, tuple)) or len(p) != 2 for p in tri)):
                issue('triangle_must_have_three_2d_points', identity)
                continue
            if any(not _finite_number(v) for p in tri for v in p):
                issue('coordinates_must_be_finite_numbers', identity)
                continue
            vertices = tuple(tuple(float(v) for v in p) for p in tri)
            area = abs(_cross(*vertices)) * 0.5
            if not math.isfinite(area):
                issue('nonfinite_derived_geometry', identity)
                continue
            if area == 0:
                issue('degenerate_triangle', identity)
                continue
            triangles.append({'points': vertices, 'bounds': _box(vertices),
                              'identity': identity, 'island_index': island_index})
            points.extend(vertices)
    counts['checked_triangles'] = len(triangles)
    if points:
        report['bounds'] = list(_box(points))
        box = report['bounds']
        # Bound differences/dot products used below must remain representable.
        if not math.isfinite(math.hypot(box[2] - box[0], box[3] - box[1])):
            issue('nonfinite_derived_geometry')
    report['omitted_validation_issues'] = counts['validation_issues'] - len(report['validation_issues'])
    if counts['validation_issues']:
        return report
    border = math.inf
    border_record = None
    for tri in triangles:
        for vertex_index, (u, v) in enumerate(tri['points']):
            for side, distance in (('left', u), ('bottom', v), ('right', 1 - u), ('top', 1 - v)):
                if distance < border:
                    border = distance
                    border_record = {'normalized': distance, 'pixels': distance * pixels,
                                     'triangle': tri['identity'], 'vertex_index': vertex_index,
                                     'coordinate': [u, v], 'side': side}
    best, pair = math.inf, None
    if len(islands) > 1:
        tree = _spacing_tree(_tree(list(range(len(triangles))), triangles), triangles)
        for index, first in enumerate(triangles):
            pending = [(0.0, tree)]
            while pending:
                lower, node = pending.pop()
                if lower >= best or node[4] == first['island_index'] or node[5] <= index:
                    continue
                counts['tree_nodes_visited'] += 1
                if node[1] is not None:
                    for other in node[1]:
                        second = triangles[other]
                        if (other <= index or first['island_index'] == second['island_index']
                                or _box_distance(first['bounds'], second['bounds']) >= best):
                            continue
                        counts['triangle_pair_distances'] += 1
                        try:
                            distance = triangle_distance(first['points'], second['points'])
                        except (ValueError, OverflowError):
                            issue('nonfinite_distance_arithmetic', first['identity'])
                            report['omitted_validation_issues'] = counts['validation_issues'] - len(report['validation_issues'])
                            return report
                        if distance < best:
                            best, pair = distance, (first['identity'], second['identity'])
                else:
                    children = [(_box_distance(first['bounds'], child[0]), child) for child in node[2:4]]
                    # Visit the closer child first without ever ordering node/identity objects.
                    if children[0][0] < children[1][0]:
                        children.reverse()
                    pending.extend(children)
            if best == 0:
                break
    if pair is not None:
        report['minimum_interchart'] = {'normalized': best, 'pixels': best * pixels,
                                        'first': pair[0], 'second': pair[1]}
    report['minimum_signed_border'] = border_record
    # Fail closed if numeric multiplication at the requested pixel scale overflowed.
    if (not math.isfinite(border_record['pixels'])
            or pair is not None and not math.isfinite(best * pixels)):
        issue('nonfinite_pixel_measurement')
        report['minimum_interchart'] = report['minimum_signed_border'] = None
        report['omitted_validation_issues'] = counts['validation_issues'] - len(report['validation_issues'])
        return report
    report['valid'] = report['complete'] = True
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('catalogue', type=Path, help='Saved islands.json (top-level list)')
    parser.add_argument('--output', type=Path, required=True, help='Audit JSON output')
    parser.add_argument('--bounds-tolerance', type=float, default=BOUNDS_TOLERANCE)
    parser.add_argument('--area-epsilon', type=float, default=AREA_EPSILON)
    parser.add_argument('--max-details', type=int, default=100)
    parser.add_argument('--spacing', action='store_true', help='Measure chart spacing/border instead of auditing overlap')
    parser.add_argument('--pixels', type=float, default=2048, help='Square pixel reference for --spacing (default: 2048)')
    args = parser.parse_args(argv)
    if args.output.resolve() == args.catalogue.resolve():
        parser.error('output must differ from the input catalogue')
    try:
        raw = args.catalogue.read_bytes()
        catalogue = json.loads(raw)
        if args.spacing:
            report = measure_spacing(catalogue, pixels=args.pixels, max_details=args.max_details)
        else:
            report = audit_catalogue(catalogue, bounds_tolerance=args.bounds_tolerance,
                                     area_epsilon=args.area_epsilon, max_details=args.max_details)
        report['catalogue_identity'].update(source_path=str(args.catalogue.resolve()),
                                            source_file_sha256=hashlib.sha256(raw).hexdigest())
    except (OSError, ValueError, UnicodeError) as exc:
        report = {'schema': SPACING_SCHEMA if args.spacing else SCHEMA,
                  'valid' if args.spacing else 'passed': False, 'error': str(exc),
                  'catalogue_identity': {'source_path': str(args.catalogue.resolve())}}
    try:
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf8')
    except (OSError, ValueError) as exc:
        parser.exit(2, 'Cannot write audit: ' + str(exc) + '\n')
    success = report.get('valid', False) if args.spacing else report['passed']
    print(json.dumps({'valid' if args.spacing else 'passed': success, 'output': str(args.output),
                      'counts': report.get('counts'), 'error': report.get('error')}))
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
