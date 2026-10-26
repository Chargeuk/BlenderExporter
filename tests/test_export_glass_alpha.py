"""Optional glass-alpha preparation contract; no Blender runtime required."""
import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parents[1]
MODULE=ROOT/'src/babylon_js/environment_tools/recipe_cli.py'
spec=importlib.util.spec_from_file_location('glass_recipe_contract',MODULE)
recipe=importlib.util.module_from_spec(spec);spec.loader.exec_module(recipe)


def material(alpha=.43,linked=False):
    inputs={'Alpha':SimpleNamespace(default_value=alpha,is_linked=linked),
            'Base Color':SimpleNamespace(default_value=(.7,.8,.9,1))}
    node=SimpleNamespace(type='BSDF_PRINCIPLED',inputs=inputs)
    return SimpleNamespace(name='FixtureGlass',node_tree=SimpleNamespace(nodes=[node]),
                           overloadChannels=False,transparencyMode='0')


class GlassAlphaPreparationTests(unittest.TestCase):
    def test_absent_override_retains_legacy_alpha_and_evidence(self):
        mat=material();node=mat.node_tree.nodes[0]
        facts=recipe.prepare_glass_material(mat,dict(transparency_mode=2))
        self.assertEqual(node.inputs['Alpha'].default_value,.43)
        self.assertEqual(node.inputs['Base Color'].default_value,(1,1,1,.43))
        self.assertTrue(mat.overloadChannels);self.assertEqual(mat.transparencyMode,'2')
        self.assertEqual(facts,dict(original_alpha=.43,staged_alpha=.43,alpha_override=None,
                                   override_applied=False,original_alpha_linked=False))

    def test_explicit_scalar_and_both_endpoints(self):
        for value in (.18,0,1):
            with self.subTest(alpha=value):
                mat=material(alpha=1);node=mat.node_tree.nodes[0]
                facts=recipe.prepare_glass_material(mat,dict(alpha=value,transparency_mode=2))
                self.assertEqual(node.inputs['Alpha'].default_value,float(value))
                self.assertEqual(node.inputs['Base Color'].default_value,(1,1,1,float(value)))
                self.assertEqual(facts['original_alpha'],1)
                self.assertEqual(facts['staged_alpha'],float(value))
                self.assertEqual(facts['alpha_override'],float(value))
                self.assertTrue(facts['override_applied'])

    def test_invalid_scalars_reject_before_material_mutation(self):
        for value in (True,False,None,'0.18',[],{},-.01,1.01,float('nan'),
                      float('inf'),float('-inf'),10**1000):
            with self.subTest(alpha=type(value).__name__):
                mat=material();before=copy.deepcopy(vars(mat))
                with self.assertRaisesRegex(ValueError,'finite numeric scalar'):
                    recipe.prepare_glass_material(mat,dict(alpha=value,transparency_mode=2))
                self.assertEqual(vars(mat),before)

    def test_linked_alpha_override_is_unsupported_and_untouched(self):
        mat=material(linked=True);before=copy.deepcopy(vars(mat))
        with self.assertRaisesRegex(ValueError,'linked Alpha'):
            recipe.prepare_glass_material(mat,dict(alpha=.18,transparency_mode=2))
        self.assertEqual(vars(mat),before)

    def test_omitted_override_preserves_legacy_linked_input_behavior(self):
        mat=material(linked=True)
        facts=recipe.prepare_glass_material(mat,dict(transparency_mode=2))
        self.assertTrue(mat.node_tree.nodes[0].inputs['Alpha'].is_linked)
        self.assertEqual(facts['staged_alpha'],.43)
        self.assertTrue(facts['original_alpha_linked'])
        self.assertFalse(facts['override_applied'])

    def test_rule_data_is_never_modified(self):
        rule=dict(material='FixtureGlass',alpha=.18,transparency_mode=2)
        before=copy.deepcopy(rule)
        recipe.prepare_glass_material(material(),rule)
        self.assertEqual(rule,before)


if __name__=='__main__':unittest.main()
