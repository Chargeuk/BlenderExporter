"""Real Blender FILE image regression for native pre-HDR export; no rendering."""
import argparse
import hashlib
import io
import json
import sys
import unittest
from pathlib import Path

import bpy

parser = argparse.ArgumentParser()
parser.add_argument('--addon-source', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--require-registered', action='store_true')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
assert bpy.app.background and not args.output.exists()
args.output.mkdir(parents=True)
sys.path.insert(0, str(args.addon_source.parent))
import babylon_js
from babylon_js.environment_tools.recipe_cli import native_combined_image

assert Path(babylon_js.__file__).resolve().parent == args.addon_source.resolve()
assert tuple(babylon_js.bl_info['version']) == (3, 3, 58)
if args.require_registered:
    assert bpy.ops.export.bjs.get_rna_type().identifier
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()


class NativeCombinedImageTests(unittest.TestCase):
    def setUp(self):
        self.directory = args.output / self._testMethodName
        self.path = self.directory / 'masters/combined.exr'
        self.path.parent.mkdir(parents=True)
        self.initial_images = set(bpy.data.images)

    def tearDown(self):
        for image in list(bpy.data.images):
            if image not in self.initial_images:
                bpy.data.images.remove(image)

    def make_file(self, size=32):
        image = bpy.data.images.new('TEMP fixture seed', size, size, alpha=True, float_buffer=True)
        image.colorspace_settings.name = 'Linear Rec.709'
        image.pixels.foreach_set([.2, .3, .4, 1.] * (size * size))
        image.filepath_raw = str(self.path)
        image.file_format = 'OPEN_EXR'
        image.save()
        bpy.data.images.remove(image)

    def resident(self, colour='Linear Rec.709'):
        image = bpy.data.images.load(str(self.path), check_existing=False)
        image.colorspace_settings.name = colour
        _ = image.pixels[0]  # Cache real pixels; especially important for stale dimensions.
        return image

    def test_absent_datablock_loads_validated_file_as_linear(self):
        self.make_file()
        digest = sha(self.path)
        count = len(bpy.data.images)
        record = native_combined_image(self.directory, 32)
        self.assertTrue(record['materialized'])
        self.assertEqual(len(bpy.data.images), count + 1)
        self.assertEqual(record['path'], str(self.path.resolve()))
        self.assertEqual(record['images'][0]['colour_space'], 'Linear Rec.709')
        self.assertEqual(record['images'][0]['dimensions'], [32, 32])
        self.assertFalse(record['source_saved'])
        self.assertEqual(sha(self.path), digest)

    def test_valid_resident_linear_or_noncolor_is_reused_unchanged(self):
        self.make_file()
        for colour in ('Linear Rec.709', 'Non-Color'):
            with self.subTest(colour=colour):
                image = self.resident(colour)
                pointer, count = image.as_pointer(), len(bpy.data.images)
                record = native_combined_image(self.directory, 32)
                self.assertFalse(record['materialized'])
                self.assertEqual(image.as_pointer(), pointer)
                self.assertEqual(len(bpy.data.images), count)
                self.assertEqual(image.colorspace_settings.name, colour)
                self.assertEqual(record['images'], [{'name': image.name, 'colour_space': colour, 'dimensions': [32, 32]}])
                bpy.data.images.remove(image)

    def test_all_valid_duplicate_residents_reused_without_another_load(self):
        self.make_file()
        images = [self.resident(), self.resident('Non-Color')]
        count = len(bpy.data.images)
        record = native_combined_image(self.directory, 32)
        self.assertFalse(record['materialized'])
        self.assertEqual(len(record['images']), 2)
        self.assertEqual(len(bpy.data.images), count)
        self.assertEqual({row['name'] for row in record['images']}, {image.name for image in images})

    def test_existing_srgb_is_rejected_without_retagging_or_loading(self):
        self.make_file()
        image = self.resident('sRGB')
        count, digest = len(bpy.data.images), sha(self.path)
        with self.assertRaisesRegex(ValueError, 'Combined interpretation/dimensions differ'):
            native_combined_image(self.directory, 32)
        self.assertEqual(image.colorspace_settings.name, 'sRGB')
        self.assertEqual(len(bpy.data.images), count)
        self.assertEqual(sha(self.path), digest)

    def test_existing_cached_wrong_dimensions_rejected_without_reload(self):
        self.make_file(16)
        image = self.resident()
        self.make_file(32)  # Valid current disk bytes, stale resident remains 16x16.
        self.assertEqual(tuple(image.size), (16, 16))
        count, digest = len(bpy.data.images), sha(self.path)
        with self.assertRaisesRegex(ValueError, 'Combined interpretation/dimensions differ'):
            native_combined_image(self.directory, 32)
        self.assertEqual(tuple(image.size), (16, 16))
        self.assertEqual(len(bpy.data.images), count)
        self.assertEqual(sha(self.path), digest)

    def test_valid_resident_does_not_hide_invalid_duplicate(self):
        self.make_file()
        self.resident()
        wrong = self.resident('sRGB')
        count = len(bpy.data.images)
        with self.assertRaisesRegex(ValueError, 'Combined interpretation/dimensions differ'):
            native_combined_image(self.directory, 32)
        self.assertEqual(wrong.colorspace_settings.name, 'sRGB')
        self.assertEqual(len(bpy.data.images), count)

    def test_missing_master_is_rejected_without_creating_datablock(self):
        count = len(bpy.data.images)
        with self.assertRaisesRegex(ValueError, 'Verified combined image is missing'):
            native_combined_image(self.directory, 32)
        self.assertEqual(len(bpy.data.images), count)
        self.assertFalse(self.path.exists())

    def test_invalid_file_cannot_be_promoted_as_valid_image(self):
        self.path.write_bytes(b'Invalid EXR fixture, not illumination data')
        digest = sha(self.path)
        with self.assertRaises((ValueError, RuntimeError)):
            native_combined_image(self.directory, 32)
        self.assertEqual(sha(self.path), digest)

    def test_materialization_does_not_save_or_change_source_scene_file(self):
        # Blender's atomic save adds a suffix; keep its Windows path below MAX_PATH.
        source = self.directory / 's.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(source))
        source_hash = sha(source)
        objects = [(obj.name, list(obj.location)) for obj in bpy.data.objects]
        material_names = list(bpy.data.materials.keys())
        self.make_file()
        record = native_combined_image(self.directory, 32)
        self.assertFalse(record['source_saved'])
        self.assertEqual(Path(bpy.data.filepath), source)
        self.assertEqual(sha(source), source_hash)
        self.assertEqual([(obj.name, list(obj.location)) for obj in bpy.data.objects], objects)
        self.assertEqual(list(bpy.data.materials.keys()), material_names)


report_stream = io.StringIO()
result = unittest.TextTestRunner(stream=report_stream, verbosity=2).run(
    unittest.defaultTestLoader.loadTestsFromTestCase(NativeCombinedImageTests))
report_text = report_stream.getvalue()
(args.output / 'unittest.log').write_text(report_text, encoding='utf-8')
sys.stderr.write(report_text)
receipt = {'passed': result.wasSuccessful(), 'tests': result.testsRun,
    'failures': len(result.failures), 'errors': len(result.errors),
    'failure_details': [{'test_id': test.id(), 'traceback': trace}
        for test, trace in result.failures],
    'error_details': [{'test_id': test.id(), 'traceback': trace}
        for test, trace in result.errors],
    'unittest_log': str(args.output / 'unittest.log'),
    'addon_version': list(babylon_js.bl_info['version']), 'addon_module': babylon_js.__file__,
    'helper_module': native_combined_image.__module__,
    'helper_file_sha256': sha(args.addon_source / 'environment_tools/recipe_cli.py'),
    'operator_registered': args.require_registered, 'rendered': False, 'baked': False,
    'scope': 'Real file-backed Blender images exercise the native export materializer; full immutable-P audit is separately required after installation.'}
(args.output / 'result.json').write_text(json.dumps(receipt, indent=2))
if not result.wasSuccessful():
    raise SystemExit(1)
