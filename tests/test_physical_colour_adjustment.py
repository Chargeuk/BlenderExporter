"""The disposable physical-tint contract is explicit and independent of Blender/SciPy."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src/babylon_js/environment_tools'))
from physical_colour_adjustment import validate_settings, validate_scene, verify_native_response


class PhysicalColourTests(unittest.TestCase):
    def recipe(self):
        return {'source_images': ['overlay.png'], 'physical_colour_adjustment': {
            'enabled': True, 'schema_version': 1, 'image': 'overlay.png', 'sha256': 'a'*64,
            'geometry_sha256': 'b'*64, 'strength': .7, 'objects': {'names': ['Mat']}}}

    def test_default_and_explicit_disabled_preserved(self):
        self.assertFalse(validate_settings({}))
        self.assertFalse(validate_settings({'physical_colour_adjustment': {'enabled': False}}))
        self.assertTrue(validate_settings(self.recipe()))
        recipe = self.recipe()
        recipe['physical_colour_adjustment']['strength'] = 0
        self.assertTrue(validate_settings(recipe))

    def test_explicit_saved_source_hash_uv_and_selector_required(self):
        for key in ('schema_version', 'image', 'sha256', 'geometry_sha256', 'objects', 'strength'):
            recipe = self.recipe()
            del recipe['physical_colour_adjustment'][key]
            with self.subTest(missing=key), self.assertRaises(ValueError): validate_settings(recipe)
        recipe = self.recipe()
        recipe['source_images'] = []
        with self.assertRaisesRegex(ValueError, 'source_images'): validate_settings(recipe)
        for key in ('sha256', 'geometry_sha256'):
            recipe = self.recipe()
            recipe['physical_colour_adjustment'][key] = 'unchecked'
            with self.assertRaises(ValueError): validate_settings(recipe)

    def test_strength_and_unknown_fields_rejected(self):
        for strength in (-.1, 1.1, float('nan'), float('inf'), True, '1'):
            recipe = self.recipe()
            recipe['physical_colour_adjustment']['strength'] = strength
            with self.subTest(strength=strength), self.assertRaises(ValueError): validate_settings(recipe)
        recipe = self.recipe()
        recipe['physical_colour_adjustment']['uv'] = 'Other UV'
        with self.assertRaises(ValueError): validate_settings(recipe)

    def test_scene_is_optional_exact_name(self):
        validate_scene({})
        validate_scene({'scene': 'Physical scene'})
        for value in ('', '  ', None, 1, []):
            with self.subTest(scene=value), self.assertRaises(ValueError): validate_scene({'scene': value})

    def test_native_response_same_intrinsic_tint_and_scene(self):
        tint = {'image_sha256': 'a'*64, 'geometry_sha256': 'b'*64, 'strength': 1}
        verify_native_response({'scene': 'Physical', 'physical_colour_adjustment': tint},
                               {'scene': 'Physical', 'physical_colour_adjustment': dict(tint)})
        verify_native_response({}, {})
        with self.assertRaisesRegex(ValueError, 'adjustment differ'):
            verify_native_response({'scene': 'Physical', 'physical_colour_adjustment': tint},
                                   {'scene': 'Physical', 'physical_colour_adjustment': dict(tint, strength=0)})
        with self.assertRaisesRegex(ValueError, 'scenes differ'):
            verify_native_response({'scene': 'Preview'}, {'scene': 'Physical'})


if __name__ == '__main__': unittest.main()
