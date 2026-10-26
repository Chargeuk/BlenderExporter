"""Geometric UV overlap and thin-chart diagnostics without bake alpha or SciPy."""
import sys
import importlib.util
import subprocess
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src/babylon_js/workflow_tools'))
from uv_audit import audit, chart_ids, convex_hull, hull_minimum_width, triangle_intersection_area


def row(points, index=0, obj='A', chart=1):
    return {'object': obj, 'triangle': index, 'polygon': index, 'loops': [index*3+i for i in range(3)],
            'vertices': [index*3+i for i in range(3)], 'chart': chart, 'uv': points}


def check(rows, **settings):
    return audit({'schema_version': 1, 'triangles': rows, 'provenance': {'fixture': True}},
                 pixels=2048, overlap_area_tolerance=1e-12, **settings)


class UVAuditTests(unittest.TestCase):
    def setUp(self):
        self.first = [[0, 0], [.5, 0], [0, .5]]
        self.overlap = [[.25, 0], [.75, 0], [.25, .5]]
        self.shared = [[.5, 0], [.5, .5], [0, .5]]

    def test_intersection_and_negative_orientation(self):
        for a in (self.first, self.first[::-1]):
            for b in (self.overlap, self.overlap[::-1]):
                self.assertAlmostEqual(triangle_intersection_area(a, b), .03125)

    def test_intersection_wrapper_matches_canonical_geometry_and_validates_inputs(self):
        helper = Path(__file__).resolve().parents[1]/'src/babylon_js/environment_tools/uv_geometry.py'
        spec = importlib.util.spec_from_file_location('canonical_uv_geometry_test', helper)
        geometry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(geometry)
        triangles = [self.first, self.overlap, self.shared,
                     [[.1, .1], [.2, .1], [.1, .2]],
                     [[.5-1e-5, 0], [1-1e-5, 0], [.5-1e-5, .5]]]
        for first in triangles:
            for second in triangles:
                for a, b in ((first, second), (first[::-1], second[::-1])):
                    self.assertEqual(triangle_intersection_area(a, b), geometry.triangle_intersection_area(a, b))
        for invalid in ([[0, 0], [1, 0]], [[0, 0], [1, 0], [0, np.inf]]):
            for a, b in ((invalid, self.first), (self.first, invalid)):
                with self.assertRaisesRegex(ValueError, 'finite 3x2'):
                    triangle_intersection_area(a, b)
        zero = [[0, 0], [0, 0], [0, 0]]
        self.assertEqual(triangle_intersection_area(zero, self.first), 0)
        self.assertEqual(triangle_intersection_area(self.first, zero), 0)

    def test_import_by_path_does_not_load_blender_package(self):
        helper = Path(__file__).resolve().parents[1]/'src/babylon_js/workflow_tools/uv_audit.py'
        code = """import importlib.util, sys
class NoBlender:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'bpy' or fullname == 'babylon_js' or fullname.startswith('babylon_js.'):
            raise ImportError('Unexpected Blender package import: ' + fullname)
sys.meta_path.insert(0, NoBlender())
spec = importlib.util.spec_from_file_location('standalone_audit', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert module.triangle_intersection_area([[0,0],[1,0],[0,1]], [[0,0],[0,1],[1,0]]) == .5
"""
        result = subprocess.run([sys.executable, '-B', '-c', code, str(helper)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_separated_and_shared_edge_are_zero_area(self):
        self.assertEqual(triangle_intersection_area(self.first, self.shared), 0)
        self.assertEqual(triangle_intersection_area(self.first, [[.6, .6], [.9, .6], [.6, .9]]), 0)
        report = check([row(self.first), row(self.shared, 1)])
        self.assertEqual(report['totals']['positive_overlap_pairs'], 0)
        self.assertEqual(report['status'], 'passed')

    def test_overlap_within_chart_and_across_chart_object(self):
        for second, metric in ((row(self.overlap, 1), 'same_chart_overlap_pairs'),
                               (row(self.overlap, 1, chart=2), 'same_object_cross_chart_overlap_pairs'),
                               (row(self.overlap, obj='B'), 'cross_object_overlap_pairs')):
            report = check([row(self.first), second])
            self.assertEqual(report['totals'][metric], 1)
            self.assertAlmostEqual(report['overlap_samples'][0]['area_uv2'], .03125)
            self.assertEqual(report['overlap_samples'][0]['first']['loops'], [0, 1, 2])

    def test_positive_tiny_overlap_respects_explicit_area_tolerance(self):
        tiny = [[.5-1e-5, 0], [1-1e-5, 0], [.5-1e-5, .5]]
        area = triangle_intersection_area(self.first, tiny)
        self.assertGreater(area, 1e-12)
        self.assertLess(area, 1e-9)
        self.assertEqual(check([row(self.first), row(tiny, 1)])['totals']['positive_overlap_pairs'], 1)
        report = audit({'schema_version': 1, 'triangles': [row(self.first), row(tiny, 1)]},
                       pixels=2048, overlap_area_tolerance=1e-9)
        self.assertEqual(report['totals']['positive_overlap_pairs'], 0)

    def test_degenerate_and_out_of_bounds_are_reported(self):
        report = check([row([[0, 0], [.1, .1], [.2, .2]]),
                        row([[-.1, .6], [.1, .6], [.1, .8]], 1, chart=2)])
        self.assertEqual(report['totals']['degenerate_triangles'], 1)
        self.assertEqual(report['totals']['out_of_bounds_triangles'], 1)
        self.assertEqual(report['status'], 'issues')
        self.assertEqual(report['settings']['actual_bounds_uv'][0][0], -.1)

    def test_chart_connectivity_geometry_uv_edges_and_object_membership(self):
        rows = [row([[0, 0], [.5, 0], [.5, .5]]), row([[0, 0], [.5, .5], [0, .5]], 1)]
        rows[0]['vertices'] = [0, 1, 2]
        rows[1]['vertices'] = [0, 2, 3]
        for item in rows: del item['chart']
        self.assertEqual(chart_ids(rows), [1, 1])
        rows.append(dict(rows[0], object='B'))
        self.assertEqual(chart_ids(rows), [1, 1, 2])
        report = check(rows)
        self.assertEqual(report['totals']['charts'], 2)
        self.assertEqual(report['totals']['cross_object_overlap_pairs'], 1)

    def test_minimum_altitude_and_caliper_are_diagnostics_not_deletion(self):
        report = check([row([[0, 0], [.8, 0], [.4, 1e-5]])])
        chart = report['charts'][0]
        self.assertAlmostEqual(chart['minimum_triangle_altitude_pixels'], .02048)
        self.assertAlmostEqual(chart['convex_hull_minimum_caliper_width_pixels'], .02048)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['totals']['triangles'], 1)
        self.assertTrue(any('not an internal neck' in limit for limit in report['limits']))
        hull = convex_hull([[0, 0], [1, 0], [1, .1], [.1, .1], [.1, 1], [0, 1]])
        self.assertGreater(hull_minimum_width(hull), .2)

    def test_grid_broadphase_avoids_whole_mesh_pair_scan(self):
        rows = []
        for y in range(45):
            for x in range(45):
                u, v = x/45, y/45
                rows.append(row([[u, v], [u+.01, v], [u, v+.01]], len(rows)))
        report = check(rows)
        self.assertTrue(report['complete'])
        self.assertEqual(report['totals']['positive_overlap_pairs'], 0)
        self.assertLess(report['totals']['grid_candidate_pairs'], len(rows)*20)

    def test_candidate_limit_never_reports_pass(self):
        report = check([row(self.first, index, obj=str(index)) for index in range(10)], max_candidate_pairs=3)
        self.assertFalse(report['complete'])
        self.assertEqual(report['status'], 'incomplete')
        self.assertEqual(report['totals']['grid_candidate_pairs'], 3)

    def test_malformed_and_nonfinite_snapshots_rejected(self):
        for tolerance in (0, -1, float('nan')):
            with self.assertRaises(ValueError):
                audit({'schema_version': 1, 'triangles': [row(self.first)]}, pixels=2048, overlap_area_tolerance=tolerance)
        with self.assertRaisesRegex(ValueError, 'Duplicate'): check([row(self.first), row(self.first)])
        with self.assertRaisesRegex(ValueError, 'finite'): check([row([[0, 0], [np.nan, 0], [0, 1]])])


class UVClearanceTests(unittest.TestCase):
    @staticmethod
    def square(x, y, size, chart, start=0, obj='A'):
        return [row([[x, y], [x+size, y], [x+size, y+size]], start, obj, chart),
                row([[x, y], [x+size, y+size], [x, y+size]], start+1, obj, chart)]

    def measure(self, rows, **settings):
        return check(rows, measure_clearance=True, **settings)['clearance']

    def test_opt_in_preserves_default_report(self):
        self.assertNotIn('clearance', check(self.square(.1, .1, .2, 1)))

    def test_analytical_gap_border_and_witness(self):
        rows = self.square(.1, .1, .2, 1)+self.square(.4, .1, .2, 2, 2)
        result = self.measure(rows)
        self.assertTrue(result['complete'])
        self.assertAlmostEqual(result['minimum_inter_chart_clearance_pixels'], .1*2048)
        self.assertAlmostEqual(result['minimum_tile_border_distance_pixels'], .1*2048)
        self.assertAlmostEqual(result['signed_tile_inset_pixels'], .1*2048)
        witness = result['inter_chart_witness']
        self.assertNotEqual(witness['first']['chart'], witness['second']['chart'])
        self.assertAlmostEqual(np.linalg.norm(np.subtract(witness['first']['point_uv'], witness['second']['point_uv']))*2048, .1*2048)
        for item in witness.values():
            source = next(r for r in rows if r['triangle'] == item['triangle'])
            edge = item['segment']
            self.assertIsNotNone(edge)
            a, b = np.array(source['uv'][edge]), np.array(source['uv'][(edge+1) % 3])
            p = np.array(item['point_uv'])
            self.assertAlmostEqual(float(np.cross(b-a, p-a)), 0.)
            self.assertLessEqual(float(np.dot(p-a, p-b)), 1e-15)

    def test_rotation_reversed_winding_and_slivers(self):
        base = self.square(.1, .1, .1, 1)+self.square(.23, .1, .1, 2, 2)
        angle = .43
        rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
        for item in base:
            item['uv'] = ((np.asarray(item['uv'])-.2)@rotation+.5).tolist()[::-1]
        self.assertAlmostEqual(self.measure(base)['minimum_inter_chart_clearance_pixels'], .03*2048)
        slivers = [row([[.1, .1], [.4, .1], [.2, .100000001]], chart=1),
                   row([[.1, .2], [.4, .2], [.2, .200000001]], 1, chart=2)]
        self.assertAlmostEqual(self.measure(slivers)['minimum_inter_chart_clearance_pixels'], (.1-1e-9)*2048)

    def test_concave_union_and_internal_diagonal_do_not_invent_gap(self):
        # L-shaped chart: the second chart occupies its empty upper-right notch.
        outer = self.square(.1, .1, .2, 1)
        outer += [row([[.1, .3], [.2, .3], [.2, .5]], 2, chart=1),
                  row([[.1, .3], [.2, .5], [.1, .5]], 3, chart=1)]
        notch = self.square(.3, .4, .1, 2, 4)
        self.assertAlmostEqual(self.measure(outer+notch)['minimum_inter_chart_clearance_pixels'], .1*2048)
        # Each square's internal triangulation diagonal is ignored as a pair.
        same = self.square(.1, .1, .2, 1)
        self.assertIsNone(self.measure(same)['minimum_inter_chart_clearance_pixels'])

    def test_touch_crossing_containment_and_tiny_overlap_are_zero(self):
        first = row([[.1, .1], [.7, .1], [.1, .7]])
        cases = [row([[.2, .2], [.21, .2], [.2, .21]], 1, chart=2),
                 row([[.7, .1], [.8, .1], [.7, .2]], 1, chart=2),
                 row([[.4, .05], [.45, .4], [.5, .05]], 1, chart=2),
                 row([[.7-1e-7, .1], [.8, .1], [.7-1e-7, .2]], 1, chart=2)]
        for other in cases:
            for winding in (other['uv'], other['uv'][::-1]):
                result = self.measure([first, dict(other, uv=winding)])
                self.assertEqual(result['minimum_inter_chart_clearance_pixels'], 0.)
        # Even an overlap ignored by the original area threshold cannot be a gap.
        tiny = check([first, cases[-1]], measure_clearance=True)
        self.assertEqual(tiny['totals']['positive_overlap_pairs'], 0)
        self.assertEqual(tiny['clearance']['minimum_inter_chart_clearance_pixels'], 0.)

    def test_distinct_objects_are_distinct_charts_and_degeneracy_is_retained(self):
        rows = [row([[.1, .1], [.2, .1], [.1, .2]], obj='A'),
                row([[.3, .1], [.3, .1], [.3, .1]], obj='B')]
        report = check(rows, measure_clearance=True)
        self.assertEqual(report['totals']['degenerate_triangles'], 1)
        self.assertAlmostEqual(report['clearance']['minimum_inter_chart_clearance_pixels'], .1*2048)

    def test_border_crossing_and_wholly_outside_are_distinguished(self):
        crossing = self.measure([row([[-.1, .2], [.1, .2], [.1, .3]])])
        self.assertEqual(crossing['minimum_tile_border_distance_pixels'], 0.)
        self.assertAlmostEqual(crossing['signed_tile_inset_pixels'], -.1*2048)
        outside = self.measure([row([[1.1, .2], [1.2, .2], [1.1, .3]])])
        self.assertAlmostEqual(outside['minimum_tile_border_distance_pixels'], .1*2048)
        self.assertAlmostEqual(outside['signed_tile_inset_pixels'], -.2*2048)

    def test_tolerance_and_candidate_cap_have_explicit_semantics(self):
        rows = self.square(.1, .1, .1, 1)+self.square(.20001, .1, .1, 2, 2)
        self.assertGreater(self.measure(rows)['minimum_inter_chart_clearance_pixels'], 0)
        self.assertEqual(self.measure(rows, clearance_tolerance_pixels=.1)['minimum_inter_chart_clearance_pixels'], 0.)
        rows = [row([[x, .1], [x+.01, .1], [x, .11]], i, chart=i)
                for i, x in enumerate((.1, .8, .4))]
        report = check(rows, measure_clearance=True, max_clearance_candidate_pairs=1)
        result = report['clearance']
        self.assertFalse(result['complete'])
        self.assertEqual(report['status'], 'incomplete')
        self.assertIsNone(result['minimum_inter_chart_clearance_pixels'])
        self.assertIsNotNone(result['observed_inter_chart_upper_bound_pixels'])
        self.assertEqual(result['counts']['tested_triangle_pairs'], 1)

    def test_empty_nonfinite_and_bad_clearance_settings_are_rejected(self):
        for rows in ([], [row([[0, 0], [1, 0], [0, np.inf]])]):
            with self.assertRaises(ValueError): self.measure(rows)
        for tolerance in (-1., float('nan'), float('inf')):
            with self.assertRaises(ValueError): self.measure(self.square(.1, .1, .2, 1), clearance_tolerance_pixels=tolerance)
        with self.assertRaises(ValueError): self.measure(self.square(.1, .1, .2, 1), max_clearance_candidate_pairs=0)

    def test_aabb_broadphase_does_not_allocate_or_visit_all_pairs(self):
        rows = [row([[x/30+.001, y/30+.001], [x/30+.01, y/30+.001], [x/30+.001, y/30+.01]],
                    y*30+x, chart=y*30+x) for y in range(30) for x in range(30)]
        result = self.measure(rows)
        self.assertTrue(result['complete'])
        self.assertAlmostEqual(result['minimum_inter_chart_clearance_pixels'], (1/30-.009)*2048)
        self.assertLess(result['counts']['tested_triangle_pairs'], len(rows)*20)


if __name__ == '__main__': unittest.main()
