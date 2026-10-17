"""Analytic UV geometry regressions; requires only standard-library Python."""
import copy
import importlib.util
import itertools
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HELPER = Path(__file__).resolve().parents[1] / 'src/babylon_js/environment_tools/uv_geometry.py'
spec = importlib.util.spec_from_file_location('uv_geometry', HELPER)
geometry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(geometry)
audit = geometry.audit_catalogue
intersection = geometry.triangle_intersection_area

A = [[0, 0], [1, 0], [0, 1]]
B = [[0.5, 0], [1, 0.5], [0.5, 1]]


def island(k, triangles, label=None):
    return {'id': k, 'object': label or 'Object' + str(k), 'polygon_root': 0,
            'triangles': triangles}


class UVGeometryTests(unittest.TestCase):
    def test_crossing_known_area(self):
        # Intersection corners (.5,0), (.75,.25), (.5,.5): base .5, altitude .25.
        self.assertAlmostEqual(intersection(A, B), 1 / 16, places=14)

    def test_containment_known_area(self):
        small = [[0.125, 0.125], [0.375, 0.125], [0.125, 0.375]]
        self.assertAlmostEqual(intersection(A, small), 1 / 32, places=14)

    def test_duplicate_known_area(self):
        self.assertAlmostEqual(intersection(A, A), 0.5, places=14)

    def test_every_winding_and_triangle_order(self):
        for first, second in itertools.product(itertools.permutations(A), itertools.permutations(B)):
            with self.subTest(first=first, second=second):
                self.assertAlmostEqual(intersection(first, second), 1 / 16, places=14)
                self.assertAlmostEqual(intersection(second, first), 1 / 16, places=14)

    def test_shared_diagonal_is_legal_within_island(self):
        upper = [[1, 0], [1, 1], [0, 1]]
        report = audit([island(1, [A, upper])])
        self.assertTrue(report['passed'])
        self.assertEqual(report['counts']['candidate_pairs'], 1)
        self.assertEqual(intersection(A, upper), 0)

    def test_shared_edge_and_vertex_are_legal_across_islands(self):
        first = [[0, 0], [0.5, 0], [0, 0.5]]
        edge = [[0.5, 0], [0.5, 0.5], [0, 0.5]]
        vertex = [[0.5, 0.5], [1, 0.5], [0.5, 1]]
        self.assertTrue(audit([island(1, [first]), island(2, [edge]), island(3, [vertex])])['passed'])
        self.assertEqual(intersection(first, edge), 0)
        self.assertEqual(intersection(edge, vertex), 0)

    def test_subpixel_duplicate_still_detected(self):
        # Whole chart sits inside one 2048 pixel cell; no pixel centre lies in it.
        tiny = [[0.1002, 0.1002], [0.1003, 0.1002], [0.1002, 0.1003]]
        report = audit([island(1, [tiny]), island(2, [tiny])])
        self.assertFalse(report['passed'])
        self.assertEqual(report['counts']['overlap_pairs'], 1)
        self.assertAlmostEqual(report['overlaps'][0]['area'], 5e-9, delta=1e-19)

    def test_within_and_across_island_identities(self):
        report = audit([island(1, [A, A], 'Wall'), island(2, [A], 'Floor')])
        self.assertEqual(report['counts']['within_island_overlap_pairs'], 1)
        self.assertEqual(report['counts']['across_island_overlap_pairs'], 2)
        self.assertEqual(report['counts']['overlap_pairs'], 3)
        detail = report['overlaps'][0]
        self.assertEqual(detail['first']['object'], 'Wall')
        self.assertEqual(detail['first']['polygon_root'], 0)
        self.assertEqual(detail['first']['triangle_index'], 0)
        self.assertEqual(detail['second']['triangle_index'], 1)

    def test_detail_cap_preserves_total(self):
        report = audit([island(1, [A] * 5)], max_details=2)
        self.assertEqual(report['counts']['overlap_pairs'], 10)
        self.assertEqual(len(report['overlaps']), 2)
        self.assertEqual(report['omitted_details']['overlaps'], 8)
        self.assertFalse(audit([island(1, [A, A])], max_details=0)['passed'])

    def test_ids_and_object_metadata(self):
        for value in (None, 0, -1, True, 1.0, '1'):
            row = island(1, [A]); row['id'] = value
            with self.subTest(value=value):
                self.assertFalse(audit([row])['passed'])
        self.assertFalse(audit([island(1, [A]), island(1, [B])])['passed'])
        for value in (None, '', '  ', 123):
            row = island(1, [A]); row['object'] = value
            self.assertFalse(audit([row])['passed'])

    def test_optional_polygon_root(self):
        row = island(1, [A]); del row['polygon_root']
        self.assertTrue(audit([row])['passed'])
        for value in (None, -1, True, '0'):
            row['polygon_root'] = value
            self.assertFalse(audit([row])['passed'])

    def test_malformed_catalogue_and_triangle_shapes(self):
        for rows in (None, {}, [], [None], [island(1, [])], [dict(id=1, object='X')]):
            self.assertFalse(audit(rows)['passed'])
        for tri in (None, [], A[:2], A + [[1, 1]], [[0, 0, 0], [1, 0], [0, 1]], ['a', 'b', 'c']):
            self.assertFalse(audit([island(1, [tri])])['passed'])

    def test_nonfinite_and_non_numeric_coordinates(self):
        for value in (float('nan'), float('inf'), -float('inf'), '0', True, None, 10 ** 1000):
            tri = copy.deepcopy(A); tri[0][0] = value
            report = audit([island(1, [tri])])
            self.assertFalse(report['passed'])
            self.assertEqual(report['counts']['checked_triangles'], 0)
            json.dumps(report, allow_nan=False)

    def test_bounds_tolerance_and_raw_coordinates_preserved(self):
        tri = [[-5e-7, 0], [0.5, 0], [0, 0.5]]
        rows = [island(1, [tri])]; before = copy.deepcopy(rows)
        report = audit(rows)
        self.assertTrue(report['passed'])
        self.assertEqual(report['bounds'][0], -5e-7)
        self.assertEqual(rows, before)
        report = audit(rows, bounds_tolerance=1e-8)
        self.assertEqual(report['counts']['bounds_violations'], 1)
        self.assertFalse(report['passed'])

    def test_degenerate_and_threshold_decisions(self):
        flat = [[0, 0], [0.5, 0.5], [1, 1]]
        tiny = [[0, 0], [1e-7, 0], [0, 1e-7]]
        report = audit([island(1, [flat, tiny])])
        self.assertEqual(report['counts']['degenerate_triangles'], 2)
        self.assertFalse(report['passed'])
        self.assertTrue(audit([island(1, [tiny])], area_epsilon=0)['passed'])
        self.assertEqual(audit([island(1, [A, B])], area_epsilon=1 / 16)['counts']['overlap_pairs'], 0)

    def test_invalid_options_raise(self):
        for options in ({'area_epsilon': -1}, {'area_epsilon': float('nan')},
                        {'bounds_tolerance': float('inf')}, {'bounds_tolerance': 2},
                        {'max_details': -1}, {'max_details': True}):
            with self.assertRaises(ValueError):
                audit([island(1, [A])], **options)

    def test_deterministic_20k_triangle_tiling(self):
        rows = []
        n = 100
        for y in range(n):
            for x in range(n):
                a, b, c, d = [x / n, y / n], [(x + 1) / n, y / n], [(x + 1) / n, (y + 1) / n], [x / n, (y + 1) / n]
                rows.append(island(len(rows) + 1, [[a, b, c], [a, c, d]]))
        report = audit(rows)
        self.assertTrue(report['passed'], report['overlaps'])
        self.assertEqual(report['counts']['checked_triangles'], 20000)
        self.assertEqual(report['counts']['candidate_pairs'], 10000)
        self.assertEqual(report['counts']['overlap_pairs'], 0)

    def test_canonical_identity_ignores_mapping_key_order(self):
        row = island(1, [A])
        self.assertEqual(audit([row])['catalogue_identity'], audit([dict(reversed(list(row.items())))])['catalogue_identity'])
        self.assertNotEqual(audit([row])['catalogue_identity'], audit([island(1, [B])])['catalogue_identity'])

    def test_cli_stdlib_only_pass_fail_and_invalid_json(self):
        with tempfile.TemporaryDirectory() as temp:
            source, output = Path(temp) / 'islands.json', Path(temp) / 'audit.json'
            for rows, code in (([island(1, [A])], 0), ([island(1, [A, A])], 1), ([], 1)):
                source.write_text(json.dumps(rows), encoding='utf8')
                result = subprocess.run([sys.executable, '-B', '-S', str(HELPER), str(source), '--output', str(output)], capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, code, result.stderr)
                report = json.loads(output.read_text(encoding='utf8'))
                self.assertEqual(report['passed'], code == 0)
                self.assertIn('source_file_sha256', report['catalogue_identity'])
            source.write_text('{invalid', encoding='utf8')
            result = subprocess.run([sys.executable, '-B', '-S', str(HELPER), str(source), '--output', str(output)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 1)
            self.assertIn('error', json.loads(output.read_text(encoding='utf8')))

    def test_cli_refuses_input_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'islands.json'
            raw = json.dumps([island(1, [A])]); source.write_text(raw, encoding='utf8')
            result = subprocess.run([sys.executable, '-B', '-S', str(HELPER), str(source), '--output', str(source)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(source.read_text(encoding='utf8'), raw)


if __name__ == '__main__':
    unittest.main()
