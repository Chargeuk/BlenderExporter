"""Focused glass recipe preparation regressions; fake RNA, no bpy/render/export."""
import importlib.util,os
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
ROOT=Path(os.environ.get('BJS_TEST_ADDON_ROOT',Path(__file__).resolve().parents[1]/'src/babylon_js'))
spec=importlib.util.spec_from_file_location('glass_recipe_contract',ROOT/'environment_tools/recipe_cli.py')
recipe=importlib.util.module_from_spec(spec);spec.loader.exec_module(recipe)
def material(name='Glass'):
    node=NS(type='BSDF_PRINCIPLED',inputs={'Base Color':NS(default_value=(.67,.86,.94,1)),
        'Alpha':NS(default_value=.18),'Roughness':NS(default_value=.09),'IOR':NS(default_value=1.45),'Transmission Weight':NS(default_value=.9)})
    return NS(name=name,node_tree=NS(nodes=[node]),overloadChannels=False,transparencyMode='0')
def state(mat):
    return (tuple(mat.node_tree.nodes[0].inputs['Base Color'].default_value),mat.overloadChannels,mat.transparencyMode,
        tuple((k,v.default_value)for k,v in mat.node_tree.nodes[0].inputs.items() if k!='Base Color'))
class GlassRecipeTests(unittest.TestCase):
    def test_legacy_default_white_alpha_override_unchanged(self):
        mat=material();facts=recipe.prepare_export_glass({'Glass':mat},[dict(material='Glass',transparency_mode=2)])
        self.assertEqual(mat.node_tree.nodes[0].inputs['Base Color'].default_value,(1,1,1,.18))
        self.assertTrue(mat.overloadChannels);self.assertEqual(mat.transparencyMode,'2');self.assertEqual(facts,{'Glass':.18})
    def test_explicit_false_remains_legacy(self):
        mat=material();recipe.prepare_export_glass({'Glass':mat},[dict(material='Glass',transparency_mode=2,preserve_rgb=False)])
        self.assertEqual(mat.node_tree.nodes[0].inputs['Base Color'].default_value,(1,1,1,.18))
    def test_true_keeps_native_rgb_and_other_principled_parameters(self):
        mat=material();before=state(mat)[3]
        recipe.prepare_export_glass({'Glass':mat},[dict(material='Glass',transparency_mode=2,preserve_rgb=True)])
        self.assertEqual(mat.node_tree.nodes[0].inputs['Base Color'].default_value,(.67,.86,.94,.18))
        self.assertEqual(state(mat)[3],before);self.assertTrue(mat.overloadChannels);self.assertEqual(mat.transparencyMode,'2')
    def test_invalid_nonboolean_values_are_rejected(self):
        for value in (None,0,1,'true','false',[],{}):
            with self.subTest(value=value):
                mat=material();before=state(mat)
                with self.assertRaisesRegex(ValueError,'preserve_rgb must be a boolean'):
                    recipe.prepare_export_glass({'Glass':mat},[dict(material='Glass',transparency_mode=2,preserve_rgb=value)])
                self.assertEqual(state(mat),before)
    def test_all_rules_validated_before_any_glass_mutation(self):
        one=material('one');two=material('two');before=[state(one),state(two)]
        with self.assertRaises(ValueError):recipe.prepare_export_glass({'one':one,'two':two},[
            dict(material='one',transparency_mode=2,preserve_rgb=True),dict(material='two',transparency_mode=2,preserve_rgb='true')])
        self.assertEqual([state(one),state(two)],before)
    def test_empty_rules_are_noop(self):self.assertEqual(recipe.prepare_export_glass({},[]),{})
if __name__=='__main__':unittest.main()
