"""Exact-source reuse checks saved labels once against validated native samples."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src/babylon_js/environment_tools'))
from common import sha
import native_recombine
from native_ownership import catalogue_identity, validate_native_ids


class NativeRecombineTests(unittest.TestCase):
    def fixture(self, root, *, duplicate_chart=False):
        original = root/'original'
        tools = root/'tools'
        original.mkdir()
        source = root/'source.blend'
        source.write_bytes(b'immutable saved source')
        recipe = {'blend': source.name, 'size': 2,
                  'ownership': {'schema_version': 1, 'method': 'native-island-id'}}
        config = root/'recipe.json'
        config.write_text(json.dumps(recipe), encoding='utf8')
        c = dict(recipe, _source=str(source), _config=str(config), _root=str(root))
        inputs = {str(source): sha(source), str(config): sha(config)}
        tool_hashes = {}
        for name in ('worker.py', 'native_ownership.py'):
            relative = 'src/babylon_js/environment_tools/'+name
            tool = tools/relative
            tool.parent.mkdir(parents=True, exist_ok=True)
            tool.write_text('# retained original '+name, encoding='utf8')
            inputs[str(tool)] = tool_hashes[relative] = sha(tool)
        (tools/'source-copy-provenance.json').write_text(json.dumps({'final_source_files': [
            {'relative_path': name, 'sha256': digest} for name, digest in tool_hashes.items()]}), encoding='utf8')
        catalogue = [{'id': 1, 'object': 'A', 'polygon_root': 0, 'triangles': [[[0, 0], [1, 0], [0, 1]]]},
                     {'id': 2, 'object': 'A' if duplicate_chart else 'B', 'polygon_root': 0,
                      'triangles': [[[1, 0], [1, 1], [0, 1]]]}]
        ids = np.array([[[1, 1, 1, 1], [0, 0, 0, 0]], [[0, 0, 0, 0], [2, 2, 2, 1]]], np.float32)
        for folder in ('bake', 'geometry', 'ownership', 'masters'):
            (original/folder).mkdir()
        (original/'geometry/result.json').write_text(json.dumps({'geometry_sha256': 'geometry'}), encoding='utf8')
        (original/'bake/islands.json').write_text(json.dumps(catalogue), encoding='utf8')
        np.save(original/'bake/native_island_ids.npy', ids)
        for mode in ('direct', 'indirect'):
            np.save(original/'bake'/ (mode+'_raw.npy'), ids)
        labels = np.array([[1, 0], [0, 2]], np.int32)
        np.save(original/'ownership/labels.npy', labels)
        (original/'ownership/result.json').write_text('{}', encoding='utf8')
        for name in ('direct_final.exr', 'indirect_final.exr', 'island_ids.exr', 'albedo_uv.exr', 'albedo_uv.exr.json'):
            (original/'masters'/name).write_bytes(b'retained master')
        native = dict(schema_version=1, method='native-island-id', kind='native-island-id-ownership',
            source_sha256=sha(source), config_sha256=sha(config), scene=None, size=2, uv='SimpleBake',
            geometry_sha256='geometry', geometry_record_sha256=sha(original/'geometry/result.json'),
            catalogue_file_sha256=sha(original/'bake/islands.json'), catalogue_identity_sha256=catalogue_identity(catalogue),
            native_ids_sha256=sha(original/'bake/native_island_ids.npy'),
            raw_sha256={mode: sha(original/'bake'/(mode+'_raw.npy')) for mode in ('direct', 'indirect')},
            build_inputs_sha256=hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
            helper_sha256=tool_hashes['src/babylon_js/environment_tools/native_ownership.py'],
            worker_sha256=tool_hashes['src/babylon_js/environment_tools/worker.py'])
        (original/'bake/native_ownership.provenance.json').write_text(json.dumps(native), encoding='utf8')
        outputs = {str(path.relative_to(original)): sha(path) for path in original.rglob('*') if path.is_file()}
        build = {'schema_version': 1, 'inputs': inputs, 'stages': {
            name: {'outputs': outputs if name == 'combine' else {}}
            for name in ('preflight', 'geometry', 'bake', 'ownership', 'process', 'combine')}}
        (original/'build.json').write_text(json.dumps(build), encoding='utf8')
        return c, original, tools, build

    def test_original_native_raster_is_validated_once_and_report_is_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            c, original, tools, _ = self.fixture(Path(tmp))
            with patch.object(native_recombine, 'validate_native_ids', wraps=validate_native_ids) as validate, \
                 patch('native_ownership.validate_native_ids', validate):
                plan = native_recombine.validate_original(c, original, tools)
            self.assertEqual(validate.call_count, 1)
            catalogue = json.loads((original/'bake/islands.json').read_text())
            self.assertEqual(plan['catalogue_identity_sha256'], catalogue_identity(catalogue))
            self.assertIn('ownership/labels.npy', plan['copies'])

    def test_saved_labels_must_keep_schema_known_ids_and_exact_native_footprint(self):
        variants = [np.array([[1, 0], [0, 2]], float), np.ones((3, 3), np.int32),
                    np.array([[1, -1], [0, 2]], np.int32), np.zeros((2, 2), np.int32),
                    np.array([[1, 0], [0, 3]], np.int32), np.array([[2, 0], [0, 1]], np.int32),
                    np.array([[1, 2], [0, 0]], np.int32)]
        for labels in variants:
            with self.subTest(labels=labels), tempfile.TemporaryDirectory() as tmp:
                c, original, tools, build = self.fixture(Path(tmp))
                path = original/'ownership/labels.npy'
                np.save(path, labels)
                build['stages']['combine']['outputs'][str(path.relative_to(original))] = sha(path)
                (original/'build.json').write_text(json.dumps(build), encoding='utf8')
                with self.assertRaises(ValueError):
                    native_recombine.validate_original(c, original, tools)

    def test_duplicate_object_root_is_rejected_even_with_unique_native_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            c, original, tools, _ = self.fixture(Path(tmp), duplicate_chart=True)
            with self.assertRaisesRegex(ValueError, 'duplicate chart identity'):
                native_recombine.validate_original(c, original, tools)


if __name__ == '__main__':
    unittest.main()
