"""Analytic/oracle spacing checks; pure standard-library Python, no Blender."""
import copy
import importlib.util
import itertools
import json
import math
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

HELPER = Path(__file__).resolve().parents[1] / 'src/babylon_js/environment_tools/uv_geometry.py'
spec = importlib.util.spec_from_file_location('uv_geometry_spacing', HELPER)
geometry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(geometry)


def island(k, triangles):
    return {'id': k, 'object': 'Object' + str(k), 'polygon_root': k * 10, 'triangles': triangles}


def rectangle(x0, y0, x1, y1):
    a, b, c, d = [x0, y0], [x1, y0], [x1, y1], [x0, y1]
    return [[a, b, c], [a, c, d]]


def transformed(rows, scale=1, angle=0, translation=(0, 0)):
    result = copy.deepcopy(rows)
    c, s = math.cos(angle) * scale, math.sin(angle) * scale
    for row in result:
        row['triangles'] = [[[c*x-s*y+translation[0], s*x+c*y+translation[1]]
                             for x, y in tri] for tri in row['triangles']]
    return result


def brute_distance(rows):
    """Small-set oracle: clipping for intersections, complex edge projections.

    Intentionally no BVH, box-distance or new distance primitives from the helper.
    Positive-area crossings/containment use the preexisting clipping kernel;
    boundary contact and separated edges use point-to-segment projections.
    """
    def point_edge(p, a, b):
        p, a, b = complex(*p), complex(*a), complex(*b)
        direction = b-a
        t = max(0, min(1, ((p-a)/direction).real))
        return abs(p-(a+t*direction))
    best = math.inf
    for a, b in itertools.combinations(rows, 2):
        for first, second in itertools.product(a['triangles'], b['triangles']):
            if geometry.triangle_intersection_area(first, second) > 0:
                return 0.0
            for p in first:
                best = min(best, *(point_edge(p, second[j], second[(j+1) % 3]) for j in range(3)))
            for p in second:
                best = min(best, *(point_edge(p, first[j], first[(j+1) % 3]) for j in range(3)))
    return best


class UVSpacingTests(unittest.TestCase):
    def measured(self, rows, **options):
        before = copy.deepcopy(rows)
        result = geometry.measure_spacing(rows, **options)
        self.assertTrue(result['valid'], result['validation_issues'])
        self.assertTrue(result['complete'])
        self.assertEqual(rows, before)
        json.dumps(result, allow_nan=False)
        return result

    def test_rectangles_identity_and_pixel_reference(self):
        rows = [island(7, rectangle(.1, .2, .3, .4)), island(9, rectangle(.5, .2, .7, .4))]
        result = self.measured(rows, pixels=1024)
        gap = result['minimum_interchart']
        self.assertAlmostEqual(gap['normalized'], .2)
        self.assertAlmostEqual(gap['pixels'], 204.8)
        self.assertEqual({gap['first']['island_id'], gap['second']['island_id']}, {7, 9})
        self.assertIn(gap['first']['triangle_index'], (0, 1))
        self.assertEqual(gap['first']['object'], 'Object7')
        self.assertEqual(gap['first']['polygon_root'], 70)
        self.assertAlmostEqual(result['minimum_signed_border']['normalized'], .1)

    def test_rotation_invariance_and_true_distance_inside_overlapping_boxes(self):
        rows = [island(1, [[[.1, .1], [.9, .1], [.1, .9]]]),
                island(2, [[[.9, .9], [.6, .9], [.9, .6]]])]
        expected = .5 / math.sqrt(2)
        self.assertAlmostEqual(self.measured(rows)['minimum_interchart']['normalized'], expected)
        for angle in (.1, .55, math.pi / 2):
            result = self.measured(transformed(rows, angle=angle, translation=(.3, .2)))
            self.assertAlmostEqual(result['minimum_interchart']['normalized'], expected)

    def test_nonconvex_union_not_convex_hull_or_bbox(self):
        # L surrounds two sides of the inner chart, without containing it.
        rows = [island(1, rectangle(.1, .1, .2, .8) + rectangle(.2, .1, .8, .2)),
                island(2, rectangle(.4, .4, .5, .5))]
        self.assertAlmostEqual(self.measured(rows)['minimum_interchart']['normalized'], .2)

    def test_disconnected_chart_uses_nearest_component(self):
        rows = [island(1, rectangle(.05, .05, .1, .1) + rectangle(.7, .7, .8, .8)),
                island(2, rectangle(.5, .7, .6, .8))]
        gap = self.measured(rows)['minimum_interchart']
        self.assertAlmostEqual(gap['normalized'], .1)
        self.assertIn(gap['first']['triangle_index'], (2, 3))

    def test_containment_crossing_edge_touch_and_vertex_touch_are_zero(self):
        a = [[.1, .1], [.9, .1], [.1, .9]]
        fixtures = [
            [[.2, .2], [.3, .2], [.2, .3]],  # containment
            [[.5, 0], [1, .5], [.5, 1]],  # positive crossing
            [[.1, .1], [.5, .1], [.2, .02]],  # shared boundary segment
            [[.9, .1], [1, .1], [1, .2]],  # vertex
            a,
        ]
        for b in fixtures:
            with self.subTest(b=b):
                self.assertEqual(self.measured([island(1, [a]), island(2, [b])])['minimum_interchart']['normalized'], 0)

    def test_internal_selfchart_overlap_ignored_and_single_chart_null(self):
        tri = [[.1, .1], [.2, .1], [.1, .2]]
        rows = [island(1, [tri, tri])]
        result = self.measured(rows)
        self.assertIsNone(result['minimum_interchart'])
        self.assertEqual(result['counts']['triangle_pair_distances'], 0)
        rows.append(island(2, rectangle(.6, .1, .7, .2)))
        self.assertAlmostEqual(self.measured(rows)['minimum_interchart']['normalized'], .4)

    def test_signed_border_is_negative_outside_and_exactly_zero_on_edge(self):
        result = self.measured([island(1, rectangle(-.02, .2, .1, .4))])
        border = result['minimum_signed_border']
        self.assertEqual(border['side'], 'left')
        self.assertAlmostEqual(border['normalized'], -.02)
        self.assertAlmostEqual(border['pixels'], -40.96)
        self.assertEqual(border['coordinate'][0], -.02)
        result = self.measured([island(1, rectangle(.2, .8, .3, 1))])
        self.assertEqual(result['minimum_signed_border']['normalized'], 0)

    def test_subconfigured_gap_transparently_measured_without_pass_policy(self):
        gap = 3.9918212890625
        rows = [island(1, rectangle(10/2048, 10/2048, 20/2048, 20/2048)),
                island(2, rectangle((20+gap)/2048, 10/2048, (30+gap)/2048, 20/2048))]
        result = self.measured(rows)
        self.assertAlmostEqual(result['minimum_interchart']['pixels'], gap, places=11)
        self.assertNotIn('passed', result)

    def test_permutation_winding_translation_and_scaling(self):
        a, b = [[.1, .1], [.3, .1], [.1, .3]], [[.5, .5], [.7, .5], [.5, .7]]
        rows = [island(1, [a]), island(2, [b])]
        expected = self.measured(rows)['minimum_interchart']['normalized']
        for first, second in itertools.product(itertools.permutations(a), itertools.permutations(b)):
            candidate = [island(2, [list(second)]), island(1, [list(first)])]
            self.assertAlmostEqual(self.measured(candidate)['minimum_interchart']['normalized'], expected)
        for scale, translation in ((.25, (.2, .3)), (2, (-1, 2)), (1, (13, -7))):
            result = self.measured(transformed(rows, scale=scale, translation=translation))
            self.assertAlmostEqual(result['minimum_interchart']['normalized'], expected * scale)
        many = [island(1, rectangle(.1, .1, .2, .2) + rectangle(.7, .7, .8, .8)),
                island(2, rectangle(.4, .4, .5, .5))]
        expected = self.measured(many)['minimum_interchart']['normalized']
        for row in many:
            row['triangles'].reverse()
        many.reverse()
        self.assertAlmostEqual(self.measured(many)['minimum_interchart']['normalized'], expected)

    def test_small_catalogues_match_brute_oracle(self):
        rng = random.Random(503)
        for trial in range(16):
            rows = []
            for i in range(8):
                x, y = rng.random() * .9, rng.random() * .9
                # Disconnected, rotated triangle sets; some intersect in later trials.
                triangle = [[x, y], [x+.025, y+.01], [x+.01, y+.045]]
                rows.append(island(i+1, [triangle]))
            if trial % 2:
                rows[0]['triangles'] += rectangle(.4, .4, .45, .45)
            result = self.measured(rows)
            self.assertAlmostEqual(result['minimum_interchart']['normalized'], brute_distance(rows), places=13)

    def test_invalid_catalogues_fail_closed_and_cap_details(self):
        valid = island(1, rectangle(.1, .1, .2, .2))
        invalid = [None, [], {}, [None], [island(1, [])],
                   [valid, valid], [dict(valid, id=True)], [dict(valid, object=' ')],
                   [dict(valid, polygon_root=-1)], [dict(valid, extra=object())],
                   [island(1, [[[0, 0], [1, 1], [2, 2]]])]]
        for value in (float('nan'), float('inf'), True, '0', None, 10**1000):
            invalid.append([island(1, [[[value, 0], [.2, .1], [.1, .2]]])])
        invalid += [[island(1, [[[0, 0], [1e308, 0], [0, 1e308]]])],
                    [island(1, [[[0, 0], [1, 0]]])]]
        for rows in invalid:
            with self.subTest(rows=str(rows)[:80]):
                result = geometry.measure_spacing(rows, max_details=0)
                self.assertFalse(result['valid'])
                self.assertFalse(result['complete'])
                self.assertIsNone(result['minimum_interchart'])
                self.assertIsNone(result['minimum_signed_border'])
                self.assertEqual(result['validation_issues'], [])
                self.assertGreater(result['omitted_validation_issues'], 0)
                json.dumps(result, allow_nan=False)

    def test_bad_options_and_pixel_overflow(self):
        rows = [island(1, rectangle(.1, .1, .2, .2))]
        for value in (0, -1, True, float('inf'), float('nan')):
            with self.assertRaises(ValueError):
                geometry.measure_spacing(rows, pixels=value)
        for value in (-1, True, 1.2):
            with self.assertRaises(ValueError):
                geometry.measure_spacing(rows, max_details=value)
        result = geometry.measure_spacing(transformed(rows, translation=(10, 10)), pixels=1e308)
        self.assertFalse(result['valid'])
        json.dumps(result, allow_nan=False)

    def test_identity_and_nonmutation(self):
        rows = [island(1, rectangle(.1, .1, .2, .2))]
        report = self.measured(rows)
        self.assertEqual(report['catalogue_identity'], geometry.audit_catalogue(rows)['catalogue_identity'])
        reordered = [dict(reversed(list(rows[0].items())))]
        self.assertEqual(report['catalogue_identity'], self.measured(reordered)['catalogue_identity'])

    def test_twenty_thousand_triangles_bvh_smoke(self):
        rows = [island(y*100+x+1, rectangle((x+.1)/100, (y+.1)/100, (x+.5)/100, (y+.5)/100))
                for y in range(100) for x in range(100)]
        result = self.measured(rows)
        self.assertEqual(result['counts']['checked_triangles'], 20000)
        self.assertAlmostEqual(result['minimum_interchart']['normalized'], .006)
        # Count actual geometric evaluations instead of imposing a host-speed timing policy.
        self.assertLess(result['counts']['triangle_pair_distances'], 2000000)

    def test_stdlib_cli_measurement_does_not_apply_gap_threshold(self):
        with tempfile.TemporaryDirectory() as temp:
            source, output = Path(temp)/'islands.json', Path(temp)/'spacing.json'
            source.write_text(json.dumps([island(1, rectangle(-.02, .1, .2, .3))]), encoding='utf8')
            command = [sys.executable, '-B', '-S', str(HELPER), str(source), '--spacing',
                       '--pixels', '1024', '--output', str(output)]
            process = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(output.read_text(encoding='utf8'))
            self.assertEqual(result['schema'], geometry.SPACING_SCHEMA)
            self.assertAlmostEqual(result['minimum_signed_border']['pixels'], -20.48)
            self.assertIn('source_file_sha256', result['catalogue_identity'])
            self.assertNotIn('passed', result)
            source.write_text('[]', encoding='utf8')
            process = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(process.returncode, 1, process.stderr)
            self.assertFalse(json.loads(output.read_text(encoding='utf8'))['valid'])


if __name__ == '__main__':
    unittest.main()
