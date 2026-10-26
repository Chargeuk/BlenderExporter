"""Opt-in intrinsic art preparation: protected pixels, exact levels and guarded I/O."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('intrinsic_core', ROOT / 'src/babylon_js/ktx_conversion.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


class IntrinsicPaletteTests(unittest.TestCase):
    def fixture(self):
        # Four known table-0 levels plus arbitrary nonopaque protected pixels.
        palette = np.array([[25,58,58], [31,64,64], [35,68,68], [41,74,74]], np.uint8)
        image = np.zeros((3, 5, 4), np.uint8)
        image[:] = [109, 83, 227, 17]
        image[0, :4, :3] = palette
        image[1, :2, :3] = [[10,20,30], [11,23,35]]
        return image, palette, [dict(name='cloth', rect=[0,0,4,1], method='rgb5-luma',
                                    rgb5=[4,8,8], modifiers=[-8,-2,2,8]),
                                dict(name='flat', rect=[0,1,2,2], method='flat-mean')]

    def test_empty_operation_is_exact_noop_and_does_not_mutate(self):
        image, _, _ = self.fixture()
        before = image.copy()
        result, proof = core.intrinsic_palette_pixels(image, [])
        np.testing.assert_array_equal(result, before)
        np.testing.assert_array_equal(image, before)
        self.assertEqual(proof['changed_RGB_pixels'], 0)

    def test_exact_palette_flat_rounding_outside_and_alpha(self):
        image, palette, regions = self.fixture()
        before = image.copy()
        result, proof = core.intrinsic_palette_pixels(image, regions)
        np.testing.assert_array_equal(result[0,:4,:3], palette)
        np.testing.assert_array_equal(result[1,:2,:3], [[10,22,32], [10,22,32]])
        np.testing.assert_array_equal(result[...,3], image[...,3])
        np.testing.assert_array_equal(result[2], image[2])
        np.testing.assert_array_equal(result[:,4], image[:,4])
        np.testing.assert_array_equal(image, before)
        self.assertTrue(proof['outside_selected_RGBA_exact'])

    def test_nearest_source_linear_luminance_not_nearest_code(self):
        image = np.array([[[34,67,67,255], [40,73,73,255]]], np.uint8)
        region = dict(name='cloth', rect=[0,0,2,1], method='rgb5-luma', rgb5=[4,8,8], modifiers=[-8,-2,2,8])
        result, _ = core.intrinsic_palette_pixels(image, [region])
        np.testing.assert_array_equal(result, [[[35,68,68,255], [41,74,74,255]]])

    def test_rejects_overlaps_bad_bounds_palette_and_unknown_fields(self):
        image, _, regions = self.fixture()
        mutations = [dict(rect=[0,0,6,1]), dict(rect=[0,0,True,1]),
                     dict(rgb5=[True,8,8]), dict(rgb5=[32,8,8]), dict(rgb5=[0,8,8]),
                     dict(modifiers=[-8,-2,2,float('nan')]), dict(modifiers=[-8,2,-2,8]),
                     dict(method='lighting-fit'), dict(unknown=1)]
        for change in mutations:
            with self.subTest(change=change), self.assertRaises(ValueError):
                core.intrinsic_palette_pixels(image, [{**regions[0], **change}])
        with self.assertRaisesRegex(ValueError, 'overlap'):
            core.intrinsic_palette_pixels(image, [regions[0], {**regions[0], 'name':'other'}])
        with self.assertRaises(ValueError):
            core.intrinsic_palette_pixels(image.astype(np.float32), [])

    def io_fixture(self, root):
        image, _, regions = self.fixture()
        source = root/'source.png'
        Image.fromarray(image).save(source)
        recipe = dict(schema_version=1, color_space='srgb', dimensions=[5,3],
                      input=dict(path='source.png', sha256=core.sha(source)), regions=regions,
                      output='art.png')
        path = root/'recipe.json'
        path.write_text(json.dumps(recipe), encoding='utf8')
        return source, path, recipe

    def test_hash_bound_file_roundtrip_and_fresh_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, path, _ = self.io_fixture(root)
            original = source.read_bytes()
            receipt = core.prepare_intrinsic_palette(path, root/'result')
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(receipt['output']['sha256'], core.sha(root/'result/art.png'))
            self.assertEqual(receipt['helper']['sha256'], core.sha(Path(core.__file__)))
            self.assertTrue(receipt['PNG_reload_exact'])
            with self.assertRaises(FileExistsError):
                core.prepare_intrinsic_palette(path, root/'result')
            self.assertEqual(source.read_bytes(), original)

    def test_bad_input_hash_dimensions_schema_and_expected_output_write_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, path, recipe = self.io_fixture(root)
            variants = [{**recipe, 'input':dict(path='source.png', sha256='0'*64)},
                        {**recipe, 'dimensions':[4,3]}, {**recipe, 'color_space':'linear'},
                        {**recipe, 'schema_version':True}, {**recipe, 'output':'../overwrite.png'},
                        {**recipe, 'expected_output_sha256':'0'*64},
                        {**recipe, 'unrecognized':True}]
            for data in variants:
                with self.subTest(data=data):
                    path.write_text(json.dumps(data), encoding='utf8')
                    with self.assertRaises(ValueError):
                        core.prepare_intrinsic_palette(path, root/'absent')
                    self.assertFalse((root/'absent').exists())

    def test_rgb_input_is_not_silently_converted(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            source,path,recipe=self.io_fixture(root)
            Image.new('RGB',(5,3),(1,2,3)).save(source)
            recipe['input']['sha256']=core.sha(source)
            path.write_text(json.dumps(recipe),encoding='utf8')
            with self.assertRaisesRegex(ValueError,'RGBA8'):
                core.prepare_intrinsic_palette(path,root/'absent')
            self.assertFalse((root/'absent').exists())


if __name__ == '__main__':
    unittest.main()
