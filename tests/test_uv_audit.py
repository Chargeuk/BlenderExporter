"""Geometric UV overlap and thin-chart diagnostics without bake alpha or SciPy."""
import sys
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


if __name__ == '__main__': unittest.main()
